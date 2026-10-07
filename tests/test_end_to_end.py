"""The config flow with the real sign-in code, against the fake Amazon."""

from __future__ import annotations

from functools import partial
from unittest.mock import patch

import aiohttp
import pytest
from homeassistant.config_entries import SOURCE_USER, ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers.aiohttp_client import async_create_clientsession

from custom_components.amazon_orders.api import AmazonAuth, AmazonOrdersClient
from custom_components.amazon_orders.const import (
    CONF_LOGIN_DATA,
    CONF_OTP,
    CONF_SITE,
    DOMAIN,
)

from .fake_amazon import EMAIL, OTP, PASSWORD, REFRESH_TOKEN, FakeAmazon

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")

USER_INPUT = {
    CONF_SITE: "it",
    "email": EMAIL,
    "password": PASSWORD,
    CONF_OTP: OTP,
}


def _login_session(hass: HomeAssistant) -> aiohttp.ClientSession:
    """Like create_login_session, but accepting cookies from an IP address."""
    return async_create_clientsession(
        hass, auto_cleanup=False, cookie_jar=aiohttp.CookieJar(unsafe=True)
    )


@pytest.fixture
def amazon_is_fake(fake_amazon: FakeAmazon):
    """Point the integration to the fake Amazon instead of the real one."""
    auth = partial(AmazonAuth, signin_url=fake_amazon.url, api_url=fake_amazon.url)
    client = partial(AmazonOrdersClient, base_url=fake_amazon.url)
    with (
        patch("custom_components.amazon_orders.config_flow.AmazonAuth", auth),
        patch("custom_components.amazon_orders.AmazonAuth", auth),
        patch("custom_components.amazon_orders.AmazonOrdersClient", client),
        patch(
            "custom_components.amazon_orders.config_flow.create_login_session",
            _login_session,
        ),
    ):
        yield fake_amazon


async def test_sign_in_and_setup(
    hass: HomeAssistant, amazon_is_fake: FakeAmazon
) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    login_data = result["data"][CONF_LOGIN_DATA]
    assert login_data["refresh_token"] == REFRESH_TOKEN
    assert result["result"].unique_id == "amazon.it_amzn1.account.TEST"
    # Setting up the entry exchanged the refresh token for the session cookies.
    assert result["result"].state is ConfigEntryState.LOADED
    assert amazon_is_fake.issued_sessions == 1
    assert amazon_is_fake.token_requests[0]["domain"] == "www.amazon.it"
    # It also read the orders list and the tracking page of the tracked package.
    assert [request.path for request in amazon_is_fake.page_requests] == [
        "/your-orders/orders",
        "/progress-tracker/package",
    ]
    assert hass.states.get("sensor.amazon_packages_in_transit").state == "0"
    assert hass.states.get("sensor.amazon_last_delivered_package").state == (
        "Consegnato 4 ottobre"
    )


async def test_wrong_password_shows_amazons_message(
    hass: HomeAssistant, amazon_is_fake: FakeAmazon
) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {**USER_INPUT, "password": "wrong"}
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}
    assert result["description_placeholders"]["amazon_message"] == (
        "There was a problem Your password is incorrect"
    )
    assert amazon_is_fake.register_body is None


async def test_removing_the_entry_deregisters_the_device(
    hass: HomeAssistant, amazon_is_fake: FakeAmazon
) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )
    await hass.async_block_till_done()

    assert await hass.config_entries.async_remove(result["result"].entry_id)
    await hass.async_block_till_done()

    assert amazon_is_fake.deregister_auth == "Bearer Atna|test-access-token"
