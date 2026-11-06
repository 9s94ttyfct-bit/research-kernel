"""Plan-1 tools: projects, search, library, full text, notes, verification."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from rk.fulltext import FulltextResult, attach_pdf, fetch_fulltext
from rk.library.formats import FORMATS
from rk.library.models import ItemIn
from rk.notes import NoteIn, NoteRejected, add_note, list_notes, text_find, text_read
from rk.sources import openalex
from rk.sources.identifiers import Identifier, parse_identifier
from rk.sources.resolver import resolve, search
from rk.tools.registry import CallContext, Tool, ToolError, ToolRegistry
from rk.verify.records import verify_item
from rk.verify.retract import FLAGGED, retraction_status
from rk.workspace import Kernel

ROLES = frozenset({"cli", "lead"})


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


def build_registry(k: Kernel) -> ToolRegistry:
    reg = ToolRegistry(k.home / "kernel.sqlite")

    def tool(name: str, summary: str, inp: type[BaseModel], writes: bool, roles: frozenset[str] = ROLES):
        def deco(fn):
            reg.register(Tool(name=name, summary=summary, input_model=inp, output_model=Out, roles=roles,
                              writes=writes, handler=fn))
            return fn
        return deco

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
        count = lambda t: ps.db.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]  # noqa: E731
        flagged = [i.id for i in lib.list() if i.retraction in FLAGGED]
        return Out(project=a.project, title=ps.meta("title"), items=count("items"), snapshots=count("snapshots"),
                   notes=count("notes"), verifications=count("verifications"), events=count("events"),
                   flagged_items=flagged, chain=ps.log.verify_chain().model_dump())

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
        item = a.item
        if a.identifier:
            try:
                ident = parse_identifier(a.identifier)
            except ValueError as e:
                raise ToolError("invalid", str(e)) from e
            r = resolve(k.ctx(), ident) if ident.kind not in ("url", "isbn") else None
            if r is None and item is None:
                raise ToolError("not_found", f"{a.identifier} not found; supply item metadata to add it anyway",
                                status=404)
            if r is not None:
                item = r.item
        if item is None:
            raise ToolError("invalid", "need identifier or item")
        res = lib.add_item(item.model_copy(update={"discovered_via": a.discovered_via}), added_by=_added_by(ctx),
                           actor=ctx.actor)
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

    @tool("rk_source_fetch_fulltext", "Fetch open-access full text into a verified text layer.", SourceArg, True)
    def _fetch(ctx: CallContext, a: SourceArg) -> Out:
        ps, lib = _project(k, a.project)
        r: FulltextResult | None = fetch_fulltext(ps, lib, k.ctx(), a.source_id, actor=ctx.actor)
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

    @tool("rk_verify", "Run V-REF and V-RETRACT on a library item.", VerifyArgs, True)
    def _verify(ctx: CallContext, a: VerifyArgs) -> Out:
        ps, lib = _project(k, a.project)
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

    # CLI only: the path is read on the server host, so agents must not be able to point it at arbitrary files.
    @tool("rk_library_attach", "Attach a local PDF to an item (builds its text layer).", AttachArgs, True,
          roles=frozenset({"cli"}))
    def _attach(ctx: CallContext, a: AttachArgs) -> Out:
        ps, lib = _project(k, a.project)
        try:
            r = attach_pdf(ps, lib, a.source_id, a.path, origin="user_upload", actor=ctx.actor,
                           actor_kind=_added_by(ctx))
        except (ValueError, FileNotFoundError) as e:
            raise ToolError("invalid", str(e)) from e
        return Out(**r.model_dump())

    return reg
