"""Tests for the client reading the orders list and the tracking pages."""

from __future__ import annotations

import aiohttp
import pytest

from custom_components.amazon_orders.api.auth import AmazonAuth
from custom_components.amazon_orders.api.client import (
    TIME_FILTER_30_DAYS,
    AmazonOrdersClient,
)
from custom_components.amazon_orders.api.exceptions import (
    AmazonAuthError,
    AmazonCaptchaError,
    AmazonConnectionError,
    AmazonParseError,
)

from .fake_amazon import REFRESH_TOKEN, FakeAmazon

TRACKING_PATH = (
    "/progress-tracker/package?orderId=123-1234567-1234567&shipmentId=TESTSHIP1"
)


def _client(
    session: aiohttp.ClientSession, fake: FakeAmazon, refresh_token: str = REFRESH_TOKEN
) -> AmazonOrdersClient:
    auth = AmazonAuth(session, signin_url=fake.url, api_url=fake.url)
    return AmazonOrdersClient(
        session, auth, refresh_token, "amazon.it", base_url=fake.url
    )


async def test_get_orders(fake_amazon: FakeAmazon, plain_session) -> None:
    orders = await _client(plain_session, fake_amazon).async_get_orders()

    assert [order.order_id for order in orders] == [
        "123-1234567-1234567",
        "123-7654321-7654321",
    ]
    # Links are made absolute on the site of the account.
    assert (
        orders[0]
        .shipments[0]
        .tracking_url.startswith(f"{fake_amazon.url}/progress-tracker/package?")
    )
    assert fake_amazon.issued_sessions == 1
    request = fake_amazon.page_requests[0]
    assert request.query["timeFilter"] == "months-3"
    assert request.headers["Accept-Language"].startswith("it-IT")


async def test_get_orders_reuses_the_session(
    fake_amazon: FakeAmazon, plain_session
) -> None:
    client = _client(plain_session, fake_amazon)

    await client.async_get_orders()
    await client.async_get_orders(TIME_FILTER_30_DAYS)

    assert fake_amazon.issued_sessions == 1
    assert fake_amazon.page_requests[1].query["timeFilter"] == "last30"


async def test_expired_session_is_renewed_once(
    fake_amazon: FakeAmazon, plain_session
) -> None:
    client = _client(plain_session, fake_amazon)
    await client.async_get_orders()
    # Amazon now accepts only a newer session: the one the client holds is stale.
    fake_amazon.issued_sessions += 1

    orders = await client.async_get_orders()

    assert len(orders) == 2
    assert fake_amazon.issued_sessions == 3


async def test_sign_in_still_required_after_renewal(
    fake_amazon: FakeAmazon, plain_session
) -> None:
    fake_amazon.always_ask_signin = True

    with pytest.raises(AmazonAuthError):
        await _client(plain_session, fake_amazon).async_get_orders()

    # One session when starting, one more for the single retry.
    assert fake_amazon.issued_sessions == 2
    assert len(fake_amazon.page_requests) == 2


async def test_refresh_token_refused(fake_amazon: FakeAmazon, plain_session) -> None:
    client = _client(plain_session, fake_amazon, refresh_token="Atnr|revoked")

    with pytest.raises(AmazonAuthError):
        await client.async_get_orders()

    assert fake_amazon.page_requests == []


async def test_captcha(
    fake_amazon: FakeAmazon, plain_session, tmp_path, monkeypatch
) -> None:
    captcha = tmp_path / "captcha.html"
    captcha.write_text(
        '<form action="/errors/validateCaptcha"></form>', encoding="utf-8"
    )
    monkeypatch.setattr("tests.fake_amazon.FIXTURES", tmp_path)
    fake_amazon.orders_page = "captcha.html"

    with pytest.raises(AmazonCaptchaError):
        await _client(plain_session, fake_amazon).async_get_orders()


async def test_amazon_error_status(fake_amazon: FakeAmazon, plain_session) -> None:
    fake_amazon.orders_status = 503

    with pytest.raises(AmazonConnectionError, match="503"):
        await _client(plain_session, fake_amazon).async_get_orders()


async def test_unexpected_page(fake_amazon: FakeAmazon, plain_session) -> None:
    fake_amazon.orders_page = "tracking_delivered.html"

    with pytest.raises(AmazonParseError):
        await _client(plain_session, fake_amazon).async_get_orders()


async def test_redirect_inside_the_site_is_followed(
    fake_amazon: FakeAmazon, plain_session
) -> None:
    fake_amazon.orders_redirect = "/elsewhere"

    with pytest.raises(AmazonParseError):
        await _client(plain_session, fake_amazon).async_get_orders()

    assert fake_amazon.page_requests[-1].path == "/elsewhere"


async def test_redirect_to_another_site_is_refused(
    fake_amazon: FakeAmazon, plain_session
) -> None:
    fake_amazon.orders_redirect = "http://example.invalid/steal"

    with pytest.raises(AmazonConnectionError, match="unexpected site"):
        await _client(plain_session, fake_amazon).async_get_orders()


@pytest.mark.parametrize("as_absolute", [True, False])
async def test_get_tracking(
    fake_amazon: FakeAmazon, plain_session, as_absolute: bool
) -> None:
    url = f"{fake_amazon.url}{TRACKING_PATH}" if as_absolute else TRACKING_PATH

    tracking = await _client(plain_session, fake_amazon).async_get_tracking(url)

    assert tracking.short_status == "DELIVERED"
    assert len(tracking.events) == 4
    assert fake_amazon.page_requests[0].query["shipmentId"] == "TESTSHIP1"


@pytest.mark.parametrize(
    "url",
    [
        "https://example.invalid/progress-tracker/package?orderId=1",
        "/your-orders/orders",
        "/ap/signin",
    ],
)
async def test_get_tracking_refuses_other_urls(
    fake_amazon: FakeAmazon, plain_session, url: str
) -> None:
    with pytest.raises(ValueError):
        await _client(plain_session, fake_amazon).async_get_tracking(url)

    assert fake_amazon.page_requests == []
    assert fake_amazon.issued_sessions == 0
