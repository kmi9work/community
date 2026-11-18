"""Мероприятия: предстоящие, запись, перенос, отмена, проведение."""
from __future__ import annotations

from datetime import date, time
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import logs
from ..models import (
    UPCOMING_STATUSES,
    Broadcast,
    Event,
    EventType,
    Participant,
    Participation,
    Reminder,
)
from ..util import audit, now_utc, today_local


class EventError(ValueError):
    pass


def upcoming(session: Session, type_identifier: Optional[str] = None, limit: int = 20) -> list[Event]:
    q = (
        select(Event)
        .join(EventType, EventType.id == Event.event_type_id)
        .where(Event.status.in_(UPCOMING_STATUSES), Event.date >= today_local())
        .order_by(Event.date, Event.time.is_(None), Event.time, Event.id)
        .limit(limit)
    )
    if type_identifier:
        q = q.where(EventType.identifier == type_identifier)
    return list(session.scalars(q))


def is_open(event: Event) -> bool:
    return event.status in UPCOMING_STATUSES and event.date >= today_local()


def registered_count(session: Session, event_id: int) -> int:
    return session.scalar(
        select(func.count(Participation.id)).where(
            Participation.event_id == event_id, Participation.status == "registered"
        )
    ) or 0


def is_full(session: Session, event: Event) -> bool:
    return bool(event.seats_limit) and registered_count(session, event.id) >= (event.seats_limit or 0)


def participation(session: Session, participant_id: int, event_id: int) -> Optional[Participation]:
    return session.scalar(
        select(Participation).where(Participation.participant_id == participant_id, Participation.event_id == event_id)
    )


def register_participant(session: Session, participant: Participant, event: Event, actor: str) -> Participation:
    """Создать запись «записан» (или вернуть отменённую)."""
    part = participation(session, participant.id, event.id)
    if part is None:
        part = Participation(participant_id=participant.id, event_id=event.id, status="registered",
                             registered_at=now_utc())
        session.add(part)
    else:
        part.status = "registered"
        part.registered_at = now_utc()
    session.flush()
    audit(session, actor, "participation_registered", part, event_id=event.id)
    logs.event("event.registered", participant_id=participant.id, event_id=event.id)
    return part


def _cancel_pending_reminders(session: Session, event: Event, reason: str) -> None:
    pending = session.scalars(
        select(Broadcast).where(
            Broadcast.event_id == event.id,
            Broadcast.kind == "reminder",
            Broadcast.status.in_(("draft", "queued", "sending")),
        )
    )
    for b in pending:
        b.status = "cancelled"
        b.cancel_reason = reason
        b.finished_at = now_utc()


def move(session: Session, event: Event, *, new_date: date, new_time: Optional[time], new_address: str,
         reason: str, admin_id: Optional[int]) -> Broadcast:
    from .broadcasts import create_system_broadcast

    if event.status in ("cancelled", "completed"):
        raise EventError("Отменённое или проведённое мероприятие не переносится")
    if not reason.strip():
        raise EventError("Укажите причину переноса")
    if new_date == event.date and new_time == event.time and new_address == event.address:
        raise EventError("Дата, время и адрес не изменились")
    old_date, old_time = event.date, event.time
    event.previous_date, event.previous_time = old_date, old_time
    event.date, event.time, event.address = new_date, new_time, new_address
    event.change_reason = reason.strip()
    event.status = "moved"
    event.updated_by_id = admin_id
    event.updated_at = now_utc()
    # напоминания считаются заново от новых даты и времени
    for r in session.scalars(select(Reminder).where(Reminder.event_id == event.id)):
        r.sent_at = None
    _cancel_pending_reminders(session, event, "мероприятие перенесено")
    session.flush()
    audit(session, f"admin:{admin_id}" if admin_id else "system", "event_moved", event,
          old_date=str(old_date), old_time=str(old_time or ""), reason=reason)
    payload = {"variant": "moved", "old_date": old_date.isoformat(), "old_time": old_time.isoformat() if old_time else ""}
    return create_system_broadcast(session, event, "event_change", f"Перенос: {event.title}", payload)


def cancel(session: Session, event: Event, *, reason: str, admin_id: Optional[int]) -> Broadcast:
    from .broadcasts import create_system_broadcast

    if event.status in ("cancelled", "completed"):
        raise EventError("Мероприятие уже отменено или проведено")
    if not reason.strip():
        raise EventError("Укажите причину отмены")
    event.status = "cancelled"
    event.change_reason = reason.strip()
    event.updated_by_id = admin_id
    event.updated_at = now_utc()
    _cancel_pending_reminders(session, event, "мероприятие отменено")
    session.flush()
    audit(session, f"admin:{admin_id}" if admin_id else "system", "event_cancelled", event, reason=reason)
    return create_system_broadcast(session, event, "event_change", f"Отмена: {event.title}",
                                   {"variant": "cancelled", "reason": reason.strip()})


def complete(session: Session, event: Event, *, admin_id: Optional[int]) -> None:
    if event.status == "cancelled":
        raise EventError("Отменённое мероприятие нельзя провести")
    event.status = "completed"
    event.updated_by_id = admin_id
    event.updated_at = now_utc()
    _cancel_pending_reminders(session, event, "мероприятие проведено")
    audit(session, f"admin:{admin_id}" if admin_id else "system", "event_completed", event)
