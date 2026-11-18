"""Зарегистрированный участник: меню, мероприятия, опросы, интересы, «Мои данные»."""
from __future__ import annotations

import html

from sqlalchemy import select

from .. import logs
from ..messengers.base import MENU_ABOUT, MENU_EVENTS, MENU_ME, MENU_ORG, Button, OutMessage
from ..models import (
    CHANNELS,
    PARTICIPATION_ACTIVE,
    UPCOMING_STATUSES,
    Appeal,
    Broadcast,
    Delivery,
    Event,
    EventType,
    Interest,
    Level,
    Participation,
    Questionnaire,
    Subscription,
    SurveyRefusal,
    SurveyRun,
)
from ..services import events as esvc
from ..services import forms as fsvc
from ..services import participants as psvc
from ..texts import read_doc, render
from ..util import audit, extract_phone, fmt_date, fmt_datetime_local, fmt_time, now_utc, split_name, today_local
from . import form
from .core import Ctx
from .render import TEXT_LIMITS, announcement, btn_about, btn_events, btn_me, btn_org, rows, trunc

S_EDIT_NAME = "me_edit_name"
S_EDIT_PHONE = "me_edit_phone"
S_ORG = "org_message"
S_INTERESTS = "interests"

MENU_TEXT = {MENU_EVENTS: "menu:events", MENU_ABOUT: "menu:about", MENU_ME: "me:data", MENU_ORG: "me:org"}
COMMANDS = {"/menu": "menu", "/events": "menu:events", "/about": "menu:about", "/me": "me:data"}


def handle(ctx: Ctx) -> None:
    inc = ctx.inc
    if not ctx.participant.consent:
        ask_consent_again(ctx)
        return
    if inc.kind == "start":
        on_start(ctx)
    elif inc.kind == "callback":
        on_callback(ctx, inc.callback_data)
    elif inc.kind == "text":
        on_text(ctx, inc.text.strip())
    else:
        show_menu(ctx)


def ask_consent_again(ctx: Ctx) -> None:
    """Согласие отозвано: без нового согласия бот не обрабатывает данные участника."""
    from ..models import Consent

    code = ctx.inc.callback_data if ctx.inc.kind == "callback" else ""
    doc = psvc.current_consent_document(ctx.session)
    if code == "reg:consent_yes" and doc is not None:
        ctx.notice()
        ctx.session.add(Consent(participant_id=ctx.participant.id, document_id=doc.id, status="given",
                                channel=ctx.channel, given_at=now_utc()))
        ctx.participant.consent = True
        audit(ctx.session, f"participant:{ctx.participant.id}", "consent_given", ctx.participant)
        show_menu(ctx)
        return
    if code == "reg:consent_no":
        ctx.notice()
        ctx.send(OutMessage(render("10"), key="10-consent-declined"))
        return
    if doc is None:
        raise RuntimeError("нет действующей редакции документа о согласии")
    ctx.send(OutMessage(render("09", {"consent_link": doc.url}),
                        buttons=[[Button("Согласен", "reg:consent_yes"), Button("Не согласен", "reg:consent_no")]],
                        key="09-consent"))


def show_menu(ctx: Ctx) -> None:
    ctx.send(OutMessage(render("12"), menu=True, key="12-menu"))


def on_start(ctx: Ctx) -> None:
    run = fsvc.latest_started_run(ctx.session, ctx.participant.id)
    payload = ctx.inc.start_payload
    if payload.startswith("ev"):
        # диплинк на мероприятие: ?start=ev<id>
        try:
            join(ctx, int(payload[2:]))
            return
        except ValueError:
            pass
    if run is not None:
        ctx.send(OutMessage("Продолжим с того места, где остановились.", key="form-resume"))
        form.resume(ctx, run)
        return
    ctx.reset_state("idle")
    show_menu(ctx)


