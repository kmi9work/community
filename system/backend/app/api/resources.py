"""Разделы админки: поля, фильтры, поиск, правила сохранения."""
from __future__ import annotations

from sqlalchemy import func, or_, select

from ..models import (
    ANSWER_TYPE,
    ATTENDANCE,
    BROADCAST_KIND,
    BROADCAST_STATUS,
    CHANNELS,
    CONSENT_STATUS,
    DELIVERY_STATUS,
    EVENT_STATUS,
    PARTICIPANT_STATUS,
    PARTICIPATION_STATUS,
    QUESTIONNAIRE_PURPOSE,
    RECIPIENT_MODE,
    ROLE_KIND,
    RUN_STATUS,
    UPCOMING_STATUSES,
    ActivityType,
    Admin,
    Answer,
    AnswerOption,
    Appeal,
    Broadcast,
    Consent,
    ConsentDocument,
    Delivery,
    Event,
    EventType,
    GameRole,
    Interest,
    Level,
    OrganizerParticipation,
    Participant,
    Participation,
    Question,
    Questionnaire,
    Reminder,
    Subscription,
    SurveyRefusal,
    SurveyRun,
)
from ..auth import hash_password
from ..util import audit, now_utc, today_local
from .crud import F, Filter, Resource, ValidationFailed, register, search_ilike


def actor(admin) -> str:
    return f"admin:{admin.id}" if admin is not None else "system"


def participant_search(pattern: str):
    return or_(Participant.first_name.ilike(pattern), Participant.last_name.ilike(pattern),
               Participant.messenger_phone.ilike(pattern), Participant.contact_phone.ilike(pattern),
               (Participant.first_name + " " + Participant.last_name).ilike(pattern))


def via_participant(model):
    def build(pattern: str):
        return model.participant.has(participant_search(pattern))

    return build


def date_range(name="created_at", label="Период"):
    return Filter(name, label, "date_range")


# --- участники ----------------------------------------------------------------------------


def _levels(session, p: Participant) -> list[str]:
    """Уровни: выбранные в интересах и уровни типов, на которые участник подписан.

    Новый участник подписан на типы уровня new («Игры», «Лекции»), поэтому уровень
    виден сразу — так же, как его считает отбор получателей рассылки «по уровням».
    """
    ids = set(session.scalars(select(Interest.level_id).where(Interest.participant_id == p.id)))
    ids |= set(session.scalars(
        select(EventType.level_id)
        .join(Subscription, Subscription.event_type_id == EventType.id)
        .where(Subscription.participant_id == p.id, Subscription.subscribed.is_(True))
    ))
    if not ids:
        return []
    return list(session.scalars(select(Level.name).where(Level.id.in_(ids)).order_by(Level.order)))


def _subscriptions(session, p: Participant) -> list[str]:
    return list(session.scalars(
        select(EventType.name)
        .join(Subscription, Subscription.event_type_id == EventType.id)
        .where(Subscription.participant_id == p.id, Subscription.subscribed.is_(True))
        .order_by(EventType.order, EventType.id)
    ))


def _activities(session, p: Participant) -> list[str]:
    return list(session.scalars(
        select(ActivityType.name)
        .join(Interest, Interest.activity_type_id == ActivityType.id)
        .where(Interest.participant_id == p.id)
        .order_by(ActivityType.order, ActivityType.id)
    ))


def _participations_count(session, p: Participant) -> int:
    return session.scalar(select(func.count(Participation.id)).where(Participation.participant_id == p.id,
                                                                     Participation.status == "registered")) or 0


def _filter_messenger(q, raw):
    if raw == "telegram":
        return q.where(Participant.telegram_id.is_not(None))
    if raw == "max":
        return q.where(Participant.max_id.is_not(None))
    return q


def _filter_level(q, raw):
    level_id = int(raw)
    by_interest = select(Interest.participant_id).where(Interest.level_id == level_id)
    by_subscription = (
        select(Subscription.participant_id)
        .join(EventType, EventType.id == Subscription.event_type_id)
        .where(EventType.level_id == level_id, Subscription.subscribed.is_(True))
    )
    return q.where(or_(Participant.id.in_(by_interest), Participant.id.in_(by_subscription)))


