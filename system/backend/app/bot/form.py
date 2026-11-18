"""Форма в боте (docs/form/README.md): один вопрос — одно сообщение, ответы — в «Ответ».

Коды кнопок:
  q:<вопрос>:<вариант>        выбор одного варианта
  m:<вопрос>:<вариант>:<бит>  переключатель для выбора нескольких
  g:<вопрос>                  «Готово»
  s:<вопрос>                  «Пропустить»
  x:<прохождение>             «Отказаться»
"""
from __future__ import annotations

import html
from typing import Optional

from sqlalchemy import select

from .. import logs
from ..messengers.base import Button, OutMessage
from ..models import (
    PARTICIPATION_ACTIVE,
    Answer,
    AnswerOption,
    Event,
    Question,
    Questionnaire,
    SurveyRefusal,
    SurveyRun,
)
from ..services import events as esvc
from ..services import forms as fsvc
from ..texts import render
from ..util import audit, now_utc
from .core import Ctx
from .render import BUTTON_LIMIT, btn_events, btn_me, btn_org, multi_line, question_text, trunc

S_FORM = "form"


# --- старт и возобновление ------------------------------------------------------------------


def start_run(ctx: Ctx, questionnaire: Questionnaire, questions: list[Question], event: Optional[Event]) -> SurveyRun:
    run = SurveyRun(
        participant_id=ctx.participant.id,
        questionnaire_id=questionnaire.id,
        event_id=event.id if event else None,
        status="started",
        channel=ctx.channel,
        question_ids=[q.id for q in questions],
        current_question_id=questions[0].id,
        pending=[],
        started_at=now_utc(),
    )
    ctx.session.add(run)
    ctx.session.flush()
    logs.event("form.started", run_id=run.id, questionnaire_id=questionnaire.id,
               event_id=run.event_id, participant_id=ctx.participant.id, questions=len(questions))
    send_question(ctx, run)
    return run


def resume(ctx: Ctx, run: SurveyRun) -> None:
    """Продолжить с «Текущего вопроса» новым сообщением."""
    if run.current_question_id is None:
        finish(ctx, run)
        return
    run.channel = ctx.channel
    send_question(ctx, run)


# --- сообщение вопроса -------------------------------------------------------------------------


def _build(run: SurveyRun, question: Question) -> OutMessage:
    n, m = run.position()
    text = question_text(n, m, question.text)
    buttons: list[list[Button]] = []
    options = question.active_options()
    truncated = any(len(o.text) > BUTTON_LIMIT - 3 for o in options)
    if question.answer_type == "open":
        text += "\n\n" + render("29")
    else:
        if truncated:
            text += "\n\n" + "\n".join(f"{i}. {html.escape(o.text, quote=False)}" for i, o in enumerate(options, 1))
        if question.answer_type == "multi":
            text += "\n\n" + multi_line(question.max_choices, len(run.pending or []))
        for i, o in enumerate(options, 1):
            label = f"{i}. {o.text}" if truncated else o.text
            if question.answer_type == "multi":
                on = o.id in (run.pending or [])
                label = ("✓ " if on else "") + label
                code = f"m:{question.id}:{o.id}:{0 if on else 1}"
            else:
                code = f"q:{question.id}:{o.id}"
            buttons.append([Button(trunc(label), code)])
        if question.answer_type == "multi":
            buttons.append([Button("Готово", f"g:{question.id}")])
    last: list[Button] = []
    if not question.required:
        last.append(Button("Пропустить", f"s:{question.id}"))
    last.append(Button("Отказаться", f"x:{run.id}"))
    buttons.append(last)
    return OutMessage(text, buttons=buttons, key="27-form-question")


def send_question(ctx: Ctx, run: SurveyRun) -> None:
    question = ctx.session.get(Question, run.current_question_id)
    msg = _build(run, question)
    result = ctx.send(msg)
    run.message_id = result.message_id
    if ctx.dialog is not None:
        ctx.set_state(S_FORM, run_id=run.id)


