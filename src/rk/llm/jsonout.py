"""Pull the structured answer out of model text: strip fences, take the first parseable JSON object."""

from __future__ import annotations

import json
import re

from pydantic import BaseModel

_FENCE = re.compile(r"```[a-zA-Z0-9_-]*\s*\n(.*?)\n?```", re.DOTALL)


def _balanced_end(text: str, start: int) -> int | None:
    """Index just past the ``}`` closing the object opened at ``start``, honouring JSON strings."""
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(text)):
        c = text[i]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
        elif c == '"':
            in_str = True
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return i + 1
    return None


def _first_object(text: str) -> dict | None:
    i = text.find("{")
    while i >= 0:
        end = _balanced_end(text, i)
        if end is not None:
            try:
                obj = json.loads(text[i:end])
            except json.JSONDecodeError:
                obj = None
            if isinstance(obj, dict):
                return obj
        i = text.find("{", i + 1)
    return None


def extract_json(text: str) -> dict | None:
    for block in _FENCE.findall(text):
        obj = _first_object(block)
        if obj is not None:
            return obj
    return _first_object(text)


def parse_output(text: str, schema: type[BaseModel]) -> BaseModel:
    obj = extract_json(text)
    if obj is None:
        raise ValueError("no JSON object in model output")
    return schema.model_validate(obj)
