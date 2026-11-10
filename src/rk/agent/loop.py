"""The chat-API tool loop: model ↔ registry, with role/task scoping and bounded output repair (spec §8.2)."""

from __future__ import annotations

import hashlib
import time
from collections.abc import Iterator

from pydantic import ValidationError

from rk.canon import canonical_json
from rk.llm.chat.base import ChatBackend, Msg, ToolDef
from rk.llm.jsonout import parse_output
from rk.llm.types import ChatEvent, ChatSpec, ProviderError, TaskResult, TaskSpec
from rk.roles.prompts import render_inputs, render_tool_result
from rk.tools.registry import CallContext, PermissionDenied, ToolError

MAX_REPAIRS = 2


def tool_defs(registry, names: list[str]) -> list[ToolDef]:
    """Tool definitions for the model; ``project`` is removed because the loop injects it."""
    defs = []
    for name in names:
        tool = registry.get(name)
        schema = tool.input_model.model_json_schema()
        props = dict(schema.get("properties", {}))
        props.pop("project", None)
        required = [r for r in schema.get("required", []) if r != "project"]
        params = {**schema, "properties": props, "required": required}
        defs.append(ToolDef(name=name, description=tool.summary, parameters=params))
    return defs


def idempotency_key(task_id: str, name: str, args: dict) -> str:
    return f"{task_id}:{hashlib.sha256((name + canonical_json(args)).encode()).hexdigest()[:16]}"


def execute_tool(registry, name: str, arguments: dict, *, allowed: list[str], role: str, actor: str,
                 task_id: str, project: str | None, call_scope: str | None = None) -> dict:
    """Run one model-requested tool call. Every failure is returned to the model as data, never raised. The
    idempotency key covers ``task_id`` (plus ``call_scope``, when given), the tool and its arguments."""
    if name not in allowed:
        return {"error": "permission_denied", "message": f"tool {name} not permitted for role {role}"}
    args = dict(arguments)
    if project is not None:
        args["project"] = project
    scope = task_id if call_scope is None else f"{task_id}:{call_scope}"
    ctx = CallContext(role=role, actor=actor, task_id=task_id, project=project, allowed_tools=frozenset(allowed),
                      idempotency_key=idempotency_key(scope, name, args))
    try:
        return {"result": registry.call(name, args, ctx)}
    except ToolError as e:
        return {"error": e.type, "message": e.message, "details": e.details}
    except PermissionDenied as e:
        return {"error": "permission_denied", "message": str(e)}
    except ValidationError as e:
        return {"error": "invalid_arguments", "details": e.errors(include_url=False, include_context=False)}
    except Exception as e:  # noqa: BLE001 - surfaced to the model and kept in the transcript
        return {"error": type(e).__name__, "message": str(e)}


def run_tool_loop(backend: ChatBackend, spec: TaskSpec, registry, *, actor: str, system: str,
                  provider: str) -> TaskResult:
    start = time.monotonic()
    tools = tool_defs(registry, spec.tools) if spec.tools else []
    schema = spec.output_schema.model_json_schema()
    messages = [Msg(role="system", content=system),
                Msg(role="user", content=f"{spec.instructions}\n\n{render_inputs(spec.inputs)}".strip())]
    repairs = calls = 0
    last_text = ""
    model = backend.model

    def done(ok: bool, output=None, error: str | None = None) -> TaskResult:
        return TaskResult(task_id=spec.task_id, ok=ok, output=output, raw=last_text, provider=provider, model=model,
                          duration_s=round(time.monotonic() - start, 3), error=error, tool_calls=calls)

    for _ in range(spec.max_turns):
        if time.monotonic() - start > spec.timeout_s:
            return done(False, error="timeout")
        try:
            turn = backend.complete(messages, tools, None if tools else schema)
        except ProviderError as e:
            return done(False, error=f"provider_error: {e}")
        model = turn.model or model
        last_text = turn.text
        if turn.tool_calls:
            messages.append(Msg(role="assistant", content=turn.text, tool_calls=turn.tool_calls))
            for c in turn.tool_calls:
                calls += 1
                result = execute_tool(registry, c.name, c.arguments, allowed=spec.tools, role=spec.role, actor=actor,
                                      task_id=spec.task_id, project=spec.project)
                if (spent := registry.budget_exhausted(spec.task_id)) is not None:
                    raise spent  # spec §11.3: the task is suspended (run_task), the model never sees the error
                messages.append(Msg(role="tool", content=render_tool_result(result), tool_call_id=c.id, name=c.name))
            continue
        try:
            return done(True, output=parse_output(turn.text, spec.output_schema))
        except (ValueError, ValidationError) as e:
            if repairs >= MAX_REPAIRS:
                return done(False, error="invalid_output")
            repairs += 1
            messages += [Msg(role="assistant", content=turn.text),
                         Msg(role="user", content=f"Your reply was not valid: {str(e)[:500]}\nReply with only one JSON "
                                                  "object matching the output schema.")]
    return done(False, error="max_turns")


class ChatApiSession:
    """Multi-turn chat over a ChatBackend: the history lives here; each ``send`` runs tool rounds until the model
    answers in text. Tool calls go through the same scoped ``execute_tool`` as single tasks, but each call is its own
    write: a chat repeats a call on purpose (run a stage, advance, run it again), so every call gets its own
    idempotency key."""

    MAX_ROUNDS = 12

    def __init__(self, backend: ChatBackend, spec: ChatSpec, registry, *, actor: str, session_id: str):
        self.backend, self.spec, self.registry, self.actor = backend, spec, registry, actor
        self.session_id = session_id
        self.tools = tool_defs(registry, spec.tools) if spec.tools else []
        self.messages: list[Msg] = [Msg(role="system", content=spec.system)]
        self._turn = 0
        self._calls = 0

    def send(self, user_message: str) -> Iterator[ChatEvent]:
        self._turn += 1
        self.messages.append(Msg(role="user", content=user_message))
        for _ in range(self.MAX_ROUNDS):
            try:
                turn = self.backend.complete(self.messages, self.tools, None)
            except ProviderError as e:
                yield ChatEvent("error", {"message": str(e)})
                return
            if not turn.tool_calls:
                self.messages.append(Msg(role="assistant", content=turn.text))
                yield ChatEvent("text", {"text": turn.text})
                yield ChatEvent("done", {"session_id": self.session_id})
                return
            self.messages.append(Msg(role="assistant", content=turn.text, tool_calls=turn.tool_calls))
            if turn.text:
                yield ChatEvent("text", {"text": turn.text})
            for c in turn.tool_calls:
                yield ChatEvent("tool_call", {"id": c.id, "name": c.name, "arguments": c.arguments})
                self._calls += 1
                result = execute_tool(self.registry, c.name, c.arguments, allowed=self.spec.tools,
                                      role=self.spec.role, actor=self.actor,
                                      task_id=f"{self.session_id}:{self._turn}", project=self.spec.project,
                                      call_scope=str(self._calls))
                yield ChatEvent("tool_result", {"id": c.id, "content": result})
                self.messages.append(Msg(role="tool", content=render_tool_result(result), tool_call_id=c.id,
                                         name=c.name))
        yield ChatEvent("error", {"message": f"stopped after {self.MAX_ROUNDS} tool rounds"})

    def close(self) -> None:
        self.messages.clear()
        close = getattr(self.backend, "close", None)  # the session's own backend (ChatManager builds one per chat)
        if close is not None:
            close()