def on_text(ctx: Ctx, text: str) -> None:
    code = MENU_TEXT.get(text) or COMMANDS.get(text.split(" ")[0].lower())
    if code:
        if ctx.dialog.state != "idle":
            ctx.reset_state("idle")
        if code == "menu":
            show_menu(ctx)
        else:
            on_callback(ctx, code)
        return
    state = ctx.dialog.state
    if state == form.S_FORM:
        run = ctx.session.get(SurveyRun, ctx.data.get("run_id")) if ctx.data.get("run_id") else None
        if run is not None and run.status == "started" and form.handle_text(ctx, run):
            return
        ctx.reset_state("idle")
    if state == S_EDIT_NAME:
        first, last = split_name(text)
        if not first or not last:
            ctx.send(OutMessage(render("66"), buttons=[[Button("Отмена", "me:data")]], key="66-edit-name"))
            return
        ctx.participant.first_name, ctx.participant.last_name = first, last
        audit(ctx.session, f"participant:{ctx.participant.id}", "name_changed", ctx.participant)
        ctx.reset_state("idle")
        ctx.send(OutMessage(render("66", {"name": ctx.participant.full_name}, variant=1), key="66-edit-name"))
        my_data(ctx, edit=False)
        return
    if state == S_EDIT_PHONE:
        phone = extract_phone(text)
        if not phone:
            ctx.send(OutMessage(render("67") + "\n\nНапример: +7 900 123-45-67",
                                buttons=[[Button("Отмена", "me:data")]], key="67-edit-phone"))
            return
        ctx.participant.contact_phone = phone
        audit(ctx.session, f"participant:{ctx.participant.id}", "contact_phone_changed", ctx.participant)
        ctx.reset_state("idle")
        ctx.send(OutMessage(render("67", {"phone": phone}, variant=1), key="67-edit-phone"))
        my_data(ctx, edit=False)
        return
    if state == S_ORG:
        if len(text) > ctx.adapter.text_limit:
            ctx.send(OutMessage(render("91", {"limit": ctx.adapter.text_limit}), key="91-text-too-long"))
            return
        appeal = Appeal(participant_id=ctx.participant.id, text=text, channel=ctx.channel, created_at=now_utc())
        ctx.session.add(appeal)
        ctx.session.flush()
        logs.event("appeal.created", appeal_id=appeal.id, participant_id=ctx.participant.id, channel=ctx.channel)
        ctx.reset_state("idle")
        ctx.send(OutMessage(render("68"), buttons=[[btn_me()]], key="68-appeal-sent"))
        return
    show_menu(ctx)


def on_callback(ctx: Ctx, code: str) -> None:
    prefix = code.split(":", 1)[0]
    if prefix in ("q", "m", "g", "s", "x"):
        form.handle_callback(ctx, code)
        return
    if prefix == "reg":
        ctx.notice("Вы уже зарегистрированы")
        return
    if ctx.dialog.state in (S_EDIT_NAME, S_EDIT_PHONE, S_ORG) and code != "me:org":
        ctx.reset_state("idle")
    parts = code.split(":")
    handlers = {
        "menu:events": events_menu,
        "menu:about": about,
        "menu:me": lambda c: my_data(c),
        "menu:org": contact_organizer,
        "me:org": contact_organizer,
        "me:data": lambda c: my_data(c),
        "me:edit_name": edit_name,
        "me:edit_phone": edit_phone,
        "me:subs": subscriptions,
        "me:interests": my_interests,
        "me:events": my_events,
        "me:surveys": my_surveys,
        "ev:all": lambda c: list_events(c, None),
        "int:start": interests_start,
        "int:save": interests_save,
        "sv:decline_all": survey_decline_all,
    }
    if code in handlers:
        handlers[code](ctx)
        return
    arg = parts[2] if len(parts) > 2 else ""
    try:
        if code.startswith("ev:type:"):
            list_events(ctx, arg)
        elif code.startswith("ev:join:"):
            join(ctx, int(arg))
        elif code.startswith("ev:cancel_ask:"):
            cancel_ask(ctx, int(arg))
        elif code.startswith("ev:cancel:"):
            cancel_own(ctx, int(arg))
        elif code.startswith("ev:unsub:"):
            unsubscribe(ctx, int(arg))
        elif code.startswith("sub:toggle:"):
            toggle_subscription(ctx, int(arg))
        elif code.startswith("int:level:"):
            interests_toggle(ctx, level_id=int(arg))
        elif code.startswith("int:activity:"):
            interests_toggle(ctx, activity_id=int(arg))
        elif code.startswith("sv:take:"):
            survey_take(ctx, int(arg))
        elif code.startswith("sv:decline:"):
            survey_decline(ctx, int(arg))
        else:
            ctx.notice("Кнопка устарела")
    except ValueError:
        ctx.notice("Кнопка устарела")


