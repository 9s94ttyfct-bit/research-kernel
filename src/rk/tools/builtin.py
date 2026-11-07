"""Built-in tools: projects, search, library, full text, notes, verification, checkpoints, tokens, role tasks."""

from __future__ import annotations

import importlib
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, Field

from rk.argument import (
    ArgumentService,
    ClaimIn,
    ClaimOrigin,
    EdgeIn,
    EvidenceIn,
    ObjectionIn,
)
from rk.browser.service import BrowserService
from rk.browser.tools import register_browser_tools
from rk.checkpoints import answer_checkpoint, get_checkpoint, list_checkpoints
from rk.discovery.models import Intake
from rk.discovery.store import intake_of, record_intake
from rk.discovery.tools import register_discovery_tools
from rk.flags import flag_injection
from rk.fulltext import FulltextResult, attach_pdf, fulltext_with_fallback
from rk.library.add import add_source
from rk.library.formats import FORMATS
from rk.library.models import ItemIn
from rk.llm.catalog import driver_warnings, validate_drivers
from rk.llm.drivers import DriverSpec, available_all
from rk.llm.runner import FreeText, run_task
from rk.llm.vendors import vendor_of
from rk.notes import NoteIn, NoteRejected, add_note, list_notes, text_find, text_read
from rk.orchestrator.runs import no_run_active
from rk.orchestrator.state import latest_attempt, orchestrator_drives
from rk.production.tools import register_production_tools
from rk.roles.policy import (
    INTAKE_HUMAN_ONLY,
    ROLES,
    lead_closed_after_discovery,
    tool_roles,
)
from rk.sources import openalex
from rk.sources.identifiers import Identifier, parse_identifier
from rk.sources.resolver import resolve, search
from rk.support_check import support_check
from rk.tools.registry import CallContext, Tool, ToolError, ToolRegistry
from rk.verify.records import verify_item
from rk.verify.retract import FLAGGED, retraction_status
from rk.verify.support import verify_support
from rk.workspace import Kernel

if TYPE_CHECKING:
    from rk.orchestrator.runs import RunManager


def _added_by(ctx: CallContext) -> Literal["human", "agent"]:
    return "human" if ctx.role == "cli" else "agent"


def _project(k: Kernel, slug: str):
    try:
        return k.project(slug)
    except LookupError as e:
        raise ToolError("not_found", str(e), status=404) from e


def _collection(lib, name_or_id: str | None) -> str | None:
    """Resolve a collection name or id; unknown collections are an error, never 'the whole library'."""
    if not name_or_id:
        return None
    if name_or_id.startswith("col_"):
        known = {c["id"] for c in lib.collections()}
        col = name_or_id if name_or_id in known else None
    else:
        col = lib.collection_by_name(name_or_id)
    if col is None:
        raise ToolError("not_found", f"no collection {name_or_id}", status=404)
    return col


class Empty(BaseModel):
    pass


class Out(BaseModel):
    model_config = {"extra": "allow"}


class ProjectArg(BaseModel):
    project: str


def _lead_stage_guard(k: Kernel) -> Callable[[Tool, dict, CallContext], None]:
    """The registry guard of the Lead's stage-dependent access (spec §8.1, ``lead_closed_after_discovery``): once the
    orchestrator drives the call's project (every discovery stage passed, or production began: a back-edge to question
    does not reopen it), the Lead is refused every tool that writes (and the browser) with ``stage_closed``. A call
    without a project, or with an unknown one, goes on (the tool reports it)."""
    def guard(tool: Tool, args: dict, ctx: CallContext) -> None:
        if ctx.role != "lead" or not lead_closed_after_discovery(tool.name, tool.writes):
            return
        slug = args.get("project")
        if not isinstance(slug, str) or not slug:
            return
        try:
            ps, _ = k.project(slug)
        except LookupError:
            return
        if orchestrator_drives(ps):
            raise ToolError("stage_closed", f"{tool.name} is closed to the Lead: {slug} passed question (every "
                                            f"discovery stage), or its production stages began; from protocol on the "
                                            f"orchestrator's roles do this work, and the Lead only reads and reports "
                                            f"status", status=409)
    return guard


