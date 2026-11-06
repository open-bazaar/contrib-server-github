"""The GitHub login's settings: the OAuth App it logs in through, the role it
gives the people logging in for the first time and how long their sessions
last, kept in one row and set on its own page inside the login methods part
of the settings page."""

from __future__ import annotations

from dataclasses import dataclass
lazy from time import time_ns
lazy from typing import Annotated, cast
lazy from urllib.parse import urlsplit

lazy from bazaar_contrib_server import Account, Pages
lazy from bazaar_contrib_server_access import METHODS, Access
lazy from bazaar_contrib_storage import (
    Column,
    Compare,
    Conflict,
    Database,
    Insert,
    Select,
    Value,
)
lazy from pydantic import (
    BaseModel,
    BeforeValidator,
    Field,
    StringConstraints,
    ValidationError,
    field_validator,
)
lazy from starlette.exceptions import HTTPException
lazy from starlette.requests import Request
lazy from starlette.responses import RedirectResponse, Response
lazy from starlette.routing import Route

# where GitHub sends the browser back to, under the address the page is seen at
CALLBACK = "/login/github/callback"


@dataclass(frozen=True, slots=True)
class Configured:
    client_id: str
    client_secret: str
    redirect_uri: str
    # nothing for the global default role, and for as long as a password login
    default_role_id: str | None
    session_minutes: int | None


async def configured(database: Database) -> Configured | None:
    rows = await database.read(
        lambda session: session.select(
            Select(
                table="github_settings",
                where=Compare(
                    left=Column(name="id"), operator="=", right=Value(value=1)
                ),
            )
        )
    )
    if not rows:
        return None
    row = rows[0]
    return Configured(
        client_id=cast(str, row["client_id"]),
        client_secret=cast(str, row["client_secret"]),
        redirect_uri=cast(str, row["redirect_uri"]),
        default_role_id=cast(str | None, row["default_role_id"]),
        session_minutes=cast(int | None, row["session_minutes"]),
    )


def _blank(value: object) -> object:
    return None if value == "" else value


type _Text = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)
]


class _Given(BaseModel):
    client_id: _Text
    # left empty, the secret saved before stays
    client_secret: Annotated[_Text | None, BeforeValidator(_blank)] = None
    default_role: Annotated[str | None, BeforeValidator(_blank)] = None
    session_minutes: Annotated[
        Annotated[int, Field(ge=1)] | None, BeforeValidator(_blank)
    ] = None
    redirect_uri: str = Field(max_length=2048)

    @field_validator("redirect_uri")
    @classmethod
    def callback(cls, value: str) -> str:
        parts = urlsplit(value)
        if parts.scheme not in ("http", "https") or not parts.netloc:
            raise ValueError("redirect_uri is an address of this site")
        if parts.path != CALLBACK or parts.query or parts.fragment:
            raise ValueError("redirect_uri is the GitHub login's callback")
        return value


class Settings:
    def __init__(self, pages: Pages, access: Access, database: Database) -> None:
        self._pages = pages
        self._access = access
        self._database = database
        self.app = pages.application(
            [Route("/", self._page), Route("/", self._save, methods=["POST"])]
        )

    async def _page(
        self, request: Request, *, error: bool = False, status_code: int = 200
    ) -> Response:
        _allow(request)
        return await self._pages.render(
            request,
            "github/settings.html",
            status_code=status_code,
            configured=await configured(self._database),
            roles=await self._access.roles(),
            default=await self._access.default_role(),
            minutes=self._access.minutes,
            callback=CALLBACK,
            saved="saved" in request.query_params,
            error=error,
        )

    async def _save(self, request: Request) -> Response:
        _allow(request)
        form = await request.form()
        try:
            given = _Given.model_validate(
                {name: form.get(name, "") for name in _Given.model_fields}
            )
        except ValidationError:
            return await self._page(request, error=True, status_code=422)
        stored = await configured(self._database)
        secret = given.client_secret or (
            None if stored is None else stored.client_secret
        )
        roles = {role.id for role in await self._access.roles()}
        if secret is None or given.default_role not in roles | {None}:
            return await self._page(request, error=True, status_code=422)
        await self._database.write(
            lambda session: session.insert(
                Insert(
                    table="github_settings",
                    rows=(
                        {
                            "id": 1,
                            "client_id": given.client_id,
                            "client_secret": secret,
                            "redirect_uri": given.redirect_uri,
                            "default_role_id": given.default_role,
                            "session_minutes": given.session_minutes,
                            "updated_at_ms": time_ns() // 1_000_000,
                        },
                    ),
                    conflict=Conflict(
                        columns=("id",),
                        update=(
                            "client_id",
                            "client_secret",
                            "redirect_uri",
                            "default_role_id",
                            "session_minutes",
                            "updated_at_ms",
                        ),
                    ),
                )
            )
        )
        return RedirectResponse("/ui/github/?saved", status_code=303)


def _allow(request: Request) -> None:
    account: Account = request.state.account
    if not account.allows(METHODS):
        raise HTTPException(status_code=403)
