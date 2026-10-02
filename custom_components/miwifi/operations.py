"""Serialized, read-back verified router changes (independent of HA)."""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from .exceptions import LuciError


class OperationError(Exception):
    """A requested state could not be confirmed; safe for user-facing output."""


@dataclass
class Operation:
    """Only non-secret, scalar desired values belong in this record."""

    request_id: int
    status: str = "pending"


class OperationManager:
    """One lock per router for changes AND coordinator polling.

    A lost response is not success or proof of failure. Only read-back decides.
    Commands run at most once; retries are exclusively reads. The previous value
    is obtained after acquiring the lock, not while a previous request is pending.
    """

    def __init__(self, attempts: int = 3, delay: float = 1, timeout: float = 45):
        self.lock = asyncio.Lock()
        self.records: OrderedDict[str, Operation] = OrderedDict()
        self._counter = 0
        self.attempts = attempts
        self.delay = delay
        self.timeout = timeout
        self._active: set[asyncio.Task] = set()
        self._stopping = False

    async def async_shutdown(self) -> None:
        """Cancel queued/in-flight changes before coordinator storage/logout."""
        self._stopping = True
        tasks = self._active - {asyncio.current_task()}
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def execute(self, *args, **kwargs) -> Any:
        if self._stopping:
            raise OperationError("Router integration is unloading; no command sent")
        task = asyncio.current_task()
        self._active.add(task)
        try:
            return await self._execute_locked(*args, **kwargs)
        finally:
            self._active.discard(task)

    def observe(self, key: str) -> None:
        """A fresh authoritative poll restores certainty, not an old cache."""
        if (record := self.records.get(key)) and record.status == "unknown":
            record.status = "confirmed"

    async def _execute_locked(
        self,
        key: str,
        desired: Any,
        command: Callable[[], Awaitable[Any]],
        read: Callable[[], Awaitable[Any]],
        apply: Callable[[Any], None],
        notify: Callable[[], None],
        *,
        matches: Callable[[Any, Any], bool] | None = None,
        optimistic: Callable[[], None] | None = None,
        preflight: Callable[[Any], None] | None = None,
    ) -> Any:
        """Return verified actual state, or publish rollback and raise.

        Preflight reads must succeed before any command. Optimism is optional
        and never changes the authoritative value used for comparisons.
        """
        matches = matches or (lambda actual, target: actual == target)
        async with self.lock:
            self._counter += 1
            record = Operation(self._counter)
            self.records[key] = record
            self.records.move_to_end(key)
            while len(self.records) > 256:
                self.records.popitem(last=False)
            previous = actual = None
            have_previous = have_actual = False
            try:
                async with asyncio.timeout(self.timeout):
                    previous = await read()
                    have_previous = True
                    if preflight:
                        preflight(previous)
                    if matches(previous, desired):
                        record.status = "confirmed"
                        apply(previous)
                        notify()
                        return previous
                    if optimistic:
                        optimistic()
                    notify()
                    # Swallow only expected API/transport errors long enough to
                    # reconcile. An exception never fabricates a successful ack.
                    try:
                        await command()
                    except LuciError:
                        pass
                    for attempt in range(self.attempts):
                        try:
                            actual = await read()
                            have_actual = True
                        except (LuciError, ValueError, TypeError, KeyError):
                            # A failed later read makes the earlier snapshot stale.
                            have_actual = False
                        else:
                            if matches(actual, desired):
                                record.status = "confirmed"
                                apply(actual)
                                notify()
                                return actual
                        if attempt + 1 < self.attempts:
                            await asyncio.sleep(self.delay)
            except asyncio.CancelledError:
                record.status = "unknown"
                if have_previous:
                    apply(previous)
                notify()
                raise
            except (LuciError, ValueError, TypeError, KeyError, TimeoutError):
                have_actual = False
            except Exception:
                record.status = "unknown"
                if have_previous:
                    apply(previous)
                notify()
                raise
            record.status = "failed" if have_actual else "unknown"
            if have_actual:
                apply(actual)
            elif have_previous:
                apply(previous)
            notify()
            raise OperationError(
                "Router did not apply the requested state"
                if have_actual
                else "Router state could not be verified; command was not retried"
            )


def manager_for(updater: Any) -> OperationManager:
    """Also usable with small fake coordinators in portable regression tests."""
    if not isinstance(getattr(updater, "operations", None), OperationManager):
        updater.operations = OperationManager()
    return updater.operations
