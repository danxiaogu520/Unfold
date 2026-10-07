from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from pydantic import ValidationError

from unfold.app.generation import Generator
from unfold.app.service import Claim, RunService
from unfold.provider import GenerationError

logger = logging.getLogger("unfold.worker")


class Worker:
    def __init__(self, service: RunService, generator: Generator):
        self.service = service
        self.generator = generator
        self.maintenance_lock = threading.Lock()
        self.next_prune_at = 0.0

    def execute(self, claim: Claim) -> bool:
        stop = threading.Event()

        def renew() -> None:
            while not stop.wait(self.service.settings.lease_seconds / 3):
                try:
                    valid = self.service.heartbeat(claim)
                except Exception:
                    logger.error(
                        "lease renewal unavailable run_id=%s attempt=%s",
                        claim.run_id,
                        claim.attempt,
                    )
                    break
                if not valid:
                    break

        heartbeat = threading.Thread(target=renew, daemon=True)
        heartbeat.start()
        try:
            proposal = self.service.completed_candidate(claim)
            if proposal is None:
                proposal = self.generator.generate(claim)
            return self.service.finish_success(claim, proposal)
        except GenerationError as exc:
            return self.service.finish_failure(claim, exc)
        except (ValidationError, ValueError):
            return self.service.finish_failure(
                claim,
                GenerationError(
                    "domain_validation", "candidate rejected by application validation"
                ),
            )
        except Exception:
            # Do not log arbitrary exception strings that may contain DSNs or keys.
            logger.error(
                "generation execution failed run_id=%s attempt=%s", claim.run_id, claim.attempt
            )
            return self.service.finish_failure(
                claim,
                GenerationError(
                    "provider_error", "generation execution failed; inspect recorded attempt"
                ),
            )
        finally:
            stop.set()
            heartbeat.join(timeout=2)

    def tick(self) -> bool:
        self.service.recover_expired()
        with self.maintenance_lock:
            if time.monotonic() >= self.next_prune_at:
                self.service.prune_events()
                self.next_prune_at = time.monotonic() + min(
                    60, self.service.settings.event_retention_seconds / 2
                )
        claim = self.service.claim()
        if claim is None:
            return False
        self.execute(claim)
        return True

    def run(self, stop: threading.Event | None = None) -> None:
        stop = stop or threading.Event()

        def loop() -> None:
            while not stop.is_set():
                try:
                    worked = self.tick()
                except Exception:
                    logger.error("worker poll unavailable; retrying without claiming success")
                    worked = False
                if not worked:
                    stop.wait(0.25)

        with ThreadPoolExecutor(max_workers=self.service.settings.worker_concurrency) as pool:
            futures = [pool.submit(loop) for _ in range(self.service.settings.worker_concurrency)]
            for future in futures:
                future.result()