def _filter_type(q, raw):
    return q.where(Participant.id.in_(select(Subscription.participant_id).where(
        Subscription.event_type_id == int(raw), Subscription.subscribed.is_(True))))


register(Resource(
    name="participants", title="Участники", model=Participant, section="main", can_create=False,
    default_sort="-registered_at",
    fields=[
        F("first_name", "Имя", required=True),
        F("last_name", "Фамилия", required=True),
        F("messenger_phone", "Телефон из мессенджера", editable=False),
        F("contact_phone", "Контактный телефон", required=True),
        F("messengers", "Мессенджеры", "computed",
          getter=lambda s, p: [CHANNELS[m] for m in p.messengers]),
        F("telegram_id", "Идентификатор в Телеграм", editable=False, list=False),
        F("max_id", "Идентификатор в MAX", editable=False, list=False),
        F("status", "Статус", "enum", PARTICIPANT_STATUS, editable=False),
        F("subscriptions", "Подписки", "computed", getter=_subscriptions, list=False),
        F("levels", "Уровни", "computed", getter=_levels),
        F("activities", "Типы деятельности", "computed", getter=_activities, list=False),
        F("participations_count", "Записей", "computed", getter=_participations_count),
        F("consent", "Согласие на обработку ПД", "bool", editable=False, list=False),
        F("surveys_declined_all", "Отказ от всех опросов", "bool", list=False),
        F("registered_at", "Дата регистрации", "datetime", editable=False),
        F("deleted_at", "Дата удаления", "datetime", editable=False, list=False),
    ],
    search=participant_search,
    filters=[
        Filter("status", "Статус", "enum", PARTICIPANT_STATUS),
        Filter("messenger", "Мессенджер", "enum", CHANNELS, apply=_filter_messenger),
        Filter("level", "Уровень", "fk", ref="levels", apply=_filter_level),
        Filter("event_type", "Тип мероприятия", "fk", ref="event_types", apply=_filter_type),
        Filter("surveys_declined_all", "Отказ от опросов", "bool"),
    ],
    can_edit=lambda p: p.status == "active",
    after_save=lambda s, o, d, a, c: audit(s, actor(a), "participant_edited", o, fields=sorted(d)),
))


# --- мероприятия ----------------------------------------------------------------------------


def _registered(session, e: Event) -> str:
    n = session.scalar(select(func.count(Participation.id)).where(Participation.event_id == e.id,
                                                                  Participation.status == "registered")) or 0
    return f"{n} / {e.seats_limit}" if e.seats_limit else str(n)


def _announced(session, e: Event) -> bool:
    return bool(session.scalar(select(Broadcast.id).where(
        Broadcast.event_id == e.id, Broadcast.kind == "announcement", Broadcast.is_test.is_(False),
        Broadcast.status.in_(("queued", "sending", "finished"))).limit(1)))


def _editor(session, e: Event) -> str:
    admin_id = e.updated_by_id or e.created_by_id
    admin = session.get(Admin, admin_id) if admin_id else None
    return admin.label() if admin else ""


def _filter_when(q, raw):
    if raw == "upcoming":
        return q.where(Event.date >= today_local(), Event.status.in_(UPCOMING_STATUSES))
    if raw == "past":
        return q.where(or_(Event.date < today_local(), Event.status.in_(("completed", "cancelled"))))
    return q


def _filter_type_identifier(q, raw):
    return q.where(Event.event_type.has(EventType.identifier == raw))


def _event_before_save(session, event: Event, data: dict, admin, creating: bool):
    if creating:
        event.status = "planned"
        event.created_by_id = admin.id if admin else None
        return
    changing = {k for k in ("date", "time", "address") if k in data and data[k] != getattr(event, k)}
    if changing:
        n = session.scalar(select(func.count(Participation.id)).where(Participation.event_id == event.id,
                                                                      Participation.status == "registered"))
        if n:
            raise ValidationFailed("На мероприятие уже записались — дату, время и адрес меняет кнопка «Перенести»",
                                   sorted(changing)[0])
    event.updated_by_id = admin.id if admin else None
    event.updated_at = now_utc()


