from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import httpx
import keyring
from keyring.errors import KeyringError, PasswordDeleteError

from atopile.auth.runtime import GATEWAY_BASE_URL
from faebryk.libs.http import http_client
from faebryk.libs.paths import get_config_dir

CLERK_ISSUER = "https://clerk.atopile.io"
CLERK_CLIENT_ID = "ToL0oSxCjE1hzOMN"
CLERK_DISCOVERY_URL = f"{CLERK_ISSUER}/.well-known/oauth-authorization-server"
CLERK_SCOPES = ("openid", "profile", "email")
POLL_REDIRECT_URI = f"{GATEWAY_BASE_URL}/oauth/callback/openvscode-server"
REFRESH_BEFORE_EXPIRY_MS = 10_000
DEFAULT_LOGIN_TIMEOUT_SECONDS = 120
WEB_POLL_INTERVAL_SECONDS = 1.5
KEYRING_SERVICE_NAME = "atopile.clerkOauth"
KEYRING_ACCOUNT_NAME = "default"
FILE_SESSION_NAME = "clerk_oauth_session.json"


class AuthError(RuntimeError):
    pass


class KeyringUnavailableError(AuthError):
    pass


@dataclass(slots=True)
class AuthUser:
    id: str
    name: str
    email: str | None = None
    image_url: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AuthUser":
        return cls(
            id=str(data["id"]),
            name=str(data["name"]),
            email=str(data["email"]) if data.get("email") else None,
            image_url=str(data["image_url"]) if data.get("image_url") else None,
        )

    def display_name(self) -> str:
        return self.email or self.name or self.id


@dataclass(slots=True)
class OAuthDiscovery:
    authorization_endpoint: str
    token_endpoint: str


@dataclass(slots=True)
class OAuthCallbackResult:
    code: str | None
    state: str | None
    error: str | None
    error_description: str | None


@dataclass(slots=True)
class ClerkSession:
    access_token: str
    refresh_token: str
    id_token: str | None = None
    user: AuthUser | None = None

    def to_json(self) -> str:
        payload = asdict(self)
        return json.dumps(payload)

    @classmethod
    def from_json(cls, raw: str) -> "ClerkSession":
        payload = json.loads(raw)
        user_payload = payload.get("user")
        user = (
            AuthUser.from_dict(user_payload) if isinstance(user_payload, dict) else None
        )
        return cls(
            access_token=str(payload["access_token"]),
            refresh_token=str(payload["refresh_token"]),
            id_token=str(payload["id_token"]) if payload.get("id_token") else None,
            user=user,
        )


def create_session_id() -> str:
    return secrets.token_urlsafe(16)


def create_oauth_state(session_id: str) -> str:
    return f"{secrets.token_urlsafe(32)}.{session_id}"


def create_pkce_code_verifier() -> str:
    return secrets.token_urlsafe(64)


