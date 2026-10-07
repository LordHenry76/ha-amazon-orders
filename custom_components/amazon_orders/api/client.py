"""HTTP client for the orders list and the package tracking pages."""

from __future__ import annotations

import asyncio
from urllib.parse import urljoin, urlsplit

import aiohttp

from .auth import USER_AGENT, AmazonAuth
from .exceptions import (
    AmazonAuthError,
    AmazonCaptchaError,
    AmazonConnectionError,
)
from .models import Order, Tracking
from .parser import is_captcha_page, parse_orders, parse_tracking

# Values of the "timeFilter" parameter of the orders list.
TIME_FILTER_30_DAYS = "last30"
TIME_FILTER_3_MONTHS = "months-3"

ORDERS_PATH = "/your-orders/orders"
TRACKING_PATH_PREFIX = "/progress-tracker/"
SIGNIN_PATH_PREFIX = "/ap/signin"

# Language of the pages, by site. Only amazon.it has been tested.
SITE_LANGUAGES = {"amazon.it": "it-IT"}
DEFAULT_LANGUAGE = "en-US"

REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=30)
MAX_REDIRECTS = 5


class AmazonOrdersClient:
    """Reads the orders of one Amazon account.

    The session cookies are obtained from the refresh token and sent explicitly
    with each request, only to the Amazon site of the account: the HTTP session
    passed in is expected not to store cookies (``aiohttp.DummyCookieJar``).
    """

    def __init__(
        self,
        session: aiohttp.ClientSession,
        auth: AmazonAuth,
        refresh_token: str,
        site: str,
        *,
        base_url: str | None = None,
    ) -> None:
        """Initialize. ``site`` is the Amazon domain of the account, e.g. ``amazon.it``."""
        self._session = session
        self._auth = auth
        self._refresh_token = refresh_token
        self._site = site
        self._base_url = (base_url or f"https://www.{site}").rstrip("/")
        self._language = SITE_LANGUAGES.get(site, DEFAULT_LANGUAGE)
        self._cookies: dict[str, str] = {}

    async def async_refresh_session(self) -> None:
        """Get new session cookies from the refresh token.

        Raise AmazonAuthError if Amazon no longer accepts the refresh token:
        the user has to sign in again.
        """
        self._cookies = await self._auth.async_get_cookies(
            self._refresh_token, self._site
        )

    async def async_get_orders(
        self, time_filter: str = TIME_FILTER_3_MONTHS
    ) -> list[Order]:
        """Return the orders of the given period, newest first."""
        html = await self._get_page(
            f"{self._base_url}{ORDERS_PATH}?timeFilter={time_filter}"
        )
        return await asyncio.to_thread(parse_orders, html, self._base_url)

    async def async_get_tracking(self, tracking_url: str) -> Tracking:
        """Return the state of a package, given the tracking URL of its shipment."""
        url = urljoin(f"{self._base_url}/", tracking_url)
        parts = urlsplit(url)
        if not self._is_own_site(url) or not parts.path.startswith(
            TRACKING_PATH_PREFIX
        ):
            raise ValueError("Not a package tracking URL of this Amazon site")
        html = await self._get_page(url)
        return await asyncio.to_thread(parse_tracking, html)

    def _is_own_site(self, url: str) -> bool:
        base = urlsplit(self._base_url)
        parts = urlsplit(url)
        return (parts.scheme, parts.netloc) == (base.scheme, base.netloc)

    async def _get_page(self, url: str) -> str:
        """Return the HTML of a page that requires the user to be signed in.

        When Amazon asks to sign in, the cookies are renewed once and the
        request is repeated.
        """
        if not self._cookies:
            await self.async_refresh_session()
        html = await self._fetch(url)
        if html is None:
            await self.async_refresh_session()
            html = await self._fetch(url)
            if html is None:
                raise AmazonAuthError(
                    "Amazon asks to sign in even with a renewed session"
                )
        if is_captcha_page(html):
            raise AmazonCaptchaError("Amazon answered with a CAPTCHA")
        return html

    async def _fetch(self, url: str) -> str | None:
        """Return the HTML of a page, or None if Amazon redirects to the sign-in.

        Redirects are followed by hand, so that the session cookies never leave
        the Amazon site of the account.
        """
        headers = {
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": f"{self._language},{self._language[:2]};q=0.9",
            "Cookie": "; ".join(
                f"{name}={value}" for name, value in self._cookies.items()
            ),
        }
        for _ in range(MAX_REDIRECTS + 1):
            try:
                async with self._session.get(
                    url, headers=headers, allow_redirects=False, timeout=REQUEST_TIMEOUT
                ) as response:
                    if response.status in (301, 302, 303, 307, 308):
                        url = urljoin(url, response.headers.get("Location", ""))
                        if not self._is_own_site(url):
                            raise AmazonConnectionError(
                                "Amazon redirected to an unexpected site"
                            )
                        if urlsplit(url).path.startswith(SIGNIN_PATH_PREFIX):
                            return None
                        continue
                    if response.status != 200:
                        raise AmazonConnectionError(
                            f"Amazon answered HTTP {response.status}"
                        )
                    if response.url.path.startswith(SIGNIN_PATH_PREFIX):
                        return None
                    return await response.text()
            except (aiohttp.ClientError, TimeoutError) as err:
                raise AmazonConnectionError(f"Cannot reach Amazon: {err!r}") from err
        raise AmazonConnectionError("Too many redirects")
