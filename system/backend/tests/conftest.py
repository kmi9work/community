import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
FIXTURES = Path(__file__).parent / "fixtures" / "personal_docs"
REAL_DOCS = ROOT.parents[1] / "personal_docs"

os.environ["DATABASE_URL"] = "sqlite://"
os.environ["PERSONAL_DOCS_DIR"] = str(FIXTURES)
os.environ["SEND_PAUSE_SECONDS"] = "0"
os.environ["ADMIN_LOGIN"] = "admin"
os.environ["ADMIN_PASSWORD"] = "secret-pass"
os.environ["TIMEZONE"] = "Europe/Moscow"

from app import db, texts  # noqa: E402
from app.config import reload_settings  # noqa: E402
from app.messengers import reset as reset_adapters, set_adapter  # noqa: E402
from app.messengers.base import Incoming  # noqa: E402
from app.messengers.fake import FakeAdapter  # noqa: E402


@pytest.fixture()
def engine(tmp_path):
    os.environ["MEDIA_DIR"] = str(tmp_path / "media")
    reload_settings()
    texts.reload()
    url = os.environ.get("TEST_DATABASE_URL", "sqlite://")
    engine = db.make_engine(url)
    db.set_engine(engine)
    from app import models  # noqa: F401
    from app.seed import seed

    db.Base.metadata.drop_all(engine)
    db.Base.metadata.create_all(engine)
    with db.session_scope() as s:
        seed(s)
    yield engine
    engine.dispose()


@pytest.fixture()
def tg(engine):
    adapter = FakeAdapter("telegram", 4096, 1024)
    set_adapter("telegram", adapter)
    yield adapter
    reset_adapters()


@pytest.fixture()
def mx(engine):
    adapter = FakeAdapter("max", 4000, 4000)
    set_adapter("max", adapter)
    yield adapter
    reset_adapters()


class User:
    """Пользователь мессенджера для сценариев бота."""

    counter = 0

    def __init__(self, adapter: FakeAdapter, user_id: str, first="Иван", last="Петров"):
        self.adapter = adapter
        self.user_id = user_id
        self.first = first
        self.last = last

    def _send(self, **kw):
        from app.bot.core import dispatch

        User.counter += 1
        inc = Incoming(channel=self.adapter.channel, user_id=self.user_id, chat_id=self.user_id,
                       first_name=self.first, last_name=self.last, dedup_key=f"t{User.counter}", **kw)
        with db.session_scope() as s:
            dispatch(s, self.adapter, inc)
        return self.adapter.last() if self.adapter.log else None

    def start(self):
        return self._send(kind="start", text="/start")

    def text(self, text):
        return self._send(kind="text", text=text)

    def contact(self, phone, own=True):
        return self._send(kind="contact", contact_phone=phone, contact_is_own=own)

    def press(self, data, message_id=None):
        if message_id is None:
            message_id = self.last_message_id()
        return self._send(kind="callback", callback_data=data, callback_id=f"cb{User.counter}",
                          message_id=message_id)

    def stopped(self):
        return self._send(kind="stopped")

    def last_message_id(self):
        for s in reversed(self.adapter.log):
            if s.op in ("send", "edit") and s.msg and s.msg.buttons:
                return s.message_id
        return ""

    def buttons(self):
        msg = self.adapter.last()
        return [b.data for row in msg.buttons for b in row]

    def register(self, phone="+79001234567"):
        self.start()
        self.press("reg:name_yes")
        self.contact(phone)
        self.press("reg:contact_yes")
        return self.press("reg:consent_yes")


@pytest.fixture()
def user(tg):
    return User(tg, "1001")