# --- сценарий №1: мероприятия ---------------------------------------------------------------------


def events_menu(ctx: Ctx) -> None:
    types = list(ctx.session.scalars(select(EventType).where(EventType.is_active.is_(True))
                                     .order_by(EventType.order, EventType.id)))
    buttons = [Button(trunc(t.name), f"ev:type:{t.identifier}") for t in types] + [Button("Все", "ev:all")]
    ctx.send(OutMessage(render("20"), buttons=rows(buttons, 2), key="20-events-type"))


def list_events(ctx: Ctx, type_identifier: str | None) -> None:
    ctx.notice()
    events = esvc.upcoming(ctx.session, type_identifier)
    if not events:
        if type_identifier:
            t = ctx.session.scalar(select(EventType).where(EventType.identifier == type_identifier))
            text = render("21", {"type_name": t.name if t else type_identifier})
        else:
            text = render("21", variant=1)
        ctx.send(OutMessage(text, buttons=[[btn_events(), btn_about()]], key="21-events-none"))
        return
    for event in events:
        msgs = announcement(event, ctx.channel, with_unsubscribe=False)
        for msg in msgs:
            result = ctx.send(msg)
            if result.telegram_file_id:
                event.poster_telegram_file_id = result.telegram_file_id
            if result.max_token:
                event.poster_max_token = result.max_token


def join(ctx: Ctx, event_id: int) -> None:
    event = ctx.session.get(Event, event_id)
    if event is None:
        ctx.notice("Мероприятие не найдено")
        return
    ctx.notice()
    if not esvc.is_open(event):
        ctx.send(OutMessage(render("32", {"event_title": event.title}), buttons=[[btn_events()]],
                            key="32-event-closed"))
        return
    part = esvc.participation(ctx.session, ctx.participant.id, event.id)
    if part is not None and part.status in PARTICIPATION_ACTIVE:
        # 0 — запись уже подтверждена, 1 — заявка ждёт подтверждения
        variant = 1 if part.status == "pending" else 0
        ctx.send(OutMessage(render("25", {"event_title": event.title}, variant=variant),
                            buttons=[[btn_org()], [btn_me()]], key="25-event-already"))
        return
    if part is not None and part.status == "declined":
        ctx.send(OutMessage(render("25", {"event_title": event.title}, variant=2),
                            buttons=[[btn_org()], [btn_me()]], key="25-event-declined"))
        return
    if esvc.is_full(ctx.session, event):
        ctx.send(OutMessage(render("31", {"event_title": event.title}), buttons=[[btn_org()], [btn_events()]],
                            key="31-event-full"))
        return
    questionnaire, questions = fsvc.registration_questions(ctx.session, ctx.participant, event)
    if questionnaire is not None:
        run = fsvc.started_run(ctx.session, ctx.participant.id, questionnaire.id, event.id)
        if run is not None:
            form.resume(ctx, run)
            return
    if not questions:
        form.complete_registration(ctx, event)
        return
    form.start_run(ctx, questionnaire, questions, event)


