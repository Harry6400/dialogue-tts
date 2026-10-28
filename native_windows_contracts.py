"""Small explicit ports for the existing Windows mixin host; no JS setters."""
from dataclasses import dataclass, field
from typing import ContextManager, Protocol
import threading


class LockPort(Protocol):
    def acquire(self, blocking: bool = True) -> bool: ...
    def release(self) -> None: ...
    def __enter__(self) -> object: ...
    def __exit__(self, *args: object) -> object: ...


class WindowPort(Protocol):
    def evaluate_js(self, script: str) -> object: ...
    def create_file_dialog(self, *args: object, **kwargs: object) -> object: ...
    def create_confirmation_dialog(self, title: str, message: str) -> bool: ...
    def destroy(self) -> None: ...


class WorkPoolPort(Protocol):
    def transaction(self) -> ContextManager[object]: ...
    def has_running(self) -> bool: ...


class ServicePort(Protocol):
    update_lock: LockPort
    update_in_progress: bool
    jobs: WorkPoolPort
    batches: WorkPoolPort
    workspaces: object


class UpdaterPort(Protocol):
    offer: dict | None
    state: dict
    def check(self) -> dict: ...
    def prepare_confirmed(self) -> object: ...
    def start_helper(self, pids: list[int]) -> object: ...


@dataclass
class NativeLocks:
    # Preserve the existing update -> save -> folder -> service -> work-pool
    # locking order. Each bridge owns independent locks unless explicitly given.
    save: LockPort = field(default_factory=threading.Lock)
    folders: LockPort = field(default_factory=threading.RLock)
    update: LockPort = field(default_factory=threading.Lock)
