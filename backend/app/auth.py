"""Who is asking. Three kinds of caller, each with its own credential:

- an operator (a person in the console): email + password, then a session cookie
- a business system: the X-API-Key header
- a callee: the unguessable token in the link they were sent

This module handles the first; the API key and callee token are checked where used.
"""

import asyncio
import time
from typing import Any
from urllib.parse import urlsplit

from sqlalchemy.exc import IntegrityError
from starlette.requests import Request

from app.config import Settings
from app.db import Repository
from app.security import DUMMY_HASH, hash_password, hash_token, new_token, verify_password

COOKIE = "va_session"
ROLES = ("admin", "operator")
UNSAFE_METHODS = ("POST", "PUT", "PATCH", "DELETE")


class EmailTaken(Exception):
    """Sign-up with an email that already has an account."""


def public_user(user: dict[str, Any]) -> dict[str, Any]:
    # None for a user that predates multi-tenancy (or the synthetic anonymous admin used when
    # AUTH_REQUIRED=false, which has no row at all): the frontend treats that the same as "no
    # organization yet", not as an error.
    return {"id": user["id"], "email": user["email"], "role": user["role"], "organization_id": user.get("organization_id")}


class Auth:
    def __init__(self, repo: Repository, settings: Settings) -> None:
        self.repo = repo
        self.settings = settings

    async def _db(self, fn, *args, **kwargs):
        return await asyncio.to_thread(fn, *args, **kwargs)

    # --- users -------------------------------------------------------------

    def create_user(self, email: str, password: str, role: str = "operator") -> dict[str, Any]:
        if role not in ROLES:
            raise ValueError(f"role must be one of {', '.join(ROLES)}")
        if "@" not in email or len(email) > 254:
            raise ValueError("That does not look like an email address")

        return self.repo.create_user(email, hash_password(password), role)

    async def register(self, email: str, password: str, workspace_name: str) -> dict[str, Any]:
        """Self-service sign-up: a new operator and a workspace (organization) of their own.

        Always the "operator" role: administrators can edit customer data that is shared between
        organizations, so that power is never handed out by a public form. Raises ValueError for
        input we refuse (message fit to show) and EmailTaken for a duplicate."""
        email = email.strip()
        workspace_name = " ".join(workspace_name.split())

        if "@" not in email or len(email) > 254:
            raise ValueError("That does not look like an email address")
        if not workspace_name:
            raise ValueError("Give your workspace a name")

        # hash_password enforces the password policy (raises ValueError with the reason); scrypt is
        # deliberately slow, so it runs off the event loop like verify_password does.
        password_hash = await asyncio.to_thread(hash_password, password)

        try:
            return await self._db(self.repo.create_account, email, password_hash, "operator", workspace_name)
        except IntegrityError:
            raise EmailTaken(email) from None

    async def authenticate(self, email: str, password: str) -> dict[str, Any] | None:
        user = await self._db(self.repo.get_user_by_email, email)
        usable = user is not None and not user["disabled"]

        # Always do the same work, so an unknown email is not faster than a wrong password.
        ok = await asyncio.to_thread(verify_password, password, user["password_hash"] if usable else DUMMY_HASH)

        return user if ok and usable else None

    async def start_session(self, user: dict[str, Any]) -> str:
        token = new_token()
        expires = time.time() + self.settings.auth_session_hours * 3600
        await self._db(self.repo.create_auth_session, hash_token(token), user["id"], expires)
        return token

    async def end_session(self, token: str | None) -> None:
        if token:
            await self._db(self.repo.delete_auth_session, hash_token(token))

    async def user_for_request(self, request: Request) -> dict[str, Any] | None:
        token = request.cookies.get(COOKIE)

        if not token:
            return None

        session = await self._db(self.repo.get_auth_session, hash_token(token))

        if session is None:
            return None

        if session["expires_at"] <= time.time():
            await self._db(self.repo.delete_auth_session, hash_token(token))
            return None

        user = await self._db(self.repo.get_user, session["user_id"])

        # A disabled user is signed out immediately, not when the cookie expires.
        return user if user and not user["disabled"] else None

    # --- request hygiene -----------------------------------------------------

    def allowed_origins(self) -> set[str]:
        base = urlsplit(self.settings.public_base_url)
        origins = set(self.settings.cors_origins)
        origins.add(f"{base.scheme}://{base.netloc}")
        return origins

    def origin_ok(self, request: Request) -> bool:
        """Cookie-authenticated writes must come from our own pages. (SameSite=Lax
        already stops cross-site POSTs; this is the second lock on the same door.)"""
        if request.method not in UNSAFE_METHODS:
            return True

        origin = request.headers.get("origin")
        return origin is None or origin in self.allowed_origins()

    def client_ip(self, request: Request) -> str:
        hops = self.settings.trusted_proxy_hops
        forwarded = request.headers.get("x-forwarded-for")

        if hops > 0 and forwarded:
            parts = [part.strip() for part in forwarded.split(",") if part.strip()]

            # Only the entries our own proxies appended can be trusted, counting
            # from the right. Anything further left was written by the client.
            if len(parts) >= hops:
                return parts[-hops]

        return request.client.host if request.client else "unknown"
