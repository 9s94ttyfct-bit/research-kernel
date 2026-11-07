"""Which role may call which tool (spec §8.1, §8.4, §9.3). The single source of truth for tool permissions:
``build_registry`` refuses to register a tool that has no entry here."""

from __future__ import annotations

ROLES: tuple[str, ...] = ("lead", "scout", "screener_a", "screener_b", "reader", "gapminer", "ideator", "arguer",
                          "skeptic", "simreader", "writer", "judge", "judge_2", "triage")

# Roles that take inline inputs only and never call tools.
NO_TOOL_ROLES = frozenset({"judge", "judge_2", "triage"})
# Spec §8.1 "读取" (read everything in the project): roles that reason over the whole project. Screeners see
# metadata and abstracts only; the Scout sees metadata plus discovery tools; the Reader sees full text and notes.
_FULL_READERS = frozenset({"lead", "gapminer", "ideator", "arguer", "skeptic", "simreader", "writer"})
_ALL_TOOL_ROLES = frozenset(ROLES) - NO_TOOL_ROLES
# The roles that work on the discovery stages (Plan 4): the Lead (D1), Scout, Reader, GapMiner and Ideator.
_DISCOVERY_ROLES = frozenset({"lead", "scout", "reader", "gapminer", "ideator"})


def _r(*roles: str) -> frozenset[str]:
    return frozenset({"cli", *roles})


TOOL_ROLES: dict[str, frozenset[str]] = {
    # bibliographic metadata and abstracts: everyone who uses tools
    **{name: _r(*_ALL_TOOL_ROLES) for name in ("rk_get_work", "rk_library_list", "rk_flag_injection")},
    # full text and notes (rk_library_search returns note and full-text snippets): not screeners, not scout
    **{name: _r(*_FULL_READERS, "reader") for name in ("rk_text_read", "rk_text_find", "rk_note_list",
                                                       "rk_library_search")},
    # the argument graph and project-wide views
    **{name: _r(*_FULL_READERS) for name in ("rk_claim_get", "rk_claim_list", "rk_project_status",
                                             "rk_library_export")},
    # discovery
    **{name: _r("lead", "scout", "skeptic") for name in ("rk_search_works", "rk_expand_citations", "rk_source_add")},
    # reading and note taking
    **{name: _r("lead", "reader") for name in ("rk_source_fetch_fulltext", "rk_note_add")},
    # argument building (Plan 2B)
    **{name: _r("lead", "arguer") for name in ("rk_claim_add", "rk_evidence_link", "rk_edge_add")},
    "rk_objection_add": _r("lead", "skeptic"),
    # the Lead shows and explains checkpoints, manages the library and projects; it never answers checkpoints
    **{name: _r("lead") for name in ("rk_checkpoint_list", "rk_verify", "rk_project_create", "rk_project_list",
                                     "rk_project_intake", "rk_library_tag", "rk_library_collect",
                                     "rk_library_import", "rk_providers")},
    # human only: host file paths, credentials, answering checkpoints, launching tasks
    **{name: _r() for name in ("rk_library_attach", "rk_token_create", "rk_token_list", "rk_token_revoke",
                               "rk_checkpoint_answer", "rk_task_run", "rk_support_check", "rk_browser_save_page")},
    # built-in browser (spec §8.1: Scout searches, Reader fetches full text; the Lead uses both in D1)
    "rk_browser_search": _r("lead", "scout"),
    "rk_browser_fetch_fulltext": _r("lead", "reader"),
    **{name: _r("lead", "scout", "reader") for name in ("rk_browser_open", "rk_browser_snapshot", "rk_browser_act",
                                                        "rk_browser_tabs", "rk_browser_request_human",
                                                        "rk_browser_status")},
    # discovery stages (Plan 4): D2 runs are yours alone; the Lead sees a run and drives D1 stages and rounds; the
    # discovery roles read the artifacts; every tool role sees where the stages stand
    **{name: _r() for name in ("rk_run_start", "rk_run_pause", "rk_run_resume", "rk_run_cancel")},
    "rk_run_status": _r("lead"),
    **{name: _r("lead") for name in ("rk_stage_run", "rk_stage_advance", "rk_exploration_round")},
    **{name: _r(*_DISCOVERY_ROLES) for name in ("rk_gap_list", "rk_topic_list", "rk_rq_get")},
    "rk_stage_status": _r(*_ALL_TOOL_ROLES),
}

# Spec §8.1: the Lead's write access opens by stage. While a discovery stage is current (D1) it may use every tool its
# policy grants; once `question` passed it only reads and queries status, so every tool that writes, and the built-in
# browser (§9.3: the Lead browses in D1 only), is refused to it with `stage_closed`. A new write tool is closed by
# default; these stay open: the injection report (a safety duty) and the browser's status tools.
LEAD_OPEN_AFTER_DISCOVERY = frozenset({"rk_flag_injection", "rk_browser_status", "rk_browser_tabs"})


def lead_closed_after_discovery(name: str, writes: bool) -> bool:
    """Whether the tool is refused to the Lead once every discovery stage of the project passed."""
    if "lead" not in tool_roles(name) or name in LEAD_OPEN_AFTER_DISCOVERY:
        return False
    return writes or name.startswith("rk_browser_")


def tool_roles(name: str) -> frozenset[str]:
    try:
        return TOOL_ROLES[name]
    except KeyError:
        raise KeyError(f"tool {name} has no entry in rk.roles.policy.TOOL_ROLES") from None


def role_tools(role: str, registry) -> list[str]:
    """Registered tools the role may call (sorted)."""
    return sorted(t.name for t in registry.list(role))
