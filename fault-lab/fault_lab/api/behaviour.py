"""Injectable runtime behaviour of the demo API (fault fixtures B-F04..B-F06, B-N01).

The fault controller switches behaviour through an internal endpoint; the API
itself only knows *how* to behave, never which benchmark case is running.
The release identifier is neutral (``rel-<n>``) so it carries no hint of the
injected fault.
"""

from __future__ import annotations

import random
import threading
import time
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Final

LEAK_CHUNK_BYTES: Final = 256 * 1024
LEAK_CAP_BYTES: Final = 400 * 1024 * 1024
FAULTY_ERROR_RATE: Final = 0.3
NOISE_ERROR_RATE: Final = 0.15


class Profile(StrEnum):
    HEALTHY = "healthy"
    FAULTY = "faulty"  # B-F05: a release that fails a share of requests
    LEAKY = "leaky"  # B-F04: a release that retains memory on every request


class InjectedFailureError(RuntimeError):
    """A request failed because of an injected behaviour."""


@dataclass
class Behaviour:
    release_id: str = "rel-1"
    profile: Profile = Profile.HEALTHY
    noise_until: float = 0.0
    rng: random.Random = field(default_factory=lambda: random.Random(4242))  # noqa: S311
    _leak: list[bytearray] = field(default_factory=list)
    _cpu_stop: threading.Event | None = None
    _cpu_threads: list[threading.Thread] = field(default_factory=list)

    def snapshot(self) -> dict[str, object]:
        return {
            "release_id": self.release_id,
            "profile": self.profile.value,
            "cpu_load": self.cpu_load_active,
            "noise_active": time.time() < self.noise_until,
            "leaked_bytes": self.leaked_bytes,
        }

    @property
    def leaked_bytes(self) -> int:
        return len(self._leak) * LEAK_CHUNK_BYTES

    @property
    def cpu_load_active(self) -> bool:
        return self._cpu_stop is not None and not self._cpu_stop.is_set()

    def deploy(self, release_id: str, profile: Profile) -> None:
        self.release_id = release_id
        self.profile = profile
        if profile is not Profile.LEAKY:
            self._leak.clear()  # a rollback re-creates the process in a real deploy

    def on_request(self) -> None:
        """Apply injected effects for one request; may raise InjectedFailureError."""
        if self.profile is Profile.LEAKY and self.leaked_bytes < LEAK_CAP_BYTES:
            self._leak.append(bytearray(LEAK_CHUNK_BYTES))
        if self.profile is Profile.FAULTY and self.rng.random() < FAULTY_ERROR_RATE:
            raise InjectedFailureError("release error")
        if time.time() < self.noise_until and self.rng.random() < NOISE_ERROR_RATE:
            raise InjectedFailureError("transient error")

    def start_noise(self, seconds: float) -> None:
        self.noise_until = time.time() + seconds

    def start_cpu_load(self, threads: int = 2) -> None:
        if self.cpu_load_active:
            return
        stop = threading.Event()

        def burn() -> None:
            # Pure-Python busy loop: competes for the GIL with request handling.
            while not stop.is_set():
                sum(i * i for i in range(10_000))

        self._cpu_stop = stop
        self._cpu_threads = [threading.Thread(target=burn, daemon=True) for _ in range(threads)]
        for thread in self._cpu_threads:
            thread.start()

    def stop_cpu_load(self) -> None:
        if self._cpu_stop is not None:
            self._cpu_stop.set()
            for thread in self._cpu_threads:
                thread.join(timeout=2)
        self._cpu_stop = None
        self._cpu_threads = []

    def reset(self) -> None:
        self.stop_cpu_load()
        self.deploy("rel-1", Profile.HEALTHY)
        self.noise_until = 0.0