def _close_question(ctx: Ctx, run: SurveyRun, question: Question, answer_text: str) -> None:
    """Снять клавиатуру с отвеченного вопроса, ответ остаётся в тексте сообщения."""
    n, m = run.position()
    text = question_text(n, m, question.text)
    text += "\n\nОтвет: " + html.escape(trunc(answer_text, 300), quote=False)
    ctx.edit(run.message_id, OutMessage(text, key="27-form-question"))


# --- ответы -----------------------------------------------------------------------------------


def _run_for(ctx: Ctx, run_id: Optional[int] = None, question_id: Optional[int] = None) -> Optional[SurveyRun]:
    q = select(SurveyRun).where(SurveyRun.participant_id == ctx.participant.id, SurveyRun.status == "started")
    if run_id is not None:
        q = q.where(SurveyRun.id == run_id)
    if question_id is not None:
        q = q.where(SurveyRun.current_question_id == question_id)
    return ctx.session.scalar(q.order_by(SurveyRun.id.desc()))


def _save_answer(ctx: Ctx, run: SurveyRun, question: Question, text: str = "",
                 options: Optional[list[AnswerOption]] = None) -> Answer:
    answer = Answer(participant_id=run.participant_id, question_id=question.id, run_id=run.id,
                    event_id=run.event_id, text=text, answered_at=now_utc())
    answer.options = options or []
    ctx.session.add(answer)
    ctx.session.flush()
    logs.event("form.answer", run_id=run.id, question_id=question.id, participant_id=run.participant_id)
    return answer


def _advance(ctx: Ctx, run: SurveyRun) -> None:
    ids = list(run.question_ids or [])
    idx = ids.index(run.current_question_id) if run.current_question_id in ids else len(ids) - 1
    run.pending = []
    for next_id in ids[idx + 1:]:
        q = ctx.session.get(Question, next_id)
        if q is not None and q.is_active:
            run.current_question_id = next_id
            send_question(ctx, run)
            return
    run.current_question_id = None
    finish(ctx, run)


def handle_callback(ctx: Ctx, code: str) -> None:
    parts = code.split(":")
    kind = parts[0]
    if kind == "x":
        run = _run_for(ctx, run_id=_int(parts, 1))
        if run is None:
            ctx.notice("Опрос уже завершён")
            return
        decline(ctx, run)
        return
    question_id = _int(parts, 1)
    run = _run_for(ctx, question_id=question_id)
    if run is None:
        # повторное нажатие на уже отвеченный вопрос — второй ответ не создаётся
        ctx.notice("Этот вопрос уже позади")
        return
    question = ctx.session.get(Question, question_id)
    if run.message_id != ctx.inc.message_id and ctx.inc.message_id:
        run.message_id = ctx.inc.message_id
    if question.answer_type == "open" and kind != "s":
        ctx.notice(render("29"))
        return
    if kind == "q" and question.answer_type == "single":
        option = ctx.session.get(AnswerOption, _int(parts, 2))
        if option is None or option.question_id != question.id:
            ctx.notice("Вариант недоступен")
            return
        ctx.notice(option.text if len(option.text) > BUTTON_LIMIT - 3 else "")
        _save_answer(ctx, run, question, options=[option])
        _close_question(ctx, run, question, option.text)
        _advance(ctx, run)
    elif kind == "m" and question.answer_type == "multi":
        option_id = _int(parts, 2)
        option = ctx.session.get(AnswerOption, option_id)
        if option is None or option.question_id != question.id:
            ctx.notice("Вариант недоступен")
            return
        pending = list(run.pending or [])
        # бит — желаемое состояние: двойное нажатие не переключает обратно
        want = parts[3] == "1" if len(parts) > 3 else option_id not in pending
        if not want and option_id in pending:
            pending.remove(option_id)
        elif want and option_id not in pending:
            if question.max_choices and len(pending) >= question.max_choices:
                ctx.notice()
                ctx.send(OutMessage(render("28", {"max": question.max_choices}), key="28-form-multi-limit"))
                return
            pending.append(option_id)
        ctx.notice(option.text if len(option.text) > BUTTON_LIMIT - 3 else "")
        run.pending = pending
        msg = _build(run, question)
        if not ctx.edit(run.message_id, msg):
            run.message_id = ctx.send(msg).message_id
    elif kind == "g" and question.answer_type == "multi":
        pending = list(run.pending or [])
        if not pending and question.required:
            ctx.notice("Выберите хотя бы один вариант")
            return
        ctx.notice()
        if pending:
            options = [o for o in question.options if o.id in pending]
            _save_answer(ctx, run, question, options=options)
            _close_question(ctx, run, question, "; ".join(o.text for o in options))
        else:
            _close_question(ctx, run, question, "пропущен")
        _advance(ctx, run)
    elif kind == "s":
        if question.required:
            ctx.notice("Этот вопрос обязательный")
            return
        ctx.notice()
        _close_question(ctx, run, question, "пропущен")
        _advance(ctx, run)
    else:
        ctx.notice("Кнопка устарела")