def _event_after_save(session, event, data, admin, creating):
    audit(session, actor(admin), "event_created" if creating else "event_edited", event, fields=sorted(data))


register(Resource(
    name="events", title="Мероприятия", model=Event, section="main", default_sort="date",
    fields=[
        F("title", "Название", required=True),
        F("event_type", "Тип мероприятия", "fk", ref="event_types", required=True),
        F("date", "Дата", "date", required=True),
        F("time", "Время", "time", create=False),
        F("address", "Место", create=False),
        F("status", "Статус", "enum", EVENT_STATUS, editable=False, create=False),
        F("registered", "Записано", "computed", getter=_registered),
        F("seats_limit", "Лимит мест", "int", create=False, list=False),
        F("poster", "Афиша", "image", editable=False, create=False),
        F("announced", "Анонс разослан", "computed", getter=_announced),
        F("description", "Описание", "text", create=False, list=False),
        F("previous_date", "Прежняя дата", "date", editable=False, create=False, list=False),
        F("previous_time", "Прежнее время", "time", editable=False, create=False, list=False),
        F("change_reason", "Причина переноса или отмены", "text", editable=False, create=False, list=False),
        F("created_at", "Создано", "datetime", editable=False, create=False, list=False),
        F("updated_at", "Изменено", "datetime", editable=False, create=False, list=False),
        F("updated_by", "Кем изменено", "computed", list=False, getter=_editor),
    ],
    search=search_ilike(Event.title, Event.address),
    filters=[
        Filter("when", "Когда", "enum", {"upcoming": "предстоящие", "past": "прошедшие"}, apply=_filter_when),
        Filter("event_type", "Тип", "fk", ref="event_types"),
        Filter("event_type__identifier", "Тип (identifier)", "str", apply=_filter_type_identifier),
        Filter("status", "Статус", "enum", EVENT_STATUS),
        date_range("date", "Дата"),
    ],
    before_save=_event_before_save, after_save=_event_after_save,
))


def _starred_answers(session, part: Participation) -> list[dict]:
    rows = session.execute(
        select(Answer, Question).join(Question, Question.id == Answer.question_id)
        .where(Answer.participant_id == part.participant_id, Answer.event_id == part.event_id,
               Question.ask_every_time.is_(True))
        .order_by(Question.order, Answer.id)
    ).all()
    latest: dict[int, tuple[str, str]] = {}
    for answer, question in rows:
        latest[question.id] = (question.text, answer.display())
    return [{"question": qt, "answer": at} for qt, at in latest.values()]


def _participation_before(session, part: Participation, data, admin, creating):
    role = data.get("role")
    if role is not None and role.kind != "participant":
        raise ValidationFailed("Выберите роль вида «участник»", "role")
    if creating:
        dup = session.scalar(select(Participation.id).where(
            Participation.participant_id == data["participant"].id, Participation.event_id == data["event"].id))
        if dup:
            raise ValidationFailed("Участник уже есть в списке мероприятия", "participant")
        part.registered_at = now_utc()
        part.status = data.get("status") or "registered"


register(Resource(
    name="participations", title="Участие в мероприятии", model=Participation, default_sort="registered_at",
    can_delete=False,
    fields=[
        F("participant", "Участник", "fk", ref="participants", required=True, immutable=True),
        F("event", "Мероприятие", "fk", ref="events", required=True, immutable=True),
        F("contact_phone", "Контактный телефон", "computed", getter=lambda s, p: p.participant.contact_phone),
        F("status", "Статус записи", "enum", PARTICIPATION_STATUS, create=False),
        F("role", "Роль участника", "fk", ref="roles", ref_filter={"kind": "participant", "is_active": "true"},
          create=False),
        F("attendance", "Присутствие", "enum", ATTENDANCE, create=False),
        F("registered_at", "Дата записи", "datetime", editable=False, create=False),
        F("starred", "Ответы на вопросы со звёздочкой", "computed", getter=_starred_answers),
    ],
    search=via_participant(Participation),
    filters=[
        Filter("event", "Мероприятие", "fk", ref="events"),
        Filter("participant", "Участник", "fk", ref="participants"),
        Filter("status", "Статус записи", "enum", PARTICIPATION_STATUS),
        Filter("attendance", "Присутствие", "enum", ATTENDANCE),
    ],
    before_save=_participation_before,
    after_save=lambda s, o, d, a, c: audit(s, actor(a), "participation_saved", o, fields=sorted(d)),
))


