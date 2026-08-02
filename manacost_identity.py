"""OAuth Device Flow client used to link a Telegram user to Manacost ID."""

from __future__ import annotations

import re
import threading
from typing import Any
from urllib.parse import urljoin, urlparse

import requests

from config import MANACOST_PUBLIC_API_BASE_URL, MANACOST_PUBLIC_API_TIMEOUT


_API_ROOT = f"{MANACOST_PUBLIC_API_BASE_URL.rstrip('/')}/api/v1"
_CLIENT_ID = "manacost-tracker"
_SCOPES = "profile.read subscription.read"
_USER_CODE_RE = re.compile(r"^[A-Z2-9]{4}-[A-Z2-9]{4}$")
_thread_local = threading.local()


class ManacostIdentityError(RuntimeError):
    """Safe, user-displayable failure from the identity API."""

    def __init__(self, message: str, *, code: str = "identity_error"):
        super().__init__(message)
        self.code = code


class AuthorizationPending(ManacostIdentityError):
    def __init__(self):
        super().__init__(
            "Подтверждение ещё не получено.",
            code="authorization_pending",
        )


def _http_session() -> requests.Session:
    session = getattr(_thread_local, "session", None)
    if session is None:
        session = requests.Session()
        _thread_local.session = session
    return session


def _headers() -> dict[str, str]:
    return {
        "Accept": "application/json",
        "User-Agent": "DeckviewBot/1.0",
    }


def _json(response: requests.Response) -> dict[str, Any]:
    try:
        payload = response.json()
    except Exception as exc:
        raise ManacostIdentityError(
            "Manacost ID вернул ответ неверного формата."
        ) from exc
    if not isinstance(payload, dict):
        raise ManacostIdentityError("Manacost ID вернул ответ неверного формата.")
    return payload


def _same_origin_https_url(raw_url: object, *, allow_relative: bool = False) -> str:
    value = str(raw_url or "").strip()
    if allow_relative and value.startswith("/"):
        value = urljoin(f"{MANACOST_PUBLIC_API_BASE_URL.rstrip('/')}/", value)
    parsed = urlparse(value)
    expected = urlparse(MANACOST_PUBLIC_API_BASE_URL)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.hostname.lower() != str(expected.hostname or "").lower()
    ):
        raise ManacostIdentityError("Manacost ID вернул небезопасную ссылку.")
    return value


def start_device_authorization() -> dict[str, Any]:
    response = _http_session().post(
        f"{_API_ROOT}/oauth/device/code",
        data={"client_id": _CLIENT_ID, "scope": _SCOPES},
        headers=_headers(),
        timeout=MANACOST_PUBLIC_API_TIMEOUT,
    )
    payload = _json(response)
    if response.status_code >= 400:
        raise ManacostIdentityError(
            "Не удалось начать авторизацию Manacost ID. Попробуйте позже."
        )

    device_code = str(payload.get("device_code") or "")
    user_code = str(payload.get("user_code") or "")
    if not device_code or len(device_code) > 1024 or not _USER_CODE_RE.fullmatch(user_code):
        raise ManacostIdentityError("Manacost ID вернул неверный код авторизации.")
    expires_in = int(payload.get("expires_in") or 0)
    interval = int(payload.get("interval") or 0)
    if not 60 <= expires_in <= 1800 or not 5 <= interval <= 60:
        raise ManacostIdentityError("Manacost ID вернул неверный срок авторизации.")

    return {
        "device_code": device_code,
        "user_code": user_code,
        "verification_uri": _same_origin_https_url(payload.get("verification_uri")),
        "verification_uri_complete": _same_origin_https_url(
            payload.get("verification_uri_complete")
        ),
        "expires_in": expires_in,
        "interval": interval,
    }


