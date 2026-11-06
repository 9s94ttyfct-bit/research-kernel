"""``human`` driver: a role task becomes a checkpoint for the researcher; the task waits (spec §8.2)."""

from __future__ import annotations

import json

from rk.checkpoints import create_checkpoint
from rk.llm.types import (
    Availability,
    ChatSpec,
    ProviderError,
    RunContext,
    TaskResult,
    TaskSpec,
)


class HumanProvider:
    name = "human"
    model = None
    vendor = "human"
    kind = "human"

    def available(self) -> Availability:
        return Availability(ok=True)

    def run(self, spec: TaskSpec, ctx: RunContext) -> TaskResult:
        if spec.project is None:
            return TaskResult(spec.task_id, False, None, "", self.name, None, 0.0, "human tasks need a project")
        ps, _ = ctx.kernel.project(spec.project)
        prompt = f"[{spec.role}] {spec.instructions}"
        if spec.inputs:
            prompt += "\n\nInputs:\n" + json.dumps(spec.inputs, ensure_ascii=False, indent=1)
        chk = create_checkpoint(ps, "task_input", prompt, {"output_schema": spec.output_schema.model_json_schema()},
                                task_id=spec.task_id, actor="kernel")
        return TaskResult(spec.task_id, False, None, chk, self.name, None, 0.0, "waiting_human", session_id=chk)

    def open_chat(self, spec: ChatSpec, ctx: RunContext):
        raise ProviderError("the human driver cannot hold a chat; the human is the other side of every chat")