def _organizer_before(session, org, data, admin, creating):
    for role in data.get("roles") or []:
        if role.kind != "organizer":
            raise ValidationFailed("Выберите роли вида «организатор»", "roles")
    if creating:
        dup = session.scalar(select(OrganizerParticipation.id).where(
            OrganizerParticipation.participant_id == data["participant"].id,
            OrganizerParticipation.event_id == data["event"].id))
        if dup:
            raise ValidationFailed("Организатор уже добавлен", "participant")


register(Resource(
    name="organizers", title="Организаторы", model=OrganizerParticipation, can_delete=True, default_sort="id",
    fields=[
        F("participant", "Участник", "fk", ref="participants", required=True, immutable=True),
        F("event", "Мероприятие", "fk", ref="events", required=True, immutable=True),
        F("roles", "Роли организатора", "m2m", ref="roles", ref_filter={"kind": "organizer", "is_active": "true"}),
    ],
    search=via_participant(OrganizerParticipation),
    filters=[Filter("event", "Мероприятие", "fk", ref="events"),
             Filter("participant", "Участник", "fk", ref="participants")],
    before_save=_organizer_before,
))

register(Resource(
    name="reminders", title="Напоминания", model=Reminder, can_delete=True, default_sort="-hours_before",
    fields=[
        F("event", "Мероприятие", "fk", ref="events", required=True, immutable=True),
        F("hours_before", "За сколько часов до начала", "float", required=True),
        F("text", "Текст (пусто — текст по умолчанию)", "text"),
        F("is_active", "Активно", "bool"),
        F("sent_at", "Отправлено", "datetime", editable=False, create=False),
    ],
    filters=[Filter("event", "Мероприятие", "fk", ref="events"), Filter("is_active", "Активно", "bool")],
))


# --- опросы ------------------------------------------------------------------------------------


def _questionnaire_before(session, q, data, admin, creating):
    if data.get("purpose") == "registration" or (not creating and q.purpose == "registration" and "purpose" in data):
        other = session.scalar(select(Questionnaire.id).where(Questionnaire.purpose == "registration",
                                                              Questionnaire.id != (q.id or 0)))
        if other and data.get("purpose", q.purpose) == "registration":
            raise ValidationFailed("Анкета записи на мероприятие может быть только одна", "purpose")


register(Resource(
    name="questionnaires", title="Опросники", model=Questionnaire, section="main", default_sort="id",
    fields=[
        F("title", "Название", required=True),
        F("identifier", "identifier", required=True, immutable=True),
        F("purpose", "Назначение", "enum", QUESTIONNAIRE_PURPOSE, required=True),
        F("description", "Описание", "text", list=False),
        F("is_active", "Активен", "bool"),
        F("questions_count", "Вопросов", "computed", getter=lambda s, q: len([x for x in q.questions if x.is_active])),
    ],
    search=search_ilike(Questionnaire.title, Questionnaire.identifier),
    filters=[Filter("purpose", "Назначение", "enum", QUESTIONNAIRE_PURPOSE), Filter("is_active", "Активен", "bool")],
    before_save=_questionnaire_before,
))


def _question_before(session, q, data, admin, creating):
    atype = data.get("answer_type", q.answer_type)
    if data.get("max_choices") is not None and atype != "multi":
        data["max_choices"] = None
    if data.get("max_choices") is not None and data["max_choices"] < 1:
        raise ValidationFailed("Максимум — не меньше 1", "max_choices")