def create_pkce_code_challenge(code_verifier: str) -> str:
    digest = hashlib.sha256(code_verifier.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def decode_jwt_payload(token: str) -> dict[str, Any] | None:
    parts = token.split(".")
    if len(parts) < 2:
        return None

    padded = parts[1] + "=" * (-len(parts[1]) % 4)
    try:
        payload = base64.urlsafe_b64decode(padded.encode("ascii"))
        decoded = json.loads(payload)
    except UnicodeDecodeError, ValueError, json.JSONDecodeError:
        return None

    return decoded if isinstance(decoded, dict) else None


def jwt_exp_ms(token: str) -> int | None:
    payload = decode_jwt_payload(token)
    value = payload.get("exp") if payload else None
    return int(value * 1000) if isinstance(value, int | float) else None


def is_token_expiring_soon(token: str) -> bool:
    exp = jwt_exp_ms(token)
    return exp is not None and exp - int(time.time() * 1000) <= REFRESH_BEFORE_EXPIRY_MS


def user_from_id_token(
    id_token: str | None, previous: AuthUser | None = None
) -> AuthUser | None:
    if not id_token:
        return previous

    payload = decode_jwt_payload(id_token)
    if not payload:
        return previous

    subject = payload.get("sub")
    if not isinstance(subject, str) or not subject.strip():
        return previous

    name = next(
        (
            candidate
            for candidate in (payload.get("name"), payload.get("email"), subject)
            if isinstance(candidate, str) and candidate.strip()
        ),
        subject,
    )

    email = payload.get("email")
    image_url = payload.get("picture")

    return AuthUser(
        id=subject,
        name=name,
        email=email if isinstance(email, str) else previous.email if previous else None,
        image_url=(
            image_url
            if isinstance(image_url, str)
            else previous.image_url
            if previous
            else None
        ),
    )


def fetch_discovery(client: httpx.Client) -> OAuthDiscovery:
    response = client.get(CLERK_DISCOVERY_URL)
    if not response.is_success:
        raise AuthError(f"OAuth discovery failed ({response.status_code})")

    payload = response.json()
    if not isinstance(payload, dict):
        raise AuthError("OAuth discovery returned an invalid payload")

    authorization_endpoint = payload.get("authorization_endpoint")
    token_endpoint = payload.get("token_endpoint")
    if not isinstance(authorization_endpoint, str) or not isinstance(
        token_endpoint, str
    ):
        raise AuthError("OAuth discovery payload is missing required endpoints")

    return OAuthDiscovery(
        authorization_endpoint=authorization_endpoint,
        token_endpoint=token_endpoint,
    )


def build_authorization_url(
    discovery: OAuthDiscovery, code_challenge: str, state: str
) -> str:
    url = httpx.URL(discovery.authorization_endpoint)
    return str(
        url.copy_merge_params(
            {
                "client_id": CLERK_CLIENT_ID,
                "response_type": "code",
                "redirect_uri": POLL_REDIRECT_URI,
                "scope": " ".join(CLERK_SCOPES),
                "state": state,
                "code_challenge": code_challenge,
                "code_challenge_method": "S256",
            }
        )
    )


def post_token_request(
    client: httpx.Client, token_endpoint: str, body: dict[str, str]
) -> dict[str, Any]:
    response = client.post(
        token_endpoint,
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
        },
        content=str(httpx.QueryParams(body)),
    )
    if not response.is_success:
        raise AuthError(
            f"Token request failed ({response.status_code}): {response.text}"
        )

    payload = response.json()
    if not isinstance(payload, dict) or not isinstance(
        payload.get("access_token"), str
    ):
        raise AuthError("Token response missing access_token")

    return payload


def exchange_oauth_code(
    client: httpx.Client,
    token_endpoint: str,
    authorization_code: str,
    code_verifier: str,
) -> ClerkSession:
    tokens = post_token_request(
        client,
        token_endpoint,
        {
            "grant_type": "authorization_code",
            "client_id": CLERK_CLIENT_ID,
            "redirect_uri": POLL_REDIRECT_URI,
            "code": authorization_code,
            "code_verifier": code_verifier,
        },
    )
    refresh_token = tokens.get("refresh_token")
    if not isinstance(refresh_token, str) or not refresh_token:
        raise AuthError("Token exchange did not return a refresh token")

    id_token = tokens.get("id_token")
    id_token_str = id_token if isinstance(id_token, str) else None
    return ClerkSession(
        access_token=tokens["access_token"],
        refresh_token=refresh_token,
        id_token=id_token_str,
        user=user_from_id_token(id_token_str),
    )


def refresh_session(client: httpx.Client, session: ClerkSession) -> ClerkSession:
    tokens = post_token_request(
        client,
        fetch_discovery(client).token_endpoint,
        {
            "grant_type": "refresh_token",
            "client_id": CLERK_CLIENT_ID,
            "refresh_token": session.refresh_token,
        },
    )

    next_refresh = tokens.get("refresh_token")
    next_id_token = tokens.get("id_token")
    return ClerkSession(
        access_token=tokens["access_token"],
        refresh_token=(
            next_refresh if isinstance(next_refresh, str) else session.refresh_token
        ),
        id_token=next_id_token if isinstance(next_id_token, str) else session.id_token,
        user=user_from_id_token(
            next_id_token if isinstance(next_id_token, str) else session.id_token,
            session.user,
        ),
    )


