"""The GitHub login in a real browser: set up on the settings page, then used
from the login page."""

from __future__ import annotations

lazy import re

from fake import Person
from playwright.async_api import async_playwright, expect
from served import serving


async def test_set_up_on_the_settings_page_and_used_from_the_login_page():
    async with serving() as served, async_playwright() as driver:
        browser = await driver.chromium.launch()
        try:
            # it trusts the GitHub on this machine as the server does
            context = await browser.new_context(ignore_https_errors=True)
            page = await context.new_page()
            await page.goto(f"{served.url}/login/")
            await page.fill("#name", "admin")
            await page.fill("#password", served.password)
            await page.click("button[type=submit]")
            await page.wait_for_url(re.compile(r"/settings$"))
            # a default role for the people logging in for the first time
            made = await page.request.post(
                f"{served.url}/ui/access/roles/",
                form={"name": "readers"},
                max_redirects=0,
            )
            await page.request.post(
                f"{served.url}{made.headers['location']}/default", max_redirects=0
            )
            await page.goto(f"{served.url}/settings/methods")
            methods = page.frame_locator("iframe.frame").frame_locator("iframe.pane")
            github = methods.frame_locator("details.method iframe.settings-of")
            # the callback is made from the address the page is open at
            await expect(github.locator("#redirect_uri")).to_have_value(served.callback)
            await github.locator("#client_id").fill(served.github.client_id)
            secret = github.locator("#client_secret")
            await secret.fill(served.github.client_secret)
            # what is typed shows at a click, and hides again
            await github.locator("button.eye").click()
            await expect(secret).to_have_attribute("type", "text")
            await github.locator("button.eye").click()
            await expect(secret).to_have_attribute("type", "password")
            await github.locator("button[type=submit]").click()
            # the part around it is drawn again, GitHub set up and folded
            await expect(methods.locator("details.method .tag.done")).to_be_visible()
            await expect(methods.locator("details.method")).not_to_have_attribute(
                "open", ""
            )
            visitor = await (
                await browser.new_context(ignore_https_errors=True)
            ).new_page()
            served.github.approver = Person(
                id=1, login="mona", name="Mona", email="m@example.com"
            )
            await visitor.goto(f"{served.url}/login/")
            await visitor.click("a.logo.big[href='/login/github/']")
            await visitor.wait_for_url(re.compile(rf"^{re.escape(served.url)}/"))
            await expect(visitor.locator(".me .who b")).to_have_text("Mona")
        finally:
            await browser.close()
