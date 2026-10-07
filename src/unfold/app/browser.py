"""Disposable managed Chromium: no candidate script executes in the application UI."""

from __future__ import annotations

import base64
import json
import os
import selectors
import shutil
import signal
import subprocess
import threading
import time
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from unfold.provider import GenerationError

ROOT = Path(__file__).resolve().parents[3]
MAX_WEB_BYTES = 24000


def validate_web(html: str) -> None:
    import re

    if not html or len(html.encode()) > MAX_WEB_BYTES:
        raise GenerationError("budget_exceeded", "web candidate exceeds 24 KB")
    # Defense in depth, not a universal proof of JavaScript termination.
    if re.search(
        r"\b(?:fetch|XMLHttpRequest|WebSocket|WebTransport|RTCPeerConnection|Worker|eval|Function|require)\b|\bimport\s|\bwhile\s*\(|for\s*\(\s*;|(?:https?|wss?|file|ftp)://",
        html,
    ):
        raise GenerationError(
            "capability_limit", "network, imports, workers and unbounded loops unsupported"
        )

    class Parser(HTMLParser):
        def handle_starttag(self, tag: str, attributes: list[tuple[str, str | None]]) -> None:
            attrs = dict(attributes)
            if tag in {"iframe", "object", "embed", "link", "base", "form", "video", "audio"}:
                raise GenerationError("capability_limit", "unsupported web element")
            if tag == "script" and "src" in attrs:
                raise GenerationError("capability_limit", "external scripts unsupported")
            if tag == "input" and (attrs.get("type") or "").lower() == "file":
                raise GenerationError("capability_limit", "file access unsupported")
            if tag == "meta" and "http-equiv" in attrs:
                raise GenerationError(
                    "capability_limit", "candidate cannot change browser policies"
                )

    Parser().feed(html)


class BrowserProcess:
    """Wall-clock watchdog kills only this disposable process group on timeout."""

    def __init__(self, html: str, *, timeout: float = 6, validate: bool = True):
        if validate:
            validate_web(html)
        node = shutil.which("node")
        if node is None or not (ROOT / "web/node_modules/@playwright/test").exists():
            raise GenerationError("capability_limit", "managed browser dependencies unavailable")
        # Do not propagate the worker's model credentials or application config.
        environment = {
            key: os.environ[key]
            for key in (
                "PATH",
                "HOME",
                "TMPDIR",
                "TEMP",
                "TMP",
                "LANG",
                "TZ",
                "PLAYWRIGHT_BROWSERS_PATH",
            )
            if key in os.environ
        }
        self.process = subprocess.Popen(
            [node, str(ROOT / "web/runtime/browser.mjs")],
            cwd=ROOT,
            env=environment,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            text=True,
            bufsize=1,
        )
        self.timeout = timeout
        self.sequence = 0
        self.lock = threading.Lock()
        self.started = time.monotonic()
        try:
            self.metadata = self.command("open", html=html)
            if self.metadata["model_key_in_driver"]:
                raise GenerationError(
                    "configuration", "browser driver received forbidden credentials"
                )
        except Exception:
            self.close()
            raise

    def command(self, operation: str, **parameters: Any) -> dict[str, Any]:
        with self.lock:
            if self.process.poll() is not None:
                raise GenerationError("capability_limit", "managed browser stopped")
            self.sequence += 1
            message = {"id": self.sequence, "op": operation, **parameters}
            assert self.process.stdin and self.process.stdout
            try:
                self.process.stdin.write(json.dumps(message) + "\n")
                self.process.stdin.flush()
                with selectors.DefaultSelector() as selector:
                    selector.register(self.process.stdout, selectors.EVENT_READ)
                    if not selector.select(self.timeout):
                        self.kill()
                        raise GenerationError(
                            "timeout", "managed browser operation exceeded wall-clock limit"
                        )
                line = self.process.stdout.readline(3_000_001)
                if len(line) > 3_000_000:
                    self.kill()
                    raise GenerationError("budget_exceeded", "managed browser output exceeds limit")
                response = json.loads(line)
            except (OSError, ValueError) as exc:
                self.kill()
                raise GenerationError(
                    "provider_error", "managed browser communication failed"
                ) from exc
            if response.get("id") != self.sequence:
                self.kill()
                raise GenerationError(
                    "provider_error", "managed browser response identity mismatch"
                )
            if not response.get("ok"):
                if response.get("errorCode") == "timeout":
                    self.kill()
                    raise GenerationError("timeout", "managed browser operation exceeded limit")
                raise GenerationError(
                    "domain_validation", str(response.get("error", "browser failed"))
                )
            result = response["result"]
            if not isinstance(result, dict):
                raise GenerationError("domain_validation", "invalid managed browser result")
            return result

    def screenshot(self) -> bytes:
        return base64.b64decode(self.command("snapshot")["png"], validate=True)

    def kill(self) -> None:
        if self.process.poll() is None:
            try:
                os.killpg(self.process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            self.process.wait(timeout=3)

    def close(self) -> None:
        if self.process.poll() is None:
            try:
                self.command("stop")
                self.process.wait(timeout=2)
            except Exception:
                self.kill()
        for stream in (self.process.stdin, self.process.stdout):
            if stream:
                stream.close()

    def __enter__(self) -> BrowserProcess:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()
