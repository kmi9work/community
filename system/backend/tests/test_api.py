import io

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from tests.conftest import User
from tests.test_bot import make_event, q

H = {"X-Requested-With": "fetch"}


@pytest.fixture()
def client(engine, tg):
    from app.main import create_app

    with TestClient(create_app()) as c:
        yield c


@pytest.fixture()
def admin(client):
    r = client.post("/api/auth/login", json={"login": "admin", "password": "secret-pass"}, headers=H)
    assert r.status_code == 200, r.text
    return client


def test_login_required_and_csrf(client):
    assert client.get("/api/meta").status_code == 401
    assert client.post("/api/auth/login", json={"login": "admin", "password": "x"}, headers=H).status_code == 401
    assert client.post("/api/auth/login", json={"login": "admin", "password": "secret-pass"}).status_code == 403


def test_meta_and_lists(admin):
    meta = admin.get("/api/meta").json()["resources"]
    assert {"participants", "events", "broadcasts", "appeals", "roles"} <= set(meta)
    r = admin.get("/api/r/event_types", params={"sort": "order"}).json()
    assert [t["identifier"] for t in r["items"]][:2] == ["game", "lecture"]
    views = admin.get("/api/views").json()
    assert any(v["name"] == "Ошибки доставки" for v in views)


def test_event_lifecycle(admin, tg):
    types = admin.get("/api/r/event_types/options").json()
    game = [t for t in types if t["label"] == "Игры"][0]
    r = admin.post("/api/r/events", json={"title": "Игра", "event_type": game["id"], "date": "2099-01-10"}, headers=H)
    assert r.status_code == 200, r.text
    ev = r.json()
    assert ev["status"] == "planned"
    # список с фильтром предстоящих
    items = admin.get("/api/r/events", params={"f.when": "upcoming", "f.event_type": game["id"]}).json()["items"]
    assert [e["id"] for e in items] == [ev["id"]]
    # identifier не меняется
    t = admin.patch(f"/api/r/event_types/{game['id']}", json={"identifier": "zzz"}, headers=H)
    assert t.status_code == 422
    # афиша: неверный размер
    buf = io.BytesIO()
    Image.new("RGB", (100, 100)).save(buf, "JPEG")
    r = admin.post(f"/api/events/{ev['id']}/poster", files={"file": ("a.jpg", buf.getvalue(), "image/jpeg")}, headers=H)
    assert r.status_code == 422 and "1080" in r.json()["detail"]["message"]
    buf = io.BytesIO()
    Image.new("RGB", (1080, 1350)).save(buf, "JPEG")
    r = admin.post(f"/api/events/{ev['id']}/poster", files={"file": ("a.jpg", buf.getvalue(), "image/jpeg")}, headers=H)
    assert r.status_code == 200
    assert admin.get(r.json()["poster"]).status_code == 200
    # запись участника, затем дату меняет только перенос
    u = User(tg, "1001")
    u.register()
    u.press(f"ev:join:{ev['id']}")
    while tg.last().key == "27-form-question":
        b = [x for x in u.buttons() if x[0] in "qm"]
        if b and b[0].startswith("q:"):
            u.press(b[0])
        elif b:
            u.press(b[0])
            u.press("g:" + b[0].split(":")[1])
        else:
            u.text("ответ")
    r = admin.patch(f"/api/r/events/{ev['id']}", json={"date": "2099-01-11"}, headers=H)
    assert r.status_code == 422
    r = admin.post(f"/api/events/{ev['id']}/move", json={"date": "2099-01-11", "time": "19:00", "address": "Тверь",
                                                         "reason": "площадка"}, headers=H)
    assert r.status_code == 200 and r.json()["recipients"] == 1
    page = admin.get(f"/api/events/{ev['id']}/master-list")
    assert page.status_code == 200 and "Иван Петров" in page.text and "+79001234567" in page.text
    parts = admin.get("/api/r/participations", params={"f.event": ev["id"]}).json()["items"]
    assert len(parts) == 1 and parts[0]["starred"]
    roles = admin.get("/api/r/roles/options", params={"f.kind": "participant"}).json()
    r = admin.patch(f"/api/r/participations/{parts[0]['id']}", json={"role": roles[0]["id"], "attendance": "came"},
                    headers=H)
    assert r.status_code == 200 and r.json()["role"]["label"] == roles[0]["label"]
    org_roles = admin.get("/api/r/roles/options", params={"f.kind": "organizer"}).json()
    r = admin.patch(f"/api/r/participations/{parts[0]['id']}", json={"role": org_roles[0]["id"]}, headers=H)
    assert r.status_code == 422
    assert admin.post(f"/api/events/{ev['id']}/complete", headers=H).status_code == 200
    # выгрузка
    x = admin.get("/api/export/runs", params={"f.event": ev["id"]})
    assert x.status_code == 200 and x.headers["content-type"].startswith("application/vnd.openxml")


