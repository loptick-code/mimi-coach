"""Auth simple par mot de passe + cookie de session signe."""

from __future__ import annotations

from typing import Optional

import bcrypt
from fastapi import Depends, HTTPException, Request, status
from sqlmodel import Session as DBSession
from sqlmodel import select

from app.db import get_session
from app.models import User, UserRole


def _truncate(password: str) -> bytes:
    """Bcrypt 5.x refuse les mots de passe > 72 octets - on tronque silencieusement."""
    return password.encode("utf-8")[:72]


def hash_password(password: str) -> str:
    return bcrypt.hashpw(_truncate(password), bcrypt.gensalt()).decode()


def verify_password(password: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(_truncate(password), hashed.encode())
    except Exception:
        return False


def login_user(request: Request, user: User) -> None:
    request.session["user_id"] = user.id


def logout_user(request: Request) -> None:
    request.session.clear()


def current_user(
    request: Request, db: DBSession = Depends(get_session)
) -> Optional[User]:
    uid = request.session.get("user_id")
    if not uid:
        return None
    return db.get(User, uid)


def require_user(
    user: Optional[User] = Depends(current_user),
) -> User:
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Pas connecte",
            headers={"Location": "/login"},
        )
    return user


def require_coach(user: User = Depends(require_user)) -> User:
    if user.role != UserRole.coach:
        raise HTTPException(status_code=403, detail="Reserve au coach")
    return user
