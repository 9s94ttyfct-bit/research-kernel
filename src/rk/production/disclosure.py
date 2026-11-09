"""``disclosure.md`` (spec §9.2 G-DISCLOSE, §5.2, §5.3, §8.3; Plan 5c Task 5): what the reader must know about how
the manuscript was made, in five parts (``PARTS``), each line one sentence citing the event it reports
(``[[run:evt_…]]``), so the disclosure verifies as a manuscript section (V-COVER: every sentence has a marker whose
target exists; data, ids, counts, times and texts from the sources and models are in code spans, which no prose rule
reads):

1. **Models used:** per role and driver, from the task log (``task.created``; the models the attempts reported in
   ``task.attempt``), the task count and the stages the tasks were linked to (``run.task``); the line cites the
   group's first task.
2. **Human decisions:** every checkpoint a human answered (``checkpoint.answered``, ``answered_by`` human), with its
   kind, decision and time.
3. **Not reviewed by a human:** every checkpoint the P1 vote answered, every P1 vote (``vote.decided``; the final
   review's reads "Final review: approved by the P1 vote, not reviewed by a human"), every P1 degrade
   (``stage.degraded``), a research question an agent chose (``rq.frozen`` adopted by an agent), and every gap behind
   the chosen topic (``gap.added``), verified only within the search scope.
4. **Limitations:** the limitations inputs of synthesize's, argue's and (a research proposal) design's passed attempts
   (``synthesis_limitations``, ``argue_limitations``, ``design_limitations``), each entry citing the event that
   records it where there is one (a deviation, a withdrawal, a narrowed claim, a conceded objection, a contested
   claim, a dropped hypothesis), else the stage's pass; every cited claim with a conceded sourced objection
   (``balance_requirements(...).conceded_sourced``: claim, objection and source), the gap V-BALANCE leaves (Task 3);
   and what a section lacked from the argument (``draft.unmet_support``: each ``draft->argue`` back-edge and what a
   section still lacked after it, each citing its ``draft.noted`` event), whatever the Writer then wrote.
5. **Preregistration deviations:** every protocol deviation (``version`` and ``analysis``).

A part with nothing to list says so, citing the intake (who answers checkpoints follows from its production mode),
synthesize's pass or the frozen protocol. Rendering reads stored state only, in event order, so the same events give
the same bytes (``write_disclosure`` then writes no new version). ``disclosure_problems`` is G-DISCLOSE. The tasks and
votes of an evaluation of the project (``EVALUATION_STAGES``: ``rk.eval.sr_repro``'s scoring) are not part of how the
manuscript was made: no part lists them and G-DISCLOSE does not ask for them."""

from __future__ import annotations

import json
from collections.abc import Iterator

from rk.argument import ArgumentService
from rk.discovery.store import frozen_rq
from rk.orchestrator.state import passed_attempt
from rk.production.argue import argue_limitations
from rk.production.common import write_atomic
from rk.production.design import design_limitations
from rk.production.draft import (
    STAGE_NAMES,
    latest_version,
    section_verdict,
    themes,
    unmet_support,
)
from rk.production.manuscript import (
    parse_section,
    record_section_verdict,
    source_markers,
    write_draft,
)
from rk.production.sections import required_sections
from rk.production.store import deviations, frozen_protocol
from rk.production.synthesis import synthesis_limitations
from rk.store.project import ProjectStore
from rk.verify.balance import argued, balance_requirements

SECTION = "disclosure"
STAGE = "audit"
PARTS = ("Models used", "Human decisions", "Not reviewed by a human", "Limitations", "Preregistration deviations")
DISCLOSURE_PATH = "artifacts/audit/disclosure.md"
NOT_HUMAN = "not reviewed by a human"
FINAL_REVIEW = "final_review"
# stages of an evaluation of the project (``rk.eval.sr_repro.STAGE``): its tasks and votes score the paper and are not
# part of how it was made, so the disclosure neither lists them nor G-DISCLOSE asks for them
EVALUATION_STAGES = frozenset({"sr_repro"})


# ---- formatting ----
def _code(value) -> str:
    """``value`` as an inline code span: data the reader sees as such (no marker, number or quotation in it counts for
    a sentence rule), on one line, with no backtick that could end the span and no pipe that could start a table."""
    text = value if isinstance(value, str) else json.dumps(value, sort_keys=True, ensure_ascii=False)
    text = " ".join(text.replace("`", "'").replace("|", "/").split())
    return f"`{text or '-'}`"


def _line(text: str, *events: str | None) -> str:
    return text + "".join(f" [[run:{e}]]" for e in events if e) + "."


