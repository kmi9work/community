"""Диспетчер входящих событий: отсечка повторов, шаг диалога, выбор обработчика."""
from __future__ import annotations

import time as _time
from dataclasses import dataclass, field
from typing import Optional

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .. import logs, metrics
from ..db import is_postgres
from ..messengers.base import Adapter, Incoming, OutMessage, SendError, SendResult
from ..models import DialogState, InboundEvent, Participant
from ..services import participants as psvc
from ..texts import render


@dataclass
class Ctx:
    session: Session
    adapter: Adapter
    inc: Incoming
    dialog: DialogState
    participant: Optional[Participant] = None
    answered: bool = False
    sent: list[str] = field(default_factory=list)

    @property
    def channel(self) -> str:
        return self.inc.channel

    @property
    def chat(self) -> str:
        # Телеграм: личный чат = id пользователя; MAX: пишем по user_id
        return self.inc.chat_id if self.channel == "telegram" and self.inc.chat_id else self.inc.user_id

    def send(self, msg: OutMessage) -> SendResult:
        for attempt in range(2):
            try:
                result = self.adapter.send(self.chat, msg)
                metrics.SENDS.labels(self.channel, "sent").inc()
                logs.event("bot.sent", channel=self.channel, key=msg.key, message_id=result.message_id,
                           participant_id=self.participant.id if self.participant else None)
                self.sent.append(msg.key)
                return result
            except SendError as exc:
                metrics.SENDS.labels(self.channel, "error").inc()
                logs.event("bot.send_failed", channel=self.channel, key=msg.key, error=str(exc)[:300],
                           attempt=attempt + 1)
                if exc.blocked:
                    raise
                if attempt == 0 and not exc.permanent:
                    _time.sleep(min(exc.retry_after or 1, 5))
                    continue
                raise
        raise SendError("unreachable")  # pragma: no cover

    def edit(self, message_id: str, msg: OutMessage) -> bool:
        if not message_id:
            return False
        try:
            self.adapter.edit(self.chat, message_id, msg)
            self.sent.append(msg.key)
            return True
        except SendError as exc:
            # Телеграм не даёт править сообщения старше 48 часов
            logs.event("bot.edit_failed", channel=self.channel, key=msg.key, error=str(exc)[:300])
            return False

    def show(self, msg: OutMessage) -> None:
        """Экран меню: по нажатию кнопки — правим сообщение, иначе отправляем новое."""
        if self.inc.kind == "callback" and self.inc.message_id and not msg.photo and msg.menu is None:
            if self.edit(self.inc.message_id, msg):
                return
        self.send(msg)

    def notice(self, text: str = "") -> None:
        if self.inc.kind != "callback" or self.answered:
            return
        self.answered = True
        try:
            self.adapter.answer_callback(self.inc.callback_id, text, self.chat)
        except SendError as exc:
            logs.event("bot.answer_failed", channel=self.channel, error=str(exc)[:200])

    def set_state(self, state: str, **data) -> None:
        self.dialog.state = state
        merged = dict(self.dialog.data or {})
        merged.update(data)
        self.dialog.data = merged

    def reset_state(self, state: str = "idle") -> None:
        self.dialog.state = state
        self.dialog.data = {}

    @property
    def data(self) -> dict:
        return dict(self.dialog.data or {})


def _is_duplicate(session: Session, inc: Incoming) -> bool:
    if not inc.dedup_key:
        return False
    try:
        with session.begin_nested():
            session.add(InboundEvent(channel=inc.channel, key=inc.dedup_key[:128]))
            session.flush()
        return False
    except IntegrityError:
        return True


def _dialog(session: Session, inc: Incoming) -> DialogState:
    q = select(DialogState).where(DialogState.channel == inc.channel, DialogState.user_id == inc.user_id)
    if is_postgres(session):
        q = q.with_for_update()
    dialog = session.scalar(q)
    if dialog is None:
        try:
            with session.begin_nested():
                dialog = DialogState(channel=inc.channel, user_id=inc.user_id, chat_id=inc.chat_id, state="new",
                                     data={})
                session.add(dialog)
                session.flush()
        except IntegrityError:
            dialog = session.scalar(q)
    if inc.chat_id and dialog.chat_id != inc.chat_id:
        dialog.chat_id = inc.chat_id
    return dialog


def dispatch(session: Session, adapter: Adapter, inc: Optional[Incoming]) -> None:
    if inc is None or not inc.user_id or inc.user_id == "None":
        metrics.INBOUND.labels(adapter.channel, "ignored").inc()
        return
    started = _time.monotonic()
    if _is_duplicate(session, inc):
        metrics.INBOUND.labels(inc.channel, "duplicate").inc()
        logs.event("inbound.duplicate", channel=inc.channel, key=inc.dedup_key)
        return
    metrics.INBOUND.labels(inc.channel, "accepted").inc()
    if inc.kind == "stopped":
        psvc.channel_stopped(session, inc.channel, inc.user_id)
        logs.event("inbound.stopped", channel=inc.channel)
        return
    if inc.kind == "resumed":
        psvc.channel_resumed(session, inc.channel, inc.user_id)
        return

    dialog = _dialog(session, inc)
    participant = psvc.by_channel(session, inc.channel, inc.user_id)
    if participant is not None and participant.status != "active":
        participant = None
    ctx = Ctx(session=session, adapter=adapter, inc=inc, dialog=dialog, participant=participant)
    state_before = dialog.state
    from . import menu, registration

    try:
        with session.begin_nested():
            if participant is not None:
                if inc.channel == "telegram" and not participant.telegram_active:
                    participant.telegram_active = True
                if inc.channel == "max" and not participant.max_active:
                    participant.max_active = True
                menu.handle(ctx)
            else:
                registration.handle(ctx)
            session.flush()
    except Exception as exc:  # noqa: BLE001 — участнику без технических подробностей
        logs.error("bot.handler_failed", channel=inc.channel, kind=inc.kind, code=inc.callback_data[:64],
                   state=state_before)
        if not (isinstance(exc, SendError) and exc.blocked):
            try:
                ctx.send(OutMessage(render("90"), key="90-error-retry"))
            except Exception:  # noqa: BLE001
                pass
    finally:
        ctx.notice()
        metrics.HANDLE_SECONDS.labels(inc.channel).observe(_time.monotonic() - started)
        logs.event("inbound.handled", channel=inc.channel, kind=inc.kind, code=inc.callback_data[:64] or None,
                   state_before=state_before, state_after=dialog.state,
                   participant_id=ctx.participant.id if ctx.participant else None,
                   duration_ms=int((_time.monotonic() - started) * 1000))
