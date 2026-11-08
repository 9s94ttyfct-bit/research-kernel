"""Stand-in for `claude` and `codex`, selected by FAKE_KIND. Records argv/cwd to FAKE_RECORD (JSON lines) and
behaves per FAKE_MODE: ok | invalid | exit1 | sleep | limit (the subscription's session/usage limit reply) |
limit_warning (codex: a limit warning event next to a real answer)."""

import json
import os
import sys
import time
from pathlib import Path

kind = os.environ.get("FAKE_KIND", "claude")
mode = os.environ.get("FAKE_MODE", "ok")
argv = sys.argv[1:]
if argv == ["--version"]:
    print("2.1.309 (Claude Code)" if kind == "claude" else "codex-cli 0.166.0-alpha.6")
    sys.exit(0)
rec = {"argv": argv, "cwd": os.getcwd(), "cwd_files": sorted(os.listdir("."))}
if kind == "codex" and "--output-schema" in argv:
    rec["schema"] = json.loads(Path(argv[argv.index("--output-schema") + 1]).read_text())
with open(os.environ["FAKE_RECORD"], "a") as f:
    f.write(json.dumps(rec) + "\n")
if mode == "sleep":
    time.sleep(60)
if mode == "exit1":
    print("boom", file=sys.stderr)
    sys.exit(1)
if mode == "limit":
    if kind == "claude":  # Claude Code 2.1: print mode reports the limit as a "success" result with exit 1
        print(json.dumps({"type": "result", "subtype": "success", "is_error": True, "num_turns": 1,
                          "result": "You've hit your session limit · resets 3pm (UTC)", "session_id": "sess-1"}))
    else:
        for ev in [{"type": "thread.started", "thread_id": "thread-1"}, {"type": "turn.started"},
                   {"type": "error", "message": "You’ve hit your usage limit. Visit https://chatgpt.com/codex/settings/"
                                                "usage to purchase more credits or try again in 2 hours 5 minutes."},
                   {"type": "turn.failed", "error": {"message": "You’ve hit your usage limit."}}]:
            print(json.dumps(ev))
    sys.exit(1)
answer = {"found": True} if mode in ("ok", "limit_warning") else {"found": "not-a-bool"}
if mode == "limit_warning":  # a passing warning next to a real answer
    print(json.dumps({"type": "error", "message": "You’ve hit your usage limit for GPT-6 Sol; switched to Luna."}))
resumed = "--resume" in argv or (len(argv) > 1 and argv[1] == "resume")
if kind == "claude":
    if "stream-json" in argv:
        sid = argv[argv.index("--resume") + 1] if "--resume" in argv else "sess-1"
        for ev in [{"type": "system", "subtype": "init", "session_id": sid},
                   {"type": "assistant", "message": {"content": [
                       {"type": "tool_use", "id": "tu1", "name": "mcp__rk__rk_text_find", "input": {"query": "x"}}]}},
                   {"type": "user", "message": {"content": [
                       {"type": "tool_result", "tool_use_id": "tu1", "content": "found"}]}},
                   {"type": "assistant", "message": {"content": [{"type": "text", "text": f"turn resumed={resumed}"}]}},
                   {"type": "result", "subtype": "success", "session_id": sid}]:
            print(json.dumps(ev))
    else:
        print(json.dumps({"type": "result", "subtype": "success", "is_error": False, "num_turns": 3,
                          "structured_output": answer, "result": json.dumps(answer), "session_id": "sess-1",
                          "modelUsage": {"claude-fake": {}}}))
else:
    tid = argv[argv.index("resume") + 1] if "resume" in argv else "thread-1"
    for ev in [{"type": "thread.started", "thread_id": tid}, {"type": "turn.started"},
               {"type": "item.completed", "item": {"type": "mcp_tool_call", "server": "rk", "tool": "rk_text_find"}},
               {"type": "item.completed", "item": {"type": "agent_message",
                                                  "text": json.dumps(answer) if "-o" in argv else f"resumed={resumed}"}},
               {"type": "turn.completed", "usage": {}}]:
        print(json.dumps(ev))
    if "-o" in argv:
        Path(argv[argv.index("-o") + 1]).write_text(json.dumps(answer))
