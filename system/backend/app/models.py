"""Сущности CRM (HUMAN_DESC.md, раздел «Сущности в CRM»).

Значения перечислений хранятся латиницей, подписи для админки — в CHOICES.
Время хранится в UTC (timezone-aware), дата и время мероприятия — местные
(часовой пояс сообщества, settings.timezone).
"""
from __future__ import annotations

from datetime import date, datetime, time, timezone
from typing import Optional

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Table,
    Text,
    Time,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


# --- перечисления -----------------------------------------------------------

CHANNELS = {"telegram": "Телеграм", "max": "MAX"}
PARTICIPANT_STATUS = {"active": "активен", "deleted": "удалён"}
CONSENT_STATUS = {"given": "дано", "revoked": "отозвано"}
EVENT_STATUS = {
    "planned": "запланировано",
    "moved": "перенесено",
    "cancelled": "отменено",
    "completed": "проведено",
}
UPCOMING_STATUSES = ("planned", "moved")
PARTICIPATION_STATUS = {"registered": "записан", "cancelled": "отменил запись"}
ATTENDANCE = {"came": "Пришёл", "absent": "Не пришёл"}
ROLE_KIND = {"participant": "участник", "organizer": "организатор"}
QUESTIONNAIRE_PURPOSE = {"registration": "анкета записи на мероприятие", "survey": "опрос"}
ANSWER_TYPE = {"open": "открытый (текст)", "single": "выбор одного варианта", "multi": "выбор нескольких вариантов"}
RUN_STATUS = {"started": "начат", "completed": "завершён", "declined": "отказ"}
BROADCAST_KIND = {
    "announcement": "анонс мероприятия",
    "survey": "опрос",
    "reminder": "напоминание",
    "event_change": "изменение мероприятия",
    "text": "текст",
}
BROADCAST_STATUS = {
    "draft": "черновик",
    "queued": "в очереди",
    "sending": "отправляется",
    "finished": "завершена",
    "cancelled": "отменена",
}
RECIPIENT_MODE = {
    "all": "все активные участники",
    "event_types": "по типам мероприятий",
    "levels": "по уровням",
    "selected": "выбранные участники",
    "event_registered": "записанные на мероприятие",
}
DELIVERY_STATUS = {"queued": "в очереди", "sent": "отправлено", "error": "ошибка"}


# --- связи многие-ко-многим ---------------------------------------------------

level_event_types = Table(
    "level_event_types",
    Base.metadata,
    Column("level_id", ForeignKey("levels.id", ondelete="CASCADE"), primary_key=True),
    Column("event_type_id", ForeignKey("event_types.id", ondelete="CASCADE"), primary_key=True),
)
level_activity_types = Table(
    "level_activity_types",
    Base.metadata,
    Column("level_id", ForeignKey("levels.id", ondelete="CASCADE"), primary_key=True),
    Column("activity_type_id", ForeignKey("activity_types.id", ondelete="CASCADE"), primary_key=True),
)
question_event_types = Table(
    "question_event_types",
    Base.metadata,
    Column("question_id", ForeignKey("questions.id", ondelete="CASCADE"), primary_key=True),
    Column("event_type_id", ForeignKey("event_types.id", ondelete="CASCADE"), primary_key=True),
)
answer_selected_options = Table(
    "answer_selected_options",
    Base.metadata,
    Column("answer_id", ForeignKey("answers.id", ondelete="CASCADE"), primary_key=True),
    Column("option_id", ForeignKey("answer_options.id", ondelete="CASCADE"), primary_key=True),
)
organizer_roles = Table(
    "organizer_participation_roles",
    Base.metadata,
    Column("organizer_participation_id", ForeignKey("organizer_participations.id", ondelete="CASCADE"), primary_key=True),
    Column("role_id", ForeignKey("game_roles.id", ondelete="CASCADE"), primary_key=True),
)
broadcast_event_types = Table(
    "broadcast_event_types",
    Base.metadata,
    Column("broadcast_id", ForeignKey("broadcasts.id", ondelete="CASCADE"), primary_key=True),
    Column("event_type_id", ForeignKey("event_types.id", ondelete="CASCADE"), primary_key=True),
)
broadcast_levels = Table(
    "broadcast_levels",
    Base.metadata,
    Column("broadcast_id", ForeignKey("broadcasts.id", ondelete="CASCADE"), primary_key=True),
    Column("level_id", ForeignKey("levels.id", ondelete="CASCADE"), primary_key=True),
)
broadcast_participants = Table(
    "broadcast_participants",
    Base.metadata,
    Column("broadcast_id", ForeignKey("broadcasts.id", ondelete="CASCADE"), primary_key=True),
    Column("participant_id", ForeignKey("participants.id", ondelete="CASCADE"), primary_key=True),
)


