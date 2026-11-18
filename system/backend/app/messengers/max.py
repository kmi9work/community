"""MAX: MAX Bot API, https://platform-api2.max.ru (docs/form/max.md)."""
from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Optional

import httpx

from .base import MENU_ITEMS, Button, Incoming, OutMessage, SendError, SendResult

TEXT_LIMIT = 4000
MENU_CODES = ("menu:events", "menu:about", "menu:me", "menu:org")


def _vcf_phone(vcf: str) -> str:
    for line in (vcf or "").splitlines():
        if line.upper().startswith("TEL"):
            return line.split(":", 1)[-1].strip()
    match = re.search(r"\+?\d{10,15}", vcf or "")
    return match.group(0) if match else ""


class MaxAdapter:
    channel = "max"
    text_limit = TEXT_LIMIT
    caption_limit = TEXT_LIMIT

    def __init__(self, token: str, api: str = "https://platform-api2.max.ru", client: Optional[httpx.Client] = None):
        self.token = token
        self.base = api.rstrip("/")
        self.client = client or httpx.Client(timeout=httpx.Timeout(20, connect=10),
                                             headers={"Authorization": token})

    def call(self, method: str, path: str, params: dict | None = None, json_body: dict | None = None) -> dict:
        try:
            resp = self.client.request(method, f"{self.base}{path}", params=params, json=json_body,
                                       headers={"Authorization": self.token})
        except httpx.HTTPError as exc:
            raise SendError(f"network: {type(exc).__name__}") from exc
        try:
            body = resp.json() if resp.content else {}
        except ValueError:
            body = {}
        if resp.status_code < 300 and body.get("success", True) is not False:
            return body
        code = resp.status_code
        desc = str(body.get("message") or body.get("code") or resp.text)[:400]
        retry_after = 0.0
        if code == 429:
            retry_after = float(resp.headers.get("Retry-After") or 1)
        blocked = code == 403 or "chat.denied" in desc or "dialog.suspended" in desc
        raise SendError(f"{code}: {desc}", code=code, retry_after=retry_after, blocked=blocked,
                        permanent=code in (400, 403, 404))

    # --- клавиатура --------------------------------------------------------------------

    @staticmethod
    def _keyboard(msg: OutMessage) -> list[dict]:
        rows: list[list[Button]] = [list(r) for r in msg.buttons]
        if msg.request_contact:
            rows.append([Button("Отправить телефон", request_contact=True)])
        if msg.menu is True:
            rows.append([Button(MENU_ITEMS[0], MENU_CODES[0]), Button(MENU_ITEMS[1], MENU_CODES[1])])
            rows.append([Button(MENU_ITEMS[2], MENU_CODES[2]), Button(MENU_ITEMS[3], MENU_CODES[3])])
        if not rows:
            return []
        out = []
        for row in rows:
            r = []
            for b in row:
                if b.request_contact:
                    r.append({"type": "request_contact", "text": b.text})
                elif b.url:
                    r.append({"type": "link", "text": b.text, "url": b.url})
                else:
                    r.append({"type": "callback", "text": b.text, "payload": b.data})
            out.append(r)
        return [{"type": "inline_keyboard", "payload": {"buttons": out}}]

    # --- афиша -----------------------------------------------------------------------------

    def _upload_image(self, path: Path) -> str:
        info = self.call("POST", "/uploads", params={"type": "image"})
        url = info.get("url")
        if not url:
            raise SendError("upload: нет url")
        try:
            with path.open("rb") as fh:
                resp = self.client.post(url, files={"data": (path.name, fh)}, headers={"Authorization": self.token})
            body = resp.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise SendError(f"upload: {type(exc).__name__}") from exc
        if body.get("token"):
            return body["token"]
        photos = body.get("photos") or {}
        for value in photos.values():
            if value.get("token"):
                return value["token"]
        raise SendError("upload: нет token")

    # --- операции ------------------------------------------------------------------------

    def send(self, chat_id: str, msg: OutMessage) -> SendResult:
        attachments = []
        uploaded = ""
        if msg.photo:
            token = msg.photo.max_token or self._upload_image(Path(msg.photo.path))
            if not msg.photo.max_token:
                uploaded = token
            attachments.append({"type": "image", "payload": {"token": token}})
        attachments += self._keyboard(msg)
        body = {"text": msg.text, "format": "html", "attachments": attachments or None, "notify": True}
        body = {k: v for k, v in body.items() if v is not None}
        for attempt in range(5):
            try:
                result = self.call("POST", "/messages", params={"user_id": chat_id}, json_body=body)
                break
            except SendError as exc:
                # загруженная картинка обрабатывается не мгновенно
                if "attachment.not.ready" in str(exc) and attempt < 4:
                    time.sleep(1 + attempt)
                    continue
                raise
        mid = ((result.get("message") or {}).get("body") or {}).get("mid", "")
        return SendResult(message_id=str(mid), max_token=uploaded)

    def edit(self, chat_id: str, message_id: str, msg: OutMessage) -> None:
        body = {"text": msg.text, "format": "html", "attachments": self._keyboard(msg)}
        self.call("PUT", "/messages", params={"message_id": message_id}, json_body=body)

    def answer_callback(self, callback_id: str, notice: str = "", chat_id: str = "") -> None:
        if not callback_id:
            return
        self.call("POST", "/answers", params={"callback_id": callback_id},
                  json_body={"notification": notice or None})

    # --- разбор входящих ------------------------------------------------------------------

    @staticmethod
    def _names(user: dict) -> tuple[str, str]:
        first = user.get("first_name") or ""
        last = user.get("last_name") or ""
        if not first and user.get("name"):
            parts = str(user["name"]).split(" ", 1)
            first, last = parts[0], (parts[1] if len(parts) > 1 else "")
        return first, last

    def parse(self, update: dict) -> Optional[Incoming]:
        kind = update.get("update_type")
        ts = update.get("timestamp")
        if kind == "message_created":
            m = update.get("message") or {}
            sender = m.get("sender") or {}
            recipient = m.get("recipient") or {}
            if recipient.get("chat_type") not in (None, "dialog"):
                return None
            body = m.get("body") or {}
            first, last = self._names(sender)
            base = dict(
                channel=self.channel,
                user_id=str(sender.get("user_id")),
                chat_id=str(recipient.get("chat_id") or ""),
                first_name=first,
                last_name=last,
                message_id=str(body.get("mid", "")),
                dedup_key=f"m:{body.get('mid') or ts}",
            )
            for att in body.get("attachments") or []:
                if att.get("type") == "contact":
                    payload = att.get("payload") or {}
                    info = payload.get("max_info") or {}
                    own = not info.get("user_id") or str(info.get("user_id")) == str(sender.get("user_id"))
                    return Incoming(kind="contact", contact_phone=_vcf_phone(payload.get("vcf_info", "")),
                                    contact_is_own=own, **base)
            text = body.get("text") or ""
            if text.startswith("/start"):
                return Incoming(kind="start", text=text, start_payload=text[6:].strip(), **base)
            if not text:
                return Incoming(kind="other", **base)
            return Incoming(kind="text", text=text, **base)
        if kind == "message_callback":
            cb = update.get("callback") or {}
            user = cb.get("user") or {}
            m = update.get("message") or {}
            first, last = self._names(user)
            return Incoming(
                channel=self.channel,
                kind="callback",
                user_id=str(user.get("user_id")),
                chat_id=str((m.get("recipient") or {}).get("chat_id") or ""),
                callback_data=cb.get("payload") or "",
                callback_id=str(cb.get("callback_id") or ""),
                message_id=str((m.get("body") or {}).get("mid", "")),
                first_name=first,
                last_name=last,
                dedup_key=f"c:{cb.get('callback_id') or ts}",
            )
        if kind in ("bot_started", "bot_stopped", "dialog_removed"):
            user = update.get("user") or {}
            first, last = self._names(user)
            return Incoming(
                channel=self.channel,
                kind="start" if kind == "bot_started" else "stopped",
                user_id=str(user.get("user_id")),
                chat_id=str(update.get("chat_id") or ""),
                first_name=first,
                last_name=last,
                start_payload=str(update.get("payload") or ""),
                dedup_key=f"{kind}:{user.get('user_id')}:{ts}",
            )
        return None

    # --- служебное -----------------------------------------------------------------------

    def subscribe(self, url: str, secret: str) -> dict:
        body = {
            "url": url,
            "update_types": ["message_created", "message_callback", "bot_started", "bot_stopped", "dialog_removed"],
        }
        if secret:
            body["secret"] = secret
        return self.call("POST", "/subscriptions", json_body=body)

    def set_commands(self) -> dict:
        commands = [
            {"name": "start", "description": "Начать / продолжить"},
            {"name": "menu", "description": "Меню участника"},
        ]
        return self.call("PATCH", "/me", json_body={"commands": commands})

    def get_updates(self, marker: Optional[int], timeout: int = 25) -> tuple[list[dict], Optional[int]]:
        params = {"timeout": timeout, "limit": 100}
        if marker is not None:
            params["marker"] = marker
        try:
            resp = self.client.get(f"{self.base}/updates", params=params, timeout=timeout + 10,
                                   headers={"Authorization": self.token})
            body = resp.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise SendError(f"network: {type(exc).__name__}") from exc
        return body.get("updates") or [], body.get("marker")
