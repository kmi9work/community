"""Рассылки: отбор получателей, запуск, тест, отмена, повтор, содержимое сообщений."""
from __future__ import annotations

from typing import Optional

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from .. import logs
from ..bot.render import (
    TEXT_LIMITS,
    announcement,
    btn_events,
    btn_me,
    btn_org,
    event_values,
    split_long,
)
from ..messengers.base import Button, OutMessage
from ..models import (
    PARTICIPATION_ACTIVE,
    Admin,
    Broadcast,
    Delivery,
    Event,
    EventType,
    Interest,
    Participant,
    Participation,
    Subscription,
    SurveyRefusal,
)
from ..texts import render, render_template, sanitize_html
from ..util import audit, fmt_date, fmt_time, now_utc


class BroadcastError(ValueError):
    pass


# --- получатели ----------------------------------------------------------------------


def _active_participants():
    return select(Participant).where(
        Participant.status == "active",
        Participant.consent.is_(True),
        or_(
            (Participant.telegram_id.is_not(None)) & Participant.telegram_active.is_(True),
            (Participant.max_id.is_not(None)) & Participant.max_active.is_(True),
        ),
    )


def select_recipients(session: Session, b: Broadcast) -> list[Participant]:
    q = _active_participants()
    mode = b.recipient_mode
    if mode == "event_types":
        type_ids = [t.id for t in b.event_types]
        if not type_ids:
            return []
        q = q.where(Participant.id.in_(select(Subscription.participant_id).where(
            Subscription.event_type_id.in_(type_ids), Subscription.subscribed.is_(True))))
    elif mode == "levels":
        level_ids = [lv.id for lv in b.levels]
        if not level_ids:
            return []
        # «подписан на уровень»: выбрал уровень в интересах или подписан на тип,
        # базовый уровень которого — этот уровень
        by_interest = select(Interest.participant_id).where(Interest.level_id.in_(level_ids))
        by_type = (
            select(Subscription.participant_id)
            .join(EventType, EventType.id == Subscription.event_type_id)
            .where(EventType.level_id.in_(level_ids), Subscription.subscribed.is_(True))
        )
        q = q.where(or_(Participant.id.in_(by_interest), Participant.id.in_(by_type)))
    elif mode == "selected":
        ids = [p.id for p in b.participants]
        if not ids:
            return []
        q = q.where(Participant.id.in_(ids))
    elif mode == "event_registered":
        if not b.event_id:
            return []
        q = q.where(Participant.id.in_(select(Participation.participant_id).where(
            Participation.event_id == b.event_id, Participation.status.in_(PARTICIPATION_ACTIVE))))
    elif mode == "event_confirmed":
        if not b.event_id:
            return []
        q = q.where(Participant.id.in_(select(Participation.participant_id).where(
            Participation.event_id == b.event_id, Participation.status == "registered")))
    elif mode != "all":
        raise BroadcastError("Неизвестный способ выбора получателей")
    if b.kind == "survey":
        q = q.where(Participant.surveys_declined_all.is_(False))
        if b.questionnaire_id:
            q = q.where(Participant.id.not_in(select(SurveyRefusal.participant_id).where(
                SurveyRefusal.questionnaire_id == b.questionnaire_id)))
    return list(session.scalars(q.order_by(Participant.last_name, Participant.first_name, Participant.id)))


def validate(b: Broadcast) -> None:
    if b.kind in ("announcement", "reminder", "event_change", "participation") and not b.event_id:
        raise BroadcastError("Выберите мероприятие")
    if b.kind == "survey" and not b.questionnaire_id:
        raise BroadcastError("Выберите опросник")
    if b.kind == "text" and not (b.text or "").strip():
        raise BroadcastError("Для рассылки вида «текст» нужен текст")
    if not (b.subject or "").strip():
        raise BroadcastError("Укажите тему")


# --- содержимое --------------------------------------------------------------------------