def unsubscribe(ctx: Ctx, type_id: int) -> None:
    t = ctx.session.get(EventType, type_id)
    if t is None:
        ctx.notice("Направление не найдено")
        return
    psvc.set_subscription(ctx.session, ctx.participant, t.id, False)
    audit(ctx.session, f"participant:{ctx.participant.id}", "unsubscribed", t)
    ctx.notice()
    ctx.send(OutMessage(render("26", {"type_name": t.name}), buttons=[[btn_me()]], key="26-unsubscribed"))


# --- сценарий №2: опросы ------------------------------------------------------------------------------


def survey_take(ctx: Ctx, questionnaire_id: int) -> None:
    q = ctx.session.get(Questionnaire, questionnaire_id)
    if q is None or not q.is_active or q.purpose != "survey":
        ctx.notice("Опрос закрыт")
        return
    ctx.notice()
    run = fsvc.started_run(ctx.session, ctx.participant.id, q.id, None)
    if run is not None:
        form.resume(ctx, run)
        return
    questions = fsvc.survey_questions(ctx.session, ctx.participant, q)
    if not questions:
        ctx.session.add(SurveyRun(participant_id=ctx.participant.id, questionnaire_id=q.id, status="completed",
                                  channel=ctx.channel, question_ids=[], completed_at=now_utc()))
        ctx.send(OutMessage(render("43", {"survey_title": q.title}), buttons=[[btn_me()]], key="43-survey-completed"))
        return
    form.start_run(ctx, q, questions, None)


def survey_decline(ctx: Ctx, questionnaire_id: int) -> None:
    q = ctx.session.get(Questionnaire, questionnaire_id)
    if q is None:
        ctx.notice("Опрос не найден")
        return
    ctx.notice()
    for run in ctx.session.scalars(select(SurveyRun).where(
            SurveyRun.participant_id == ctx.participant.id, SurveyRun.questionnaire_id == q.id,
            SurveyRun.status == "started")):
        run.status = "declined"
    exists = ctx.session.scalar(select(SurveyRefusal.id).where(
        SurveyRefusal.participant_id == ctx.participant.id, SurveyRefusal.questionnaire_id == q.id))
    if not exists:
        ctx.session.add(SurveyRefusal(participant_id=ctx.participant.id, questionnaire_id=q.id))
    if ctx.dialog.state == form.S_FORM:
        ctx.reset_state("idle")
    audit(ctx.session, f"participant:{ctx.participant.id}", "survey_declined", q)
    ctx.send(OutMessage(render("41"), key="41-survey-declined"))


def survey_decline_all(ctx: Ctx) -> None:
    ctx.notice()
    if not ctx.participant.surveys_declined_all:
        ctx.participant.surveys_declined_all = True
        ctx.session.add(SurveyRefusal(participant_id=ctx.participant.id, questionnaire_id=None))
        audit(ctx.session, f"participant:{ctx.participant.id}", "surveys_declined_all", ctx.participant)
    ctx.send(OutMessage(render("42"), buttons=[[btn_me()]], key="42-surveys-all-declined"))


# --- сценарий №3: о сообществе и интересы ------------------------------------------------------------


def _levels(ctx: Ctx) -> list[Level]:
    return list(ctx.session.scalars(select(Level).order_by(Level.order, Level.id)))


def about(ctx: Ctx) -> None:
    ctx.notice()
    description = html.escape(read_doc("description.md"), quote=False)
    blocks = []
    for i, level in enumerate(_levels(ctx), 1):
        lines = [f"{i}. {html.escape(level.name, quote=False)}"]
        lines += [f"— {html.escape(a.name, quote=False)}" for a in level.activity_types]
        blocks.append("\n".join(lines))
    buttons = [[Button("Выбрать интересы", "int:start")], [btn_events()]]
    text = render("50", {"description": description, "levels": "\n\n".join(blocks)}, raw={"description", "levels"})
    if len(text) <= TEXT_LIMITS[ctx.channel]:
        ctx.send(OutMessage(text, buttons=buttons, key="50-about"))
        return
    # не помещается — уровни отдельными сообщениями
    ctx.send(OutMessage(description, key="50-about"))
    for i, block in enumerate(blocks):
        ctx.send(OutMessage(block, buttons=buttons if i == len(blocks) - 1 else [], key="50-about"))