def handle_text(ctx: Ctx, run: SurveyRun) -> bool:
    """Текст во время формы. False — текст не относится к форме."""
    question = ctx.session.get(Question, run.current_question_id) if run.current_question_id else None
    if question is None:
        return False
    text = ctx.inc.text.strip()
    if question.answer_type != "open":
        ctx.send(OutMessage("Выберите вариант кнопкой под вопросом.", key="27-form-question"))
        return True
    if len(text) > ctx.adapter.text_limit:
        ctx.send(OutMessage(render("91", {"limit": ctx.adapter.text_limit}), key="91-text-too-long"))
        return True
    if not text:
        ctx.send(OutMessage(render("29"), key="29-form-open-answer"))
        return True
    _save_answer(ctx, run, question, text=text)
    _close_question(ctx, run, question, text)
    _advance(ctx, run)
    return True


# --- завершение и отказ ---------------------------------------------------------------------------


def finish(ctx: Ctx, run: SurveyRun) -> None:
    run.status = "completed"
    run.completed_at = now_utc()
    run.current_question_id = None
    run.pending = []
    ctx.reset_state("idle")
    logs.event("form.completed", run_id=run.id, participant_id=run.participant_id, event_id=run.event_id)
    if run.event_id:
        event = ctx.session.get(Event, run.event_id)
        complete_registration(ctx, event)
    else:
        ctx.send(OutMessage(render("43", {"survey_title": run.questionnaire.title}), buttons=[[btn_me()]],
                            key="43-survey-completed"))


def complete_registration(ctx: Ctx, event: Event) -> None:
    """Создать «Участие в мероприятии» (после формы или сразу, если вопросов нет)."""
    from .render import event_values

    if not esvc.is_open(event):
        ctx.send(OutMessage(render("32", {"event_title": event.title}), buttons=[[btn_events()]],
                            key="32-event-closed"))
        return
    part = esvc.participation(ctx.session, ctx.participant.id, event.id)
    if part is not None and part.status in PARTICIPATION_ACTIVE:
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
    esvc.register_participant(ctx.session, ctx.participant, event, f"participant:{ctx.participant.id}")
    ctx.send(OutMessage(render("24", event_values(event)), buttons=[[btn_me(), btn_events()]],
                        key="24-event-pending"))


def decline(ctx: Ctx, run: SurveyRun) -> None:
    run.status = "declined"
    run.pending = []
    ctx.session.add(SurveyRefusal(participant_id=run.participant_id, questionnaire_id=run.questionnaire_id,
                                  refused_at=now_utc()))
    ctx.reset_state("idle")
    ctx.notice()
    if run.message_id:
        question = ctx.session.get(Question, run.current_question_id) if run.current_question_id else None
        if question is not None:
            n, m = run.position()
            ctx.edit(run.message_id, OutMessage(question_text(n, m, question.text),
                                                key="27-form-question"))
    audit(ctx.session, f"participant:{run.participant_id}", "form_declined", run)
    logs.event("form.declined", run_id=run.id, participant_id=run.participant_id)
    if run.event_id:
        ctx.send(OutMessage(render("30"), buttons=[[btn_events()]], key="30-form-declined"))
    else:
        ctx.send(OutMessage(render("41"), key="41-survey-declined"))


def _int(parts: list[str], i: int) -> Optional[int]:
    try:
        return int(parts[i])
    except (IndexError, ValueError):
        return None
