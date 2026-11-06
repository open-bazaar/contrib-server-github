"""The server's entries as installed, behind server-access, with the GitHub
login talking to the GitHub on this machine."""

from __future__ import annotations

import re
from contextlib import asynccontextmanager
from dataclasses import dataclass
from functools import partial
lazy import asyncio
lazy import logging
lazy from collections.abc import AsyncIterator

import httpx
from bazaar_contrib_http import HTTP
from bazaar_core import Context, Contrib, ContribEntry, ContribHub, ContribSpec, load
from fake import FakeGitHub, github

from bazaar_contrib_server_github import _github
from bazaar_contrib_server_github import github as entry

ENTRIES = (
    ("timer-wheel", {}),
    ("http", {"listen": "127.0.0.1:0"}),
    ("server-database", {}),
    ("server-migrations", {}),
    ("server", {}),
    ("server-theme-night", {}),
    ("server-theme-day", {}),
    ("server-access", {}),
    ("server-github", {}),
)


@dataclass(frozen=True, slots=True)
class Served:
    url: str
    password: str
    github: FakeGitHub

    @property
    def callback(self) -> str:
        return f"{self.url}/login/github/callback"


@asynccontextmanager
async def serving() -> AsyncIterator[Served]:
    found: dict[str, str] = {}
    printed: list[str] = []

    class Printed(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            printed.append(record.getMessage())

    @asynccontextmanager
    async def look(ctx: Context) -> AsyncIterator[None]:
        found["url"] = ctx.accept(HTTP).url
        yield

    async with github() as fake:
        composition = await asyncio.to_thread(
            load, [ContribSpec(contrib=name, config=config) for name, config in ENTRIES]
        )
        # the installed entry, talking to the GitHub on this machine
        here = Contrib(
            accepts=entry.accepts,
            lifespan=partial(
                _github, site=fake.url, api=fake.url, context=fake.context
            ),
        )
        composition = composition.model_copy(
            update={
                "entries": (
                    *(
                        installed.model_copy(update={"definition": here})
                        if installed.name == "server-github"
                        else installed
                        for installed in composition.entries
                    ),
                    ContribEntry(
                        name="look", definition=Contrib(accepts=[HTTP], lifespan=look)
                    ),
                )
            }
        )
        logger = logging.getLogger("bazaar_contrib_server_access")
        handler = Printed()
        logger.addHandler(handler)
        try:
            async with ContribHub() as hub, hub.enable(composition):
                password = next(
                    created.group(1)
                    for message in printed
                    if (created := re.search(r"with password (\S+);", message))
                )
                yield Served(found["url"], password, fake)
        finally:
            logger.removeHandler(handler)


@asynccontextmanager
async def client(served: Served) -> AsyncIterator[httpx.AsyncClient]:
    """A browser of sorts: it keeps cookies, follows no redirects, and trusts
    the GitHub on this machine."""
    async with await asyncio.to_thread(
        httpx.AsyncClient, timeout=60, verify=served.github.context
    ) as session:
        yield session
