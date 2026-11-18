"""Настройки из переменных окружения (deploy/.env на сервере)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

# Корень репозитория (community/) при запуске из исходников. В контейнере пути
# короче (/app/app/config.py) — там PERSONAL_DOCS_DIR и FRONTEND_DIST заданы явно.
_parents = Path(__file__).resolve().parents
REPO_ROOT = _parents[3] if len(_parents) > 3 else _parents[-1]


def _bool(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass
class Settings:
    database_url: str = field(default_factory=lambda: os.environ.get("DATABASE_URL", "sqlite:///./community.db"))
    # Подпись cookie сессии админки
    session_secret: str = field(default_factory=lambda: os.environ.get("SESSION_SECRET", "dev-secret-change-me"))
    session_max_age: int = field(default_factory=lambda: int(os.environ.get("SESSION_MAX_AGE", str(7 * 24 * 3600))))
    secure_cookies: bool = field(default_factory=lambda: _bool("SECURE_COOKIES", False))
    public_url: str = field(default_factory=lambda: os.environ.get("PUBLIC_URL", "http://localhost:8000").rstrip("/"))
    timezone: str = field(default_factory=lambda: os.environ.get("TIMEZONE", "Europe/Moscow"))

    # Специфика сообщества: тексты, уровни, вопросы (personal_docs/ в корне репозитория)
    personal_docs_dir: Path = field(
        default_factory=lambda: Path(os.environ.get("PERSONAL_DOCS_DIR", str(REPO_ROOT / "personal_docs")))
    )
    media_dir: Path = field(default_factory=lambda: Path(os.environ.get("MEDIA_DIR", "./media")))
    frontend_dist: Path = field(
        default_factory=lambda: Path(os.environ.get("FRONTEND_DIST", str(REPO_ROOT / "system" / "frontend" / "dist")))
    )

    telegram_token: str = field(default_factory=lambda: os.environ.get("TELEGRAM_BOT_TOKEN", ""))
    telegram_webhook_secret: str = field(default_factory=lambda: os.environ.get("TELEGRAM_WEBHOOK_SECRET", ""))
    telegram_api: str = field(default_factory=lambda: os.environ.get("TELEGRAM_API", "https://api.telegram.org"))

    max_token: str = field(default_factory=lambda: os.environ.get("MAX_BOT_TOKEN", ""))
    max_webhook_secret: str = field(default_factory=lambda: os.environ.get("MAX_WEBHOOK_SECRET", ""))
    max_api: str = field(default_factory=lambda: os.environ.get("MAX_API", "https://platform-api2.max.ru"))

    # Первый администратор создаётся при старте, если учётных записей нет
    admin_login: str = field(default_factory=lambda: os.environ.get("ADMIN_LOGIN", ""))
    admin_password: str = field(default_factory=lambda: os.environ.get("ADMIN_PASSWORD", ""))

    # Воркер рассылки
    worker_poll_seconds: float = field(default_factory=lambda: float(os.environ.get("WORKER_POLL_SECONDS", "1")))
    send_pause_seconds: float = field(default_factory=lambda: float(os.environ.get("SEND_PAUSE_SECONDS", "0.05")))
    max_send_attempts: int = field(default_factory=lambda: int(os.environ.get("MAX_SEND_ATTEMPTS", "3")))
    export_row_limit: int = field(default_factory=lambda: int(os.environ.get("EXPORT_ROW_LIMIT", "20000")))
    log_level: str = field(default_factory=lambda: os.environ.get("LOG_LEVEL", "INFO"))


settings = Settings()


def reload_settings() -> Settings:
    """Перечитать окружение (для тестов)."""
    global settings
    new = Settings()
    settings.__dict__.update(new.__dict__)
    return settings
