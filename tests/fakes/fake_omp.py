#!/usr/bin/env python3
"""Stand-in for `omp auth-gateway stdio`: JSON lines in, JSON lines out.

A request whose last user message is "OUT_OF_ORDER" first gets a stray reply for another id, to prove the client
matches replies by id. "FAIL" yields status 401. "FALLBACK" is answered by another model, as omp's model fallback does.
With FAKE_OMP_PIDS set, each started gateway appends its pid to that file.
"""

import json
import os
import sys

if sys.argv[1:3] != ["auth-gateway", "stdio"]:
    sys.exit(f"unexpected argv {sys.argv[1:]}")
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
    if last == "FAIL":
        print(json.dumps({"id": req["id"], "status": 401, "body": {"error": "unauthorized"}}), flush=True)
        continue
    model = "anthropic/claude-sonnet-5-5" if last == "FALLBACK" else body["model"]
    reply = {"id": "chatcmpl-x", "object": "chat.completion", **({} if last == "NOMODEL" else {"model": model}),
             "choices": [{"index": 0, "message": {"role": "assistant", "content": f"echo:{last}"},
                          "finish_reason": "stop"}], "usage": {"total_tokens": 3}}
    print(json.dumps({"id": req["id"], "status": 200, "body": reply}), flush=True)
