"""structlog processor that mirrors every event into the ops store."""

from __future__ import annotations

from contextlib import suppress

from structlog.types import EventDict, WrappedLogger

from .ops import OpsStore


class OpsLogProcessor:
    """Copy each event into the store, then hand it on untouched.

    Storage is a bounded queue append, so this never blocks and never raises
    into application code. A failure here must not cost a log line either; the
    JSON renderer still runs after this processor.
    """

    def __init__(self, store: OpsStore) -> None:
        self.store = store

    def __call__(
        self, logger: WrappedLogger, method_name: str, event_dict: EventDict
    ) -> EventDict:
        _ = logger
        with suppress(Exception):
            self.store.record_log(dict(event_dict), method_name)
        return event_dict