def test_broadcast_flow_api(admin, tg):
    u = User(tg, "1001")
    u.register()
    r = admin.post("/api/r/broadcasts", json={"kind": "text", "subject": "Привет", "text": "Текст",
                                              "recipient_mode": "all"}, headers=H)
    assert r.status_code == 200, r.text
    bid = r.json()["id"]
    p = admin.get(f"/api/broadcasts/{bid}/preview").json()
    assert p["recipients_count"] == 1
    r = admin.post(f"/api/broadcasts/{bid}/test", headers=H)
    assert r.status_code == 422  # карточка администратора не привязана
    participant = admin.get("/api/r/participants").json()["items"][0]
    assert admin.patch("/api/auth/me", json={"participant_id": participant["id"]}, headers=H).status_code == 200
    assert admin.post(f"/api/broadcasts/{bid}/test", headers=H).status_code == 200
    assert admin.post(f"/api/broadcasts/{bid}/launch", headers=H).status_code == 200
    assert admin.patch(f"/api/r/broadcasts/{bid}", json={"subject": "x"}, headers=H).status_code == 422
    r = admin.post(f"/api/broadcasts/{bid}/cancel", json={"reason": ""}, headers=H)
    assert r.status_code == 422
    assert admin.post(f"/api/broadcasts/{bid}/cancel", json={"reason": "ошибка"}, headers=H).status_code == 200


def test_appeals_and_search(admin, tg):
    u = User(tg, "1001")
    u.register()
    u.text("Написать организатору")
    u.text("Вопрос про игру")
    items = admin.get("/api/r/appeals", params={"f.processed": "false"}).json()["items"]
    assert len(items) == 1 and items[0]["text"] == "Вопрос про игру"
    admin.patch(f"/api/r/appeals/{items[0]['id']}", json={"processed": True, "comment": "ответили"}, headers=H)
    assert admin.get("/api/r/appeals", params={"f.processed": "false"}).json()["total"] == 0
    found = admin.get("/api/r/participants", params={"q": "79001234567"}).json()
    assert found["total"] == 1


def test_consent_document_single_current(admin):
    r = admin.post("/api/r/consent_documents", json={"title": "Согласие", "version": "2027.01",
                                                     "url": "https://example.org/v2", "published_at": "2027-01-01",
                                                     "is_current": True}, headers=H)
    assert r.status_code == 200
    docs = admin.get("/api/r/consent_documents").json()["items"]
    assert sum(1 for d in docs if d["is_current"]) == 1


def test_healthz_and_metrics(client):
    assert client.get("/healthz").json()["ok"] is True
    assert client.get("/metrics").status_code == 200
    assert client.get("/metrics", headers={"X-Forwarded-For": "1.2.3.4"}).status_code == 404


def test_webhook_secret(client, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "telegram_webhook_secret", "s3")
    assert client.post("/webhook/telegram", json={"update_id": 1}).status_code == 403
    r = client.post("/webhook/telegram", json={"update_id": 1}, headers={"X-Telegram-Bot-Api-Secret-Token": "s3"})
    assert r.status_code == 200
