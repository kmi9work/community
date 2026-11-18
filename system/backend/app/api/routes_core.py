"""Вход, профиль, метаданные разделов, общий CRUD, представления."""
from __future__ import annotations

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import logs
from ..auth import hash_password, limiter, verify_password
from ..models import Admin, SavedView
from ..util import audit, now_utc
from . import crud
from . import resources  # noqa: F401 — регистрация разделов
from .deps import current_admin, get_db

router = APIRouter(prefix="/api")


class LoginIn(BaseModel):
    login: str
    password: str


@router.post("/auth/login")
def login(body: LoginIn, request: Request, session: Session = Depends(get_db)):
    key = request.headers.get("x-real-ip") or (request.client.host if request.client else "?")
    if limiter.blocked(key):
        logs.event("admin.login", result="blocked")
        raise HTTPException(429, "Слишком много попыток. Подождите 15 минут.")
    admin = session.scalar(select(Admin).where(Admin.login == body.login.strip()))
    if admin is None or not admin.is_active or not verify_password(body.password, admin.password_hash):
        limiter.fail(key)
        logs.event("admin.login", result="fail")
        raise HTTPException(401, "Неверный логин или пароль")
    limiter.reset(key)
    admin.last_login_at = now_utc()
    request.session.clear()
    request.session["admin_id"] = admin.id
    request.session["pw"] = admin.password_hash[-12:]
    logs.event("admin.login", result="ok", admin_id=admin.id)
    return me_payload(admin)


@router.post("/auth/logout")
def logout(request: Request):
    request.session.clear()
    return {"ok": True}


def me_payload(admin: Admin) -> dict:
    return {"id": admin.id, "login": admin.login, "name": admin.name,
            "participant": crud.ref_value(admin.participant)}


@router.get("/auth/me")
def me(admin: Admin = Depends(current_admin)):
    return me_payload(admin)


class ProfileIn(BaseModel):
    name: str | None = None
    participant_id: int | None = None
    current_password: str | None = None
    new_password: str | None = None


@router.patch("/auth/me")
def update_me(body: ProfileIn, request: Request, admin: Admin = Depends(current_admin),
              session: Session = Depends(get_db)):
    if body.name is not None:
        admin.name = body.name.strip()
    if body.participant_id is not None:
        admin.participant_id = body.participant_id or None
    if body.new_password:
        if not body.current_password or not verify_password(body.current_password, admin.password_hash):
            raise crud.ValidationFailed("Текущий пароль неверен", "current_password")
        if len(body.new_password) < 10:
            raise crud.ValidationFailed("Пароль — не короче 10 символов", "new_password")
        admin.password_hash = hash_password(body.new_password)
        request.session["pw"] = admin.password_hash[-12:]
        audit(session, f"admin:{admin.id}", "password_changed", admin)
    session.flush()
    session.refresh(admin)
    return me_payload(admin)


@router.get("/meta")
def meta(admin: Admin = Depends(current_admin)):
    return {"resources": {name: res.meta() for name, res in crud.REGISTRY.items()}}


# --- представления ------------------------------------------------------------------------


@router.get("/views")
def list_views(resource: str | None = None, admin: Admin = Depends(current_admin), session: Session = Depends(get_db)):
    q = select(SavedView).order_by(SavedView.is_system.desc(), SavedView.order, SavedView.id)
    if resource:
        q = q.where(SavedView.resource == resource)
    return [{"id": v.id, "resource": v.resource, "name": v.name, "params": v.params, "is_system": v.is_system}
            for v in session.scalars(q)]


class ViewIn(BaseModel):
    resource: str
    name: str
    params: dict


@router.post("/views")
def create_view(body: ViewIn, admin: Admin = Depends(current_admin), session: Session = Depends(get_db)):
    crud.get_resource(body.resource)
    if not body.name.strip():
        raise crud.ValidationFailed("Назовите представление", "name")
    v = SavedView(resource=body.resource, name=body.name.strip(), params=body.params, created_by_id=admin.id)
    session.add(v)
    session.flush()
    return {"id": v.id, "resource": v.resource, "name": v.name, "params": v.params, "is_system": False}


@router.delete("/views/{view_id}")
def delete_view(view_id: int, admin: Admin = Depends(current_admin), session: Session = Depends(get_db)):
    v = session.get(SavedView, view_id)
    if v is None:
        raise HTTPException(404, "Нет представления")
    if v.is_system:
        raise crud.ValidationFailed("Системное представление не удаляется")
    session.delete(v)
    return {"ok": True}


# --- общий CRUD -------------------------------------------------------------------------------


def _params(request: Request) -> dict[str, str]:
    return dict(request.query_params)


@router.get("/r/{resource}")
def list_resource(resource: str, request: Request, admin: Admin = Depends(current_admin),
                  session: Session = Depends(get_db)):
    res = crud.get_resource(resource)
    return crud.list_objects(session, res, _params(request))


@router.get("/r/{resource}/options")
def resource_options(resource: str, request: Request, admin: Admin = Depends(current_admin),
                     session: Session = Depends(get_db)):
    res = crud.get_resource(resource)
    return crud.options(session, res, _params(request))


@router.get("/r/{resource}/{obj_id}")
def get_resource(resource: str, obj_id: int, admin: Admin = Depends(current_admin),
                 session: Session = Depends(get_db)):
    res = crud.get_resource(resource)
    obj = session.get(res.model, obj_id)
    if obj is None:
        raise HTTPException(404, "Запись не найдена")
    return crud.serialize(session, res, obj)


@router.post("/r/{resource}")
def create_resource(resource: str, data: dict = Body(...), admin: Admin = Depends(current_admin),
                    session: Session = Depends(get_db)):
    res = crud.get_resource(resource)
    if not res.can_create:
        raise HTTPException(405, "В этом разделе записи не создаются")
    obj = res.model()
    crud.save(session, res, obj, data, admin, creating=True)
    logs.event("admin.created", resource=resource, object_id=obj.id, admin_id=admin.id)
    return crud.serialize(session, res, obj)


@router.patch("/r/{resource}/{obj_id}")
def update_resource(resource: str, obj_id: int, data: dict = Body(...), admin: Admin = Depends(current_admin),
                    session: Session = Depends(get_db)):
    res = crud.get_resource(resource)
    obj = session.get(res.model, obj_id)
    if obj is None:
        raise HTTPException(404, "Запись не найдена")
    crud.save(session, res, obj, data, admin, creating=False)
    logs.event("admin.updated", resource=resource, object_id=obj.id, admin_id=admin.id, fields=sorted(data))
    session.refresh(obj)
    return crud.serialize(session, res, obj)


@router.delete("/r/{resource}/{obj_id}")
def delete_resource(resource: str, obj_id: int, admin: Admin = Depends(current_admin),
                    session: Session = Depends(get_db)):
    res = crud.get_resource(resource)
    if not res.can_delete:
        raise HTTPException(405, "Записи этого раздела не удаляются")
    obj = session.get(res.model, obj_id)
    if obj is None:
        raise HTTPException(404, "Запись не найдена")
    audit(session, f"admin:{admin.id}", "deleted", obj, resource=resource)
    if resource == "refusals" and obj.questionnaire_id is None:
        # снят отказ от всех опросов — снимается и флаг в карточке
        obj.participant.surveys_declined_all = False
    session.delete(obj)
    logs.event("admin.deleted", resource=resource, object_id=obj_id, admin_id=admin.id)
    return {"ok": True}
