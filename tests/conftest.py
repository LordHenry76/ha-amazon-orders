"""Shared fixtures for the tests."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Iterator
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import aiohttp
import pytest
from aiohttp.test_utils import TestServer

from custom_components.amazon_orders.api.auth import AmazonAuth, LoginData
from custom_components.amazon_orders.api.client import AmazonOrdersClient
from custom_components.amazon_orders.api.models import (
    Address,
    Milestone,
    Order,
    OrderItem,
    Shipment,
    Tracking,
    TrackingEvent,
)

from .fake_amazon import FakeAmazon

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def load_fixture() -> Callable[[str], str]:
    """Return a function reading a synthetic page from tests/fixtures."""

    def _load(name: str) -> str:
        return (FIXTURES / name).read_text(encoding="utf-8")

    return _load


@pytest.fixture
def local_sockets(request: pytest.FixtureRequest) -> None:
    """Allow connections to the local fake server.

    The Home Assistant test plugin blocks sockets unless its ``socket_enabled``
    fixture is requested. Without that plugin there is nothing to do.
    """
    try:
        request.getfixturevalue("socket_enabled")
    except pytest.FixtureLookupError:
        pass


@pytest.fixture
async def fake_amazon(local_sockets: None) -> AsyncIterator[FakeAmazon]:
    """Start a fake Amazon on a local port. Its address is ``fake.url``."""
    fake = FakeAmazon()
    server = TestServer(fake.app)
    await server.start_server()
    fake.url = str(server.make_url("")).rstrip("/")
    try:
        yield fake
    finally:
        await server.close()


@pytest.fixture
async def login_session() -> AsyncIterator[aiohttp.ClientSession]:
    """A session with a cookie jar, as the sign-in needs (IP hosts allowed)."""
    async with aiohttp.ClientSession(
        cookie_jar=aiohttp.CookieJar(unsafe=True)
    ) as session:
        yield session


@pytest.fixture
async def plain_session() -> AsyncIterator[aiohttp.ClientSession]:
    """A session that stores no cookies, as the integration uses for polling."""
    async with aiohttp.ClientSession(cookie_jar=aiohttp.DummyCookieJar()) as session:
        yield session


EMAIL = "user@example.com"
USER_ID = "amzn1.account.TEST"
LOGIN_DATA = LoginData(
    refresh_token="Atnr|new-refresh-token",
    device_serial="0123456789ABCDEF0123456789ABCDEF",
    device_info={
        "device_name": "Test's Home Assistant Amazon Orders",
        "device_serial_number": "0123456789ABCDEF0123456789ABCDEF",
        "device_type": "A2IVLV5VM2W81",
    },
    customer_info={
        "account_pool": "Amazon",
        "given_name": "Test",
        "home_region": "EU",
        "name": "Test User",
        "user_id": USER_ID,
    },
)


STEP_LABELS = ("Ordinato", "Spedito", "In consegna", "Consegnato")


def make_tracking(
    status: str = "DELIVERED",
    steps: int = 4,
    progress: int = 100,
    expected: str = "Consegnato 4 ottobre",
    message: str = "Consegnato",
) -> Tracking:
    """Build the state of a package. Codes other than DELIVERED are invented."""
    return Tracking(
        order_id="123-1234567-1234567",
        shipment_id="TESTSHIP",
        tracking_id="TESTTRACKING123",
        short_status=status,
        last_milestone=status,
        milestones_reached=steps,
        percent_complete=progress,
        promise_message=expected,
        carrier="Consegna da Amazon",
        timezone="Europe/Rome",
        seller_fulfilled=False,
        events=(TrackingEvent("domenica 4 ottobre", "16:07", message, "ROMA, IT"),),
        status_label=None if steps == 4 else STEP_LABELS[steps - 1],
        milestones=()
        if steps == 4
        else tuple(
            Milestone(label, index < steps, 100 if index < steps else 0)
            for index, label in enumerate(STEP_LABELS)
        ),
        raw_state={"shortStatus": status, "customerId": "TESTCUSTOMER"},
    )


def make_order(
    number: int, *, tracking: bool = True, status_text: str = "In arrivo"
) -> Order:
    """Build an order with one shipment, tracked or not."""
    order_id = f"123-{number:07d}-7654321"
    url = (
        f"https://www.amazon.it/progress-tracker/package?orderId={order_id}"
        f"&shipmentId=SHIP{number}&packageIndex=0"
    )
    return Order(
        order_id=order_id,
        placed_text="3 ottobre 2026",
        total_text="10,98 €",
        details_url=None,
        shipments=(
            Shipment(
                status_text=status_text,
                status_detail="Dettaglio",
                tracking_url=url if tracking else None,
                items=(OrderItem(f"Example product {number}", None, None),),
            ),
        ),
        address=Address(("Test User", "Via Esempio 1", "ROMA, RM 00100", "Italia")),
    )


def tracking_url(number: int) -> str:
    """Return the tracking URL of the shipment built by make_order."""
    return make_order(number).shipments[0].tracking_url


@pytest.fixture
def mock_amazon() -> Iterator[SimpleNamespace]:
    """Replace the calls to Amazon with mocks.

    By default there are no orders. ``trackings`` maps a tracking URL to the
    state the mock returns for it (or to an exception to raise).
    """
    trackings: dict[str, Tracking | Exception] = {}

    async def _get_tracking(client: AmazonOrdersClient, url: str) -> Tracking:
        result = trackings[url]
        if isinstance(result, Exception):
            raise result
        return result

    with (
        patch.object(
            AmazonAuth, "async_login", autospec=True, return_value=LOGIN_DATA
        ) as login,
        patch.object(
            AmazonAuth,
            "async_get_cookies",
            autospec=True,
            return_value={"session-token": "token"},
        ) as get_cookies,
        patch.object(AmazonAuth, "async_deregister", autospec=True) as deregister,
        patch.object(
            AmazonOrdersClient, "async_get_orders", autospec=True, return_value=[]
        ) as get_orders,
        patch.object(
            AmazonOrdersClient,
            "async_get_tracking",
            autospec=True,
            side_effect=_get_tracking,
        ) as get_tracking,
    ):
        yield SimpleNamespace(
            login=login,
            get_cookies=get_cookies,
            deregister=deregister,
            get_orders=get_orders,
            get_tracking=get_tracking,
            trackings=trackings,
        )
