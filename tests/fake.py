"""GitHub's login and API as the GitHub login uses them, served over TLS on
this machine under a certificate of a test authority, the way Dex tests its
GitHub connector."""

from __future__ import annotations

import base64
import hashlib
import ssl
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
lazy import asyncio
lazy from collections.abc import AsyncIterator
lazy from secrets import token_urlsafe
lazy from urllib.parse import urlencode

import trustme
from aiohttp import web


@dataclass
class Person:
    id: int
    login: str
    name: str | None
    email: str


@dataclass
class _Code:
    person: Person
    redirect_uri: str
    challenge: str


@dataclass
class FakeGitHub:
    url: str
    # what trusts the certificate it serves under
    context: ssl.SSLContext
    client_id: str = "Ov23liTest"
    client_secret: str = "test-secret"
    # who approves the next login on the authorization page; nobody declines it
    approver: Person | None = None
    # the token endpoint and the API failing, as when GitHub is down
    down: bool = False
    # access tokens that expire and come with a refresh token, as a new
    # OAuth App's do unless its owner turns it off
    expiring: bool = True
    # how many times a refresh token was redeemed
    refreshed: int = 0
    tokens: dict[str, Person] = field(default_factory=dict)
    refreshes: dict[str, Person] = field(default_factory=dict)
    _codes: dict[str, _Code] = field(default_factory=dict)

    def expire(self, person: Person) -> None:
        """The person's access tokens run out; their refresh tokens hold."""
        for token in [t for t, held in self.tokens.items() if held is person]:
            del self.tokens[token]

    def revoke(self, person: Person) -> None:
        """The person takes back the authorization on GitHub."""
        self.expire(person)
        for token in [t for t, held in self.refreshes.items() if held is person]:
            del self.refreshes[token]

    def _grant(self, person: Person) -> web.StreamResponse:
        token = token_urlsafe(16)
        self.tokens[token] = person
        answer: dict[str, object] = {"access_token": token, "token_type": "bearer"}
        if self.expiring:
            refresh = token_urlsafe(16)
            self.refreshes[refresh] = person
            answer |= {"expires_in": 28800, "refresh_token": refresh}
        return web.json_response(answer)

    async def _authorize(self, request: web.Request) -> web.StreamResponse:
        query = request.query
        back = {"state": query["state"]}
        if (
            query.get("client_id") != self.client_id
            or query.get("code_challenge_method") != "S256"
            or self.approver is None
        ):
            back |= {"error": "access_denied"}
        else:
            code = token_urlsafe(16)
            self._codes[code] = _Code(
                self.approver, query["redirect_uri"], query["code_challenge"]
            )
            back |= {"code": code}
        raise web.HTTPFound(f"{query['redirect_uri']}?{urlencode(back)}")

    async def _token(self, request: web.Request) -> web.StreamResponse:
        if self.down:
            raise web.HTTPServiceUnavailable
        form = await request.post()
        if (
            form.get("client_id") != self.client_id
            or form.get("client_secret") != self.client_secret
        ):
            return web.json_response({"error": "incorrect_client_credentials"})
        if form.get("grant_type") == "refresh_token":
            # used once: a second request with the same token is refused
            person = self.refreshes.pop(str(form.get("refresh_token", "")), None)
            # GitHub takes a moment, long enough for requests to cross
            await asyncio.sleep(0.2)
            if person is None:
                return web.json_response({"error": "bad_refresh_token"})
            self.refreshed += 1
            return self._grant(person)
        code = self._codes.pop(str(form.get("code", "")), None)
        verifier = str(form.get("code_verifier", "")).encode("ascii")
        if (
            code is None
            or form.get("redirect_uri") != code.redirect_uri
            or base64.urlsafe_b64encode(hashlib.sha256(verifier).digest())
            .rstrip(b"=")
            .decode("ascii")
            != code.challenge
        ):
            return web.json_response({"error": "bad_verification_code"})
        return self._grant(code.person)

    def _holder(self, request: web.Request) -> Person:
        if self.down:
            raise web.HTTPServiceUnavailable
        person = self.tokens.get(
            request.headers.get("Authorization", "").removeprefix("Bearer ")
        )
        if person is None:
            raise web.HTTPUnauthorized
        return person

    async def _user(self, request: web.Request) -> web.StreamResponse:
        person = self._holder(request)
        return web.json_response(
            {
                "id": person.id,
                "login": person.login,
                "name": person.name,
                "avatar_url": f"{self.url}/avatars/{person.id}",
            }
        )

    async def _emails(self, request: web.Request) -> web.StreamResponse:
        person = self._holder(request)
        return web.json_response(
            [{"email": person.email, "primary": True, "verified": True}]
        )


@asynccontextmanager
async def github() -> AsyncIterator[FakeGitHub]:
    authority = await asyncio.to_thread(trustme.CA)
    certificate = await asyncio.to_thread(authority.issue_cert, "127.0.0.1")
    served = await asyncio.to_thread(
        ssl.create_default_context, ssl.Purpose.CLIENT_AUTH
    )
    trusting = await asyncio.to_thread(ssl.create_default_context)
    await asyncio.to_thread(certificate.configure_cert, served)
    await asyncio.to_thread(authority.configure_trust, trusting)
    # its address is known once it listens
    fake = FakeGitHub("", trusting)
    app = web.Application()
    app.router.add_get("/login/oauth/authorize", fake._authorize)
    app.router.add_post("/login/oauth/access_token", fake._token)
    app.router.add_get("/user", fake._user)
    app.router.add_get("/user/emails", fake._emails)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0, ssl_context=served)
    await site.start()
    host, port = runner.addresses[0][:2]
    fake.url = f"https://{host}:{port}"
    try:
        yield fake
    finally:
        await runner.cleanup()
