"""Журнал: одна строка — одно событие в JSON, время в UTC.

В журнал не пишутся тексты ответов, телефоны, адреса, токены и пароли —
только идентификаторы и коды (README.md, «Наблюдаемость»).
"""
from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone

from .config import settings

SERVICE = "api"


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        data = {
            "ts": datetime.fromtimestamp(record.created, timezone.utc).isoformat(timespec="milliseconds"),
            "level": record.levelname.lower(),
            "service": SERVICE,
            "event": record.getMessage(),
        }
        extra = getattr(record, "fields", None)
        if extra:
            data.update({k: v for k, v in extra.items() if v is not None})
        if record.exc_info:
            data["error"] = self.formatException(record.exc_info)[-2000:]
        return json.dumps(data, ensure_ascii=False, default=str)


def setup_logging(service: str) -> None:
    global SERVICE
    SERVICE = service
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(settings.log_level)
    for noisy in ("httpx", "httpcore", "uvicorn.access"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


log = logging.getLogger("community")


def event(name: str, level: int = logging.INFO, **fields) -> None:
    log.log(level, name, extra={"fields": fields})


def error(name: str, exc_info: bool = True, **fields) -> None:
    log.error(name, exc_info=exc_info, extra={"fields": fields})
