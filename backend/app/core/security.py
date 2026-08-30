from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime

from fastapi import Header, HTTPException, Request, status


@dataclass(frozen=True, slots=True)
class SignedConversationToken:
    session_id: str
    expires_at: datetime
    raw_token: str
    token_sha256: str


class ConversationTokenSigner:
    """Issues opaque, signed owner tokens without storing bearer secrets in plaintext."""

    def __init__(self, secret: str) -> None:
        if len(secret) < 24:
            raise ValueError("Conversation token secret must be at least 24 characters")
        self._secret = secret.encode("utf-8")

    def issue(self, *, session_id: str, expires_at: datetime) -> SignedConversationToken:
        expiry = int(expires_at.timestamp())
        nonce = secrets.token_urlsafe(24)
        payload = f"{session_id}.{expiry}.{nonce}"
        signature = hmac.new(self._secret, payload.encode("utf-8"), hashlib.sha256).hexdigest()
        raw_token = f"{payload}.{signature}"
        return SignedConversationToken(
            session_id=session_id,
            expires_at=expires_at,
            raw_token=raw_token,
            token_sha256=hashlib.sha256(raw_token.encode("utf-8")).hexdigest(),
        )

    def verify(self, token: str, *, now: datetime | None = None) -> tuple[str, datetime] | None:
        try:
            session_id, expiry_text, nonce, signature = token.split(".", 3)
            expiry = int(expiry_text)
        except (TypeError, ValueError):
            return None
        payload = f"{session_id}.{expiry}.{nonce}"
        expected = hmac.new(self._secret, payload.encode("utf-8"), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected):
            return None
        expires_at = datetime.fromtimestamp(expiry, tz=UTC)
        if expires_at <= (now or datetime.now(UTC)):
            return None
        return session_id, expires_at


def conversation_cookie_name(conversation_id: str) -> str:
    return f"gursar_owner_{conversation_id}"


def valid_public_access_credentials(
    authorization: str | None,
    *,
    expected_username: str,
    expected_password: str,
) -> bool:
    if not authorization or not authorization.startswith("Basic "):
        return False
    try:
        decoded = base64.b64decode(authorization[6:], validate=True).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError):
        return False
    username, separator, password = decoded.partition(":")
    return (
        bool(separator)
        and hmac.compare_digest(
            username,
            expected_username,
        )
        and hmac.compare_digest(password, expected_password)
    )


def require_admin_key(
    request: Request,
    x_admin_key: str | None = Header(default=None),
) -> None:
    expected: str = request.app.state.settings.admin_api_key
    if not x_admin_key or not hmac.compare_digest(x_admin_key, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Yonetici yetkilendirmesi gerekli.",
        )


@dataclass(frozen=True, slots=True)
class ReviewPrincipal:
    reviewer_id: str
    role: str


def require_review_permission(
    request: Request,
    x_admin_key: str | None = Header(default=None),
    x_reviewer_id: str | None = Header(default=None),
    x_reviewer_role: str | None = Header(default=None),
) -> ReviewPrincipal:
    require_admin_key(request, x_admin_key)
    role = (x_reviewer_role or "").strip().upper()
    if role not in {"ENGINEER", "ADMIN", "REVIEWER"}:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="ENGINEER, ADMIN veya REVIEWER rolü gerekli.",
        )
    reviewer_id = (x_reviewer_id or "").strip()
    if not reviewer_id:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="X-Reviewer-Id gerekli.",
        )
    return ReviewPrincipal(reviewer_id=reviewer_id, role=role)
