import time
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer
from jwt import InvalidTokenError
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import get_settings
from .database import get_db
from .models import Role, User

class _UzbekBearer(OAuth2PasswordBearer):
    """Same scheme, but the "no token" error speaks the interface language."""

    async def __call__(self, request: Request) -> str | None:  # type: ignore[override]
        try:
            return await super().__call__(request)
        except HTTPException as exc:
            if exc.status_code == status.HTTP_401_UNAUTHORIZED:
                raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Tizimga kirish talab qilinadi",
                                    headers=exc.headers) from None
            raise


oauth2 = _UzbekBearer(tokenUrl="/api/auth/login")


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


# A bcrypt hash of an unusable password. Verifying against it makes a login attempt for
# an unknown account cost the same as one for a real account, so response time cannot be
# used to enumerate usernames.
_DUMMY_HASH = bcrypt.hashpw(b"raqobat-nonexistent-account", bcrypt.gensalt()).decode()


def verify_password(password: str, encoded: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode(), encoded.encode())
    except ValueError:
        return False


def burn_password_check() -> None:
    """Spend the same work as a real verification for an account that does not exist."""
    bcrypt.checkpw(b"raqobat-nonexistent-account", _DUMMY_HASH.encode())


class LoginThrottle:
    """In-process lockout after repeated failures for one username or client address.

    Deliberately simple: a single-VM deployment has one backend process, and the goal is
    to stop password guessing, not to build a distributed rate limiter.
    """

    def __init__(self, limit: int = 8, window_seconds: int = 300, lock_seconds: int = 300,
                 address_limit: int = 30):
        self.limit = limit
        # A whole office shares one address, so the per-address threshold must be far
        # above the per-account one: mistyping a password must not lock out colleagues.
        self.address_limit = address_limit
        self.window = window_seconds
        self.lock = lock_seconds
        self._failures: dict[str, list[float]] = {}
        self._locked: dict[str, float] = {}

    def _limit_for(self, key: str) -> int:
        return self.address_limit if key.startswith("ip:") else self.limit

    def locked_for(self, key: str) -> int:
        remaining = self._locked.get(key, 0) - time.monotonic()
        if remaining <= 0:
            self._locked.pop(key, None)
            return 0
        return int(remaining) + 1

    def record_failure(self, key: str) -> None:
        now = time.monotonic()
        attempts = [value for value in self._failures.get(key, []) if now - value < self.window]
        attempts.append(now)
        self._failures[key] = attempts
        if len(attempts) >= self._limit_for(key):
            self._locked[key] = now + self.lock
            self._failures.pop(key, None)

    def record_success(self, key: str) -> None:
        self._failures.pop(key, None)
        self._locked.pop(key, None)


login_throttle = LoginThrottle()


def create_token(user: User) -> str:
    settings = get_settings()
    if len(settings.secret_key) < 32:
        raise RuntimeError("SECRET_KEY kamida 32 belgidan iborat bo‘lishi kerak")
    payload = {
        "sub": user.id,
        "role": user.role.value,
        "ver": user.token_version,
        "exp": datetime.now(timezone.utc) + timedelta(minutes=settings.access_token_minutes),
    }
    return jwt.encode(payload, settings.secret_key, algorithm="HS256")


def current_user(token: str = Depends(oauth2), db: Session = Depends(get_db)) -> User:
    try:
        payload = jwt.decode(token, get_settings().secret_key, algorithms=["HS256"])
        user_id = payload.get("sub")
        token_version = payload.get("ver")
    except InvalidTokenError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Sessiya yaroqsiz yoki muddati tugagan")
    user = db.scalar(select(User).where(User.id == user_id))
    if not user or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Foydalanuvchi faol emas")
    if token_version != user.token_version:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Sessiya bekor qilingan. Qayta kiring")
    return user


def require_roles(*roles: Role):
    def dependency(user: User = Depends(current_user)) -> User:
        if user.role not in roles:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Ushbu amal uchun ruxsat yetarli emas")
        return user
    return dependency
