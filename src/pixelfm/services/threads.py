"""Run blocking network calls without blocking the event loop or app exit."""

from __future__ import annotations

import asyncio
import contextlib
import threading
from collections.abc import Callable
from functools import partial


async def run_in_thread[T](fn: Callable[..., T], *args: object) -> T:
    """Run ``fn(*args)`` in a daemon thread and await its result.

    Unlike :func:`asyncio.to_thread`, the thread is a daemon, so a slow network
    call can never keep the process alive after the user quits. Cancelling the
    awaiting task returns immediately; the thread finishes in the background
    and its result is discarded.
    """
    loop = asyncio.get_running_loop()
    future: asyncio.Future[T] = loop.create_future()

    def deliver(setter: Callable[[object], None], value: object) -> None:
        if not future.done():
            setter(value)

    def runner() -> None:
        try:
            result = fn(*args)
        except BaseException as exc:
            callback = partial(deliver, future.set_exception, exc)
        else:
            callback = partial(deliver, future.set_result, result)
        # The loop may already be closed if the app exited meanwhile.
        with contextlib.suppress(RuntimeError):
            loop.call_soon_threadsafe(callback)

    threading.Thread(target=runner, name="pixelfm-io", daemon=True).start()
    return await future
