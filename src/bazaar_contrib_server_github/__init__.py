"""Logging in to the server with a GitHub account, through the OAuth App set on
the login methods part of the settings page."""

from __future__ import annotations

from contextlib import asynccontextmanager
lazy import asyncio
lazy import ssl
lazy from collections.abc import AsyncIterator
lazy from importlib.resources import files
lazy from pathlib import Path

from bazaar_contrib_server import SERVER
from bazaar_contrib_server_access import ACCESS
from bazaar_contrib_server_database import SERVER_DB, SERVER_SCHEMA
from bazaar_contrib_server_migrations import SERVER_MIGRATIONS
from bazaar_core import Context, Contrib
lazy import aiohttp

lazy from .client import GitHub
lazy from .method import GitHubLogin
lazy from .schema import MODULE
lazy from .settings import Settings

__all__ = ["github"]

SITE = "https://github.com"
API = "https://api.github.com"


@asynccontextmanager
async def _github(
    ctx: Context,
    *,
    site: str = SITE,
    api: str = API,
    context: ssl.SSLContext | None = None,
) -> AsyncIterator[None]:
    server = ctx.accept(SERVER)
    access = ctx.accept(ACCESS)
    database = ctx.accept(SERVER_DB)
    async with ctx.accept(SERVER_SCHEMA).register(MODULE):
        resources = Path(str(files(__name__))) / "resources"
        icon = await asyncio.to_thread(
            (resources / "templates" / "github" / "github.svg").read_text,
            encoding="utf-8",
        )
        pages = await server.pages("github", __name__)
        verified = context or await asyncio.to_thread(ssl.create_default_context)
        async with aiohttp.ClientSession(
            connector=aiohttp.TCPConnector(ssl=verified)
        ) as session:
            login = GitHubLogin(access, database, GitHub(session, site, api), icon)
            async with (
                server.module(
                    "github",
                    ui=Settings(pages, access, database).app,
                    static=resources / "static",
                    locales=resources / "locales",
                ),
                access.method(login),
            ):
                yield


github = Contrib(
    accepts=[SERVER, SERVER_DB, SERVER_SCHEMA, SERVER_MIGRATIONS, ACCESS],
    lifespan=_github,
)
