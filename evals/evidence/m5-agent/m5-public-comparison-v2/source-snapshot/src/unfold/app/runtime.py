"""Short-lived interactive managed browsers; model HTML stays out of the app DOM."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Annotated, Any, Literal

from pydantic import Field

from unfold.app.browser import BrowserProcess
from unfold.app.files import FileStore, StoredFile
from unfold.app.models import ResourceCandidate, ResourceRevision, ResourceTask
from unfold.app.service import RunService, ServiceError, entity_id
from unfold.contracts import Contract


class RuntimeCommand(Contract):
    action: Literal["click", "key", "text", "reset", "stop"]
    x: Annotated[float, Field(ge=0, le=800, allow_inf_nan=False)] | None = None
    y: Annotated[float, Field(ge=0, le=540, allow_inf_nan=False)] | None = None
    key: (
        Literal[
            "ArrowRight",
            "ArrowLeft",
            "ArrowUp",
            "ArrowDown",
            "Enter",
            "Tab",
            "Backspace",
            "Space",
            "r",
        ]
        | None
    ) = None
    text: Annotated[str, Field(max_length=100)] | None = None


@dataclass
class LiveBrowser:
    id: str
    resource_id: str
    space_id: str
    browser: BrowserProcess
    expires: float


class RuntimeManager:
    def __init__(self, service: RunService, *, max_sessions: int = 1, lifetime: float = 120):
        self.service, self.max_sessions, self.lifetime = service, max_sessions, lifetime
        self.lock = threading.Lock()
        self.sessions: dict[str, LiveBrowser] = {}
        self.stop = threading.Event()
        self.reaper = threading.Thread(target=self.reap, daemon=True)
        self.reaper.start()

    def reap(self) -> None:
        while not self.stop.wait(1):
            with self.lock:
                expired = [
                    key
                    for key, row in self.sessions.items()
                    if row.expires < time.monotonic() or row.browser.process.poll() is not None
                ]
                for key in expired:
                    self.sessions.pop(key).browser.close()

    @staticmethod
    def view(row: LiveBrowser) -> dict[str, Any]:
        return {
            "session_id": row.id,
            "resource_id": row.resource_id,
            "viewport": {"width": 800, "height": 540},
            "seconds_remaining": max(0, int(row.expires - time.monotonic())),
            "execution": "managed_browser",
            "source_script_in_app_dom": False,
            "frame_url": f"/api/v1/runtime/{row.id}/frame",
        }

    def start(self, space_id: str, resource_id: str) -> dict[str, Any]:
        with self.lock:
            for row in self.sessions.values():
                if (
                    row.resource_id == resource_id
                    and row.space_id == space_id
                    and row.browser.process.poll() is None
                ):
                    return self.view(row)
            if len(self.sessions) >= self.max_sessions:
                raise ServiceError("budget_exceeded", "stop the existing web preview first", 429)
            with self.service.database.transaction() as session:
                self.service.require_space(session, space_id)
                revision = session.get(ResourceRevision, resource_id)
                task = session.get(ResourceTask, revision.task_id) if revision else None
                if not task or task.space_id != space_id:
                    raise ServiceError("invalid_input", "web resource not found", 404)
                assert revision
                candidate = session.get(ResourceCandidate, revision.candidate_id)
                assert candidate
                stored = StoredFile(**candidate.file)
            store = FileStore(self.service.settings.data_directory)
            if stored.mime != "text/html" or not store.verify(stored):
                raise ServiceError("capability_limit", "web resource missing or unsupported", 409)
            row = LiveBrowser(
                entity_id("runtime"),
                resource_id,
                space_id,
                BrowserProcess(store.path(stored.relative_path).read_text()),
                time.monotonic() + self.lifetime,
            )
            self.sessions[row.id] = row
            return self.view(row)

    def require(self, session_id: str) -> LiveBrowser:
        row = self.sessions.get(session_id)
        if not row or row.expires <= time.monotonic():
            raise ServiceError("invalid_input", "web runtime ended; start a new session", 410)
        return row

    def frame(self, session_id: str) -> bytes:
        with self.lock:
            row = self.require(session_id)
            return row.browser.screenshot()

    def command(self, session_id: str, command: RuntimeCommand) -> dict[str, Any]:
        with self.lock:
            row = self.require(session_id)
            if command.action == "stop":
                self.sessions.pop(session_id).browser.close()
                return {"stopped": True}
            arguments: dict[str, Any] = {}
            if command.action == "click":
                if command.x is None or command.y is None:
                    raise ServiceError("invalid_input", "click requires viewport coordinates")
                arguments = {"x": command.x, "y": command.y}
            elif command.action == "key":
                if command.key is None:
                    raise ServiceError("invalid_input", "keyboard action requires a supported key")
                arguments = {"key": command.key}
            elif command.action == "text":
                if command.text is None:
                    raise ServiceError("invalid_input", "text action requires bounded text")
                arguments = {"text": command.text}
            result = row.browser.command(command.action, **arguments)
            return {**result, "session_id": row.id}

    def close(self) -> None:
        self.stop.set()
        with self.lock:
            for row in self.sessions.values():
                row.browser.close()
            self.sessions.clear()
        self.reaper.join(timeout=2)
