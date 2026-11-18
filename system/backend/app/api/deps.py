"""Зависимости API: сессия БД и текущий администратор."""
from __future__ import annotations

from typing import Iterator

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from ..db import SessionLocal, get_engine
from ..models import Admin


def get_db() -> Iterator[Session]:
    get_engine()
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def current_admin(request: Request, session: Session = Depends(get_db)) -> Admin:
    admin_id = request.session.get("admin_id")
    if not admin_id:
        raise HTTPException(401, "Нужен вход")
    admin = session.get(Admin, admin_id)
    if admin is None or not admin.is_active or request.session.get("pw") != admin.password_hash[-12:]:
        request.session.clear()
        raise HTTPException(401, "Нужен вход")
    return admin