def exchange_device_code(device_code: str) -> dict[str, Any]:
    code = str(device_code or "")
    if not code or len(code) > 1024:
        raise ManacostIdentityError("Код авторизации недействителен.")
    response = _http_session().post(
        f"{_API_ROOT}/oauth/token",
        data={
            "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
            "client_id": _CLIENT_ID,
            "device_code": code,
        },
        headers=_headers(),
        timeout=MANACOST_PUBLIC_API_TIMEOUT,
    )
    payload = _json(response)
    if response.status_code >= 400:
        error_code = str(payload.get("error") or "")
        if error_code == "authorization_pending":
            raise AuthorizationPending()
        messages = {
            "access_denied": "Авторизация отклонена на сайте Manacost.",
            "expired_token": "Код авторизации истёк. Начните вход заново.",
            "invalid_grant": "Код авторизации уже использован или истёк.",
            "slow_down": "Подождите несколько секунд и проверьте вход снова.",
        }
        raise ManacostIdentityError(
            messages.get(
                error_code,
                "Не удалось завершить авторизацию Manacost ID.",
            ),
            code=error_code or "token_error",
        )

    access_token = str(payload.get("access_token") or "")
    refresh_token = str(payload.get("refresh_token") or "")
    if (
        not access_token
        or len(access_token) > 4096
        or not refresh_token
        or len(refresh_token) > 4096
    ):
        raise ManacostIdentityError("Manacost ID не вернул токены авторизации.")
    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "expires_in": int(payload.get("expires_in") or 0),
        "scope": str(payload.get("scope") or ""),
    }


def get_authorized_profile(access_token: str) -> dict[str, Any]:
    token = str(access_token or "")
    if not token or len(token) > 4096:
        raise ManacostIdentityError("Токен Manacost ID недействителен.")
    response = _http_session().get(
        f"{_API_ROOT}/me",
        headers={**_headers(), "Authorization": f"Bearer {token}"},
        timeout=MANACOST_PUBLIC_API_TIMEOUT,
    )
    payload = _json(response)
    if response.status_code >= 400:
        raise ManacostIdentityError(
            "Не удалось получить профиль Manacost ID."
        )

    user = payload.get("user")
    subscription = payload.get("subscription")
    if not isinstance(user, dict) or not isinstance(subscription, dict):
        raise ManacostIdentityError("Manacost ID вернул неполный профиль.")
    manacost_user_id = str(user.get("id") or "").strip()
    public_profile_id = str(user.get("publicProfileId") or "").strip()
    name = str(user.get("name") or "").strip()
    if (
        not manacost_user_id
        or len(manacost_user_id) > 200
        or not public_profile_id
        or len(public_profile_id) > 200
        or not name
        or len(name) > 200
    ):
        raise ManacostIdentityError("Manacost ID вернул неполный профиль.")
    entitlements = subscription.get("entitlements")
    if not isinstance(entitlements, dict):
        entitlements = {}

    return {
        "manacost_user_id": manacost_user_id,
        "public_profile_id": public_profile_id,
        "profile_url": _same_origin_https_url(
            user.get("profileUrl"),
            allow_relative=True,
        ),
        "display_name": name,
        "has_access": bool(subscription.get("hasAccess")),
        "subscription_source": str(subscription.get("source") or "")[:100],
        "subscription_checked_at": (
            str(subscription.get("checkedAt") or "")[:80] or None
        ),
        "subscription_stale": bool(subscription.get("stale")),
        "entitlements": {
            str(key)[:100]: bool(value)
            for key, value in entitlements.items()
            if isinstance(key, str)
        },
    }


def revoke_refresh_token(refresh_token: str) -> None:
    """Best-effort revocation: Deckview never keeps OAuth credentials."""
    token = str(refresh_token or "")
    if not token or len(token) > 4096:
        return
    try:
        _http_session().post(
            f"{_API_ROOT}/oauth/revoke",
            data={"token": token},
            headers=_headers(),
            timeout=MANACOST_PUBLIC_API_TIMEOUT,
        )
    except Exception:
        pass