def poll_for_oauth_result(
    client: httpx.Client,
    session_id: str,
    timeout_seconds: float = DEFAULT_LOGIN_TIMEOUT_SECONDS,
) -> OAuthCallbackResult | None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            response = client.get(f"{GATEWAY_BASE_URL}/oauth/web-result/{session_id}")
        except httpx.HTTPError:
            time.sleep(WEB_POLL_INTERVAL_SECONDS)
            continue

        if response.status_code == 404:
            time.sleep(WEB_POLL_INTERVAL_SECONDS)
            continue
        if not response.is_success:
            raise AuthError(
                f"OAuth polling failed ({response.status_code}): {response.text}"
            )

        payload = response.json()
        if not isinstance(payload, dict):
            raise AuthError("OAuth polling returned an invalid payload")
        if payload.get("status") != "ready":
            time.sleep(WEB_POLL_INTERVAL_SECONDS)
            continue

        return OAuthCallbackResult(
            code=payload.get("code") if isinstance(payload.get("code"), str) else None,
            state=(
                payload.get("state") if isinstance(payload.get("state"), str) else None
            ),
            error=(
                payload.get("error") if isinstance(payload.get("error"), str) else None
            ),
            error_description=(
                payload.get("error_description")
                if isinstance(payload.get("error_description"), str)
                else None
            ),
        )

    return None


def ensure_keyring_available() -> None:
    if _storage_backend() != "keyring":
        raise KeyringUnavailableError(
            "No secure system keychain backend is available for `ato auth login`."
        )


def _storage_backend() -> str:
    backend = keyring.get_keyring()
    module_name = backend.__class__.__module__
    if module_name.startswith("keyring.backends.fail") or module_name.startswith(
        "keyring.backends.null"
    ):
        return "file"
    return "keyring"


def storage_backend_name() -> str:
    return _storage_backend()


def _file_session_path() -> Path:
    return get_config_dir() / FILE_SESSION_NAME


def _read_file_session() -> ClerkSession | None:
    path = _file_session_path()
    if not path.exists():
        return None

    try:
        return ClerkSession.from_json(path.read_text())
    except OSError, KeyError, TypeError, ValueError, json.JSONDecodeError:
        _clear_file_session()
        return None


def _write_file_session(session: ClerkSession) -> None:
    path = _file_session_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(session.to_json())
    os.chmod(path, 0o600)


def _clear_file_session() -> None:
    try:
        _file_session_path().unlink(missing_ok=True)
    except OSError:
        return


def load_stored_session() -> ClerkSession | None:
    if _storage_backend() == "file":
        return _read_file_session()

    try:
        raw = keyring.get_password(KEYRING_SERVICE_NAME, KEYRING_ACCOUNT_NAME)
    except KeyringError:
        return None

    if not raw:
        return None

    try:
        return ClerkSession.from_json(raw)
    except KeyError, TypeError, ValueError, json.JSONDecodeError:
        clear_stored_session()
        return None


def store_session(session: ClerkSession) -> None:
    if _storage_backend() == "file":
        _write_file_session(session)
        return

    try:
        keyring.set_password(
            KEYRING_SERVICE_NAME, KEYRING_ACCOUNT_NAME, session.to_json()
        )
    except KeyringError as exc:
        raise KeyringUnavailableError(
            "Unable to store the atopile auth session in the system keychain."
        ) from exc


def clear_stored_session() -> None:
    if _storage_backend() == "file":
        _clear_file_session()
        return

    try:
        keyring.delete_password(KEYRING_SERVICE_NAME, KEYRING_ACCOUNT_NAME)
    except KeyringError, PasswordDeleteError:
        return


def get_valid_stored_session() -> ClerkSession | None:
    session = load_stored_session()
    if session is None:
        return None
    if not is_token_expiring_soon(session.access_token):
        return session

    try:
        with http_client() as client:
            refreshed = refresh_session(client, session)
        store_session(refreshed)
    except AuthError, KeyringUnavailableError:
        clear_stored_session()
        return None

    return refreshed


def get_stored_access_token() -> str | None:
    session = get_valid_stored_session()
    return session.access_token if session else None
