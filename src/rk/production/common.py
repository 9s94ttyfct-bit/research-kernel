"""Small helpers the production stages share (synthesize, argue, design, the read tools and P1's narrowing)."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict

from rk.store.project import ProjectStore

if TYPE_CHECKING:
    from rk.orchestrator.engine import StageContext


class RoleOut(BaseModel):
    """A role output: unknown fields are refused, not silently dropped."""
    model_config = ConfigDict(extra="forbid")


def fold(text: str) -> str:
    """``text`` for matching: whitespace collapsed, case folded."""
    return " ".join(text.split()).casefold()


def answer_comment(answer: dict) -> str:
    """A checkpoint answer's ``comment``, stripped; ``""`` when there is none or it is not text."""
    return answer.get("comment").strip() if isinstance(answer.get("comment"), str) else ""


def stage_runner(ctx: StageContext):
    """A task runner for the verifiers (``verify_support``'s ``run``) through the stage's executor, in its stage and
    attempt."""
    def run(*, role: str, instructions: str, inputs: dict, output_schema, **task):
        return ctx.executor.run(role, instructions, inputs, output_schema, stage=ctx.stage, attempt=ctx.attempt,
                                **task)
    return run


def entity_row(ps: ProjectStore, table: str, id_: str) -> dict:
    """The JSON ``data`` of a row of ``table`` (claims, edges, evidence, objections); an unknown id is a ``KeyError``."""
    r = ps.db.execute(f"SELECT data FROM {table} WHERE id=?", (id_,)).fetchone()
    if r is None:
        raise KeyError(id_)
    return json.loads(r["data"])


def by_group(notes: list[dict], kind: str) -> dict[int, dict]:
    """group index -> the last of ``notes`` of ``kind`` that names a group."""
    return {n["group"]: n for n in notes if n["kind"] == kind and n.get("group") is not None}
