"""GitHub as a login sees it: where a person approves the login, and what the
site answers for a code and its API for a token."""

from __future__ import annotations

from dataclasses import dataclass
lazy from typing import Any
lazy from urllib.parse import urlencode

lazy import aiohttp
lazy from bazaar_contrib_server_access import Refused, Unavailable

# the API version the answers below are read as
_API = {
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
}


@dataclass(frozen=True, slots=True)
class Grant:
    """What GitHub hands over for a code or a refresh token: the access token
    and, when the app's tokens expire, the token that renews it."""

    access: str
    refresh: str | None


@dataclass(frozen=True, slots=True)
class User:
    id: str
    login: str
    # the profile's name, or the login when it has none
    name: str
    avatar: str | None


class GitHub:
    """github.com and its API, at the addresses it is given."""

    def __init__(self, session: aiohttp.ClientSession, site: str, api: str) -> None:
        self._session = session
        self._site = site
        self._api = api

    def authorize(
        self, client_id: str, redirect_uri: str, state: str, challenge: str
    ) -> str:
        return f"{self._site}/login/oauth/authorize?" + urlencode(
            {
                "client_id": client_id,
                "redirect_uri": redirect_uri,
                "scope": "read:user user:email",
                "state": state,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
            }
        )

    async def token(
        self,
        client_id: str,
        client_secret: str,
        code: str,
        redirect_uri: str,
        verifier: str,
    ) -> Grant:
        return await self._grant(
            {
                "client_id": client_id,
                "client_secret": client_secret,
                "code": code,
                "redirect_uri": redirect_uri,
                "code_verifier": verifier,
            }
        )

    async def refresh(self, client_id: str, client_secret: str, refresh: str) -> Grant:
        """A new access token, and a new refresh token in place of this one."""
        return await self._grant(
            {
                "client_id": client_id,
                "client_secret": client_secret,
                "grant_type": "refresh_token",
                "refresh_token": refresh,
            }
        )

    async def _grant(self, data: dict[str, str]) -> Grant:
        try:
            async with self._session.post(
                f"{self._site}/login/oauth/access_token",
                data=data,
                headers={"Accept": "application/json"},
            ) as response:
                if response.status >= 500:
                    raise Unavailable
                answer = await response.json(content_type=None)
        except aiohttp.ClientError, ValueError:
            raise Unavailable from None
        # what is wrong, used or expired is answered 200 with an error
        access = answer.get("access_token") if isinstance(answer, dict) else None
        if not isinstance(access, str) or not access:
            raise Refused
        refresh = answer.get("refresh_token")
        return Grant(access, refresh if isinstance(refresh, str) and refresh else None)

    async def user(self, token: str) -> User | None:
        """Whom the token is for; nothing once it no longer holds."""
        answer = await self._get("/user", token)
        if answer is None:
            return None
        login = str(answer["login"])
        return User(
            id=str(answer["id"]),
            login=login,
            name=str(answer.get("name") or login),
            avatar=answer.get("avatar_url") or None,
        )

    async def email(self, token: str) -> str | None:
        """The primary address, once verified."""
        answer = await self._get("/user/emails", token)
        return next(
            (
                str(entry["email"])
                for entry in answer or ()
                if entry.get("primary") and entry.get("verified")
            ),
            None,
        )

    async def _get(self, path: str, token: str) -> Any:
        try:
            async with self._session.get(
                f"{self._api}{path}",
                headers=_API | {"Authorization": f"Bearer {token}"},
            ) as response:
                if response.status == 401:
                    return None
                if response.status != 200:
                    raise Unavailable
                return await response.json()
        except aiohttp.ClientError, ValueError:
            raise Unavailable from None