def _stage(stage: str | None) -> str:
    return STAGE_NAMES.get(stage, stage.replace("_", " ")) if stage else "no"


def _word(key: str) -> str:
    """A payload key as prose; one with a digit as code (a digit in prose is a number V-NUM checks)."""
    return _code(key) if any(c.isdigit() for c in key) else key.replace("_", " ")


def _decision(answer: dict | None) -> str:
    """What a checkpoint answer decided: its ``decision`` (or ``choice``), else the fields it gave."""
    answer = answer or {}
    for key in ("decision", "choice"):
        if isinstance(answer.get(key), str):
            return answer[key]
    return "fields " + ", ".join(sorted(answer)) if answer else "an empty answer"


# ---- events ----
def _rows(ps: ProjectStore, type_: str) -> list:
    return ps.db.execute("SELECT evt_id, entity_id, actor_kind, payload FROM events WHERE type=? ORDER BY seq",
                         (type_,)).fetchall()


def _last(ps: ProjectStore, type_: str, entity_id: str | None = None, **where) -> str | None:
    """The evt_id of the latest event of ``type_`` (on ``entity_id``; with payload fields ``where``), else None."""
    sql, params = "SELECT evt_id FROM events WHERE type=?", [type_]
    if entity_id is not None:
        sql, params = sql + " AND entity_id=?", [*params, entity_id]
    for k, v in where.items():
        sql, params = sql + f" AND json_extract(payload, '$.{k}')=?", [*params, v]
    r = ps.db.execute(sql + " ORDER BY seq DESC LIMIT 1", params).fetchone()
    return r["evt_id"] if r else None


def _intake(ps: ProjectStore) -> str | None:
    return _last(ps, "project.intake")


# ---- the parts ----
def _models(ps: ProjectStore) -> list[str]:
    rows = ps.db.execute(
        "SELECT e.evt_id, e.entity_id, json_extract(e.payload, '$.role') AS role, "
        "json_extract(e.payload, '$.driver') AS driver, r.stage FROM events e "
        "LEFT JOIN run_tasks r ON r.task_id = e.entity_id WHERE e.type='task.created' ORDER BY e.seq").fetchall()
    models: dict[str, set[str]] = {}
    for r in _rows(ps, "task.attempt"):
        if model := json.loads(r["payload"]).get("model"):
            models.setdefault(r["entity_id"], set()).add(model)
    groups: dict[tuple[str, str], dict] = {}
    for r in rows:
        if r["stage"] in EVALUATION_STAGES:
            continue
        g = groups.setdefault((r["role"], r["driver"]), {"first": r["evt_id"], "tasks": 0, "stages": {},
                                                          "models": set()})
        g["tasks"] += 1
        g["stages"][r["stage"]] = None
        g["models"] |= models.get(r["entity_id"], set())
    out = []
    for (role, driver), g in groups.items():
        stages = [f"the {_stage(s)} stage" if s else "work outside a stage" for s in g["stages"]]
        n = g["tasks"]
        model = f" (model {', '.join(_code(m) for m in sorted(g['models']))})" if g["models"] else ""
        out.append(_line(f"The {_code(role)} role ran {_code(n)} {'task' if n == 1 else 'tasks'} on the driver "
                          f"{_code(driver)}{model}, in {', '.join(stages)}", g["first"]))
    return out or [_line("No role task is recorded in the project's task log", _intake(ps))]


def _answers(ps: ProjectStore, by: str) -> list[tuple[str, dict, dict]]:
    """``(evt_id, checkpoint row, answer payload)`` of every checkpoint answer by ``by`` (human, vote), in order."""
    out = []
    for r in _rows(ps, "checkpoint.answered"):
        p = json.loads(r["payload"])
        if p.get("answered_by") != by:
            continue
        cp = ps.db.execute("SELECT id, kind FROM checkpoints WHERE id=?", (r["entity_id"],)).fetchone()
        out.append((r["evt_id"], {"id": r["entity_id"], "kind": cp["kind"] if cp else "unknown"}, p))
    return out


def _humans(ps: ProjectStore) -> list[str]:
    out = [_line(f"The researcher answered the {_code(cp['kind'])} checkpoint {_code(cp['id'])} with "
                 f"{_code(_decision(p.get('answer')))} at {_code(p.get('answered_at') or '-')}", evt)
           for evt, cp, p in _answers(ps, "human")]
    return out or [_line("No checkpoint was answered by a human", _intake(ps))]


