"""Воркер: очередь доставки, планировщик напоминаний, при разработке — long polling.

    python -m app.worker            # рассылки и напоминания
    python -m app.worker --poll     # плюс long polling Телеграм и MAX (локально, без webhook)
"""
from __future__ import annotations

import argparse
import signal
import threading
import time

from . import logs
from .config import settings
from .db import get_engine, session_scope
from .logs import setup_logging
from .services import delivery

stop = threading.Event()


def run_queue() -> None:
    last_tick = 0.0
    while not stop.is_set():
        try:
            done = delivery.process_queue(session_scope)
        except Exception:  # noqa: BLE001
            logs.error("worker.queue_failed")
            done = 0
        now = time.monotonic()
        if now - last_tick >= 30:
            last_tick = now
            try:
                with session_scope() as s:
                    created = delivery.schedule_reminders(s)
                    delivery.finish_idle_broadcasts(s)
                    delivery.cleanup(s)
                    depth, age = delivery.queue_stats(s)
                logs.event("scheduler.tick", reminders=created, queue_depth=depth, queue_oldest_s=int(age))
            except Exception:  # noqa: BLE001
                logs.error("scheduler.failed")
        if not done:
            stop.wait(settings.worker_poll_seconds)


def poll_telegram() -> None:
    from .bot.core import dispatch
    from .messengers import get_adapter

    adapter = get_adapter("telegram")
    if adapter is None:
        return
    adapter.delete_webhook()
    offset = 0
    logs.event("poll.started", channel="telegram")
    while not stop.is_set():
        try:
            updates = adapter.get_updates(offset)
        except Exception:  # noqa: BLE001
            logs.error("poll.failed", channel="telegram")
            stop.wait(5)
            continue
        for upd in updates:
            offset = max(offset, upd["update_id"] + 1)
            try:
                with session_scope() as s:
                    dispatch(s, adapter, adapter.parse(upd))
            except Exception:  # noqa: BLE001
                logs.error("poll.dispatch_failed", channel="telegram")


def poll_max() -> None:
    from .bot.core import dispatch
    from .messengers import get_adapter

    adapter = get_adapter("max")
    if adapter is None:
        return
    marker = None
    logs.event("poll.started", channel="max")
    while not stop.is_set():
        try:
            updates, marker = adapter.get_updates(marker)
        except Exception:  # noqa: BLE001
            logs.error("poll.failed", channel="max")
            stop.wait(5)
            continue
        for upd in updates:
            try:
                with session_scope() as s:
                    dispatch(s, adapter, adapter.parse(upd))
            except Exception:  # noqa: BLE001
                logs.error("poll.dispatch_failed", channel="max")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--poll", action="store_true", help="long polling вместо webhook (для разработки)")
    args = parser.parse_args()
    setup_logging("worker")
    get_engine()

    def _stop(*_):
        stop.set()

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    threads = []
    if args.poll:
        for target in (poll_telegram, poll_max):
            t = threading.Thread(target=target, daemon=True)
            t.start()
            threads.append(t)
    logs.event("worker.started", poll=args.poll)
    run_queue()
    logs.event("worker.stopped")


if __name__ == "__main__":
    main()