def _numbering(levels: list[Level]) -> dict[tuple[int, int], str]:
    result = {}
    for li, level in enumerate(levels, 1):
        for ai, act in enumerate(level.activity_types, 1):
            result[(level.id, act.id)] = f"{li}.{ai}"
    return result


def _interests_message(ctx: Ctx, selected: set[tuple[int, int]]) -> OutMessage:
    levels = _levels(ctx)
    nums = _numbering(levels)
    blocks = []
    buttons: list[list[Button]] = []
    for li, level in enumerate(levels, 1):
        lines = [f"{li}. {html.escape(level.name, quote=False)}"]
        row = [Button(f"Уровень {li}", f"int:level:{level.id}")]
        for act in level.activity_types:
            key = (level.id, act.id)
            mark = "✓ " if key in selected else ""
            lines.append(f"{mark}{nums[key]} {html.escape(act.name, quote=False)}")
            row.append(Button(f"{mark}{nums[key]}", f"int:activity:{act.id}:{level.id}"))
        blocks.append("\n".join(lines))
        buttons += rows(row, 3)
    buttons.append([Button("Сохранить", "int:save")])
    return OutMessage(render("52", {"levels": "\n\n".join(blocks)}, raw={"levels"}), buttons=buttons,
                      key="52-interests-choose")


def _current_interests(ctx: Ctx) -> set[tuple[int, int]]:
    return {(i.level_id, i.activity_type_id) for i in ctx.session.scalars(
        select(Interest).where(Interest.participant_id == ctx.participant.id))}


def interests_start(ctx: Ctx) -> None:
    ctx.notice()
    selected = _current_interests(ctx)
    ctx.set_state(S_INTERESTS, selected=[list(x) for x in sorted(selected)])
    ctx.send(_interests_message(ctx, selected))


def interests_toggle(ctx: Ctx, level_id: int | None = None, activity_id: int | None = None) -> None:
    ctx.notice()
    if ctx.dialog.state != S_INTERESTS:
        selected = _current_interests(ctx)
    else:
        selected = {tuple(x) for x in ctx.data.get("selected", [])}
    if level_id is not None:
        level = ctx.session.get(Level, level_id)
        if level is None:
            return
        keys = {(level.id, a.id) for a in level.activity_types}
        selected = selected - keys if keys <= selected else selected | keys
    elif activity_id is not None:
        parts = ctx.inc.callback_data.split(":")
        lvl = int(parts[3]) if len(parts) > 3 else None
        if lvl is None:
            for level in _levels(ctx):
                if any(a.id == activity_id for a in level.activity_types):
                    lvl = level.id
                    break
        if lvl is None:
            return
        key = (lvl, activity_id)
        selected = selected - {key} if key in selected else selected | {key}
    ctx.set_state(S_INTERESTS, selected=[list(x) for x in sorted(selected)])
    msg = _interests_message(ctx, selected)
    if not (ctx.inc.message_id and ctx.edit(ctx.inc.message_id, msg)):
        ctx.send(msg)