def _chosen_topic_gaps(ps: ProjectStore) -> list[tuple[str, str]]:
    """``(gap id, its gap.added evt_id)`` of the gaps behind the frozen research question's topic."""
    rq = frozen_rq(ps)
    t = ps.db.execute("SELECT gap_ids FROM topics WHERE id=?", (rq["topic_id"],)).fetchone() if rq else None
    out = []
    for gid in json.loads(t["gap_ids"]) if t else []:
        if (evt := _last(ps, "gap.added", gid)) is not None:
            out.append((gid, evt))
    return out


def _not_reviewed(ps: ProjectStore) -> list[str]:
    out = [_line(f"The P1 vote answered the {_code(cp['kind'])} checkpoint {_code(cp['id'])} with "
                 f"{_code(_decision(p.get('answer')))} at {_code(p.get('answered_at') or '-')}, {NOT_HUMAN}", evt)
           for evt, cp, p in _answers(ps, "vote")]
    for r in _rows(ps, "vote.decided"):
        p = json.loads(r["payload"])
        if p.get("stage") in EVALUATION_STAGES:
            continue
        if p["subject_type"] == FINAL_REVIEW:
            what = "approved" if p["decision"] == "approve" else "not approved"
            out.append(_line(f"Final review: {what} by the P1 vote, {NOT_HUMAN}", r["evt_id"]))
            continue
        why = f", the conservative option because {_code(p.get('reason') or '-')}" if p.get("conservative_used") else ""
        out.append(_line(f"The P1 vote on the {_code(p['subject_type'])} {_code(p['subject_id'])} in the "
                         f"{_stage(p.get('stage'))} stage decided {_code(p['decision'])}{why}, {NOT_HUMAN}",
                         r["evt_id"]))
    for r in _rows(ps, "stage.degraded"):
        p = json.loads(r["payload"])
        what = "; ".join(_code(x) for x in p.get("removed") or []) or _code(p.get("reason") or "-")
        out.append(_line(f"At an edge limit the P1 degrade of the {_stage(p['stage'])} stage deleted or narrowed what "
                         f"failed, {NOT_HUMAN}: {what}", r["evt_id"]))
    for r in _rows(ps, "rq.frozen"):
        if json.loads(r["payload"]).get("adopted_by") == "agent":
            out.append(_line(f"The research question {_code(r['entity_id'])} was chosen by an agent, {NOT_HUMAN}",
                             r["evt_id"]))
    out += [_line(f"The gap {_code(gid)} behind the chosen topic was verified only within the search scope, and that "
                  f"it is a real research gap was {NOT_HUMAN}", evt) for gid, evt in _chosen_topic_gaps(ps)]
    return out or [_line("No checkpoint, vote or degrade was decided without a human, and no agent chose the "
                         "research question", _intake(ps))]


def _container(value) -> bool:
    """A limitations dict that groups entries (its values are lists, dicts or nothing), not an entry itself."""
    return isinstance(value, dict) and all(v is None or isinstance(v, list | dict) for v in value.values())


def _entries(value, path: tuple[str, ...] = ()) -> Iterator[tuple[tuple[str, ...], object]]:
    """Every limitation entry of a limitations input with the keys that lead to it."""
    if isinstance(value, list):
        for v in value:
            yield path, v
    elif _container(value):
        for k, v in value.items():
            yield from _entries(v, (*path, k))
    elif value not in (None, "", {}):
        yield path, value


def _entry_event(ps: ProjectStore, path: tuple[str, ...], entry) -> str | None:
    """The event that records a limitation entry, where there is one."""
    if not isinstance(entry, dict):
        return None
    last = path[-1] if path else ""
    if entry.get("deviation_id"):
        return entry["deviation_id"]
    if entry.get("objection_id"):
        return _last(ps, "objection.responded", entry["objection_id"]) or _last(ps, "objection.added",
                                                                                 entry["objection_id"])
    if entry.get("claim_id"):
        etype = {"withdrawn": "claim.withdrawn", "narrowed": "claim.added", "contested": "claim.contested"}.get(last)
        return _last(ps, etype, entry["claim_id"]) if etype else None
    if last == "dropped" and entry.get("id"):
        return _last(ps, "hypothesis.status_set", entry["id"])
    return None


def _identity(path: tuple[str, ...], entry) -> str:
    """What a limitation entry is about, so one listed by several limitations inputs (e.g. a conceded objection on a
    finding, in the synthesis's and the argument's) is listed once."""
    if isinstance(entry, dict):
        for key in ("objection_id", "deviation_id"):
            if entry.get(key):
                return f"{key}:{entry[key]}"
        if entry.get("claim_id") or entry.get("id"):
            return f"{path[-1] if path else ''}:{entry.get('claim_id') or entry.get('id')}"
    return json.dumps([path, entry], sort_keys=True, default=str)