# --- администраторы -----------------------------------------------------------


class Admin(Base):
    """Учётная запись администратора CRM (участники в CRM не заходят)."""

    __tablename__ = "admins"
    id: Mapped[int] = mapped_column(primary_key=True)
    login: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(200), default="")
    password_hash: Mapped[str] = mapped_column(String(300))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    # Своя карточка участника — для тестовой отправки «Отправить себе»
    participant_id: Mapped[Optional[int]] = mapped_column(ForeignKey("participants.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_login_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    participant: Mapped[Optional["Participant"]] = relationship(foreign_keys=[participant_id])

    def label(self) -> str:
        return self.name or self.login


# --- участник и согласия ------------------------------------------------------


class Participant(Base):
    __tablename__ = "participants"
    id: Mapped[int] = mapped_column(primary_key=True)
    first_name: Mapped[str] = mapped_column(String(100), default="")
    last_name: Mapped[str] = mapped_column(String(100), default="")
    messenger_phone: Mapped[str] = mapped_column(String(32), default="", index=True)
    contact_phone: Mapped[str] = mapped_column(String(32), default="", index=True)
    telegram_id: Mapped[Optional[str]] = mapped_column(String(64), unique=True)
    max_id: Mapped[Optional[str]] = mapped_column(String(64), unique=True)
    # Служебное: бот не заблокирован в мессенджере (отписка — False)
    telegram_active: Mapped[bool] = mapped_column(Boolean, default=False)
    max_active: Mapped[bool] = mapped_column(Boolean, default=False)
    registered_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), default=utcnow)
    status: Mapped[str] = mapped_column(String(16), default="active", index=True)
    consent: Mapped[bool] = mapped_column(Boolean, default=False)
    surveys_declined_all: Mapped[bool] = mapped_column(Boolean, default=False)
    deleted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    consents: Mapped[list["Consent"]] = relationship(back_populates="participant", order_by="Consent.given_at")
    subscriptions: Mapped[list["Subscription"]] = relationship(back_populates="participant")
    interests: Mapped[list["Interest"]] = relationship(back_populates="participant")
    participations: Mapped[list["Participation"]] = relationship(back_populates="participant")

    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}".strip()

    def label(self) -> str:
        if self.status == "deleted":
            return f"Удалён #{self.id}"
        return self.full_name or f"#{self.id}"

    def channel_ids(self) -> dict[str, str]:
        """Каналы, в которые можно писать: {канал: идентификатор}."""
        result: dict[str, str] = {}
        if self.telegram_id and self.telegram_active:
            result["telegram"] = self.telegram_id
        if self.max_id and self.max_active:
            result["max"] = self.max_id
        return result

    @property
    def messengers(self) -> list[str]:
        out = []
        if self.telegram_id:
            out.append("telegram")
        if self.max_id:
            out.append("max")
        return out


class ConsentDocument(Base):
    __tablename__ = "consent_documents"
    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(300))
    version: Mapped[str] = mapped_column(String(50))
    url: Mapped[str] = mapped_column(String(1000))
    published_at: Mapped[date] = mapped_column(Date)
    is_current: Mapped[bool] = mapped_column(Boolean, default=False)

    def label(self) -> str:
        return f"{self.title} ({self.version})"


class Consent(Base):
    __tablename__ = "consents"
    id: Mapped[int] = mapped_column(primary_key=True)
    participant_id: Mapped[int] = mapped_column(ForeignKey("participants.id", ondelete="CASCADE"), index=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("consent_documents.id"))
    status: Mapped[str] = mapped_column(String(16), default="given")
    channel: Mapped[str] = mapped_column(String(16))
    given_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    revoked_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    participant: Mapped[Participant] = relationship(back_populates="consents")
    document: Mapped[ConsentDocument] = relationship()

    def label(self) -> str:
        return f"Согласие #{self.id}"


# --- уровни, типы ------------------------------------------------------------


