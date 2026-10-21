"""Optional Sentry Agent Tracing. A no-op unless SENTRY_DSN is set and
sentry-sdk is installed (pip install 'morningdrop[sentry]')."""

from __future__ import annotations

import contextlib
import os
from typing import Any, Iterator


def init() -> bool:
    dsn = os.environ.get("SENTRY_DSN")
    if not dsn:
        return False
    try:
        import sentry_sdk
    except ImportError:
        return False
    sentry_sdk.init(dsn=dsn, traces_sample_rate=1.0, send_default_pii=False)
    return True


@contextlib.contextmanager
def span(op: str, name: str, **data: Any) -> Iterator[Any]:
    try:
        import sentry_sdk
    except ImportError:
        yield None
        return
    if not sentry_sdk.is_initialized():
        yield None
        return
    with sentry_sdk.start_span(op=op, name=name) as s:
        for k, v in data.items():
            s.set_data(k, v)
        yield s


@contextlib.contextmanager
def transaction(name: str) -> Iterator[Any]:
    try:
        import sentry_sdk
    except ImportError:
        yield None
        return
    if not sentry_sdk.is_initialized():
        yield None
        return
    with sentry_sdk.start_transaction(op="gen_ai.invoke_agent", name=name) as t:
        yield t
