from datetime import datetime, time, timedelta, timezone

from sqlalchemy import select

from app import db
from app.messengers.base import SendError
from app.models import Admin, Broadcast, Delivery, Event, Level, Participant, Participation, Questionnaire, Question, \
    AnswerOption, Reminder
from app.services import broadcasts as bsvc
from app.services import delivery
from app.services import events as esvc
from app.util import now_local, today_local
from tests.conftest import User
from tests.test_bot import make_event, q


def run_worker():
    for _ in range(5):
        if not delivery.process_queue(db.session_scope):
            break


def new_broadcast(**kw):
    with db.session_scope() as s:
        b = Broadcast(status="draft", **kw)
        s.add(b)
        s.flush()
        return b.id


def test_announcement_to_subscribers(tg, mx):
    a = User(tg, "1", "Анна", "А")
    a.register("+79000000001")
    b = User(tg, "2", "Борис", "Б")
    b.register("+79000000002")
    event_id = make_event()
    with db.session_scope() as s:
        event = s.get(Event, event_id)
        bc = Broadcast(kind="announcement", subject="Анонс", event_id=event_id, recipient_mode="event_types",
                       status="draft")
        bc.event_types = [event.event_type]
        s.add(bc)
        s.flush()
        bid = bc.id
    # Борис отписался от игр
    b.press(f"ev:unsub:{q(Event)[0].event_type_id}")
    tg.clear()
    with db.session_scope() as s:
        bc = s.get(Broadcast, bid)
        preview = bsvc.preview(s, bc)
        assert preview["recipients_count"] == 1
        bsvc.launch(s, bc, "admin:1")
    run_worker()
    [d] = q(Delivery)
    assert d.status == "sent" and d.message_id
    [bc] = q(Broadcast)
    assert bc.status == "finished" and bc.delivered_count == 1
    sent = tg.sent()
    assert sent[0].key == "22-event-announcement"
    assert [x.data for row in sent[0].buttons for x in row][1].startswith("ev:unsub:")


def test_survey_broadcast_and_flow(user, tg):
    user.register()
    with db.session_scope() as s:
        qn = Questionnaire(title="Отзыв", identifier="feedback", purpose="survey", description="Пара вопросов")
        s.add(qn)
        s.flush()
        qq = Question(questionnaire_id=qn.id, text="Понравилось?", answer_type="single", required=True, order=1)
        s.add(qq)
        s.flush()
        s.add_all([AnswerOption(question_id=qq.id, text="Да", order=1), AnswerOption(question_id=qq.id, text="Нет", order=2)])
        new_level = s.scalar(select(Level).where(Level.identifier == "new"))
        bc = Broadcast(kind="survey", subject="Опрос", questionnaire_id=qn.id, recipient_mode="levels", status="draft")
        bc.levels = [new_level]
        s.add(bc)
        s.flush()
        assert bsvc.preview(s, bc)["recipients_count"] == 1  # подписан на типы уровня new
        bsvc.launch(s, bc, "admin:1")
        qid = qn.id
    run_worker()
    assert tg.last().key == "40-survey-invite"
    user.text("Мои данные")
    user.press("me:surveys")
    assert "не начат" in tg.last().text
    user.press(f"sv:take:{qid}")
    assert tg.last().key == "27-form-question"
    opt = [b for b in user.buttons() if b.startswith("q:")][0]
    user.press(opt)
    assert tg.last().key == "43-survey-completed"
    user.press(f"sv:take:{qid}")  # вопросы уже отвечены
    assert tg.last().key == "43-survey-completed"


def test_survey_excludes_refused(user, tg):
    user.register()
    user.press("sv:decline_all")
    with db.session_scope() as s:
        qn = Questionnaire(title="Отзыв", identifier="fb2", purpose="survey")
        s.add(qn)
        s.flush()
        bc = Broadcast(kind="survey", subject="Опрос", questionnaire_id=qn.id, recipient_mode="all", status="draft")
        s.add(bc)
        s.flush()
        assert bsvc.preview(s, bc)["recipients_count"] == 0


def test_text_broadcast_selected_and_retry(user, tg):
    user.register()
    [p] = q(Participant)
    with db.session_scope() as s:
        bc = Broadcast(kind="text", subject="Новости", text="Привет <b>всем</b> <script>", recipient_mode="selected",
                       status="draft")
        bc.participants = [s.get(Participant, p.id)]
        s.add(bc)
        s.flush()
        bsvc.launch(s, bc, "admin:1")
        bid = bc.id
    tg.fail_with = SendError("500: oops", code=500)
    run_worker()
    [d] = q(Delivery)
    assert d.status == "queued" and d.attempt == 2  # повтор по расписанию
    with db.session_scope() as s:
        d = s.get(Delivery, d.id)
        d.next_attempt_at = None
    run_worker()
    [d] = q(Delivery)
    assert d.status == "sent"
    msg = tg.sent()[-1]
    assert "<b>Новости</b>" in msg.text and "&lt;script&gt;" in msg.text and "<b>всем</b>" in msg.text


