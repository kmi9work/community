"""Воркер рассылки: очередь «Доставка рассылки» и планировщик напоминаний."""
from __future__ import annotations

import time as _time
from datetime import timedelta

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from .. import logs, metrics
from ..config import settings
from ..db import is_postgres
from ..messengers import get_adapter
from ..messengers.base import SendError
from ..models import UPCOMING_STATUSES, Broadcast, Delivery, Event, InboundEvent, Participant, Reminder
from ..util import as_utc, event_start, hours, now_utc
from . import broadcasts as bsvc
from . import participants as psvc


def pick_batch(session: Session, limit: int = 20) -> list[Delivery]:
    now = now_utc()
    q = (
        select(Delivery)
        .join(Broadcast, Broadcast.id == Delivery.broadcast_id)
        .where(
            Delivery.status == "queued",
            Broadcast.status.in_(("queued", "sending")),
            (Delivery.next_attempt_at.is_(None)) | (Delivery.next_attempt_at <= now),
        )
        .order_by(Delivery.id)
        .limit(limit)
    )
    if is_postgres(session):
        q = q.with_for_update(skip_locked=True, of=Delivery)
    return list(session.scalars(q))


def send_delivery(session: Session, d: Delivery) -> None:
    b = d.broadcast
    if b.status == "queued":
        b.status = "sending"
        b.started_at = b.started_at or now_utc()
    p: Participant = d.participant
    target = p.channel_ids().get(d.channel) if p.status == "active" else None
    adapter = get_adapter(d.channel)
    if not target:
        _fail(d, "участник недоступен в этом мессенджере")
        return
    if adapter is None:
        _fail(d, "мессенджер не настроен (нет токена бота)")
        return
    try:
        messages = bsvc.render_messages(session, b, d.channel)
    except Exception as exc:  # noqa: BLE001 — ошибка шаблона не должна ронять воркер
        logs.error("delivery.render_failed", delivery_id=d.id, broadcast_id=b.id)
        _fail(d, f"ошибка подготовки сообщения: {type(exc).__name__}")
        return
    started = _time.monotonic()
    try:
        first_id = ""
        for msg in messages:
            result = adapter.send(target, msg)
            first_id = first_id or result.message_id
            if result.telegram_file_id and b.event is not None:
                b.event.poster_telegram_file_id = result.telegram_file_id
            if result.max_token and b.event is not None:
                b.event.poster_max_token = result.max_token
            if settings.send_pause_seconds:
                _time.sleep(settings.send_pause_seconds)
    except SendError as exc:
        metrics.SENDS.labels(d.channel, "error").inc()
        _handle_error(session, d, exc)
        return
    d.status = "sent"
    d.message_id = first_id
    d.sent_at = now_utc()
    d.error = ""
    metrics.SENDS.labels(d.channel, "sent").inc()
    logs.event("delivery.sent", delivery_id=d.id, broadcast_id=b.id, participant_id=p.id, channel=d.channel,
               attempt=d.attempt, message_id=first_id, duration_ms=int((_time.monotonic() - started) * 1000))


def _fail(d: Delivery, reason: str) -> None:
    d.status = "error"
    d.error = reason
    logs.event("delivery.error", delivery_id=d.id, broadcast_id=d.broadcast_id, channel=d.channel,
               attempt=d.attempt, error=reason)


def _handle_error(session: Session, d: Delivery, exc: SendError) -> None:
    now = now_utc()
    if exc.retry_after:
        d.next_attempt_at = now + timedelta(seconds=exc.retry_after)
        d.error = str(exc)
        logs.event("delivery.rate_limited", delivery_id=d.id, channel=d.channel, retry_after=exc.retry_after)
        return
    if exc.blocked:
        _fail(d, str(exc))
        psvc.channel_stopped(session, d.channel, d.participant.telegram_id if d.channel == "telegram"
                             else d.participant.max_id)
        return
    if exc.permanent or d.attempt >= settings.max_send_attempts:
        _fail(d, str(exc))
        return
    d.attempt += 1
    d.error = str(exc)
    d.next_attempt_at = now + timedelta(seconds=10 * 2 ** (d.attempt - 1))
    logs.event("delivery.retry_scheduled", delivery_id=d.id, channel=d.channel, attempt=d.attempt,
               error=str(exc)[:200])


def process_queue(session_factory, limit: int = 20) -> int:
    """Отправить одну пачку. Каждая строка — своя транзакция: сбой виден и продолжается с места остановки."""
    with session_factory() as session:
        ids = [d.id for d in pick_batch(session, limit)]
        session.rollback()
    done = 0
    for delivery_id in ids:
        with session_factory() as session:
            q = select(Delivery).where(Delivery.id == delivery_id, Delivery.status == "queued")
            if is_postgres(session):
                q = q.with_for_update(skip_locked=True)
            d = session.scalar(q)
            if d is None or d.broadcast.status not in ("queued", "sending"):
                session.rollback()
                continue
            try:
                send_delivery(session, d)
                bsvc.refresh_counts(session, d.broadcast)
                session.commit()
                done += 1
            except Exception:  # noqa: BLE001
                session.rollback()
                logs.error("delivery.crash", delivery_id=delivery_id)
    return done


def finish_idle_broadcasts(session: Session) -> None:
    for b in session.scalars(select(Broadcast).where(Broadcast.status.in_(("queued", "sending")))):
        bsvc.refresh_counts(session, b)


def schedule_reminders(session: Session) -> int:
    """Создать рассылки «напоминание» для наступивших сроков."""
    now = now_utc()
    created = 0
    rows = session.execute(
        select(Reminder, Event)
        .join(Event, Event.id == Reminder.event_id)
        .where(Reminder.is_active.is_(True), Reminder.sent_at.is_(None), Event.status.in_(UPCOMING_STATUSES))
    ).all()
    for reminder, event in rows:
        start = event_start(event.date, event.time)
        if now >= start:
            continue
        if now < start - hours(reminder.hours_before):
            continue
        bsvc.create_system_broadcast(session, event, "reminder", f"Напоминание: {event.title}",
                                     {"hours_before": reminder.hours_before}, reminder_id=reminder.id,
                                     recipient_mode="event_confirmed")
        reminder.sent_at = now
        session.flush()
        created += 1
        logs.event("scheduler.reminder", event_id=event.id, reminder_id=reminder.id)
    return created


def cleanup(session: Session) -> None:
    session.execute(delete(InboundEvent).where(InboundEvent.received_at < now_utc() - timedelta(days=7)))


def queue_stats(session: Session) -> tuple[int, float]:
    rows = list(session.scalars(select(Delivery.created_at).where(Delivery.status == "queued")))
    if not rows:
        return 0, 0.0
    oldest = min(as_utc(r) for r in rows)
    return len(rows), (now_utc() - oldest).total_seconds()