def render_messages(session: Session, b: Broadcast, channel: str) -> list[OutMessage]:
    limit = TEXT_LIMITS[channel]
    if b.kind == "announcement":
        event = b.event
        return announcement(event, channel, with_unsubscribe=True,
                            description=sanitize_text(b.text) if b.text.strip() else None)
    if b.kind == "survey":
        q = b.questionnaire
        description = sanitize_html(b.text) if b.text.strip() else _esc(q.description)
        text = render("40", {"survey_title": q.title, "survey_description": description},
                      raw={"survey_description"})
        buttons = [[Button("Пройти опрос", f"sv:take:{q.id}")],
                   [Button("Отказаться", f"sv:decline:{q.id}"), Button("Отказаться от всех", "sv:decline_all")]]
        return _with_buttons(split_long(text, limit), buttons, "40-survey-invite")
    if b.kind == "reminder":
        event = b.event
        custom = (b.reminder.text if b.reminder else "") or b.text
        if custom.strip():
            text = render_template(sanitize_html(custom), event_values(event))
        else:
            text = render("70", event_values(event))
        return _with_buttons(split_long(text, limit), [[btn_org()], [btn_me()]], "70-reminder")
    if b.kind == "event_change":
        event = b.event
        payload = b.payload or {}
        if payload.get("variant") == "cancelled":
            text = render("81", {"event_title": event.title, "event_date": fmt_date(event.date),
                                 "reason": payload.get("reason") or event.change_reason})
            return _with_buttons([text], [[btn_org()], [btn_events()]], "81-event-cancelled")
        from datetime import date as _date, time as _time

        old_date = _date.fromisoformat(payload["old_date"]) if payload.get("old_date") else event.previous_date
        old_time = _time.fromisoformat(payload["old_time"]) if payload.get("old_time") else None
        text = render("80", {
            "event_title": event.title,
            "old_date": fmt_date(old_date), "old_time": fmt_time(old_time),
            "new_date": fmt_date(event.date), "new_time": fmt_time(event.time),
            "event_address": event.address,
        })
        return _with_buttons([text], [[btn_org()], [btn_me()]], "80-event-moved")
    if b.kind == "text":
        body = f"<b>{_esc(b.subject)}</b>\n\n{sanitize_html(b.text)}"
        return _with_buttons(split_long(body, limit), [], "text")
    if b.kind == "participation":
        event = b.event
        if event is None:
            raise BroadcastError("Мероприятие рассылки не найдено")
        payload = b.payload or {}
        part = session.get(Participation, payload["participation_id"]) if payload.get("participation_id") else None
        values = event_values(event)
        values["event_role"] = (part.role.name if part is not None and part.role else "")
        if payload.get("variant") == "declined":
            text = render("83", values)
            return _with_buttons(split_long(text, limit), [[btn_org()], [btn_events()]],
                                 "83-event-participation-declined")
        text = render("82", values)
        return _with_buttons(split_long(text, limit), [[btn_org()], [btn_me()]],
                             "82-event-participation-confirmed")
    raise BroadcastError("Неизвестный вид рассылки")


def _esc(text: str) -> str:
    import html

    return html.escape(text or "", quote=False)


def sanitize_text(text: str) -> str:
    from ..texts import strip_html

    return strip_html(sanitize_html(text))


def _with_buttons(parts: list[str], buttons: list[list[Button]], key: str) -> list[OutMessage]:
    msgs = [OutMessage(p, key=key) for p in parts]
    if msgs:
        msgs[-1].buttons = buttons
    return msgs


def preview(session: Session, b: Broadcast) -> dict:
    validate(b)
    recipients = select_recipients(session, b)
    channels = {"telegram": 0, "max": 0}
    for p in recipients:
        for ch in p.channel_ids():
            channels[ch] += 1
    msgs = render_messages(session, b, "telegram")
    return {
        "recipients_count": len(recipients),
        "deliveries_count": sum(channels.values()),
        "channels": channels,
        "recipients": [{"id": p.id, "label": p.label(), "channels": list(p.channel_ids())} for p in recipients[:500]],
        "messages": [
            {"text": m.text, "photo": bool(m.photo), "buttons": [[x.text for x in row] for row in m.buttons]}
            for m in msgs
        ],
    }


# --- запуск -----------------------------------------------------------------------------


