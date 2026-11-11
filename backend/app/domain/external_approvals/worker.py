"""Background synchroniser: a daemon thread that runs ``tick`` every CANTON_EVENT_POLL_INTERVAL_SECONDS.

On Cloud Run (CPU allocated only during requests) use the scheduled job instead
(``python -m app.jobs canton-sync`` or ``POST /api/v1/ops/jobs/canton-sync``); both call the same tick.
"""

from __future__ import annotations

import logging
import threading

from app.core.config import get_settings
from app.infrastructure.database.session import session_factory

from . import tick

log = logging.getLogger("groundwork.canton")
_stop = threading.Event()
_thread: threading.Thread | None = None


def _loop() -> None:
    interval = max(1.0, get_settings().canton_event_poll_interval_seconds)
    while not _stop.wait(interval):
        db = session_factory()()
        try:
            tick(db)
            db.commit()
        except Exception:  # noqa: BLE001 — the loop must survive; state is retried next tick
            db.rollback()
            log.exception("canton sync tick failed")
        finally:
            db.close()


def start() -> None:
    global _thread
    if _thread is None or not _thread.is_alive():
        _stop.clear()
        _thread = threading.Thread(target=_loop, name="canton-sync", daemon=True)
        _thread.start()


def stop() -> None:
    _stop.set()