register(Resource(
    name="questions", title="Вопросы", model=Question, default_sort="order",
    fields=[
        F("questionnaire", "Опросник", "fk", ref="questionnaires", required=True, immutable=True),
        F("order", "Порядковый номер", "int"),
        F("text", "Текст вопроса", "text", required=True),
        F("answer_type", "Тип ответа", "enum", ANSWER_TYPE, required=True),
        F("max_choices", "Максимум выбранных вариантов", "int"),
        F("required", "Обязательный", "bool"),
        F("ask_every_time", "Спрашивать при каждой записи", "bool"),
        F("event_types", "Типы мероприятий", "m2m", ref="event_types",
          help="Для анкеты записи: пусто — при записи на мероприятие любого типа"),
        F("is_active", "Активен", "bool"),
        F("options_count", "Вариантов", "computed", getter=lambda s, q: len(q.active_options())),
    ],
    search=search_ilike(Question.text),
    filters=[Filter("questionnaire", "Опросник", "fk", ref="questionnaires"),
             Filter("answer_type", "Тип ответа", "enum", ANSWER_TYPE), Filter("is_active", "Активен", "bool")],
    before_save=_question_before,
))

register(Resource(
    name="options", title="Варианты ответа", model=AnswerOption, default_sort="order",
    fields=[
        F("question", "Вопрос", "fk", ref="questions", required=True, immutable=True),
        F("order", "Порядковый номер", "int"),
        F("text", "Текст варианта", required=True),
        F("is_active", "Активен", "bool"),
    ],
    filters=[Filter("question", "Вопрос", "fk", ref="questions")],
))


def _progress(session, run: SurveyRun) -> str:
    n, m = run.position()
    if run.status == "completed":
        return f"{m} из {m}"
    return f"{n} из {m}"


register(Resource(
    name="runs", title="Прохождения опросов", model=SurveyRun, can_create=False, default_sort="-started_at",
    fields=[
        F("participant", "Участник", "fk", ref="participants", editable=False),
        F("questionnaire", "Опросник", "fk", ref="questionnaires", editable=False),
        F("event", "Мероприятие", "fk", ref="events", editable=False),
        F("status", "Статус", "enum", RUN_STATUS, editable=False),
        F("channel", "Мессенджер", "enum", CHANNELS, editable=False),
        F("started_at", "Начат", "datetime", editable=False),
        F("completed_at", "Завершён", "datetime", editable=False),
        F("current_question", "Текущий вопрос", "fk", ref="questions", editable=False),
        F("progress", "Шаг", "computed", getter=_progress),
    ],
    search=via_participant(SurveyRun),
    filters=[
        Filter("status", "Статус", "enum", RUN_STATUS),
        Filter("event", "Мероприятие", "fk", ref="events"),
        Filter("questionnaire", "Опросник", "fk", ref="questionnaires"),
        Filter("channel", "Мессенджер", "enum", CHANNELS),
        Filter("participant", "Участник", "fk", ref="participants"),
    ],
))

register(Resource(
    name="answers", title="Ответы", model=Answer, can_create=False, default_sort="id",
    fields=[
        F("participant", "Участник", "fk", ref="participants", editable=False),
        F("question", "Вопрос", "fk", ref="questions", editable=False),
        F("value", "Ответ", "computed", getter=lambda s, a: a.display()),
        F("event", "Мероприятие", "fk", ref="events", editable=False),
        F("run", "Прохождение", "fk", ref="runs", editable=False, list=False),
        F("answered_at", "Дата ответа", "datetime", editable=False),
    ],
    filters=[Filter("run", "Прохождение", "fk", ref="runs"), Filter("participant", "Участник", "fk", ref="participants"),
             Filter("event", "Мероприятие", "fk", ref="events"), Filter("question", "Вопрос", "fk", ref="questions")],
))


