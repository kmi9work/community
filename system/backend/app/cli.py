"""Служебные команды.

    python -m app.cli seed                  # начальное наполнение из personal_docs/
    python -m app.cli create-admin LOGIN    # пароль — из ADMIN_PASSWORD или запросом
    python -m app.cli set-webhooks          # зарегистрировать webhook Телеграм и MAX на PUBLIC_URL
"""
from __future__ import annotations

import argparse
import getpass
import os
import sys

from sqlalchemy import select

from .auth import hash_password
from .config import settings
from .db import session_scope
from .logs import setup_logging


def cmd_seed(_args) -> None:
    from .seed import seed

    with session_scope() as s:
        seed(s)
    print("seed: ok")


def cmd_create_admin(args) -> None:
    from .models import Admin

    password = os.environ.get("ADMIN_PASSWORD") or getpass.getpass("Пароль: ")
    if len(password) < 10:
        sys.exit("Пароль — не короче 10 символов")
    with session_scope() as s:
        admin = s.scalar(select(Admin).where(Admin.login == args.login))
        if admin is None:
            admin = Admin(login=args.login, name=args.login, password_hash=hash_password(password))
            s.add(admin)
            print(f"создан администратор {args.login}")
        else:
            admin.password_hash = hash_password(password)
            admin.is_active = True
            print(f"пароль администратора {args.login} обновлён")


def cmd_set_webhooks(_args) -> None:
    from .messengers import get_adapter

    base = settings.public_url
    if not base.startswith("https://"):
        sys.exit("PUBLIC_URL должен быть https:// — мессенджеры не шлют webhook по HTTP")
    tg = get_adapter("telegram")
    if tg is not None:
        tg.set_webhook(f"{base}/webhook/telegram", settings.telegram_webhook_secret)
        print("telegram: webhook установлен")
    else:
        print("telegram: TELEGRAM_BOT_TOKEN не задан — пропуск")
    mx = get_adapter("max")
    if mx is not None:
        mx.subscribe(f"{base}/webhook/max", settings.max_webhook_secret)
        try:
            mx.set_commands()
        except Exception as exc:  # noqa: BLE001
            print(f"max: команды не обновлены: {exc}")
        print("max: подписка на webhook оформлена")
    else:
        print("max: MAX_BOT_TOKEN не задан — пропуск")


def main() -> None:
    setup_logging("cli")
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("seed").set_defaults(fn=cmd_seed)
    p = sub.add_parser("create-admin")
    p.add_argument("login")
    p.set_defaults(fn=cmd_create_admin)
    sub.add_parser("set-webhooks").set_defaults(fn=cmd_set_webhooks)
    args = parser.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
