"""Logging in with GitHub: setting off to approve on GitHub, coming back with a
code, the account each GitHub user logs in as, and asking GitHub again when a
session runs out."""

from __future__ import annotations

from dataclasses import dataclass, field
lazy import asyncio
lazy from collections.abc import Awaitable, Mapping
lazy from time import time_ns
lazy from typing import cast

from bazaar_contrib_http import redact
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Route
from starlette.types import ASGIApp
lazy from bazaar_contrib_server_access import (
    Access,
    Authorization,
    Confirmed,
    Refused,
    Unavailable,
)
lazy from bazaar_contrib_storage import (
    Column,
    Compare,
    Database,
    Insert,
    Select,
    Session,
    Update,
    Value,
)

lazy from .client import GitHub, Grant, User
lazy from .settings import Configured, configured

KEY = "github"


@dataclass
class GitHubLogin:
    access: Access
    database: Database
    github: GitHub
    icon: str

    app: ASGIApp = field(init=False)
    # GitHub takes a refresh token back once it is used: the requests that
    # find an account's access token out of date at the same time wait for one
    # refresh between them
    _refreshing: dict[str, asyncio.Future[str | None]] = field(
        init=False, default_factory=dict
    )

    key = KEY
    name = "github.name"
    description = "github.description"
    settings = "/ui/github/"

    def __post_init__(self) -> None:
        self.app = Starlette(
            routes=[Route("/", self._start), Route("/callback", self._callback)]
        )

    async def ready(self) -> bool:
        return await configured(self.database) is not None

    async def roles(self) -> set[str]:
        settings = await configured(self.database)
        return (
            {settings.default_role_id}
            if settings is not None and settings.default_role_id is not None
            else set()
        )

    async def renew(self, account_id: str) -> Confirmed | None:
        held = await self._held(account_id)
        settings = await configured(self.database)
        if held is None or settings is None:
            return None
        user = await self.github.user(cast(str, held["token"]))
        if user is None and held["refresh_token"] is not None:
            token = await self._refreshed(
                settings, account_id, cast(str, held["token"])
            )
            user = None if token is None else await self.github.user(token)
        if user is None or user.id != held["github_id"]:
            return None
        await self.database.write(
            lambda session: session.update(
                Update(
                    table="github_accounts",
                    values={
                        "login": Value(value=user.login),
                        "name": Value(value=user.name),
                        "avatar_url": Value(value=user.avatar),
                        "updated_at_ms": Value(value=time_ns() // 1_000_000),
                    },
                    where=_equals("github_id", user.id),
                )
            )
        )
        return Confirmed(account_id, user.name, user.avatar, settings.session_minutes)

    async def _held(self, account_id: str) -> Mapping[str, object] | None:
        rows = await self.database.read(
            lambda session: session.select(
                Select(table="github_accounts", where=_equals("account_id", account_id))
            )
        )
        return rows[0] if rows else None

    def _refreshed(
        self, settings: Configured, account_id: str, stale: str
    ) -> Awaitable[str | None]:
        """The access token in place of the stale one; nothing once GitHub
        takes the authorization back."""
        refreshing = self._refreshing.get(account_id)
        if refreshing is None:
            refreshing = self._refreshing[account_id] = asyncio.ensure_future(
                self._refresh(settings, account_id, stale)
            )
            refreshing.add_done_callback(
                lambda _: self._refreshing.pop(account_id, None)
            )
        # one waiting request given up leaves the refresh to the others
        return asyncio.shield(refreshing)

    async def _refresh(
        self, settings: Configured, account_id: str, stale: str
    ) -> str | None:
        held = await self._held(account_id)
        if held is None:
            return None
        # refreshed by a request before this one
        if held["token"] != stale:
            return cast(str, held["token"])
        try:
            grant = await self.github.refresh(
                settings.client_id,
                settings.client_secret,
                cast(str, held["refresh_token"]),
            )
        except Refused:
            return None
        await self.database.write(
            lambda session: session.update(
                Update(
                    table="github_accounts",
                    values={
                        "token": Value(value=grant.access),
                        "refresh_token": Value(value=grant.refresh),
                        "updated_at_ms": Value(value=time_ns() // 1_000_000),
                    },
                    where=_equals("account_id", account_id),
                )
            )
        )
        return grant.access

    async def _start(self, request: Request) -> Response:
        async def authorize(authorization: Authorization) -> str:
            settings = await configured(self.database)
            if settings is None:
                raise Unavailable
            return self.github.authorize(
                settings.client_id,
                settings.redirect_uri,
                authorization.state,
                authorization.challenge,
            )

        return await self.access.start(request, KEY, authorize)

    @redact("code", "state")
    async def _callback(self, request: Request) -> Response:
        return await self.access.finish(request, KEY, self._confirm)

    async def _confirm(
        self, authorization: Authorization, query: Mapping[str, str]
    ) -> Confirmed:
        # declined on GitHub, or nothing to redeem
        code = query.get("code", "")
        if "error" in query or not code:
            raise Refused
        settings = await configured(self.database)
        if settings is None:
            raise Unavailable
        grant = await self.github.token(
            settings.client_id,
            settings.client_secret,
            code,
            settings.redirect_uri,
            authorization.verifier,
        )
        user = await self.github.user(grant.access)
        if user is None:
            raise Refused
        email = await self.github.email(grant.access)

        async def remember(session: Session) -> str:
            return await self._remember(session, settings, user, email, grant)

        account_id = await self.database.transaction(remember)
        return Confirmed(account_id, user.name, user.avatar, settings.session_minutes)

    async def _remember(
        self,
        session: Session,
        settings: Configured,
        user: User,
        email: str | None,
        grant: Grant,
    ) -> str:
        """The account the GitHub user logs in as, made on the first login."""
        now = time_ns() // 1_000_000
        known = await session.select(
            Select(table="github_accounts", where=_equals("github_id", user.id))
        )
        profile = {
            "login": user.login,
            "name": user.name,
            "email": email,
            "avatar_url": user.avatar,
            "token": grant.access,
            "refresh_token": grant.refresh,
            "updated_at_ms": now,
        }
        if known:
            await session.update(
                Update(
                    table="github_accounts",
                    values={
                        name: Value(value=value) for name, value in profile.items()
                    },
                    where=_equals("github_id", user.id),
                )
            )
            return cast(str, known[0]["account_id"])
        account_id = await self.access.enroll(session, KEY, settings.default_role_id)
        await session.insert(
            Insert(
                table="github_accounts",
                rows=(
                    {
                        "github_id": user.id,
                        "account_id": account_id,
                        "created_at_ms": now,
                    }
                    | profile,
                ),
            )
        )
        return account_id


def _equals(column: str, value: str) -> Compare:
    return Compare(left=Column(name=column), operator="=", right=Value(value=value))