def launch(session: Session, b: Broadcast, actor: str) -> Broadcast:
    if b.status != "draft":
        raise BroadcastError("Запустить можно только черновик")
    validate(b)
    recipients = select_recipients(session, b)
    count = 0
    for p in recipients:
        for channel in p.channel_ids():
            session.add(Delivery(broadcast_id=b.id, participant_id=p.id, channel=channel, status="queued",
                                 attempt=1))
            count += 1
    b.recipients_count = len(recipients)
    b.delivered_count = 0
    b.errors_count = 0
    b.status = "queued" if count else "finished"
    b.started_at = now_utc()
    if not count:
        b.finished_at = now_utc()
    session.flush()
    audit(session, actor, "broadcast_launched", b, kind=b.kind, recipients=len(recipients), deliveries=count)
    logs.event("broadcast.launched", broadcast_id=b.id, kind=b.kind, mode=b.recipient_mode,
               recipients=len(recipients), deliveries=count, test=b.is_test)
    return b


def launch_test(session: Session, b: Broadcast, admin: Admin) -> Broadcast:
    """«Отправить себе»: отдельная тестовая рассылка только автору."""
    validate(b)
    if not admin.participant_id:
        raise BroadcastError("Привяжите свою карточку участника в профиле — туда уйдёт тестовое сообщение")
    me = session.get(Participant, admin.participant_id)
    if me is None or not me.channel_ids():
        raise BroadcastError("У Вашей карточки участника нет доступного мессенджера")
    test = Broadcast(
        kind=b.kind, subject=f"[тест] {b.subject}", event_id=b.event_id, questionnaire_id=b.questionnaire_id,
        reminder_id=b.reminder_id, text=b.text, author_id=admin.id, recipient_mode="selected", is_test=True,
        payload=dict(b.payload or {}), status="draft",
    )
    test.participants = [me]
    session.add(test)
    session.flush()
    return launch(session, test, f"admin:{admin.id}")


def create_system_broadcast(session: Session, event: Event, kind: str, subject: str, payload: dict,
                            reminder_id: Optional[int] = None,
                            participants: Optional[list[Participant]] = None,
                            recipient_mode: str = "event_registered") -> Broadcast:
    """Системная рассылка. Получатели — заявки в силе на мероприятие, либо заданный список."""
    recipients = list(participants or [])
    b = Broadcast(kind=kind, subject=subject, event_id=event.id, reminder_id=reminder_id, author_id=None,
                  recipient_mode="selected" if recipients else recipient_mode, payload=payload, status="draft")
    if recipients:
        b.participants = recipients
    session.add(b)
    session.flush()
    session.refresh(b)
    return launch(session, b, "system")


def cancel(session: Session, b: Broadcast, reason: str, actor: str) -> None:
    if b.status in ("finished", "cancelled"):
        raise BroadcastError("Рассылка уже завершена или отменена")
    if not reason.strip():
        raise BroadcastError("Укажите причину отмены")
    b.status = "cancelled"
    b.cancel_reason = reason.strip()
    b.finished_at = now_utc()
    audit(session, actor, "broadcast_cancelled", b, reason=reason)
    logs.event("broadcast.cancelled", broadcast_id=b.id)


def retry_errors(session: Session, b: Broadcast, actor: str) -> int:
    if b.status == "cancelled":
        raise BroadcastError("Рассылка отменена")
    rows = list(session.scalars(select(Delivery).where(Delivery.broadcast_id == b.id, Delivery.status == "error")))
    for d in rows:
        d.status = "queued"
        d.attempt += 1
        d.error = ""
        d.next_attempt_at = None
    if rows:
        b.status = "queued"
        b.finished_at = None
    refresh_counts(session, b)
    audit(session, actor, "broadcast_retry", b, count=len(rows))
    logs.event("broadcast.retry", broadcast_id=b.id, count=len(rows))
    return len(rows)


def refresh_counts(session: Session, b: Broadcast) -> None:
    session.flush()
    counts = dict(session.execute(
        select(Delivery.status, func.count(Delivery.id)).where(Delivery.broadcast_id == b.id).group_by(Delivery.status)
    ).all())
    b.delivered_count = counts.get("sent", 0)
    b.errors_count = counts.get("error", 0)
    if b.status in ("queued", "sending") and not counts.get("queued"):
        b.status = "finished"
        b.finished_at = now_utc()
        logs.event("broadcast.finished", broadcast_id=b.id, delivered=b.delivered_count, errors=b.errors_count)