def _fields(entry) -> str:
    if not isinstance(entry, dict):
        return _code(entry)
    return ", ".join(f"{_word(k)} {_code(v)}" for k, v in entry.items() if v not in (None, "", [], {}))


def _conceded(ps: ProjectStore) -> list[tuple[str, str]]:
    """``(objection id, line)`` for every claim the manuscript cites that has a conceded sourced objection (the
    V-BALANCE gap of Task 3): the claim, the objection and its source in one sentence."""
    paper_type = ps.meta("paper_type")
    synth = passed_attempt(ps, "synthesize")
    if paper_type is None or synth is None:
        return []
    cited: dict[str, None] = {}
    for spec in required_sections(paper_type, themes(ps, synth)):
        if spec.kernel_written or isinstance(got := latest_version(ps, spec.id), str):
            continue
        cited |= {m.target: None for m in source_markers(got[1]) if m.kind == "clm" and m.target}
    try:
        facts = argued(ps)
    except ValueError:
        return []
    svc = ArgumentService(ps)
    out = []
    for cid in cited:
        try:
            if svc.get_claim(cid)["status"] == "withdrawn":
                continue
        except KeyError:
            continue
        for o in balance_requirements(ps, cid, facts=facts).conceded_sourced:
            oid = o["objection_id"]
            evt = _last(ps, "objection.responded", oid) or _last(ps, "objection.added", oid)
            out.append((oid, _line(f"The cited claim {_code(cid)} has the conceded objection {_code(oid)} from the "
                                   f"source {_code(o['source_id'])}: {_code(o['text'])}", evt)))
    return out


def _unmet(ps: ProjectStore) -> list[tuple[str, str]]:
    """``(event id, line)`` for what a section lacked from the argument (``unmet_support``): each time drafting sent
    the stage back to argue for it, and what a section still lacked after that and was written without."""
    out = []
    for u in unmet_support(ps):
        if "asked" in u:
            text = (f"Drafting the {_code(u['section'])} section asked the argument for support it lacked, and the "
                    f"argument stage ran again: {_code(u['asked'])}")
        else:
            text = (f"After that, the {_code(u['section'])} section still lacked support for "
                    f"{_code('; '.join(u['needs']))} and was written without it")
        out.append((u["evt_id"], _line(text, u["evt_id"])))
    return out


def _limitations(ps: ProjectStore) -> list[str]:
    sources = [("synthesis", "synthesize", synthesis_limitations), ("argument", "argue", argue_limitations)]
    if ps.meta("paper_type") == "research_proposal":
        sources.append(("design", "design", design_limitations))
    conceded = _conceded(ps)
    seen = {f"objection_id:{oid}" for oid, _ in conceded}  # listed with their claim and source below
    out = []
    for where, stage, limitations in sources:
        attempt = passed_attempt(ps, stage)
        if attempt is None:
            continue
        passed = _last(ps, "stage.passed", stage=stage, attempt=attempt)
        for path, entry in _entries(limitations(ps, attempt)):
            if (key := _identity(path, entry)) in seen:
                continue
            seen.add(key)
            what = " ".join(_word(k) for k in path) or "an entry"
            out.append(_line(f"The {where} limitations list, under {what}: {_fields(entry)}",
                             _entry_event(ps, path, entry) or passed))
    out += [line for _, line in conceded]
    out += [line for _, line in _unmet(ps)]
    synth = passed_attempt(ps, "synthesize")
    return out or [_line("No limitation was recorded by the synthesis, the argument or the design",
                         _last(ps, "stage.passed", stage="synthesize", attempt=synth) if synth else _intake(ps))]


def _deviations(ps: ProjectStore) -> list[str]:
    out = []
    for d in deviations(ps):
        if d["kind"] == "analysis":
            text = (f"The {_stage(d['stage'])} stage deviated in its analysis from protocol version "
                    f"{_code(d['from_version'])}: {_code(d['reason'])}")
        else:
            text = (f"The protocol deviated from version {_code(d['from_version'])} to version "
                    f"{_code(d['to_version'])} in the {_stage(d['stage'])} stage: {_code(d['reason'])}")
        out.append(_line(text, d["id"]))
    if out:
        return out
    p = frozen_protocol(ps)
    return [_line("No deviation from the frozen protocol was recorded", p["id"] if p else _intake(ps))]