def interests_save(ctx: Ctx) -> None:
    ctx.notice()
    if ctx.dialog.state != S_INTERESTS:
        selected = _current_interests(ctx)
    else:
        selected = {tuple(x) for x in ctx.data.get("selected", [])}
    current = {(i.level_id, i.activity_type_id): i for i in ctx.session.scalars(
        select(Interest).where(Interest.participant_id == ctx.participant.id))}
    before_levels = {lv for lv, _ in current}
    for key, interest in current.items():
        if key not in selected:
            ctx.session.delete(interest)
    for level_id, activity_id in selected:
        if (level_id, activity_id) not in current:
            ctx.session.add(Interest(participant_id=ctx.participant.id, level_id=level_id,
                                     activity_type_id=activity_id, changed_at=now_utc()))
    # выбор уровня подписывает на все типы мероприятий уровня
    new_levels = {lv for lv, _ in selected} - before_levels
    for level_id in new_levels:
        level = ctx.session.get(Level, level_id)
        psvc.subscribe_types(ctx.session, ctx.participant, [t for t in level.event_types if t.is_active])
    ctx.session.flush()
    ctx.reset_state("idle")
    audit(ctx.session, f"participant:{ctx.participant.id}", "interests_saved", ctx.participant,
          count=len(selected))
    if ctx.inc.message_id:
        ctx.edit(ctx.inc.message_id, OutMessage(_interests_message(ctx, selected).text, key="52-interests-choose"))
    if not selected:
        ctx.send(OutMessage("Выбор пуст — подписки не изменились.", buttons=[[Button("Выбрать интересы", "int:start")]],
                            key="51-interests-saved"))
        return
    ctx.send(OutMessage(render("51", {"levels_and_activities": _interests_text(ctx, selected, inline=True)}),
                        buttons=[[btn_me(), btn_events()]], key="51-interests-saved"))


def _interests_text(ctx: Ctx, selected: set[tuple[int, int]], inline: bool = False) -> str:
    parts = []
    for level in _levels(ctx):
        acts = [a.name for a in level.activity_types if (level.id, a.id) in selected]
        if acts:
            parts.append(f"{level.name} ({'; '.join(acts)})" if inline else
                         f"{level.name}:\n" + "\n".join(f"— {a}" for a in acts))
    return ", ".join(parts) if inline else "\n\n".join(parts)


# --- сценарий №4: мои данные ------------------------------------------------------------------------------


def my_data(ctx: Ctx, edit: bool = True) -> None:
    ctx.notice()
    p = ctx.participant
    text = render("60", {
        "name": p.full_name,
        "messenger_phone": p.messenger_phone,
        "contact_phone": p.contact_phone,
        "messengers": ", ".join(CHANNELS[m] for m in p.messengers),
        "registered_at": fmt_datetime_local(p.registered_at),
        "consent_status": "да" if p.consent else "нет",
    })
    buttons = [
        [Button("Изменить имя", "me:edit_name"), Button("Изменить телефон", "me:edit_phone")],
        [Button("Подписки", "me:subs"), Button("Интересы", "me:interests")],
        [Button("Мои записи", "me:events"), Button("Опросы", "me:surveys")],
        [btn_org()],
    ]
    msg = OutMessage(text, buttons=buttons, key="60-my-data")
    if edit:
        ctx.show(msg)
    else:
        ctx.send(msg)


def edit_name(ctx: Ctx) -> None:
    ctx.notice()
    ctx.set_state(S_EDIT_NAME)
    ctx.send(OutMessage(render("66"), buttons=[[Button("Отмена", "me:data")]], key="66-edit-name"))


def edit_phone(ctx: Ctx) -> None:
    ctx.notice()
    ctx.set_state(S_EDIT_PHONE)
    ctx.send(OutMessage(render("67"), buttons=[[Button("Отмена", "me:data")]], key="67-edit-phone"))


def contact_organizer(ctx: Ctx) -> None:
    ctx.notice()
    ctx.set_state(S_ORG)
    ctx.send(OutMessage(render("65"), key="65-contact-organizer"))


