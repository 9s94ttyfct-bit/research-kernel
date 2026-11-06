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
                                     "rk_library_tag", "rk_library_collect", "rk_library_import", "rk_providers")},
    # human only: host file paths, credentials, answering checkpoints, launching tasks
    **{name: _r() for name in ("rk_library_attach", "rk_token_create", "rk_token_list", "rk_token_revoke",
                               "rk_checkpoint_answer", "rk_task_run")},
}


def tool_roles(name: str) -> frozenset[str]:
    try:
        return TOOL_ROLES[name]
    except KeyError:
        raise KeyError(f"tool {name} has no entry in rk.roles.policy.TOOL_ROLES") from None


def role_tools(role: str, registry) -> list[str]:
    """Registered tools the role may call (sorted)."""
    return sorted(t.name for t in registry.list(role))
