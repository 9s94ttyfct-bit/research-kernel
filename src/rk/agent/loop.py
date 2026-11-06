"""The chat-API tool loop: model ↔ registry, with role/task scoping and bounded output repair (spec §8.2)."""

from __future__ import annotations

import hashlib
import time

from pydantic import ValidationError

from rk.canon import canonical_json
from rk.llm.chat.base import ChatBackend, Msg, ToolDef
from rk.llm.jsonout import parse_output
from rk.llm.types import ProviderError, TaskResult, TaskSpec
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
                 task_id: str, project: str | None) -> dict:
    """Run one model-requested tool call. Every failure is returned to the model as data, never raised."""
    if name not in allowed:
        return {"error": "permission_denied", "message": f"tool {name} not permitted for role {role}"}
    args = dict(arguments)
    if project is not None:
        args["project"] = project
    ctx = CallContext(role=role, actor=actor, task_id=task_id, project=project, allowed_tools=frozenset(allowed),
                      idempotency_key=idempotency_key(task_id, name, args))
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
