"""Участник: регистрация, подписки, согласие, удаление."""
from __future__ import annotations

from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import logs
from ..models import (
    Appeal,
    Consent,
    ConsentDocument,
    DialogState,
    EventType,
    Level,
    Participant,
    Subscription,
)
from ..util import audit, now_utc

BASE_LEVEL = "new"


def by_channel(session: Session, channel: str, user_id: str) -> Optional[Participant]:
    column = Participant.telegram_id if channel == "telegram" else Participant.max_id
    return session.scalar(select(Participant).where(column == str(user_id)))


def find_other_channel_card(session: Session, channel: str, phone: str) -> Optional[Participant]:
    """Карточка с тем же телефоном мессенджера, заведённая из другого мессенджера."""
    if not phone:
        return None
    this_col = Participant.telegram_id if channel == "telegram" else Participant.max_id
    other_col = Participant.max_id if channel == "telegram" else Participant.telegram_id
    return session.scalar(
        select(Participant)
        .where(
            Participant.status == "active",
            Participant.messenger_phone == phone,
            this_col.is_(None),
            other_col.is_not(None),
        )
        .order_by(Participant.id)
        .limit(1)
    )


def current_consent_document(session: Session) -> Optional[ConsentDocument]:
    return session.scalar(
        select(ConsentDocument).where(ConsentDocument.is_current.is_(True)).order_by(ConsentDocument.id.desc())
    )


def base_level_types(session: Session) -> list[EventType]:
    level = session.scalar(select(Level).where(Level.identifier == BASE_LEVEL))
    if not level:
        return []
    return [t for t in level.event_types if t.is_active]


def set_subscription(session: Session, participant: Participant, event_type_id: int, subscribed: bool) -> Subscription:
    sub = session.scalar(
        select(Subscription).where(
            Subscription.participant_id == participant.id, Subscription.event_type_id == event_type_id
        )
    )
    if sub is None:
        sub = Subscription(participant_id=participant.id, event_type_id=event_type_id, subscribed=subscribed)
        session.add(sub)
    elif sub.subscribed != subscribed:
        sub.subscribed = subscribed
        sub.changed_at = now_utc()
    session.flush()
    return sub


def subscribe_types(session: Session, participant: Participant, types: list[EventType], only_missing: bool = False):
    for t in types:
        if only_missing:
            exists = session.scalar(
                select(Subscription.id).where(
                    Subscription.participant_id == participant.id, Subscription.event_type_id == t.id
                )
            )
            if exists:
                continue
        set_subscription(session, participant, t.id, True)


def subscribed_type_names(session: Session, participant: Participant) -> list[str]:
    rows = session.scalars(
        select(EventType)
        .join(Subscription, Subscription.event_type_id == EventType.id)
        .where(Subscription.participant_id == participant.id, Subscription.subscribed.is_(True))
        .order_by(EventType.order, EventType.id)
    ).all()
    return [t.name for t in rows]


def register(
    session: Session,
    *,
    channel: str,
    user_id: str,
    first_name: str,
    last_name: str,
    messenger_phone: str,
    contact_phone: str,
    link_id: Optional[int],
    document: ConsentDocument,
) -> Participant:
    """Завершение регистрации после согласия: новая карточка или привязка к найденной."""
    participant: Optional[Participant] = None
    if link_id:
        participant = session.get(Participant, link_id)
        if participant is not None and participant.status != "active":
            participant = None
    created = participant is None
    if created:
        participant = Participant(
            first_name=first_name,
            last_name=last_name,
            messenger_phone=messenger_phone,
            contact_phone=contact_phone or messenger_phone,
            registered_at=now_utc(),
            status="active",
        )
        session.add(participant)
    else:
        participant.contact_phone = contact_phone or participant.contact_phone
        if not participant.messenger_phone:
            participant.messenger_phone = messenger_phone
    if channel == "telegram":
        participant.telegram_id = str(user_id)
        participant.telegram_active = True
    else:
        participant.max_id = str(user_id)
        participant.max_active = True
    participant.consent = True
    session.flush()
    session.add(Consent(participant_id=participant.id, document_id=document.id, status="given", channel=channel,
                        given_at=now_utc()))
    subscribe_types(session, participant, base_level_types(session), only_missing=not created)
    audit(session, f"participant:{participant.id}", "registered" if created else "linked_channel", participant,
          channel=channel)
    logs.event("participant.registered", participant_id=participant.id, channel=channel, linked=not created)
    return participant


def revoke_consent(session: Session, participant: Participant, actor: str) -> None:
    last = session.scalar(
        select(Consent)
        .where(Consent.participant_id == participant.id, Consent.status == "given")
        .order_by(Consent.given_at.desc(), Consent.id.desc())
    )
    if last:
        last.status = "revoked"
        last.revoked_at = now_utc()
    participant.consent = False
    audit(session, actor, "consent_revoked", participant)


def channel_stopped(session: Session, channel: str, user_id: str) -> Optional[Participant]:
    """Участник отписался от бота в мессенджере. Нет активных каналов — карточка удаляется."""
    participant = by_channel(session, channel, user_id)
    dialog = session.scalar(select(DialogState).where(DialogState.channel == channel,
                                                      DialogState.user_id == str(user_id)))
    if dialog is not None:
        session.delete(dialog)
        session.flush()
    if participant is None:
        return None
    if channel == "telegram":
        participant.telegram_active = False
    else:
        participant.max_active = False
    logs.event("participant.channel_stopped", participant_id=participant.id, channel=channel)
    if not participant.telegram_active and not participant.max_active:
        anonymize(session, participant, "system")
    return participant


def channel_resumed(session: Session, channel: str, user_id: str) -> None:
    participant = by_channel(session, channel, user_id)
    if participant is None:
        return
    if channel == "telegram":
        participant.telegram_active = True
    else:
        participant.max_active = True


def anonymize(session: Session, participant: Participant, actor: str) -> None:
    """Статус «удалён»: персональные данные и идентификаторы стираются."""
    for channel, uid in (("telegram", participant.telegram_id), ("max", participant.max_id)):
        if uid:
            dialog = session.scalar(select(DialogState).where(DialogState.channel == channel,
                                                              DialogState.user_id == uid))
            if dialog is not None:
                session.delete(dialog)
    participant.first_name = ""
    participant.last_name = ""
    participant.messenger_phone = ""
    participant.contact_phone = ""
    participant.telegram_id = None
    participant.max_id = None
    participant.telegram_active = False
    participant.max_active = False
    participant.status = "deleted"
    participant.consent = False
    participant.deleted_at = now_utc()
    for appeal in session.scalars(select(Appeal).where(Appeal.participant_id == participant.id)):
        appeal.text = ""
    audit(session, actor, "participant_deleted", participant)
    logs.event("participant.deleted", participant_id=participant.id)