def test_blocked_user_marks_channel(user, tg):
    user.register()
    [p] = q(Participant)
    with db.session_scope() as s:
        bc = Broadcast(kind="text", subject="X", text="Y", recipient_mode="all", status="draft")
        s.add(bc)
        s.flush()
        bsvc.launch(s, bc, "admin:1")
    tg.fail_with = SendError("403: Forbidden: bot was blocked by the user", code=403, blocked=True)
    run_worker()
    [d] = q(Delivery)
    assert d.status == "error"
    [p] = q(Participant)
    assert p.status == "deleted"  # единственный мессенджер — карточка удалена
    [bc] = q(Broadcast)
    assert bc.status == "finished" and bc.errors_count == 1


def test_retry_errors_and_cancel(user, tg):
    user.register()
    with db.session_scope() as s:
        bc = Broadcast(kind="text", subject="X", text="Y", recipient_mode="all", status="draft")
        s.add(bc)
        s.flush()
        bsvc.launch(s, bc, "admin:1")
        bid = bc.id
    tg.fail_with = SendError("400: bad request", code=400, permanent=True)
    run_worker()
    with db.session_scope() as s:
        bc = s.get(Broadcast, bid)
        assert bc.errors_count == 1
        assert bsvc.retry_errors(s, bc, "admin:1") == 1
    run_worker()
    [d] = q(Delivery)
    assert d.status == "sent" and d.attempt == 2


def test_reminders_and_move(user, tg):
    user.register()
    start = now_local() + timedelta(hours=3)
    event_id = make_event("lecture", days=(start.date() - today_local()).days,
                          time=start.time().replace(second=0, microsecond=0))
    user.press(f"ev:join:{event_id}")
    with db.session_scope() as s:
        s.add(Reminder(event_id=event_id, hours_before=24, is_active=True))
        s.add(Reminder(event_id=event_id, hours_before=1, is_active=True))
    with db.session_scope() as s:
        assert delivery.schedule_reminders(s) == 1  # за 24 часа уже наступило, за 1 час — ещё нет
        assert delivery.schedule_reminders(s) == 0
    run_worker()
    assert tg.last().key == "70-reminder"
    with db.session_scope() as s:
        event = s.get(Event, event_id)
        esvc.move(s, event, new_date=event.date + timedelta(days=7), new_time=time(18, 0), new_address="Тверь",
                  reason="площадка", admin_id=None)
    run_worker()
    assert tg.last().key == "80-event-moved"
    [e] = q(Event)
    assert e.status == "moved" and e.previous_date == start.date()
    assert all(r.sent_at is None for r in q(Reminder))  # пересчёт от новой даты
    with db.session_scope() as s:
        esvc.cancel(s, s.get(Event, event_id), reason="болезнь", admin_id=None)
    run_worker()
    assert tg.last().key == "81-event-cancelled"
    assert "reason=болезнь" in tg.last().text
    with db.session_scope() as s:
        assert delivery.schedule_reminders(s) == 0


def test_cancelled_participant_gets_no_reminder(tg):
    a = User(tg, "1", "Анна", "А")
    a.register("+79000000001")
    event_id = make_event("lecture", days=1)
    a.press(f"ev:join:{event_id}")
    with db.session_scope() as s:
        part = s.scalar(select(Participation))
        part.status = "cancelled"
        b = bsvc.create_system_broadcast(s, s.get(Event, event_id), "reminder", "Напоминание", {})
        assert b.recipients_count == 0 and b.status == "finished"


def test_test_broadcast_goes_to_author(user, tg):
    user.register()
    [p] = q(Participant)
    with db.session_scope() as s:
        admin = s.scalar(select(Admin))
        admin.participant_id = p.id
        bc = Broadcast(kind="text", subject="Тема", text="Текст", recipient_mode="all", status="draft",
                       author_id=admin.id)
        s.add(bc)
        s.flush()
        test = bsvc.launch_test(s, bc, admin)
        assert test.is_test and test.recipients_count == 1
        assert bc.status == "draft"


def test_excel_export(user, tg):
    from io import BytesIO

    from openpyxl import load_workbook

    from app.models import SurveyRun
    from app.services import export

    user.register()
    event_id = make_event()
    user.press(f"ev:join:{event_id}")
    user.press([b for b in user.buttons() if b.startswith("q:")][0])
    user.text("=HYPERLINK(\"x\")")
    with db.session_scope() as s:
        runs = list(s.scalars(select(SurveyRun)))
        data = export.build(s, runs)
        assert export.filename(runs).startswith("registration-")
    wb = load_workbook(BytesIO(data))
    wide, flat = wb["Прохождения"], wb["Ответы"]
    assert wide.max_row == 2 and flat.max_row == 3
    values = [c.value for c in flat["C"]]
    assert '=HYPERLINK("x")' in values
    cell = [c for c in flat["C"] if c.value == '=HYPERLINK("x")'][0]
    assert cell.data_type == "s"
