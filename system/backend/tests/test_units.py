import os
from pathlib import Path

import pytest

from app.messengers.max import MaxAdapter
from app.messengers.telegram import TelegramAdapter
from app.texts import BLOCK_RE, VAR_RE, render_template, sanitize_html


def test_telegram_parse():
    a = TelegramAdapter("t")
    inc = a.parse({"update_id": 5, "message": {"message_id": 1, "chat": {"id": 7, "type": "private"},
                                               "from": {"id": 7, "first_name": "Иван"}, "text": "/start ev12"}})
    assert inc.kind == "start" and inc.start_payload == "ev12" and inc.dedup_key == "u:5"
    inc = a.parse({"update_id": 6, "message": {"message_id": 2, "chat": {"id": 7, "type": "private"},
                                               "from": {"id": 7}, "contact": {"phone_number": "79001112233",
                                                                              "user_id": 7}}})
    assert inc.kind == "contact" and inc.contact_is_own
    inc = a.parse({"update_id": 7, "callback_query": {"id": "c1", "data": "ev:all", "from": {"id": 7},
                                                      "message": {"message_id": 3, "chat": {"id": 7}}}})
    assert inc.kind == "callback" and inc.callback_data == "ev:all" and inc.message_id == "3"
    inc = a.parse({"update_id": 8, "my_chat_member": {"chat": {"id": 7, "type": "private"}, "from": {"id": 7},
                                                      "new_chat_member": {"status": "kicked"}}})
    assert inc.kind == "stopped"
    assert a.parse({"update_id": 9, "message": {"chat": {"id": -1, "type": "group"}, "from": {"id": 1},
                                                "text": "x"}}) is None


def test_max_parse():
    a = MaxAdapter("t")
    inc = a.parse({"update_type": "message_created", "timestamp": 1,
                   "message": {"sender": {"user_id": 9, "first_name": "Анна"}, "recipient": {"chat_id": 99,
                                                                                            "chat_type": "dialog"},
                               "body": {"mid": "mid.1", "text": "привет"}}})
    assert inc.kind == "text" and inc.user_id == "9" and inc.dedup_key == "m:mid.1"
    inc = a.parse({"update_type": "message_created", "timestamp": 2,
                   "message": {"sender": {"user_id": 9}, "recipient": {"chat_id": 99},
                               "body": {"mid": "mid.2", "attachments": [{"type": "contact", "payload": {
                                   "vcf_info": "BEGIN:VCARD\nTEL;TYPE=cell:79001112233\nEND:VCARD",
                                   "max_info": {"user_id": 9}}}]}}})
    assert inc.kind == "contact" and inc.contact_phone == "79001112233" and inc.contact_is_own
    inc = a.parse({"update_type": "message_callback", "timestamp": 3,
                   "callback": {"callback_id": "cb", "payload": "me:data", "user": {"user_id": 9}},
                   "message": {"body": {"mid": "mid.3"}, "recipient": {"chat_id": 99}}})
    assert inc.kind == "callback" and inc.message_id == "mid.3"
    inc = a.parse({"update_type": "bot_stopped", "timestamp": 4, "user": {"user_id": 9}})
    assert inc.kind == "stopped"
    kb = MaxAdapter._keyboard(__import__("app.messengers.base", fromlist=["OutMessage"]).OutMessage("x", menu=True))
    assert kb[0]["type"] == "inline_keyboard" and len(kb[0]["payload"]["buttons"]) == 2


def test_render_rules():
    tpl = "<b>{{event_title}}</b>\n{{event_date}}, {{event_time}}\n{{event_address}}\n\nКонец"
    out = render_template(tpl, {"event_title": "A & B", "event_date": "12 декабря"})
    assert out == "<b>A &amp; B</b>\n12 декабря\n\nКонец"


def test_sanitize():
    assert sanitize_html('<b>x</b><script>y</script><a href="https://e.org">l</a>') == \
        '<b>x</b>&lt;script&gt;y&lt;/script&gt;<a href="https://e.org">l</a>'


REAL = Path(__file__).resolve().parents[3] / "personal_docs" / "messages"


@pytest.mark.skipif(not REAL.is_dir(), reason="personal_docs/ нет в репозитории")
def test_real_templates_have_expected_keys():
    """Коды шаблонов, которые использует бот, есть в personal_docs/messages."""
    fixtures = Path(__file__).parent / "fixtures" / "personal_docs" / "messages"
    real = {f.stem: BLOCK_RE.findall(f.read_text(encoding="utf-8")) for f in REAL.glob("*.md") if f.stem != "README"}
    for f in fixtures.glob("*.md"):
        assert f.stem in real, f"нет шаблона {f.stem}"
        fixture_blocks = BLOCK_RE.findall(f.read_text(encoding="utf-8"))
        assert len(real[f.stem]) >= len(fixture_blocks), f.stem
        for fb, rb in zip(fixture_blocks, real[f.stem]):
            assert set(VAR_RE.findall(fb)) == set(VAR_RE.findall(rb)), f.stem
