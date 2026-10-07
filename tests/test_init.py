"""Tests for setting up, unloading and removing a config entry."""

from __future__ import annotations

import aiohttp
import pytest
from homeassistant.config_entries import SOURCE_REAUTH, ConfigEntryState
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.amazon_orders.api import AmazonOrdersClient
from custom_components.amazon_orders.api.exceptions import (
    AmazonAuthError,
    AmazonConnectionError,
)
from custom_components.amazon_orders.const import CONF_LOGIN_DATA, CONF_SITE, DOMAIN

from .conftest import (
    EMAIL,
    LOGIN_DATA,
    USER_ID,
    make_order,
    make_tracking,
    tracking_url,
)

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")

SITE = "amazon.it"


def _entry(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title=EMAIL,
        unique_id=f"{SITE}_{USER_ID}",
        data={CONF_SITE: SITE, "email": EMAIL, CONF_LOGIN_DATA: LOGIN_DATA.as_dict()},
    )
    entry.add_to_hass(hass)
    return entry


async def test_setup_and_unload(hass: HomeAssistant, mock_amazon) -> None:
    entry = _entry(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.LOADED
    coordinator = entry.runtime_data
    assert isinstance(coordinator.client, AmazonOrdersClient)
    # The orders are read once, on a session that stores no cookies.
    assert mock_amazon.get_orders.call_count == 1
    assert isinstance(coordinator.auth._session.cookie_jar, aiohttp.DummyCookieJar)
    assert coordinator.client._refresh_token == LOGIN_DATA.refresh_token
    assert coordinator.client._site == SITE

    assert await hass.config_entries.async_unload(entry.entry_id)
    assert entry.state is ConfigEntryState.NOT_LOADED


async def test_setup_session_refused_starts_reauth(
    hass: HomeAssistant, mock_amazon
) -> None:
    mock_amazon.get_orders.side_effect = AmazonAuthError("The token is not valid")
    entry = _entry(hass)

    assert not await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.SETUP_ERROR
    flows = hass.config_entries.flow.async_progress()
    assert [flow["context"]["source"] for flow in flows] == [SOURCE_REAUTH]


async def test_setup_amazon_unreachable_is_retried(
    hass: HomeAssistant, mock_amazon
) -> None:
    mock_amazon.get_orders.side_effect = AmazonConnectionError("down")
    entry = _entry(hass)

    assert not await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.SETUP_RETRY
    assert hass.config_entries.flow.async_progress() == []


@pytest.mark.parametrize("deregister_fails", [False, True])
async def test_removing_the_entry_removes_the_device(
    hass: HomeAssistant, mock_amazon, hass_storage, deregister_fails: bool
) -> None:
    if deregister_fails:
        mock_amazon.deregister.side_effect = AmazonAuthError("refused")
    mock_amazon.get_orders.return_value = [make_order(1)]
    mock_amazon.trackings[tracking_url(1)] = make_tracking()
    entry = _entry(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert f"{DOMAIN}.{entry.entry_id}" in hass_storage

    assert await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()

    assert mock_amazon.deregister.call_count == 1
    assert mock_amazon.deregister.call_args.args[1:] == (LOGIN_DATA.refresh_token, SITE)
    assert hass.config_entries.async_entries(DOMAIN) == []
    # What was remembered about the delivered packages is deleted too.
    assert f"{DOMAIN}.{entry.entry_id}" not in hass_storage