def build_registry(k: Kernel, browser: Callable[[], BrowserService | None] | None = None,
                   runs: Callable[[], RunManager | None] | None = None) -> ToolRegistry:
    """``browser()`` returns the running built-in browser service (``rk serve``), or None; ``runs()`` returns
    ``rk serve``'s RunManager (D2 runs, and its orchestrator for the D1 stages), or None."""
    reg = ToolRegistry(k.home / "kernel.sqlite", guard=_lead_stage_guard(k))
    browser = browser or (lambda: None)

    def tool(name: str, summary: str, inp: type[BaseModel], writes: bool):
        def deco(fn):
            reg.register(Tool(name=name, summary=summary, input_model=inp, output_model=Out, roles=tool_roles(name),
                              writes=writes, handler=fn))
            return fn
        return deco

    register_browser_tools(tool, browser)
    register_discovery_tools(tool, k, runs or (lambda: None))
    register_production_tools(tool, k)

    # ---- projects ----
    class ProjectCreate(BaseModel):
        slug: str
        title: str
        field: str | None = None
        paper_type: str | None = None

    @tool("rk_project_create", "Create a research project with its library.", ProjectCreate, True)
    def _create(ctx: CallContext, a: ProjectCreate) -> Out:
        try:
            ps = k.create_project(a.slug, a.title, field=a.field, paper_type=a.paper_type, actor=ctx.actor)
        except (ValueError, FileExistsError) as e:
            raise ToolError("invalid", str(e)) from e
        return Out(project_id=ps.project_id, slug=a.slug)

    @tool("rk_project_list", "List projects.", Empty, False)
    def _list(ctx: CallContext, a: Empty) -> Out:
        return Out(projects=k.list_projects())

    @tool("rk_project_status", "Counts and event-chain integrity for a project.", ProjectArg, False)
    def _status(ctx: CallContext, a: ProjectArg) -> Out:
        ps, lib = _project(k, a.project)
        count = lambda t: ps.db.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        flagged = [i.id for i in lib.list() if i.retraction in FLAGGED]
        return Out(project=a.project, title=ps.meta("title"), items=count("items"), snapshots=count("snapshots"),
                   notes=count("notes"), verifications=count("verifications"), events=count("events"),
                   injection_flags=count("injection_flags"), flagged_items=flagged,
                   chain=ps.log.verify_chain().model_dump())

    class IntakeArgs(Intake):
        project: str

    @tool("rk_project_intake", "Record the project's intake: audience, paper type, discovery mode (d1 guided, d2 "
          "autonomous), production mode (auto = P1, collab = P2), language and number of topics. Refused while a run "
          "or a guided stage call is active; once landscape ran, only you may change the discovery mode, the "
          "production mode and the paper type.", IntakeArgs, True)
    def _intake(ctx: CallContext, a: IntakeArgs) -> Out:
        ps, _ = _project(k, a.project)
        intake = Intake.model_validate(a.model_dump(exclude={"project"}))
        m = runs() if runs is not None else None
        # the manager's lock before the project's (rk.orchestrator.runs): never ps.lock first, then the manager
        with m.idle(a.project, "the intake") if m is not None else no_run_active(ps, "the intake"):
            old = intake_of(ps)
            if old is not None and ctx.role != "cli" and latest_attempt(ps, "landscape") > 0:
                changed = [f for f in INTAKE_HUMAN_ONLY if getattr(old, f) != getattr(intake, f)]
                if changed:
                    raise ToolError("intake_frozen", f"discovery started (landscape ran): only the researcher may "
                                                     f"change {', '.join(changed)}", {"fields": changed})
            record_intake(ps, intake, actor=ctx.actor, actor_kind=_added_by(ctx))
        return Out(project=a.project, **intake.model_dump(mode="json"))

    class FlagArgs(BaseModel):
        project: str
        source_id: str
        snippet: str = Field(min_length=1, max_length=2000)
        reason: str = Field(min_length=1, max_length=500)

    @tool("rk_flag_injection", "Report text in a source that tries to instruct an AI system.", FlagArgs, True)
    def _flag(ctx: CallContext, a: FlagArgs) -> Out:
        ps, lib = _project(k, a.project)
        try:
            lib.get(a.source_id)
        except KeyError as e:
            raise ToolError("not_found", f"no item {a.source_id}", status=404) from e
        return Out(flag_id=flag_injection(ps, a.source_id, a.snippet, a.reason, actor=ctx.actor,
                                          actor_kind=_added_by(ctx)))

    # ---- checkpoints ----
    class CheckpointList(BaseModel):
        project: str
        status: Literal["open", "answered", "cancelled", "superseded"] | None = None

    @tool("rk_checkpoint_list", "List checkpoints (questions waiting for the researcher).", CheckpointList, False)
    def _chk_list(ctx: CallContext, a: CheckpointList) -> Out:
        ps, _ = _project(k, a.project)
        return Out(checkpoints=list_checkpoints(ps, a.status))

    class CheckpointAnswer(BaseModel):
        project: str
        checkpoint_id: str
        answer: dict

    # Policy: cli only. No agent role can answer; the CLI asks the human to confirm before calling this.
    @tool("rk_checkpoint_answer", "Answer a checkpoint (human only).", CheckpointAnswer, True)
    def _chk_answer(ctx: CallContext, a: CheckpointAnswer) -> Out:
        ps, _ = _project(k, a.project)
        try:
            answer_checkpoint(ps, a.checkpoint_id, a.answer, answered_by="human", actor=ctx.actor)
        except KeyError as e:
            raise ToolError("not_found", f"no checkpoint {a.checkpoint_id}", status=404) from e
        except ValueError as e:
            raise ToolError("invalid", str(e), status=409) from e
        return Out(checkpoint=get_checkpoint(ps, a.checkpoint_id))

    # ---- argument: claims, evidence, edges, objections ----
    def _arg(project: str) -> ArgumentService:
        ps, _ = _project(k, project)
        return ArgumentService(ps)

    def _arg_call(fn):
        try:
            return fn()
        except KeyError as e:
            raise ToolError("not_found", f"unknown id {e.args[0]}", status=404) from e
        except ValueError as e:
            raise ToolError("invalid", str(e)) from e

    class ClaimAdd(BaseModel):
        project: str
        claim: ClaimIn

    @tool("rk_claim_add", "Add an atomic claim (status starts as draft; only V-SUPPORT changes it).", ClaimAdd, True)
    def _claim_add(ctx: CallContext, a: ClaimAdd) -> Out:
        svc = _arg(a.project)
        vendor = svc.generator_vendor(ctx.task_id, ctx.role)
        cid = svc.add_claim(a.claim, generator_vendor=vendor, actor=ctx.actor, actor_kind=_added_by(ctx))
        return Out(claim_id=cid, generator_vendor=vendor)

    class EvidenceLink(BaseModel):
        project: str
        evidence: EvidenceIn

    @tool("rk_evidence_link", "Link verified notes to a claim as its evidence.", EvidenceLink, True)
    def _evidence_link(ctx: CallContext, a: EvidenceLink) -> Out:
        svc = _arg(a.project)
        return Out(evidence_id=_arg_call(lambda: svc.link_evidence(a.evidence, actor=ctx.actor,
                                                                   actor_kind=_added_by(ctx))))

    class EdgeAdd(BaseModel):
        project: str
        edge: EdgeIn

    @tool("rk_edge_add", "Add an argument edge (reason, evidence, warrant, objection, response).", EdgeAdd, True)
    def _edge_add(ctx: CallContext, a: EdgeAdd) -> Out:
        svc = _arg(a.project)
        return Out(edge_id=_arg_call(lambda: svc.add_edge(a.edge, actor=ctx.actor, actor_kind=_added_by(ctx))))

    class ObjectionAdd(BaseModel):
        project: str
        objection: ObjectionIn

    @tool("rk_objection_add", "Record an objection to a claim, edge or piece of evidence.", ObjectionAdd, True)
    def _objection_add(ctx: CallContext, a: ObjectionAdd) -> Out:
        svc = _arg(a.project)
        return Out(objection_id=_arg_call(lambda: svc.add_objection(a.objection, actor=ctx.actor,
                                                                    actor_kind=_added_by(ctx))))

    class ClaimGet(BaseModel):
        project: str
        claim_id: str

    @tool("rk_claim_get", "A claim with its evidence notes and objections.", ClaimGet, False)
    def _claim_get(ctx: CallContext, a: ClaimGet) -> Out:
        svc = _arg(a.project)
        return Out(claim=_arg_call(lambda: svc.get_claim(a.claim_id)))

    class ClaimList(BaseModel):
        project: str
        status: Literal["draft", "supported", "uncertain", "unsupported", "contradicted", "withdrawn"] | None = None
        origin: ClaimOrigin = "argument"

    @tool("rk_claim_list", "List the argument's claims (origin 'gaps': gap-mining checks), optionally by status.",
          ClaimList, False)
    def _claim_list(ctx: CallContext, a: ClaimList) -> Out:
        return Out(claims=_arg(a.project).list_claims(a.status, a.origin))

    # ---- search & resolve ----
    class SearchArgs(BaseModel):
        query: str
        source: Literal["openalex", "europepmc", "pubmed", "arxiv", "s2"] = "openalex"
        limit: int = Field(10, ge=1, le=100)
        project: str | None = None
        add: bool = False

    @tool("rk_search_works", "Search a scholarly source; optionally add hits to a project library.", SearchArgs, True)
    def _search(ctx: CallContext, a: SearchArgs) -> Out:
        try:
            items = search(k.ctx(), a.query, a.source, a.limit)
        except Exception as e:  # NotConfigured, BudgetExhausted, HttpError
            raise ToolError(type(e).__name__, str(e)) from e
        added: list[str] = []
        if a.add:
            if not a.project:
                raise ToolError("invalid", "add=true needs a project")
            _, lib = _project(k, a.project)
            hits_col = lib.ensure_default_collections(actor="kernel")["Search hits"]
            for it in items:
                r = lib.add_item(it.model_copy(update={"discovered_via": "search"}), added_by=_added_by(ctx),
                                 actor=ctx.actor)
                lib.collect(r.item_id, hits_col, actor=ctx.actor, actor_kind="kernel")
                added.append(r.item_id)
        return Out(results=[i.model_dump(mode="json") for i in items], added=added)

    class IdentArg(BaseModel):
        identifier: str

    @tool("rk_get_work", "Resolve an identifier (DOI, PMID, PMCID, arXiv, OpenAlex) to metadata.", IdentArg, False)
    def _get(ctx: CallContext, a: IdentArg) -> Out:
        try:
            ident = parse_identifier(a.identifier)
        except ValueError as e:
            raise ToolError("invalid", str(e)) from e
        r = resolve(k.ctx(), ident)
        if r is None:
            raise ToolError("not_found", f"{a.identifier} not found in any registry", status=404)
        return Out(item=r.item.model_dump(mode="json"), sources_used=r.sources_used,
                   retraction=retraction_status(r).model_dump())

    # ---- library ----
    class SourceAdd(BaseModel):
        project: str
        identifier: str | None = None
        item: ItemIn | None = None
        discovered_via: Literal["search", "backward", "forward", "snippet", "browser", "user", "ai_suggested",
                                "import"] = "user"
        stance: Literal["supports", "challenges", "neutral", "unknown"] | None = None
        verify: bool = True

    @tool("rk_source_add", "Add a work to the project library (by identifier or metadata) and verify it.", SourceAdd, True)
    def _add(ctx: CallContext, a: SourceAdd) -> Out:
        ps, lib = _project(k, a.project)
        res = add_source(k.ctx(), lib, identifier=a.identifier, item=a.item, discovered_via=a.discovered_via,
                         added_by=_added_by(ctx), actor=ctx.actor)
        if a.stance:
            lib.update(res.item_id, stance=a.stance, actor=ctx.actor)
        verdict: dict[str, Any] = {}
        if a.verify:
            ref, ret = verify_item(ps, lib, k.ctx(), res.item_id, actor=ctx.actor)
            verdict = {"ref_status": ref.verdict, "retraction": ret.status}
        return Out(item_id=res.item_id, created=res.created, **verdict)

    class ExpandArgs(BaseModel):
        project: str
        source_id: str
        direction: Literal["backward", "forward"]
        limit: int = Field(25, ge=1, le=200)

    @tool("rk_expand_citations", "Add references (backward) or citing works (forward) of an item.", ExpandArgs, True)
    def _expand(ctx: CallContext, a: ExpandArgs) -> Out:
        _, lib = _project(k, a.project)
        item = lib.get(a.source_id)
        c = k.ctx()
        ident = (Identifier(kind="openalex", value=item.ids.openalex) if item.ids.openalex
                 else Identifier(kind="doi", value=item.ids.doi) if item.ids.doi else None)
        if ident is None:
            raise ToolError("invalid", "item has no OpenAlex id or DOI")
        work = openalex.get_work(c.http, ident, c.settings.openalex_key)
        if work is None:
            raise ToolError("not_found", "item not found in OpenAlex", status=404)
        found = (openalex.referenced(c.http, work, api_key=c.settings.openalex_key, limit=a.limit)
                 if a.direction == "backward"
                 else openalex.cited_by(c.http, work.item.ids.openalex, api_key=c.settings.openalex_key,
                                        per_page=a.limit))
        added = [lib.add_item(it, added_by=_added_by(ctx), actor=ctx.actor).item_id for it in found]
        return Out(added=added, count=len(added))

    class SourceArg(BaseModel):
        project: str
        source_id: str

    @tool("rk_source_fetch_fulltext", "Fetch full text into a verified text layer: open-access channels first, then "
          "the library or publisher page in the built-in browser.", SourceArg, True)
    def _fetch(ctx: CallContext, a: SourceArg) -> Out:
        ps, lib = _project(k, a.project)
        r: FulltextResult | None = fulltext_with_fallback(k, ps, lib, a.source_id, call=ctx, project=a.project,
                                                          browser=browser)
        if r is None:
            raise ToolError("no_fulltext", "no open-access full text found; attach a PDF with rk_library_attach",
                            status=404)
        return Out(**r.model_dump())

    class ReadArgs(BaseModel):
        project: str
        source_id: str
        page: int | None = None
        start: int | None = None
        end: int | None = None

    @tool("rk_text_read", "Read a page (or character range) of an item's text layer.", ReadArgs, False)
    def _read(ctx: CallContext, a: ReadArgs) -> Out:
        ps, _ = _project(k, a.project)
        try:
            return Out(**text_read(ps, a.source_id, a.page, a.start, a.end).model_dump())
        except (LookupError, ValueError) as e:
            raise ToolError("invalid", str(e)) from e

    class FindArgs(BaseModel):
        project: str
        source_id: str
        query: str
        limit: int = Field(10, ge=1, le=100)

    @tool("rk_text_find", "Find a phrase in an item's text layer; returns page/char locators.", FindArgs, False)
    def _find(ctx: CallContext, a: FindArgs) -> Out:
        ps, _ = _project(k, a.project)
        try:
            return Out(matches=[m.model_dump() for m in text_find(ps, a.source_id, a.query, a.limit)])
        except LookupError as e:
            raise ToolError("invalid", str(e)) from e

    class NoteArgs(BaseModel):
        project: str
        note: NoteIn

    @tool("rk_note_add", "Add a note; quotes and paraphrases are verified against the source text.", NoteArgs, True)
    def _note(ctx: CallContext, a: NoteArgs) -> Out:
        ps, lib = _project(k, a.project)
        try:
            r = add_note(ps, lib, a.note, actor=ctx.actor, actor_kind=_added_by(ctx))
        except NoteRejected as e:
            raise ToolError("note_rejected", f"{e.reason}", details={"reason": e.reason, **e.details}) from e
        except ValueError as e:
            raise ToolError("invalid", str(e)) from e
        return Out(**r.model_dump())

    class NotesList(BaseModel):
        project: str
        source_id: str | None = None

    @tool("rk_note_list", "List notes for a project or item.", NotesList, False)
    def _notes(ctx: CallContext, a: NotesList) -> Out:
        ps, _ = _project(k, a.project)
        return Out(notes=list_notes(ps, a.source_id))

    class VerifyArgs(BaseModel):
        project: str
        target_id: str
        target_type: Literal["item", "claim"] = "item"

    @tool("rk_verify", "Verify a library item (V-REF, V-RETRACT) or a claim (V-SUPPORT, V-NUM).", VerifyArgs, True)
    def _verify(ctx: CallContext, a: VerifyArgs) -> Out:
        ps, lib = _project(k, a.project)
        if a.target_type == "claim":
            try:
                r = verify_support(k, reg, ps, a.target_id, actor=ctx.actor)
            except KeyError as e:
                raise ToolError("not_found", f"no claim {a.target_id}", status=404) from e
            return Out(support=r.model_dump(mode="json"))
        ref, ret = verify_item(ps, lib, k.ctx(), a.target_id, actor=ctx.actor)
        return Out(ref=ref.model_dump(mode="json"), retraction=ret.model_dump())

    class ListArgs(BaseModel):
        project: str
        collection: str | None = None

    @tool("rk_library_list", "List library items (optionally one collection, by name or id).", ListArgs, False)
    def _llist(ctx: CallContext, a: ListArgs) -> Out:
        _, lib = _project(k, a.project)
        col = _collection(lib, a.collection)
        return Out(items=[i.model_dump(mode="json") for i in lib.list(col)], collections=lib.collections())

    class LSearch(BaseModel):
        project: str
        query: str
        limit: int = Field(20, ge=1, le=200)

    @tool("rk_library_search", "Full-text search over titles, abstracts, notes and full text.", LSearch, False)
    def _lsearch(ctx: CallContext, a: LSearch) -> Out:
        _, lib = _project(k, a.project)
        return Out(hits=[h.model_dump() for h in lib.search(a.query, a.limit)])

    class TagArgs(BaseModel):
        project: str
        item_id: str
        add: list[str] = []
        remove: list[str] = []

    @tool("rk_library_tag", "Add or remove tags on an item.", TagArgs, True)
    def _tag(ctx: CallContext, a: TagArgs) -> Out:
        _, lib = _project(k, a.project)
        lib.tag(a.item_id, a.add, a.remove, actor=ctx.actor, actor_kind=_added_by(ctx))
        return Out(tags=lib.get(a.item_id).tags)

    class CollectArgs(BaseModel):
        project: str
        item_id: str
        collection: str
        create: bool = True

    @tool("rk_library_collect", "Put an item into a collection (created if missing).", CollectArgs, True)
    def _collect(ctx: CallContext, a: CollectArgs) -> Out:
        _, lib = _project(k, a.project)
        col = a.collection if a.collection.startswith("col_") else lib.collection_by_name(a.collection)
        if col is None:
            if not a.create:
                raise ToolError("not_found", f"no collection {a.collection}", status=404)
            col = lib.create_collection(a.collection, actor=ctx.actor, actor_kind=_added_by(ctx))
        lib.collect(a.item_id, col, actor=ctx.actor, actor_kind=_added_by(ctx))
        return Out(collection_id=col)

    class ImportArgs(BaseModel):
        project: str
        format: Literal["bibtex", "ris", "csl"]
        text: str

    @tool("rk_library_import", "Import BibTeX, RIS or CSL-JSON into the library.", ImportArgs, True)
    def _import(ctx: CallContext, a: ImportArgs) -> Out:
        _, lib = _project(k, a.project)
        items = FORMATS[a.format][1](a.text)
        res = [lib.add_item(i, added_by=_added_by(ctx), actor=ctx.actor) for i in items]
        return Out(item_ids=[r.item_id for r in res], created=sum(r.created for r in res))

    class ExportArgs(BaseModel):
        project: str
        format: Literal["bibtex", "ris", "csl"]
        collection: str | None = None

    @tool("rk_library_export", "Export the library (or a collection) as BibTeX, RIS or CSL-JSON.", ExportArgs, False)
    def _export(ctx: CallContext, a: ExportArgs) -> Out:
        _, lib = _project(k, a.project)
        col = _collection(lib, a.collection)
        return Out(text=FORMATS[a.format][0](lib.list(col)))

    class AttachArgs(BaseModel):
        project: str
        source_id: str
        path: str

    # CLI only (policy): the path is read on the server host, so agents must not point it at arbitrary files.
    @tool("rk_library_attach", "Attach a local PDF to an item (builds its text layer).", AttachArgs, True)
    def _attach(ctx: CallContext, a: AttachArgs) -> Out:
        ps, lib = _project(k, a.project)
        try:
            r = attach_pdf(ps, lib, a.source_id, a.path, origin="user_upload", actor=ctx.actor,
                           actor_kind=_added_by(ctx))
        except (ValueError, FileNotFoundError) as e:
            raise ToolError("invalid", str(e)) from e
        return Out(**r.model_dump())

    # ---- tokens for external MCP clients (cli only) ----
    class TokenName(BaseModel):
        name: str = Field(min_length=1, max_length=64)

    # writes=False on purpose: the idempotency store keeps results, and a raw token must never be stored.
    # A retried create fails on the duplicate name instead of minting a second token.
    @tool("rk_token_create", "Create a long-lived lead token for an external MCP client (shown once).", TokenName,
          False)
    def _token_create(ctx: CallContext, a: TokenName) -> Out:
        try:
            return Out(name=a.name, token=k.tokens.issue_lead(a.name))
        except ValueError as e:
            raise ToolError("exists", str(e), status=409) from e

    @tool("rk_token_list", "List lead tokens (names only).", Empty, False)
    def _token_list(ctx: CallContext, a: Empty) -> Out:
        return Out(tokens=k.tokens.list_lead())

    @tool("rk_token_revoke", "Revoke a lead token by name (or a task's token by task id).", TokenName, False)
    def _token_revoke(ctx: CallContext, a: TokenName) -> Out:
        n = k.tokens.revoke(a.name)
        if not n:
            raise ToolError("not_found", f"no active token {a.name!r}", status=404)
        return Out(revoked=n)

    # ---- role tasks and drivers ----
    class TaskRunArgs(BaseModel):
        project: str
        role: str
        instructions: str = Field(min_length=1)
        inputs: dict = {}
        driver: str | None = None
        output_schema: str | None = Field(None, description="'rk.module:Class' (rk modules only); default FreeText")
        max_turns: int = Field(12, ge=1, le=60)
        timeout_s: int = Field(600, ge=10, le=7200)

    @tool("rk_task_run", "Run one role task on a driver and record it (human only).", TaskRunArgs, True)
    def _task_run(ctx: CallContext, a: TaskRunArgs) -> Out:
        _project(k, a.project)
        if a.role not in ROLES:
            raise ToolError("invalid", f"unknown role {a.role!r}")
        try:
            schema = _resolve_schema(a.output_schema)
            driver = DriverSpec.parse(a.driver) if a.driver else None
        except ValueError as e:
            raise ToolError("invalid", str(e)) from e
        r = run_task(k, reg, project=a.project, role=a.role, instructions=a.instructions, inputs=a.inputs,
                     output_schema=schema, driver=driver, max_turns=a.max_turns, timeout_s=a.timeout_s,
                     actor=ctx.actor)
        return Out(task_id=r.task_id, ok=r.ok, error=r.error, provider=r.provider, model=r.model,
                   output=r.output.model_dump(mode="json") if r.output else None, tool_calls=r.tool_calls,
                   duration_s=r.duration_s)

    @tool("rk_providers", "Driver per role, its vendor and availability; what the drivers break or weaken.", Empty,
          False)
    def _providers(ctx: CallContext, a: Empty) -> Out:
        s = k.settings()
        rows = []
        for role, (spec, avail) in available_all(s).items():
            rows.append({"role": role, "driver": str(spec), "vendor": vendor_of(spec.provider, spec.model),
                         **avail.model_dump()})
        problems = validate_drivers(s.drivers)
        return Out(providers=rows, problems=problems,
                   warnings=[] if problems else driver_warnings(s.drivers, s.support_min_judges))

    class SupportCheckArgs(BaseModel):
        project: str
        text: str = Field(min_length=1, max_length=20000)
        source_ids: list[str] = Field(min_length=1)
        driver: str | None = None
        judges: list[str] | None = None
        min_judges: int | None = Field(None, ge=1, le=3)
        gold: dict[int, Literal["supported", "unsupported", "contradicted"]] | None = None

    @tool("rk_support_check", "Per-claim support verdicts for a passage against its sources (human only).",
          SupportCheckArgs, True)
    def _support_check(ctx: CallContext, a: SupportCheckArgs) -> Out:
        _project(k, a.project)
        try:
            driver = DriverSpec.parse(a.driver or k.settings().drivers["arguer"])
            judges = [DriverSpec.parse(j) for j in a.judges] if a.judges else None
            report = support_check(k, reg, project=a.project, text=a.text, source_ids=a.source_ids, driver=driver,
                                   gold=a.gold, judge_drivers=judges, min_judges=a.min_judges, actor=ctx.actor)
        except KeyError as e:
            raise ToolError("not_found", f"unknown source {e.args[0]}", status=404) from e
        except (ValueError, RuntimeError) as e:
            raise ToolError("failed", str(e)) from e
        return Out(**report.model_dump(mode="json"))

    return reg


def _resolve_schema(ref: str | None) -> type[BaseModel]:
    if not ref:
        return FreeText
    module, _, name = ref.partition(":")
    if not (module == "rk" or module.startswith("rk.")) or not name:
        raise ValueError("output_schema must be 'rk.<module>:<Class>'")
    try:
        obj = getattr(importlib.import_module(module), name)
    except (ImportError, AttributeError) as e:
        raise ValueError(f"cannot load output schema {ref}: {e}") from e
    if not (isinstance(obj, type) and issubclass(obj, BaseModel)):
        raise ValueError(f"{ref} is not a pydantic model")  # noqa: TRY004 - invalid user input, caught as ValueError
    return obj
