"""Logging in with GitHub over real HTTP, against the GitHub on this machine."""

from __future__ import annotations

import re
lazy import asyncio

import pytest
from fake import Person
from served import Served, client, serving
lazy import httpx


def octocat() -> Person:
    return Person(id=583231, login="octocat", name="The Octocat", email="o@example.com")


async def admin(served: Served, session: httpx.AsyncClient) -> None:
    await session.post(
        f"{served.url}/login/", data={"name": "admin", "password": served.password}
    )


async def role(
    served: Served, session: httpx.AsyncClient, name: str, *, default: bool = False
) -> str:
    """A new role holding no permission point, the global default if asked;
    its id."""
    made = await session.post(
        f"{served.url}/ui/access/roles/", data={"name": name, "permissions": []}
    )
    if default:
        await session.post(f"{served.url}{made.headers['location']}/default")
    page = await session.get(f"{served.url}/ui/github/")
    found = re.search(rf'<option value="([^"]+)"[^>]*>{name}</option>', page.text)
    assert found is not None
    return found.group(1)


async def configure(
    served: Served, session: httpx.AsyncClient, **values: str
) -> httpx.Response:
    return await session.post(
        f"{served.url}/ui/github/",
        data={
            "client_id": served.github.client_id,
            "client_secret": served.github.client_secret,
            "redirect_uri": served.callback,
        }
        | values,
    )


async def through(served: Served, session: httpx.AsyncClient) -> httpx.Response:
    """Set off to GitHub, approve or decline there, and come back."""
    sent = await session.get(f"{served.url}/login/github/")
    for _ in range(2):
        if sent.status_code != 302:
            break
        sent = await session.get(sent.headers["location"])
    return sent


async def test_a_first_login_makes_an_account_and_the_next_one_finds_it():
    async with serving() as served, client(served) as boss, client(served) as octo:
        await admin(served, boss)
        await role(served, boss, "readers", default=True)
        assert (await configure(served, boss)).status_code == 303
        assert "/login/github/" in (await octo.get(f"{served.url}/login/")).text
        person = served.github.approver = octocat()
        came = await through(served, octo)
        assert came.status_code == 302
        assert came.headers["location"] == "/"
        assert (await octo.get(f"{served.url}/settings")).status_code == 200
        async with client(served) as again:
            assert (await through(served, again)).status_code == 302
        listed = await boss.get(f"{served.url}/ui/access/accounts/")
        # one account, named and pictured as on GitHub
        assert listed.text.count("The Octocat") == 1
        assert f"{served.github.url}/avatars/{person.id}" in listed.text


@pytest.mark.parametrize("expiring", [True, False])
async def test_a_session_that_ran_out_goes_on_while_github_vouches_for_it(
    expiring: bool,
):
    async with serving() as served, client(served) as boss, client(served) as octo:
        await admin(served, boss)
        await role(served, boss, "readers", default=True)
        await configure(served, boss)
        served.github.expiring = expiring
        person = served.github.approver = octocat()
        await through(served, octo)
        octo.cookies.delete("bcs_session")
        person.name = "Mona"
        assert (await octo.get(f"{served.url}/settings")).status_code == 200
        assert "bcs_session" in octo.cookies
        assert "Mona" in (await boss.get(f"{served.url}/ui/access/accounts/")).text
        # taken back on GitHub, the next session that runs out is the last
        served.github.revoke(person)
        octo.cookies.delete("bcs_session")
        ended = await octo.get(f"{served.url}/settings")
        assert ended.status_code in (302, 303, 307)
        assert "/login/" in ended.headers["location"]


async def test_an_expired_token_is_refreshed_once_for_requests_at_the_same_time():
    async with serving() as served, client(served) as boss, client(served) as octo:
        await admin(served, boss)
        await role(served, boss, "readers", default=True)
        await configure(served, boss)
        person = served.github.approver = octocat()
        await through(served, octo)
        served.github.expire(person)
        octo.cookies.delete("bcs_session")
        pages = await asyncio.gather(
            *(octo.get(f"{served.url}/settings") for _ in range(3))
        )
        assert [page.status_code for page in pages] == [200, 200, 200]
        # GitHub takes the refresh token back once it is used
        assert served.github.refreshed == 1


async def test_first_logins_take_the_method_s_own_role():
    async with serving() as served, client(served) as boss, client(served) as octo:
        await admin(served, boss)
        readers = await role(served, boss, "readers")
        await configure(served, boss)
        served.github.approver = octocat()
        # no default role anywhere
        assert (await through(served, octo)).status_code == 403
        await configure(served, boss, default_role=readers)
        assert (await through(served, octo)).status_code == 302


async def test_a_login_declined_or_with_github_down_ends_on_the_login_page():
    async with serving() as served, client(served) as boss, client(served) as octo:
        await admin(served, boss)
        await role(served, boss, "readers", default=True)
        await configure(served, boss)
        assert (await through(served, octo)).status_code == 401
        assert "bcs_session" not in octo.cookies
        served.github.approver = octocat()
        served.github.down = True
        assert (await through(served, octo)).status_code == 503
        assert "bcs_session" not in octo.cookies


async def test_the_settings_are_checked_kept_and_shown_to_those_managing_logins():
    async with serving() as served, client(served) as boss, client(served) as octo:
        await admin(served, boss)
        await role(served, boss, "readers", default=True)
        for wrong in (
            {"redirect_uri": "/login/github/callback"},
            {"redirect_uri": f"{served.url}/elsewhere"},
            {"client_secret": ""},
            {"session_minutes": "0"},
            {"default_role": "nobody"},
        ):
            assert (await configure(served, boss, **wrong)).status_code == 422
        assert (await configure(served, boss)).status_code == 303
        # the secret saved before stays when left empty, and is never shown
        assert (await configure(served, boss, client_secret="")).status_code == 303
        page = await boss.get(f"{served.url}/ui/github/")
        assert served.github.client_id in page.text
        assert served.github.client_secret not in page.text
        served.github.approver = octocat()
        await through(served, octo)
        # a reader manages no login method
        assert (await octo.get(f"{served.url}/ui/github/")).status_code == 403
