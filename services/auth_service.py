"""
Registration, login, and JWT validation for per-user features.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional, Tuple

import jwt
from werkzeug.security import check_password_hash, generate_password_hash

from config import JWT_EXPIRY_DAYS, JWT_SECRET
from logger import get_logger
from services.user_store import UserStore

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_MIN_PASSWORD_LEN = 8


class AuthService:
    def __init__(self, user_store: UserStore = None):
        self.store = user_store or UserStore()
        self.logger = get_logger()

    @staticmethod
    def normalize_email(email: str) -> str:
        return (email or "").strip().lower()

    def validate_email(self, email: str) -> bool:
        e = self.normalize_email(email)
        return bool(e) and bool(_EMAIL_RE.match(e))

    def validate_password(self, password: str) -> bool:
        return isinstance(password, str) and len(password) >= _MIN_PASSWORD_LEN

    def register(self, email: str, password: str) -> Tuple[Dict[str, Any], str]:
        e = self.normalize_email(email)
        if not self.validate_email(e):
            raise ValueError("Invalid email address.")
        if not self.validate_password(password):
            raise ValueError("Password must be at least 8 characters.")
        ph = generate_password_hash(password)
        user = self.store.create_user(e, ph)
        token = self.create_token(user["id"], user["email"])
        return user, token

    def login(self, email: str, password: str) -> Tuple[Dict[str, Any], str]:
        e = self.normalize_email(email)
        row = self.store.get_user_by_email(e)
        if not row or not check_password_hash(row["password_hash"], password):
            raise ValueError("Incorrect email or password.")
        user = {"id": row["id"], "email": row["email"], "created_at": row["created_at"]}
        token = self.create_token(user["id"], user["email"])
        return user, token

    def create_token(self, user_id: str, email: str) -> str:
        now = datetime.now(timezone.utc)
        payload = {
            "sub": user_id,
            "email": email,
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(days=JWT_EXPIRY_DAYS)).timestamp()),
        }
        return jwt.encode(payload, JWT_SECRET, algorithm="HS256")

    def decode_token(self, token: str) -> Optional[Dict[str, Any]]:
        if not token or not isinstance(token, str):
            return None
        try:
            return jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
        except jwt.PyJWTError:
            return None

    def user_from_authorization_header(self, auth_header: Optional[str]) -> Optional[Dict[str, str]]:
        if not auth_header or not isinstance(auth_header, str):
            return None
        parts = auth_header.split()
        if len(parts) != 2 or parts[0].lower() != "bearer":
            return None
        payload = self.decode_token(parts[1].strip())
        if not payload or not payload.get("sub"):
            return None
        uid = str(payload["sub"])
        email = str(payload.get("email") or "")
        return {"id": uid, "email": email}