def _subscriptions_message(ctx: Ctx) -> OutMessage:
    types = list(ctx.session.scalars(select(EventType).where(EventType.is_active.is_(True))
                                     .order_by(EventType.order, EventType.id)))
    subs = {s.event_type_id: s.subscribed for s in ctx.session.scalars(
        select(Subscription).where(Subscription.participant_id == ctx.participant.id))}
    lines = [f"{t.name} — {'вкл' if subs.get(t.id) else 'выкл'}" for t in types]
    buttons = [[Button(trunc(f"{t.name}: {'вкл' if subs.get(t.id) else 'выкл'}"), f"sub:toggle:{t.id}")]
               for t in types]
    buttons.append([Button("Назад", "me:data")])
    return OutMessage(render("61", {"subscriptions_list": "\n".join(lines)}), buttons=buttons,
                      key="61-my-subscriptions")


def subscriptions(ctx: Ctx) -> None:
    ctx.notice()
    ctx.show(_subscriptions_message(ctx))


def toggle_subscription(ctx: Ctx, type_id: int) -> None:
    t = ctx.session.get(EventType, type_id)
    if t is None:
        ctx.notice("Направление не найдено")
        return
    sub = ctx.session.scalar(select(Subscription).where(
        Subscription.participant_id == ctx.participant.id, Subscription.event_type_id == t.id))
    new_value = not (sub.subscribed if sub else False)
    psvc.set_subscription(ctx.session, ctx.participant, t.id, new_value)
    audit(ctx.session, f"participant:{ctx.participant.id}", "subscription_toggled", t, subscribed=new_value)
    ctx.notice(f"{t.name}: {'вкл' if new_value else 'выкл'}")
    ctx.show(_subscriptions_message(ctx))


def my_interests(ctx: Ctx) -> None:
    ctx.notice()
    selected = _current_interests(ctx)
    text = _interests_text(ctx, selected) if selected else "Пока не выбраны."
    ctx.show(OutMessage(render("62", {"interests_list": text}),
                        buttons=[[Button("Изменить интересы", "int:start")], [Button("Назад", "me:data")]],
                        key="62-my-interests"))


def my_events(ctx: Ctx) -> None:
    ctx.notice()
    parts = list(ctx.session.execute(
        select(Participation, Event)
        .join(Event, Event.id == Participation.event_id)
        .where(Participation.participant_id == ctx.participant.id, Event.date >= today_local())
        .order_by(Event.date, Event.time)
    ).all())
    if not parts:
        text = render("64", {"events_list": "Вы пока никуда не записаны. Посмотреть предстоящие мероприятия можно кнопкой ниже."})
        ctx.show(OutMessage(text, buttons=[[btn_events(), Button("Назад", "me:data")]], key="64-my-events"))
        return
    lines = []
    buttons: list[list[Button]] = []
    cancellable = sum(1 for part, event in parts
                      if part.status in PARTICIPATION_ACTIVE and event.status in UPCOMING_STATUSES)
    numbered = len(parts) > 1
    for i, (part, event) in enumerate(parts, 1):
        when = ", ".join(x for x in (fmt_date(event.date), fmt_time(event.time), event.address) if x)
        line = f"{i}. {event.title} — {when}" if numbered else f"{event.title} — {when}"
        if part.status == "pending":
            line += " (ждёт подтверждения)"
        elif part.status == "cancelled":
            line += " (отменил запись)"
        elif part.status == "declined":
            line += " (заявка отклонена)"
        if event.status == "cancelled":
            line += " (мероприятие отменено)"
        lines.append(line)
        if part.status in PARTICIPATION_ACTIVE and event.status in UPCOMING_STATUSES:
            label = "Отменить запись" if cancellable == 1 else f"Отменить запись {i}"
            buttons.append([Button(label, f"ev:cancel_ask:{part.id}")])
    buttons.append([btn_events(), Button("Назад", "me:data")])
    ctx.show(OutMessage(render("64", {"events_list": "\n".join(lines)}), buttons=buttons, key="64-my-events"))


