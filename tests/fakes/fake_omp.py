#!/usr/bin/env python3
"""Stand-in for `omp auth-gateway stdio`: JSON lines in, JSON lines out.

A request whose last user message is "OUT_OF_ORDER" first gets a stray reply for another id, to prove the client
matches replies by id. "FAIL" yields status 401. "FALLBACK" makes the requested model fail: like omp, the fake then
answers with another model unless the settings overlays in PI_CONFIG_FILES (read in order, the last one wins) set
`modelFallback: false`, when it returns 401. "WRONGMODEL" is answered by another model regardless. "CAPACITY" yields
status 502 with a long body whose retry-after-ms comes last (as Codex's "at capacity" event, with a long message);
"CAPACITY_EDGE" places it across character 500 of the body's text.
With FAKE_OMP_PIDS set, each started gateway appends its pid to that file.
"""

import json
import os
import re
import sys

if sys.argv[1:3] != ["auth-gateway", "stdio"]:
    sys.exit(f"unexpected argv {sys.argv[1:]}")
model_fallback = True
for path in filter(None, os.environ.get("PI_CONFIG_FILES", "").split(os.pathsep)):
    with open(path) as f:
        for m in re.finditer(r"^\s*modelFallback:\s*(true|false)\s*$", f.read(), re.MULTILINE):
            model_fallback = m.group(1) == "true"
print(json.dumps({"ready": True, "version": "fake"}), flush=True)
if os.environ.get("FAKE_OMP_PIDS"):
    with open(os.environ["FAKE_OMP_PIDS"], "a") as f:
        f.write(f"{os.getpid()}\n")
for line in sys.stdin:
    req = json.loads(line)
    body = req["body"]
    last = body["messages"][-1].get("content") or ""
    if last == "OUT_OF_ORDER":
        print(json.dumps({"id": req["id"] + 1000, "status": 200, "body": {}}), flush=True)
    if last == "FAIL" or (last == "FALLBACK" and not model_fallback):
        print(json.dumps({"id": req["id"], "status": 401, "body": {"error": "unauthorized"}}), flush=True)
        continue
    if last in ("CAPACITY", "CAPACITY_EDGE"):
        message = ("Codex error event: Codex is at capacity, please try again later. " + "x" * 600 if last == "CAPACITY"
                   else "x" * 466)  # str(body) starts with 23 characters before the message: the hint spans 490-510
        print(json.dumps({"id": req["id"], "status": 502, "body": {"error": {
            "message": f"{message} retry-after-ms=21000"}}}), flush=True)
        continue
    model = "anthropic/claude-sonnet-5-5" if last in ("FALLBACK", "WRONGMODEL") else body["model"]
    reply = {"id": "chatcmpl-x", "object": "chat.completion", **({} if last == "NOMODEL" else {"model": model}),
             "choices": [{"index": 0, "message": {"role": "assistant", "content": f"echo:{last}"},
                          "finish_reason": "stop"}], "usage": {"total_tokens": 3}}
    print(json.dumps({"id": req["id"], "status": 200, "body": reply}), flush=True)
