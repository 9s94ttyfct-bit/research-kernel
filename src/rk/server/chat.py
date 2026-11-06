"""Chat sessions hosted by ``rk serve`` (used by ``rk chat`` now, by the Workbench in Plan 3)."""

from __future__ import annotations

import threading
import uuid
from collections.abc import Iterator
from dataclasses import dataclass

from rk.llm.drivers import DriverSpec, build_provider
from rk.llm.types import ChatEvent, ChatSession, ChatSpec, RunContext
from rk.roles.policy import ROLES, role_tools
from rk.roles.prompts import build_system_prompt

SESSION_TTL_S = 12 * 3600


@dataclass
class _Open:
    session: ChatSession
    lock: threading.Lock
    driver: str


class ChatManager:
    def __init__(self, kernel, registry):
        self.kernel, self.registry = kernel, registry
        self._sessions: dict[str, _Open] = {}
        self._mutex = threading.Lock()

    def open(self, driver: str, role: str, project: str) -> str:
        if role not in ROLES:
            raise ValueError(f"unknown role {role!r}")
        self.kernel.project(project)  # raises LookupError for unknown/invalid projects
        spec = DriverSpec.parse(driver)
        provider = build_provider(spec, self.kernel.settings())
        avail = provider.available()
        if not avail.ok:
            raise ValueError(f"{driver} unavailable: {avail.reason}")
        chat_id = f"chat_{uuid.uuid4().hex[:16]}"
        # A session is bound to one project, so only project-scoped tools are offered.
        tools = [t for t in role_tools(role, self.registry)
                 if "project" in self.registry.get(t).input_model.model_fields]
        token = None
        if provider.kind == "agentic_cli" and tools:
            token = self.kernel.tokens.issue_task(chat_id, role, project, tools, ttl_s=SESSION_TTL_S)
        session = provider.open_chat(
            ChatSpec(role=role, project=project, system=build_system_prompt(role, None), tools=tools, model=spec.model),
            RunContext(kernel=self.kernel, registry=self.registry, task_token=token, actor=f"{role}@{spec}"))
        with self._mutex:
            self._sessions[chat_id] = _Open(session, threading.Lock(), str(spec))
        return chat_id

    def send(self, chat_id: str, text: str) -> Iterator[ChatEvent]:
        with self._mutex:
            entry = self._sessions.get(chat_id)
        if entry is None:
            raise KeyError(chat_id)
        if not entry.lock.acquire(blocking=False):
            raise RuntimeError("a message is already being processed in this chat")
        try:
            yield from entry.session.send(text)
        finally:
            entry.lock.release()

    def close(self, chat_id: str) -> bool:
        with self._mutex:
            entry = self._sessions.pop(chat_id, None)
        if entry is None:
            return False
        entry.session.close()
        self.kernel.tokens.revoke(chat_id)
        return True

    def close_all(self) -> None:
        for chat_id in list(self._sessions):
            self.close(chat_id)
