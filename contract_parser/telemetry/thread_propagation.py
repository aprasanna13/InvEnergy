"""Context-preserving thread execution for OpenTelemetry and Python contextvars.

Ensures worker threads spawned via ThreadPoolExecutor safely inherit active
OpenTelemetry trace and span contexts without losing parent-child relationships.
"""

import contextvars
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Iterable


def wrap_with_context(func: Callable[..., Any]) -> Callable[..., Any]:
    """Wraps a callable so that Python contextvars and OpenTelemetry contexts propagate across threads."""
    cv_ctx = contextvars.copy_context()

    def wrapped(*args: Any, **kwargs: Any) -> Any:
        return cv_ctx.run(func, *args, **kwargs)

    return wrapped


class TracedThreadPoolExecutor(ThreadPoolExecutor):
    """ThreadPoolExecutor that automatically propagates OpenTelemetry contextvars to worker threads."""

    def submit(self, fn: Callable[..., Any], *args: Any, **kwargs: Any):
        return super().submit(wrap_with_context(fn), *args, **kwargs)

    def map(
        self,
        fn: Callable[..., Any],
        *iterables: Iterable[Any],
        timeout: float | None = None,
        chunksize: int = 1,
    ):
        return super().map(
            wrap_with_context(fn),
            *iterables,
            timeout=timeout,
            chunksize=chunksize,
        )