register(Resource(
    name="refusals", title="Отказы от опросов", model=SurveyRefusal, can_create=False, can_delete=True,
    default_sort="-refused_at",
    fields=[
        F("participant", "Участник", "fk", ref="participants", editable=False),
        F("questionnaire", "Опросник", "computed",
          getter=lambda s, r: {"id": r.questionnaire.id, "label": r.questionnaire.title} if r.questionnaire
          else {"id": None, "label": "все опросы"}),
        F("refused_at", "Дата отказа", "datetime", editable=False),
    ],
    search=via_participant(SurveyRefusal),
    filters=[Filter("participant", "Участник", "fk", ref="participants"),
             Filter("questionnaire", "Опросник", "fk", ref="questionnaires")],
))


# --- карточка участника: согласия, подписки, интересы ---------------------------------------------------

register(Resource(
    name="consents", title="Согласия", model=Consent, can_create=False, default_sort="-given_at",
    fields=[
        F("participant", "Участник", "fk", ref="participants", editable=False),
        F("document", "Редакция документа", "fk", ref="consent_documents", editable=False),
        F("channel", "Канал", "enum", CHANNELS, editable=False),
        F("status", "Статус", "enum", CONSENT_STATUS, editable=False),
        F("given_at", "Дано", "datetime", editable=False),
        F("revoked_at", "Отозвано", "datetime", editable=False),
    ],
    filters=[Filter("participant", "Участник", "fk", ref="participants"), Filter("status", "Статус", "enum", CONSENT_STATUS)],
))


def _subscription_before(session, sub, data, admin, creating):
    if creating:
        dup = session.scalar(select(Subscription.id).where(
            Subscription.participant_id == data["participant"].id, Subscription.event_type_id == data["event_type"].id))
        if dup:
            raise ValidationFailed("Подписка на этот тип уже есть", "event_type")
    sub.changed_at = now_utc()


register(Resource(
    name="subscriptions", title="Подписки на типы мероприятий", model=Subscription, default_sort="-changed_at",
    fields=[
        F("participant", "Участник", "fk", ref="participants", required=True, immutable=True),
        F("event_type", "Тип мероприятия", "fk", ref="event_types", required=True, immutable=True),
        F("subscribed", "Подписан", "bool"),
        F("changed_at", "Дата изменения", "datetime", editable=False, create=False),
    ],
    search=via_participant(Subscription),
    filters=[Filter("participant", "Участник", "fk", ref="participants"),
             Filter("event_type", "Тип мероприятия", "fk", ref="event_types"), Filter("subscribed", "Подписан", "bool")],
    before_save=_subscription_before,
    after_save=lambda s, o, d, a, c: audit(s, actor(a), "subscription_saved", o, subscribed=o.subscribed),
))

register(Resource(
    name="interests", title="Интересы", model=Interest, can_create=False, default_sort="level_id",
    fields=[
        F("participant", "Участник", "fk", ref="participants", editable=False),
        F("level", "Уровень", "fk", ref="levels", editable=False),
        F("activity_type", "Тип деятельности", "fk", ref="activity_types", editable=False),
        F("changed_at", "Дата изменения", "datetime", editable=False),
    ],
    filters=[Filter("participant", "Участник", "fk", ref="participants"), Filter("level", "Уровень", "fk", ref="levels")],
))


# --- рассылки ----------------------------------------------------------------------------------------


def _broadcast_before(session, b: Broadcast, data, admin, creating):
    if creating:
        if data.get("kind") not in ("announcement", "survey", "text"):
            raise ValidationFailed("Админ создаёт рассылки видов «анонс», «опрос» и «текст»", "kind")
        b.author_id = admin.id if admin else None
        b.status = "draft"


