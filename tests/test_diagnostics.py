"""Tests for the diagnostics file."""

from __future__ import annotations

import json

import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.amazon_orders.const import CONF_LOGIN_DATA, CONF_SITE, DOMAIN
from custom_components.amazon_orders.diagnostics import (
    async_get_config_entry_diagnostics,
)

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
# Everything in the entry and in the packages that would identify a user, an
# order or a product if it were real.
PRIVATE = [
    EMAIL,
    USER_ID,
    LOGIN_DATA.refresh_token,
    LOGIN_DATA.device_serial,
    "Test User",
    "Test's Home Assistant",
    "123-0000001-7654321",
    "123-0000002-7654321",
    "123-1234567-1234567",
    "Example product",
    "SHIP1",
    "TESTSHIP",
    "TESTTRACKING123",
    "TESTCUSTOMER",
    "ROMA",
    "10,98",
    "progress-tracker",
]


async def _setup(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title=EMAIL,
        unique_id=f"{SITE}_{USER_ID}",
        data={CONF_SITE: SITE, "email": EMAIL, CONF_LOGIN_DATA: LOGIN_DATA.as_dict()},
        options={"idle_interval": 600, "active_interval": 90},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_diagnostics(hass: HomeAssistant, mock_amazon) -> None:
    mock_amazon.get_orders.return_value = [make_order(1), make_order(2)]
    mock_amazon.trackings[tracking_url(1)] = make_tracking(
        "TEST_ON_ITS_WAY", steps=2, progress=50, expected="In arrivo domani"
    )
    mock_amazon.trackings[tracking_url(2)] = make_tracking()
    entry = await _setup(hass)

    result = await async_get_config_entry_diagnostics(hass, entry)

    text = json.dumps(result, ensure_ascii=False)
    for private in PRIVATE:
        assert private not in text, private

    assert result["entry"][CONF_SITE] == SITE
    assert result["entry"]["email"] == "**REDACTED**"
    assert result["entry"][CONF_LOGIN_DATA]["customer_info"]["home_region"] == "EU"
    assert result["options"] == {"idle_interval": 600, "active_interval": 90}
    assert result["update_interval"] == 600
    assert result["last_update_success"] is True
    assert result["pending"] == 0

    (package,) = result["in_transit"]
    assert package["placed"] == "3 ottobre 2026"
    assert package["items"] == 1
    assert package["short_status"] == "TEST_ON_ITS_WAY"
    assert package["milestones_reached"] == 2
    assert package["status_label"] == "Spedito"
    assert [step["label"] for step in package["milestones"]] == [
        "Ordinato",
        "Spedito",
        "In consegna",
        "Consegnato",
    ]
    assert [step["reached"] for step in package["milestones"]] == [
        True,
        True,
        False,
        False,
    ]
    assert package["percent_complete"] == 50
    assert package["promise_message"] == "In arrivo domani"
    assert package["is_delivered"] is False
    assert package["events"] == [
        {
            "date": "domenica 4 ottobre",
            "time": "16:07",
            "message": "Consegnato",
            "location": "**REDACTED**",
        }
    ]
    # Names of the fields Amazon sent, never their values.
    assert package["state_keys"] == ["customerId", "shortStatus"]

    assert result["last_delivered"]["short_status"] == "DELIVERED"
    assert result["last_delivered"]["is_delivered"] is True


async def test_diagnostics_without_packages(hass: HomeAssistant, mock_amazon) -> None:
    entry = await _setup(hass)

    result = await async_get_config_entry_diagnostics(hass, entry)

    assert result["in_transit"] == []
    assert result["last_delivered"] is None