def render_disclosure(ps: ProjectStore) -> str:
    """The disclosure's markdown (see the module docstring): the five parts, as level-3 headings, each followed by one
    paragraph per line."""
    parts = dict(zip(PARTS, (_models(ps), _humans(ps), _not_reviewed(ps), _limitations(ps), _deviations(ps)),
                     strict=True))
    return "\n\n".join(f"### {title}\n\n" + "\n\n".join(lines) for title, lines in parts.items()) + "\n"


def write_disclosure(ps: ProjectStore, *, attempt: int, paper_type: str, actor: str) -> int:
    """Render the disclosure and make it the ``disclosure`` section's latest version (stage ``audit``; a text equal to
    the latest version's is not stored again) with its verdict, and ``disclosure.md`` (``DISCLOSURE_PATH``); returns
    the version. Applied again, also after an interruption, it writes nothing twice."""
    md = render_disclosure(ps)
    got = latest_version(ps, SECTION)
    if isinstance(got, str) or got[1] != md:
        version = write_draft(ps, SECTION, md, stage=STAGE, attempt=attempt, round=0, by_task=None, actor=actor)
        verdict = None
    else:
        version, verdict = got[0]["version"], got[0]["verdict"]
    if verdict is None:
        record_section_verdict(ps, SECTION, version, section_verdict(ps, SECTION, md, paper_type=paper_type,
                                                                     version=version), actor=actor)
    path = ps.root / DISCLOSURE_PATH
    if not path.is_file() or path.read_text(encoding="utf-8") != md:
        write_atomic(path, md.encode("utf-8"))
    return version


def disclosure_problems(ps: ProjectStore, markdown: str, cited_claims: list[str]) -> list[str]:
    """G-DISCLOSE of the disclosure section's ``markdown``: it has the five parts; it cites the event of every
    checkpoint answer (a human's or the P1 vote's), every P1 vote, every P1 degrade, an agent's choice of the research
    question and what a section lacked from the argument (``unmet_support``); it names, in one sentence, each of
    ``cited_claims`` (the live claims the manuscript cites) with a conceded sourced objection, with the objection and the
    source; and ``disclosure.md`` holds the same text."""
    out = []
    heads = {ln[4:].strip() for ln in markdown.splitlines() if ln.startswith("### ")}
    out += [f"the disclosure has no “{p}” part" for p in PARTS if p not in heads]
    cited = {m.target for m in source_markers(markdown) if m.kind == "run" and m.target}
    for by, who in (("human", "a human"), ("vote", "the P1 vote")):
        out += [f"the {cp['kind']} checkpoint {cp['id']}, answered by {who}, is not listed (its answer, event {evt}, "
                f"is not cited)" for evt, cp, _ in _answers(ps, by) if evt not in cited]
    out += [f"what the {u['section']} section lacked from the argument is not listed (event {u['evt_id']} is not "
            "cited)" for u in unmet_support(ps) if u["evt_id"] not in cited]
    for r in _rows(ps, "vote.decided"):
        p = json.loads(r["payload"])
        if r["evt_id"] not in cited and p.get("stage") not in EVALUATION_STAGES:
            out.append(f"the P1 vote {r['entity_id']} on the {p['subject_type']} {p['subject_id']} is not listed "
                       f"(event {r['evt_id']} is not cited)")
    for r in _rows(ps, "stage.degraded"):
        if r["evt_id"] not in cited:
            out.append(f"the P1 degrade of {json.loads(r['payload'])['stage']} is not listed (event {r['evt_id']} is not "
                       "cited)")
    for r in _rows(ps, "rq.frozen"):
        if json.loads(r["payload"]).get("adopted_by") == "agent" and r["evt_id"] not in cited:
            out.append(f"the research question {r['entity_id']}, chosen by an agent, is not listed (event "
                       f"{r['evt_id']} is not cited)")
    try:
        facts = argued(ps)
    except ValueError:
        facts = None
    sentences = [s.text for s in parse_section(markdown)]
    for cid in cited_claims if facts is not None else []:
        for o in balance_requirements(ps, cid, facts=facts).conceded_sourced:
            names = (cid, o["objection_id"], o["source_id"])
            if not any(all(n in s for n in names) for s in sentences):
                out.append(f"the cited claim {cid} has the conceded objection {o['objection_id']} from the source "
                           f"{o['source_id']}, and the disclosure does not name it")
    path = ps.root / DISCLOSURE_PATH
    if not path.is_file():
        out.append(f"{DISCLOSURE_PATH} is missing")
    elif path.read_text(encoding="utf-8") != markdown:
        out.append(f"{DISCLOSURE_PATH} differs from the disclosure section")
    return out