register(Resource(
    name="broadcasts", title="Рассылки", model=Broadcast, section="main", default_sort="-created_at",
    fields=[
        F("kind", "Вид", "enum", BROADCAST_KIND, required=True, immutable=True),
        F("subject", "Тема", required=True),
        F("status", "Статус", "enum", BROADCAST_STATUS, editable=False, create=False),
        F("author", "Автор", "computed", getter=lambda s, b: b.author_label),
        F("event", "Мероприятие", "fk", ref="events", list=False),
        F("questionnaire", "Опросник", "fk", ref="questionnaires", ref_filter={"purpose": "survey"}, list=False),
        F("text", "Текст", "text", list=False,
          help="Разметка: <b>, <i>, <u>, <s>, <code>, <a href=\"https://…\">"),
        F("recipient_mode", "Способ выбора получателей", "enum", RECIPIENT_MODE, required=True, list=False),
        F("event_types", "Типы мероприятий", "m2m", ref="event_types", list=False),
        F("levels", "Уровни", "m2m", ref="levels", list=False),
        F("participants", "Участники", "m2m", ref="participants", list=False),
        F("is_test", "Тестовая", "bool", editable=False, create=False, list=False),
        F("recipients_count", "Получателей", "int", editable=False, create=False),
        F("delivered_count", "Доставлено", "int", editable=False, create=False),
        F("errors_count", "Ошибок", "int", editable=False, create=False),
        F("created_at", "Создана", "datetime", editable=False, create=False),
        F("started_at", "Начата", "datetime", editable=False, create=False, list=False),
        F("finished_at", "Завершена", "datetime", editable=False, create=False),
        F("cancel_reason", "Причина отмены", "text", editable=False, create=False, list=False),
    ],
    search=search_ilike(Broadcast.subject),
    filters=[
        Filter("kind", "Вид", "enum", BROADCAST_KIND),
        Filter("status", "Статус", "enum", BROADCAST_STATUS),
        date_range("created_at", "Период"),
        Filter("is_test", "Тестовая", "bool"),
        Filter("event", "Мероприятие", "fk", ref="events"),
        Filter("questionnaire", "Опросник", "fk", ref="questionnaires"),
    ],
    can_edit=lambda b: b.status == "draft",
    before_save=_broadcast_before,
    after_save=lambda s, o, d, a, c: audit(s, actor(a), "broadcast_created" if c else "broadcast_edited", o),
))

register(Resource(
    name="deliveries", title="Доставка рассылки", model=Delivery, can_create=False, default_sort="id",
    fields=[
        F("broadcast", "Рассылка", "fk", ref="broadcasts", editable=False),
        F("participant", "Участник", "fk", ref="participants", editable=False),
        F("channel", "Канал", "enum", CHANNELS, editable=False),
        F("status", "Статус", "enum", DELIVERY_STATUS, editable=False),
        F("attempt", "Попытка", "int", editable=False),
        F("message_id", "Идентификатор сообщения", editable=False, list=False),
        F("sent_at", "Отправлено", "datetime", editable=False),
        F("error", "Ошибка", "text", editable=False),
    ],
    search=via_participant(Delivery),
    filters=[Filter("broadcast", "Рассылка", "fk", ref="broadcasts"), Filter("status", "Статус", "enum", DELIVERY_STATUS),
             Filter("channel", "Канал", "enum", CHANNELS), Filter("participant", "Участник", "fk", ref="participants")],
))


# --- обращения ---------------------------------------------------------------------------------------

register(Resource(
    name="appeals", title="Обращения", model=Appeal, section="main", can_create=False, default_sort="-created_at",
    fields=[
        F("participant", "Участник", "fk", ref="participants", editable=False),
        F("text", "Текст", "text", editable=False),
        F("channel", "Канал", "enum", CHANNELS, editable=False),
        F("created_at", "Дата", "datetime", editable=False),
        F("processed", "Обработано", "bool"),
        F("comment", "Комментарий администратора", "text", list=False),
    ],
    search=lambda pattern: or_(Appeal.text.ilike(pattern), Appeal.participant.has(participant_search(pattern))),
    filters=[Filter("processed", "Обработано", "bool"), Filter("channel", "Канал", "enum", CHANNELS),
             date_range("created_at", "Период"), Filter("participant", "Участник", "fk", ref="participants")],
    after_save=lambda s, o, d, a, c: audit(s, actor(a), "appeal_edited", o, fields=sorted(d)),
))


# --- справочники ---------------------------------------------------------------------------------------

