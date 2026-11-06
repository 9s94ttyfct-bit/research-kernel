"""System prompts (shared preamble + role duties + output schema) and untrusted-input rendering."""

from __future__ import annotations

import json
from importlib.resources import files

from pydantic import BaseModel


def _text(name: str) -> str:
    p = files("rk.roles").joinpath(name)
    return p.read_text(encoding="utf-8").strip() if p.is_file() else ""


def build_system_prompt(role: str, output_schema: type[BaseModel] | None) -> str:
    parts = [_text("preamble.md")]
    duties = _text(f"{role}.md")
    if duties:
        parts.append(f"## Your role: {role}\n\n{duties}")
    if output_schema is not None:
        schema = json.dumps(output_schema.model_json_schema(), ensure_ascii=False)
        parts.append("## Output\n\nWhen you have finished, reply with only one JSON object matching this JSON Schema, "
                     f"with no other text:\n{schema}")
    return "\n\n".join(parts)


def _escape(text: str) -> str:
    # A source cannot close our data block early and smuggle text outside it.
    return text.replace("</data>", "<\\/data>").replace("</tool_result>", "<\\/tool_result>")


def render_inputs(inputs: dict) -> str:
    blocks = []
    for name, value in inputs.items():
        body = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=1)
        blocks.append(f'<data name="{name}" untrusted="true">\n{_escape(body)}\n</data>')
    return "\n".join(blocks)


def render_tool_result(result: dict) -> str:
    return f'<tool_result untrusted="true">\n{_escape(json.dumps(result, ensure_ascii=False))}\n</tool_result>'