class Level(Base):
    __tablename__ = "levels"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    identifier: Mapped[str] = mapped_column(String(64), unique=True)
    order: Mapped[int] = mapped_column(Integer, default=0)

    event_types: Mapped[list["EventType"]] = relationship(secondary=level_event_types, order_by="EventType.order")
    activity_types: Mapped[list["ActivityType"]] = relationship(
        secondary=level_activity_types, order_by="ActivityType.order"
    )

    def label(self) -> str:
        return self.name


class ActivityType(Base):
    __tablename__ = "activity_types"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(300))
    identifier: Mapped[str] = mapped_column(String(64), unique=True)
    order: Mapped[int] = mapped_column(Integer, default=0)

    levels: Mapped[list[Level]] = relationship(secondary=level_activity_types, viewonly=True)

    def label(self) -> str:
        return self.name


class EventType(Base):
    __tablename__ = "event_types"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    identifier: Mapped[str] = mapped_column(String(64), unique=True)
    # Базовый уровень типа — уровень, на котором тип появляется впервые
    level_id: Mapped[int] = mapped_column(ForeignKey("levels.id"))
    order: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    level: Mapped[Level] = relationship(foreign_keys=[level_id])

    def label(self) -> str:
        return self.name


class GameRole(Base):
    __tablename__ = "game_roles"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    identifier: Mapped[str] = mapped_column(String(64), unique=True)
    kind: Mapped[str] = mapped_column(String(16))
    order: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    def label(self) -> str:
        return self.name


# --- мероприятия ---------------------------------------------------------------