register(Resource(
    name="event_types", title="Типы мероприятий", model=EventType, section="dict", default_sort="order",
    fields=[
        F("name", "Название", required=True),
        F("identifier", "identifier", required=True, immutable=True),
        F("level", "Уровень", "fk", ref="levels", required=True),
        F("order", "Порядковый номер", "int"),
        F("is_active", "Активен", "bool"),
        F("subscribers", "Подписчиков", "computed", getter=lambda s, t: s.scalar(
            select(func.count(Subscription.id)).where(Subscription.event_type_id == t.id,
                                                      Subscription.subscribed.is_(True))) or 0),
    ],
    search=search_ilike(EventType.name, EventType.identifier),
    filters=[Filter("level", "Уровень", "fk", ref="levels"), Filter("is_active", "Активен", "bool")],
))

register(Resource(
    name="levels", title="Уровни участия", model=Level, section="dict", default_sort="order",
    fields=[
        F("name", "Название", required=True),
        F("identifier", "identifier", required=True, immutable=True),
        F("order", "Порядковый номер", "int"),
        F("event_types", "Типы мероприятий", "m2m", ref="event_types"),
        F("activity_types", "Типы деятельности", "m2m", ref="activity_types"),
    ],
    search=search_ilike(Level.name, Level.identifier),
))

register(Resource(
    name="activity_types", title="Типы деятельности", model=ActivityType, section="dict", default_sort="order",
    fields=[
        F("name", "Название", required=True),
        F("identifier", "identifier", required=True, immutable=True),
        F("order", "Порядковый номер", "int"),
        F("levels", "Уровни", "computed", getter=lambda s, a: [lv.name for lv in a.levels]),
    ],
    search=search_ilike(ActivityType.name, ActivityType.identifier),
))

register(Resource(
    name="roles", title="Роли на игре", model=GameRole, section="dict", default_sort="order",
    fields=[
        F("name", "Название", required=True),
        F("identifier", "identifier", required=True, immutable=True),
        F("kind", "Вид роли", "enum", ROLE_KIND, required=True),
        F("order", "Порядковый номер", "int"),
        F("is_active", "Активна", "bool"),
    ],
    search=search_ilike(GameRole.name, GameRole.identifier),
    filters=[Filter("kind", "Вид роли", "enum", ROLE_KIND), Filter("is_active", "Активна", "bool")],
))


def _consent_doc_after(session, doc: ConsentDocument, data, admin, creating):
    if doc.is_current:
        for other in session.scalars(select(ConsentDocument).where(ConsentDocument.id != doc.id,
                                                                   ConsentDocument.is_current.is_(True))):
            other.is_current = False
    audit(session, actor(admin), "consent_document_saved", doc, current=doc.is_current)


register(Resource(
    name="consent_documents", title="Документы о согласии", model=ConsentDocument, section="dict",
    default_sort="-published_at",
    fields=[
        F("title", "Название", required=True),
        F("version", "Версия", required=True),
        F("url", "Ссылка", required=True),
        F("published_at", "Дата публикации", "date", required=True),
        F("is_current", "Действует", "bool"),
    ],
    search=search_ilike(ConsentDocument.title, ConsentDocument.version),
    after_save=_consent_doc_after,
))


def _admin_before(session, adm: Admin, data, admin, creating):
    password = data.pop("password", None)
    if creating and not password:
        raise ValidationFailed("Задайте пароль", "password")
    if password:
        if len(password) < 10:
            raise ValidationFailed("Пароль — не короче 10 символов", "password")
        adm.password_hash = hash_password(password)
    if not creating and admin is not None and adm.id == admin.id and data.get("is_active") is False:
        raise ValidationFailed("Нельзя отключить свою учётную запись", "is_active")


register(Resource(
    name="admins", title="Администраторы", model=Admin, section="settings", default_sort="login",
    fields=[
        F("login", "Логин", required=True, immutable=True),
        F("name", "Имя"),
        F("password", "Пароль", write_only=True, list=False),
        F("participant", "Своя карточка участника (для «Отправить себе»)", "fk", ref="participants", list=False),
        F("is_active", "Активен", "bool"),
        F("last_login_at", "Последний вход", "datetime", editable=False, create=False),
    ],
    search=search_ilike(Admin.login, Admin.name),
    before_save=_admin_before,
))
