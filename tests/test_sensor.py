"""Tests for the entities."""

from __future__ import annotations

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

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
UNIQUE_ID = f"{SITE}_{USER_ID}"
KEYS = [
    "packages_in_transit",
    "next_package_status",
    "next_package_step",
    "next_package_progress",
    "next_package_expected",
    "next_package_last_event",
    "last_delivered",
]


async def _setup(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title=EMAIL,
        unique_id=UNIQUE_ID,
        data={CONF_SITE: SITE, "email": EMAIL, CONF_LOGIN_DATA: LOGIN_DATA.as_dict()},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


def _state(hass: HomeAssistant, key: str, platform: str = "sensor"):
    entity_id = er.async_get(hass).async_get_entity_id(
        platform, DOMAIN, f"{UNIQUE_ID}_{key}"
    )
    assert entity_id is not None, key
    return hass.states.get(entity_id)


async def test_entity_ids(hass: HomeAssistant, mock_amazon) -> None:
    await _setup(hass)
    registry = er.async_get(hass)

    assert sorted(
        entity.entity_id
        for entity in registry.entities.values()
        if entity.platform == DOMAIN
    ) == [
        "binary_sensor.amazon_package_in_transit",
        "sensor.amazon_last_delivered_package",
        "sensor.amazon_next_package_delivery",
        "sensor.amazon_next_package_last_event",
        "sensor.amazon_next_package_progress",
        "sensor.amazon_next_package_status",
        "sensor.amazon_next_package_step",
        "sensor.amazon_packages_in_transit",
    ]


async def test_nothing_on_its_way(hass: HomeAssistant, mock_amazon) -> None:
    await _setup(hass)

    assert _state(hass, "packages_in_transit").state == "0"
    assert _state(hass, "packages_in_transit").attributes["packages"] == []
    assert _state(hass, "next_package_status").state == "idle"
    for key in KEYS[2:]:
        assert _state(hass, key).state == "unknown", key
    assert _state(hass, "package_in_transit", "binary_sensor").state == "off"


async def test_package_on_its_way(hass: HomeAssistant, mock_amazon) -> None:
    mock_amazon.get_orders.return_value = [make_order(1), make_order(2)]
    mock_amazon.trackings[tracking_url(1)] = make_tracking(
        "TEST_ON_ITS_WAY",
        steps=2,
        progress=80,
        expected="In arrivo domani",
        message="Spedito",
    )
    mock_amazon.trackings[tracking_url(2)] = make_tracking()
    await _setup(hass)

    count = _state(hass, "packages_in_transit")
    assert count.state == "1"
    assert count.attributes["pending"] == 0
    assert count.attributes["packages"] == [
        {
            "order_id": "123-0000001-7654321",
            "order_placed": "3 ottobre 2026",
            "items": ["Example product 1"],
            "status": "test_on_its_way",
            "milestone": "test_on_its_way",
            "step": 2,
            "step_label": "Spedito",
            # Shipped and 80 % of the way to "out for delivery": 1.8 of 3 stretches.
            "progress": 60,
            "step_progress": 80,
            "expected": "In arrivo domani",
            "carrier": "Consegna da Amazon",
            "status_text": "In arrivo",
            "status_detail": "Dettaglio",
            "last_event": "Spedito",
            "tracking_url": tracking_url(1),
            # From the orders list: the tracking page has no address here.
            "recipient": "Test User",
            "delivery_city": "Roma",
            "delivery_address": "Via Esempio 1, ROMA, RM 00100, Italia",
        }
    ]

    status = _state(hass, "next_package_status")
    assert status.state == "test_on_its_way"
    assert status.attributes["items"] == ["Example product 1"]
    step = _state(hass, "next_package_step")
    assert step.state == "Spedito"
    assert step.attributes["step_index"] == 2
    assert step.attributes["step_count"] == 4
    assert step.attributes["steps"] == [
        "Ordinato",
        "Spedito",
        "In consegna",
        "Consegnato",
    ]
    progress = _state(hass, "next_package_progress")
    assert progress.state == "60"
    assert progress.attributes["unit_of_measurement"] == "%"
    assert progress.attributes["step_progress"] == 80
    assert _state(hass, "next_package_expected").state == "In arrivo domani"
    event = _state(hass, "next_package_last_event")
    assert event.state == "Spedito"
    assert event.attributes["time"] == "16:07"
    assert event.attributes["location"] == "ROMA, IT"

    delivered = _state(hass, "last_delivered")
    assert delivered.state == "Consegnato 4 ottobre"
    assert delivered.attributes["order_id"] == "123-0000002-7654321"
    assert delivered.attributes["status"] == "delivered"

    assert _state(hass, "package_in_transit", "binary_sensor").state == "on"


async def test_entities_follow_the_updates(hass: HomeAssistant, mock_amazon) -> None:
    mock_amazon.get_orders.return_value = [make_order(1)]
    mock_amazon.trackings[tracking_url(1)] = make_tracking(
        "TEST_ON_ITS_WAY", steps=2, progress=50, expected="In arrivo domani"
    )
    entry = await _setup(hass)
    assert _state(hass, "packages_in_transit").state == "1"

    mock_amazon.trackings[tracking_url(1)] = make_tracking()
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()

    assert _state(hass, "packages_in_transit").state == "0"
    assert _state(hass, "next_package_status").state == "idle"
    assert _state(hass, "last_delivered").state == "Consegnato 4 ottobre"
    assert _state(hass, "package_in_transit", "binary_sensor").state == "off"