class Event(Base):
    __tablename__ = "events"
    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(300))
    event_type_id: Mapped[int] = mapped_column(ForeignKey("event_types.id"), index=True)
    date: Mapped[date] = mapped_column(Date, index=True)
    time: Mapped[Optional[time]] = mapped_column(Time)
    address: Mapped[str] = mapped_column(String(500), default="")
    poster: Mapped[str] = mapped_column(String(300), default="")  # имя файла в MEDIA_DIR/posters
    description: Mapped[str] = mapped_column(Text, default="")
    seats_limit: Mapped[Optional[int]] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16), default="planned", index=True)
    previous_date: Mapped[Optional[date]] = mapped_column(Date)
    previous_time: Mapped[Optional[time]] = mapped_column(Time)
    change_reason: Mapped[str] = mapped_column(Text, default="")
    # Служебное: кеш загруженной афиши в мессенджерах
    poster_telegram_file_id: Mapped[str] = mapped_column(String(300), default="")
    poster_max_token: Mapped[str] = mapped_column(String(500), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    created_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("admins.id", ondelete="SET NULL"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    updated_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("admins.id", ondelete="SET NULL"))

    event_type: Mapped[EventType] = relationship()
    participations: Mapped[list["Participation"]] = relationship(back_populates="event")
    organizers: Mapped[list["OrganizerParticipation"]] = relationship(back_populates="event")
    reminders: Mapped[list["Reminder"]] = relationship(back_populates="event", order_by="Reminder.hours_before.desc()")

    def label(self) -> str:
        return f"{self.title} ({self.date:%d.%m.%Y})" if self.date else self.title


class Participation(Base):
    __tablename__ = "participations"
    __table_args__ = (UniqueConstraint("participant_id", "event_id", name="uq_participation"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    participant_id: Mapped[int] = mapped_column(ForeignKey("participants.id", ondelete="CASCADE"), index=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("events.id", ondelete="CASCADE"), index=True)
    role_id: Mapped[Optional[int]] = mapped_column(ForeignKey("game_roles.id", ondelete="SET NULL"))
    status: Mapped[str] = mapped_column(String(16), default="registered")
    attendance: Mapped[Optional[str]] = mapped_column(String(16))
    registered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    participant: Mapped[Participant] = relationship(back_populates="participations")
    event: Mapped[Event] = relationship(back_populates="participations")
    role: Mapped[Optional[GameRole]] = relationship()

    def label(self) -> str:
        return f"Запись #{self.id}"


class OrganizerParticipation(Base):
    __tablename__ = "organizer_participations"
    __table_args__ = (UniqueConstraint("participant_id", "event_id", name="uq_organizer"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    participant_id: Mapped[int] = mapped_column(ForeignKey("participants.id", ondelete="CASCADE"), index=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("events.id", ondelete="CASCADE"), index=True)

    participant: Mapped[Participant] = relationship()
    event: Mapped[Event] = relationship(back_populates="organizers")
    roles: Mapped[list[GameRole]] = relationship(secondary=organizer_roles, order_by="GameRole.order")

    def label(self) -> str:
        return f"Организатор #{self.id}"


class Subscription(Base):
    __tablename__ = "subscriptions"
    __table_args__ = (UniqueConstraint("participant_id", "event_type_id", name="uq_subscription"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    participant_id: Mapped[int] = mapped_column(ForeignKey("participants.id", ondelete="CASCADE"), index=True)
    event_type_id: Mapped[int] = mapped_column(ForeignKey("event_types.id", ondelete="CASCADE"), index=True)
    subscribed: Mapped[bool] = mapped_column(Boolean, default=True)
    changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    participant: Mapped[Participant] = relationship(back_populates="subscriptions")
    event_type: Mapped[EventType] = relationship()

    def label(self) -> str:
        return f"Подписка #{self.id}"


class Interest(Base):
    __tablename__ = "interests"
    __table_args__ = (UniqueConstraint("participant_id", "level_id", "activity_type_id", name="uq_interest"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    participant_id: Mapped[int] = mapped_column(ForeignKey("participants.id", ondelete="CASCADE"), index=True)
    level_id: Mapped[int] = mapped_column(ForeignKey("levels.id", ondelete="CASCADE"))
    activity_type_id: Mapped[int] = mapped_column(ForeignKey("activity_types.id", ondelete="CASCADE"))
    changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    participant: Mapped[Participant] = relationship(back_populates="interests")
    level: Mapped[Level] = relationship()
    activity_type: Mapped[ActivityType] = relationship()

    def label(self) -> str:
        return f"Интерес #{self.id}"


# --- опросы --------------------------------------------------------------------


class Questionnaire(Base):
    __tablename__ = "questionnaires"
    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(300))
    identifier: Mapped[str] = mapped_column(String(64), unique=True)
    purpose: Mapped[str] = mapped_column(String(16), default="survey")
    description: Mapped[str] = mapped_column(Text, default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    questions: Mapped[list["Question"]] = relationship(
        back_populates="questionnaire", order_by="(Question.order, Question.id)"
    )

    def label(self) -> str:
        return self.title


class Question(Base):
    __tablename__ = "questions"
    id: Mapped[int] = mapped_column(primary_key=True)
    questionnaire_id: Mapped[int] = mapped_column(ForeignKey("questionnaires.id", ondelete="CASCADE"), index=True)
    text: Mapped[str] = mapped_column(Text)
    answer_type: Mapped[str] = mapped_column(String(16), default="open")
    max_choices: Mapped[Optional[int]] = mapped_column(Integer)
    required: Mapped[bool] = mapped_column(Boolean, default=False)
    ask_every_time: Mapped[bool] = mapped_column(Boolean, default=False)
    order: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    questionnaire: Mapped[Questionnaire] = relationship(back_populates="questions")
    options: Mapped[list["AnswerOption"]] = relationship(
        back_populates="question", order_by="(AnswerOption.order, AnswerOption.id)"
    )
    event_types: Mapped[list[EventType]] = relationship(secondary=question_event_types)

    def label(self) -> str:
        return self.text[:80]

    def active_options(self) -> list["AnswerOption"]:
        return [o for o in self.options if o.is_active]


class AnswerOption(Base):
    __tablename__ = "answer_options"
    id: Mapped[int] = mapped_column(primary_key=True)
    question_id: Mapped[int] = mapped_column(ForeignKey("questions.id", ondelete="CASCADE"), index=True)
    text: Mapped[str] = mapped_column(String(500))
    order: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    question: Mapped[Question] = relationship(back_populates="options")

    def label(self) -> str:
        return self.text


class SurveyRun(Base):
    """Прохождение опроса: один заход участника в форму."""

    __tablename__ = "survey_runs"
    id: Mapped[int] = mapped_column(primary_key=True)
    participant_id: Mapped[int] = mapped_column(ForeignKey("participants.id", ondelete="CASCADE"), index=True)
    questionnaire_id: Mapped[int] = mapped_column(ForeignKey("questionnaires.id", ondelete="CASCADE"), index=True)
    event_id: Mapped[Optional[int]] = mapped_column(ForeignKey("events.id", ondelete="CASCADE"), index=True)
    status: Mapped[str] = mapped_column(String(16), default="started", index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    channel: Mapped[str] = mapped_column(String(16))
    current_question_id: Mapped[Optional[int]] = mapped_column(ForeignKey("questions.id", ondelete="SET NULL"))
    # Служебное: вопросы прохождения (фиксируются при старте), сообщение с текущим
    # вопросом и отметки «выбора нескольких» до нажатия «Готово»
    question_ids: Mapped[list] = mapped_column(JSON, default=list)
    message_id: Mapped[str] = mapped_column(String(64), default="")
    pending: Mapped[list] = mapped_column(JSON, default=list)

    participant: Mapped[Participant] = relationship()
    questionnaire: Mapped[Questionnaire] = relationship()
    event: Mapped[Optional[Event]] = relationship()
    current_question: Mapped[Optional[Question]] = relationship()
    answers: Mapped[list["Answer"]] = relationship(back_populates="run", order_by="Answer.id")

    def label(self) -> str:
        return f"Прохождение #{self.id}"

    def position(self) -> tuple[int, int]:
        """(номер текущего вопроса с 1, всего вопросов)."""
        total = len(self.question_ids or [])
        if self.current_question_id in (self.question_ids or []):
            return self.question_ids.index(self.current_question_id) + 1, total
        return total, total


class Answer(Base):
    __tablename__ = "answers"
    id: Mapped[int] = mapped_column(primary_key=True)
    participant_id: Mapped[int] = mapped_column(ForeignKey("participants.id", ondelete="CASCADE"), index=True)
    question_id: Mapped[int] = mapped_column(ForeignKey("questions.id", ondelete="CASCADE"), index=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("survey_runs.id", ondelete="CASCADE"), index=True)
    event_id: Mapped[Optional[int]] = mapped_column(ForeignKey("events.id", ondelete="SET NULL"))
    text: Mapped[str] = mapped_column(Text, default="")
    answered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    participant: Mapped[Participant] = relationship()
    question: Mapped[Question] = relationship()
    run: Mapped[SurveyRun] = relationship(back_populates="answers")
    event: Mapped[Optional[Event]] = relationship()
    options: Mapped[list[AnswerOption]] = relationship(secondary=answer_selected_options, order_by="AnswerOption.order")

    def label(self) -> str:
        return f"Ответ #{self.id}"

    def display(self) -> str:
        if self.options:
            return "; ".join(o.text for o in self.options)
        return self.text


class SurveyRefusal(Base):
    __tablename__ = "survey_refusals"
    id: Mapped[int] = mapped_column(primary_key=True)
    participant_id: Mapped[int] = mapped_column(ForeignKey("participants.id", ondelete="CASCADE"), index=True)
    questionnaire_id: Mapped[Optional[int]] = mapped_column(ForeignKey("questionnaires.id", ondelete="CASCADE"))
    refused_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    participant: Mapped[Participant] = relationship()
    questionnaire: Mapped[Optional[Questionnaire]] = relationship()

    def label(self) -> str:
        return f"Отказ #{self.id}"


# --- напоминания и рассылки ------------------------------------------------------


class Reminder(Base):
    __tablename__ = "reminders"
    id: Mapped[int] = mapped_column(primary_key=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("events.id", ondelete="CASCADE"), index=True)
    hours_before: Mapped[float] = mapped_column(Float)
    text: Mapped[str] = mapped_column(Text, default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    event: Mapped[Event] = relationship(back_populates="reminders")

    def label(self) -> str:
        return f"За {self.hours_before:g} ч"


class Broadcast(Base):
    __tablename__ = "broadcasts"
    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(16), index=True)
    subject: Mapped[str] = mapped_column(String(300))
    event_id: Mapped[Optional[int]] = mapped_column(ForeignKey("events.id", ondelete="SET NULL"), index=True)
    questionnaire_id: Mapped[Optional[int]] = mapped_column(ForeignKey("questionnaires.id", ondelete="SET NULL"))
    reminder_id: Mapped[Optional[int]] = mapped_column(ForeignKey("reminders.id", ondelete="SET NULL"))
    text: Mapped[str] = mapped_column(Text, default="")
    author_id: Mapped[Optional[int]] = mapped_column(ForeignKey("admins.id", ondelete="SET NULL"))  # пусто — система
    recipient_mode: Mapped[str] = mapped_column(String(32), default="all")
    is_test: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(16), default="draft", index=True)
    recipients_count: Mapped[int] = mapped_column(Integer, default=0)
    delivered_count: Mapped[int] = mapped_column(Integer, default=0)
    errors_count: Mapped[int] = mapped_column(Integer, default=0)
    cancel_reason: Mapped[str] = mapped_column(Text, default="")
    # Служебное: данные системной рассылки (прежние дата и время при переносе и т. п.)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)

    event: Mapped[Optional[Event]] = relationship()
    questionnaire: Mapped[Optional[Questionnaire]] = relationship()
    reminder: Mapped[Optional[Reminder]] = relationship()
    author: Mapped[Optional[Admin]] = relationship()
    event_types: Mapped[list[EventType]] = relationship(secondary=broadcast_event_types)
    levels: Mapped[list[Level]] = relationship(secondary=broadcast_levels)
    participants: Mapped[list[Participant]] = relationship(secondary=broadcast_participants)

    def label(self) -> str:
        return self.subject

    @property
    def author_label(self) -> str:
        return self.author.label() if self.author else "система"


class Delivery(Base):
    __tablename__ = "deliveries"
    __table_args__ = (
        UniqueConstraint("broadcast_id", "participant_id", "channel", name="uq_delivery"),
        Index("ix_delivery_queue", "status", "next_attempt_at"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    broadcast_id: Mapped[int] = mapped_column(ForeignKey("broadcasts.id", ondelete="CASCADE"), index=True)
    participant_id: Mapped[int] = mapped_column(ForeignKey("participants.id", ondelete="CASCADE"), index=True)
    channel: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16), default="queued")
    message_id: Mapped[str] = mapped_column(String(64), default="")
    attempt: Mapped[int] = mapped_column(Integer, default=1)
    sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    next_attempt_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    broadcast: Mapped[Broadcast] = relationship()
    participant: Mapped[Participant] = relationship()

    def label(self) -> str:
        return f"Доставка #{self.id}"


# --- обращения -------------------------------------------------------------------


class Appeal(Base):
    """Обращение: сообщение участника из «Написать организатору»."""

    __tablename__ = "appeals"
    id: Mapped[int] = mapped_column(primary_key=True)
    participant_id: Mapped[int] = mapped_column(ForeignKey("participants.id", ondelete="CASCADE"), index=True)
    text: Mapped[str] = mapped_column(Text)
    channel: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    processed: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    comment: Mapped[str] = mapped_column(Text, default="")

    participant: Mapped[Participant] = relationship()

    def label(self) -> str:
        return f"Обращение #{self.id}"


# --- служебные таблицы ------------------------------------------------------------


class SavedView(Base):
    """Представление списка в админке: фильтры, сортировка, колонки."""

    __tablename__ = "saved_views"
    id: Mapped[int] = mapped_column(primary_key=True)
    resource: Mapped[str] = mapped_column(String(64), index=True)
    name: Mapped[str] = mapped_column(String(200))
    params: Mapped[dict] = mapped_column(JSON, default=dict)
    is_system: Mapped[bool] = mapped_column(Boolean, default=False)
    order: Mapped[int] = mapped_column(Integer, default=0)
    created_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("admins.id", ondelete="SET NULL"))

    def label(self) -> str:
        return self.name


class DialogState(Base):
    """Шаг диалога с пользователем мессенджера. До согласия карточки участника нет:
    черновик регистрации лежит в data."""

    __tablename__ = "dialog_states"
    __table_args__ = (UniqueConstraint("channel", "user_id", name="uq_dialog"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    channel: Mapped[str] = mapped_column(String(16))
    user_id: Mapped[str] = mapped_column(String(64))
    chat_id: Mapped[str] = mapped_column(String(64), default="")
    state: Mapped[str] = mapped_column(String(32), default="new")
    data: Mapped[dict] = mapped_column(JSON, default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class InboundEvent(Base):
    """Отсечка повторов входящих событий мессенджеров (update_id, callback_id)."""

    __tablename__ = "inbound_events"
    __table_args__ = (UniqueConstraint("channel", "key", name="uq_inbound"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    channel: Mapped[str] = mapped_column(String(16))
    key: Mapped[str] = mapped_column(String(128))
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class AuditLog(Base):
    """Кто и когда изменил данные: администратор, участник или система."""

    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    actor: Mapped[str] = mapped_column(String(100))  # admin:<id> / participant:<id> / system
    action: Mapped[str] = mapped_column(String(64))
    object_type: Mapped[str] = mapped_column(String(64))
    object_id: Mapped[Optional[int]] = mapped_column(Integer)
    details: Mapped[dict] = mapped_column(JSON, default=dict)