def _own_participation(ctx: Ctx, participation_id: int) -> Participation | None:
    part = ctx.session.get(Participation, participation_id)
    if part is None or part.participant_id != ctx.participant.id:
        ctx.notice("Запись не найдена")
        return None
    if part.status not in PARTICIPATION_ACTIVE:
        ctx.notice("Запись уже отменена")
        my_events(ctx)
        return None
    return part


def cancel_ask(ctx: Ctx, participation_id: int) -> None:
    """Отмена записи из «Мои записи»: сначала спрашиваем подтверждение."""
    part = _own_participation(ctx, participation_id)
    if part is None:
        return
    ctx.notice()
    ctx.send(OutMessage(render("69", {"event_title": part.event.title}),
                        buttons=[[Button("Да, отменить", f"ev:cancel:{part.id}")],
                                 [Button("Оставить запись", "me:events")]],
                        key="69-event-cancel"))


def cancel_own(ctx: Ctx, participation_id: int) -> None:
    part = _own_participation(ctx, participation_id)
    if part is None:
        return
    ctx.notice()
    title = part.event.title
    esvc.cancel_by_participant(ctx.session, part, f"participant:{ctx.participant.id}")
    ctx.send(OutMessage(render("69", {"event_title": title}, variant=1),
                        buttons=[[btn_events()], [btn_me()]], key="69-event-cancelled"))


def pending_surveys(ctx: Ctx) -> list[tuple[Questionnaire, SurveyRun | None]]:
    """Непройденные опросы: начатые прохождения и разосланные опросы без прохождения."""
    p = ctx.participant
    result: list[tuple[Questionnaire, SurveyRun | None]] = []
    seen: set[int] = set()
    for run in ctx.session.scalars(select(SurveyRun).where(
            SurveyRun.participant_id == p.id, SurveyRun.status == "started", SurveyRun.event_id.is_(None))
            .order_by(SurveyRun.started_at)):
        if run.questionnaire_id not in seen:
            seen.add(run.questionnaire_id)
            result.append((run.questionnaire, run))
    if p.surveys_declined_all:
        return result
    refused = set(ctx.session.scalars(select(SurveyRefusal.questionnaire_id).where(
        SurveyRefusal.participant_id == p.id)))
    finished = set(ctx.session.scalars(select(SurveyRun.questionnaire_id).where(
        SurveyRun.participant_id == p.id, SurveyRun.status.in_(("completed", "declined")),
        SurveyRun.event_id.is_(None))))
    sent = ctx.session.scalars(
        select(Questionnaire).join(Broadcast, Broadcast.questionnaire_id == Questionnaire.id)
        .join(Delivery, Delivery.broadcast_id == Broadcast.id)
        .where(Delivery.participant_id == p.id, Delivery.status == "sent", Broadcast.kind == "survey",
               Questionnaire.is_active.is_(True))
        .distinct()
    )
    for q in sent:
        if q.id in seen or q.id in refused or q.id in finished:
            continue
        seen.add(q.id)
        result.append((q, None))
    return result


def my_surveys(ctx: Ctx) -> None:
    ctx.notice()
    items = pending_surveys(ctx)
    if not items:
        ctx.show(OutMessage(render("63", {"surveys_list": "Непройденных опросов нет."}),
                            buttons=[[Button("Назад", "me:data")]], key="63-my-surveys"))
        return
    lines = []
    buttons = []
    for q, run in items:
        if run is not None:
            n, m = run.position()
            lines.append(f"{q.title} — начат, вопрос {n} из {m}")
        else:
            lines.append(f"{q.title} — не начат")
        buttons.append([Button(f"Пройти: {trunc(q.title, 14)}", f"sv:take:{q.id}")])
    buttons.append([Button("Назад", "me:data")])
    ctx.show(OutMessage(render("63", {"surveys_list": "\n".join(lines)}), buttons=buttons, key="63-my-surveys"))
