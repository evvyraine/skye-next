from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import time
from typing import Any
from urllib.parse import urlencode

import httpx
import jwt
import structlog
from jwt import PyJWKClient

from .config import Settings
from .db import Database
from .models import WebSession
from .projects import ProjectService

log = structlog.get_logger()

TELEGRAM_ISSUER = "https://oauth.telegram.org"
TELEGRAM_AUTH = "https://oauth.telegram.org/auth"
TELEGRAM_TOKEN = "https://oauth.telegram.org/token"
TELEGRAM_JWKS = "https://oauth.telegram.org/.well-known/jwks.json"
COOKIE_NAME = "skye_session"
OIDC_COOKIE = "skye_oidc"
SESSION_COOKIE = COOKIE_NAME


class AuthError(Exception):
    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status = status


class TelegramAuth:
    def __init__(self, config: Settings, database: Database, projects: ProjectService) -> None:
        self.config = config
        self.database = database
        self.projects = projects
        self._jwks = PyJWKClient(TELEGRAM_JWKS, cache_keys=True)

    @property
    def configured(self) -> bool:
        return self.config.web_enabled

    def login_url(self, origin: str) -> tuple[str, str]:
        if not self.config.telegram_login_client_id or not self.config.telegram_login_client_secret:
            raise AuthError("Telegram login is not configured.", 503)
        state = secrets.token_urlsafe(24)
        verifier = secrets.token_urlsafe(48)
        challenge = (
            base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
            .rstrip(b"=")
            .decode()
        )
        params = {
            "client_id": self.config.telegram_login_client_id,
            "redirect_uri": f"{origin.rstrip('/')}/auth/callback",
            "response_type": "code",
            "scope": "openid profile",
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
        return f"{TELEGRAM_AUTH}?{urlencode(params)}", self._pack_oidc(state, verifier)

    def parse_oidc(self, packed: str | None) -> tuple[str, str]:
        if not packed:
            raise AuthError("Login expired. Try again.")
        try:
            state, verifier, expires, signature = packed.split(".", 3)
        except ValueError as error:
            raise AuthError("Login expired. Try again.") from error
        expected = self._sign(f"{state}.{verifier}.{expires}")
        if not hmac.compare_digest(expected, signature):
            raise AuthError("Login expired. Try again.")
        if int(expires) < int(time.time()):
            raise AuthError("Login expired. Try again.")
        return state, verifier

    async def finish(
        self, origin: str, code: str, state: str, packed: str | None
    ) -> WebSession:
        saved_state, verifier = self.parse_oidc(packed)
        if not hmac.compare_digest(saved_state, state):
            raise AuthError("Login expired. Try again.")
        token = await self._exchange(origin, code, verifier)
        claims = self.verify_id_token(token)
        user_id = int(claims["id"])
        name = str(claims.get("name") or claims.get("given_name") or "User")
        username = claims.get("preferred_username")
        username = str(username) if isinstance(username, str) and username else None
        return await self.projects.create_session(user_id, name, username)

    def verify_id_token(self, token: str) -> dict[str, Any]:
        signing = self._jwks.get_signing_key_from_jwt(token)
        claims = jwt.decode(
            token,
            signing.key,
            algorithms=["RS256", "ES256"],
            audience=self.config.telegram_login_client_id,
            issuer=TELEGRAM_ISSUER,
        )
        if "id" not in claims:
            raise AuthError("Telegram login did not return a user id.")
        return claims

    async def session(self, session_id: str | None) -> WebSession | None:
        if not session_id:
            return None
        return await self.database.web_session(session_id)

    async def logout(self, session_id: str | None) -> None:
        if session_id:
            await self.database.delete_web_session(session_id)

    def cookie_kwargs(self) -> dict[str, Any]:
        return self.projects.session_cookie(self.config.skye_web_origin)

    async def _exchange(self, origin: str, code: str, verifier: str) -> str:
        if not self.config.telegram_login_client_id or not self.config.telegram_login_client_secret:
            raise AuthError("Telegram login is not configured.", 503)
        auth = httpx.BasicAuth(
            self.config.telegram_login_client_id, self.config.telegram_login_client_secret
        )
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post(
                TELEGRAM_TOKEN,
                auth=auth,
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": f"{origin.rstrip('/')}/auth/callback",
                    "client_id": self.config.telegram_login_client_id,
                    "code_verifier": verifier,
                },
            )
        if response.status_code >= 400:
            log.warning("telegram_oidc_token_failed", status=response.status_code)
            raise AuthError("Telegram login failed.")
        payload = response.json()
        token = payload.get("id_token")
        if not isinstance(token, str) or not token:
            raise AuthError("Telegram login failed.")
        return token

    def _pack_oidc(self, state: str, verifier: str) -> str:
        expires = str(int(time.time()) + 600)
        payload = f"{state}.{verifier}.{expires}"
        return f"{payload}.{self._sign(payload)}"

    def _sign(self, payload: str) -> str:
        secret = (
            self.config.telegram_login_client_secret or self.config.telegram_bot_token
        ).encode()
        return hmac.new(secret, payload.encode(), hashlib.sha256).hexdigest()
