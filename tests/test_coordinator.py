"""Tests for the coordinator: which pages are read, intervals, events, storage."""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta

import pytest
from homeassistant.config_entries import SOURCE_REAUTH, ConfigEntryState
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_capture_events,
)

from custom_components.amazon_orders.api.exceptions import (
    AmazonAuthError,
    AmazonCaptchaError,
    AmazonConnectionError,
)
from custom_components.amazon_orders.api.models import Order, Shipment
from custom_components.amazon_orders.const import (
    CAPTCHA_BACKOFF,
    CONF_LOGIN_DATA,
    CONF_SITE,
    DEFAULT_ACTIVE_INTERVAL,
    DEFAULT_IDLE_INTERVAL,
    DOMAIN,
    EVENT_PACKAGE_UPDATE,
    MAX_NEW_TRACKING_PER_UPDATE,
)
from custom_components.amazon_orders.coordinator import Package, shipment_key

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
IDLE = timedelta(seconds=DEFAULT_IDLE_INTERVAL)
ACTIVE = timedelta(seconds=DEFAULT_ACTIVE_INTERVAL)


def on_its_way(steps: int = 2, expected: str = "In arrivo domani"):
    """A package not delivered yet. The state code is invented."""
    return make_tracking(
        "TEST_ON_ITS_WAY", steps=steps, progress=steps * 25, expected=expected
    )


async def _setup(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title=EMAIL,
        unique_id=f"{SITE}_{USER_ID}",
        data={CONF_SITE: SITE, "email": EMAIL, CONF_LOGIN_DATA: LOGIN_DATA.as_dict()},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_no_orders(hass: HomeAssistant, mock_amazon) -> None:
    coordinator = (await _setup(hass)).runtime_data

    assert coordinator.data.in_transit == ()
    assert coordinator.data.last_delivered is None
    assert coordinator.data.next_package is None
    assert coordinator.update_interval == IDLE


async def test_delivered_package_is_read_only_once(
    hass: HomeAssistant, mock_amazon
) -> None:
    mock_amazon.get_orders.return_value = [make_order(1), make_order(2, tracking=False)]
    mock_amazon.trackings[tracking_url(1)] = make_tracking()
    coordinator = (await _setup(hass)).runtime_data

    assert coordinator.data.in_transit == ()
    assert coordinator.data.last_delivered.order_id == "123-0000001-7654321"
    assert coordinator.data.last_delivered.item_titles == ("Example product 1",)
    assert mock_amazon.get_tracking.call_count == 1

    await coordinator.async_refresh()
    await coordinator.async_refresh()

    assert mock_amazon.get_orders.call_count == 3
    assert mock_amazon.get_tracking.call_count == 1
    assert coordinator.data.last_delivered.order_id == "123-0000001-7654321"
    assert coordinator.update_interval == IDLE


async def test_package_in_transit_is_read_at_every_update(
    hass: HomeAssistant, mock_amazon
) -> None:
    mock_amazon.get_orders.return_value = [make_order(1), make_order(2)]
    mock_amazon.trackings[tracking_url(1)] = on_its_way()
    mock_amazon.trackings[tracking_url(2)] = make_tracking()
    coordinator = (await _setup(hass)).runtime_data

    assert [package.order_id for package in coordinator.data.in_transit] == [
        "123-0000001-7654321"
    ]
    assert coordinator.data.next_package.status == "test_on_its_way"
    assert coordinator.data.last_delivered.order_id == "123-0000002-7654321"
    assert coordinator.update_interval == IDLE

    await coordinator.async_refresh()

    # One more read for the package in transit, none for the delivered one.
    assert mock_amazon.get_tracking.call_count == 3


async def test_interval_shortens_from_the_third_step(
    hass: HomeAssistant, mock_amazon
) -> None:
    mock_amazon.get_orders.return_value = [make_order(1)]
    mock_amazon.trackings[tracking_url(1)] = on_its_way(steps=2)
    coordinator = (await _setup(hass)).runtime_data
    assert coordinator.update_interval == IDLE

    mock_amazon.trackings[tracking_url(1)] = on_its_way(steps=3)
    await coordinator.async_refresh()
    assert coordinator.update_interval == ACTIVE

    mock_amazon.trackings[tracking_url(1)] = make_tracking()
    await coordinator.async_refresh()
    assert coordinator.update_interval == IDLE
    assert coordinator.data.in_transit == ()


async def test_next_package_is_the_closest_to_delivery(
    hass: HomeAssistant, mock_amazon
) -> None:
    mock_amazon.get_orders.return_value = [make_order(1), make_order(2), make_order(3)]
    mock_amazon.trackings[tracking_url(1)] = on_its_way(steps=1)
    mock_amazon.trackings[tracking_url(2)] = on_its_way(steps=3)
    mock_amazon.trackings[tracking_url(3)] = on_its_way(steps=2)
    coordinator = (await _setup(hass)).runtime_data

    assert len(coordinator.data.in_transit) == 3
    assert coordinator.data.next_package.order_id == "123-0000002-7654321"


async def test_events(hass: HomeAssistant, mock_amazon) -> None:
    events = async_capture_events(hass, EVENT_PACKAGE_UPDATE)
    mock_amazon.get_orders.return_value = [make_order(1)]
    mock_amazon.trackings[tracking_url(1)] = on_its_way(steps=1)
    entry = await _setup(hass)
    coordinator = entry.runtime_data
    await hass.async_block_till_done()
    # Nothing for what the first update finds.
    assert events == []

    # Nothing while the package stays the same.
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert events == []

    # The package moves on.
    mock_amazon.trackings[tracking_url(1)] = make_tracking(
        "TEST_NEARLY_THERE",
        steps=3,
        progress=75,
        expected="In arrivo oggi",
        message="In consegna",
    )
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert len(events) == 1
    assert events[0].data == {
        "type": "update",
        "order_id": "123-0000001-7654321",
        "status": "test_nearly_there",
        "previous_status": "test_on_its_way",
        "step": 3,
        "step_label": "In consegna",
        # Out for delivery and 75 % of the last stretch: 2.75 of 3 stretches.
        "progress": 92,
        "expected": "In arrivo oggi",
        "carrier": "Consegna da Amazon",
        "items": ["Example product 1"],
        "last_event": "In consegna",
        "is_delivered": False,
        "config_entry_id": entry.entry_id,
    }

    # A second order is placed.
    mock_amazon.get_orders.return_value = [make_order(2), make_order(1)]
    mock_amazon.trackings[tracking_url(2)] = on_its_way(steps=1)
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert [event.data["type"] for event in events] == ["update", "new"]
    assert events[1].data["order_id"] == "123-0000002-7654321"
    assert events[1].data["previous_status"] is None

    # The first package is delivered.
    mock_amazon.trackings[tracking_url(1)] = make_tracking()
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert [event.data["type"] for event in events] == ["update", "new", "delivered"]
    assert events[2].data["status"] == "delivered"
    assert events[2].data["previous_status"] == "test_nearly_there"
    assert events[2].data["is_delivered"] is True

    # Once delivered it is not mentioned again.
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert len(events) == 3


async def test_new_shipments_are_read_a_few_at_a_time(
    hass: HomeAssistant, mock_amazon
) -> None:
    total = MAX_NEW_TRACKING_PER_UPDATE + 3
    mock_amazon.get_orders.return_value = [make_order(n) for n in range(1, total + 1)]
    for n in range(1, total + 1):
        mock_amazon.trackings[tracking_url(n)] = make_tracking()
    coordinator = (await _setup(hass)).runtime_data

    assert mock_amazon.get_tracking.call_count == MAX_NEW_TRACKING_PER_UPDATE
    assert coordinator.data.pending == 3
    # The rest is read soon, not at the next idle update.
    assert coordinator.update_interval == ACTIVE

    await coordinator.async_refresh()

    assert mock_amazon.get_tracking.call_count == total
    assert coordinator.data.pending == 0
    assert coordinator.update_interval == IDLE


async def test_tracking_page_error(hass: HomeAssistant, mock_amazon) -> None:
    mock_amazon.get_orders.return_value = [make_order(1), make_order(2)]
    mock_amazon.trackings[tracking_url(1)] = on_its_way()
    mock_amazon.trackings[tracking_url(2)] = AmazonConnectionError("HTTP 503")
    coordinator = (await _setup(hass)).runtime_data

    # A package never read stays pending, the update as a whole succeeds.
    assert coordinator.last_update_success is True
    assert len(coordinator.data.in_transit) == 1
    assert coordinator.data.pending == 1

    # A package already known keeps its last state.
    mock_amazon.trackings[tracking_url(1)] = AmazonConnectionError("HTTP 503")
    mock_amazon.trackings[tracking_url(2)] = on_its_way(steps=1)
    await coordinator.async_refresh()

    assert coordinator.last_update_success is True
    assert [package.status for package in coordinator.data.in_transit] == [
        "test_on_its_way",
        "test_on_its_way",
    ]
    assert coordinator.data.in_transit[0].tracking.milestones_reached == 2
    assert coordinator.data.pending == 0


async def test_session_refused_starts_reauth(hass: HomeAssistant, mock_amazon) -> None:
    entry = await _setup(hass)
    mock_amazon.get_orders.side_effect = AmazonAuthError("The token is not valid")

    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()

    assert entry.runtime_data.last_update_success is False
    flows = hass.config_entries.flow.async_progress()
    assert [flow["context"]["source"] for flow in flows] == [SOURCE_REAUTH]


async def test_captcha_backs_off(hass: HomeAssistant, mock_amazon) -> None:
    mock_amazon.get_orders.return_value = [make_order(1)]
    mock_amazon.trackings[tracking_url(1)] = on_its_way(steps=3)
    coordinator = (await _setup(hass)).runtime_data
    assert coordinator.update_interval == ACTIVE

    mock_amazon.trackings[tracking_url(1)] = AmazonCaptchaError("captcha")
    await coordinator.async_refresh()

    assert coordinator.last_update_success is False
    assert coordinator.update_interval == CAPTCHA_BACKOFF
    assert hass.config_entries.flow.async_progress() == []

    mock_amazon.trackings[tracking_url(1)] = on_its_way(steps=3)
    await coordinator.async_refresh()

    assert coordinator.last_update_success is True
    assert coordinator.update_interval == ACTIVE


async def test_amazon_unreachable(hass: HomeAssistant, mock_amazon) -> None:
    mock_amazon.get_orders.return_value = [make_order(1)]
    mock_amazon.trackings[tracking_url(1)] = on_its_way()
    coordinator = (await _setup(hass)).runtime_data

    mock_amazon.get_orders.side_effect = AmazonConnectionError("down")
    await coordinator.async_refresh()

    assert coordinator.last_update_success is False
    assert coordinator.update_interval == IDLE


async def test_delivered_packages_are_remembered_across_restarts(
    hass: HomeAssistant, mock_amazon, hass_storage
) -> None:
    mock_amazon.get_orders.return_value = [make_order(1), make_order(2)]
    mock_amazon.trackings[tracking_url(1)] = on_its_way()
    mock_amazon.trackings[tracking_url(2)] = make_tracking()
    entry = await _setup(hass)

    stored = hass_storage[f"{DOMAIN}.{entry.entry_id}"]["data"]["delivered"]
    assert list(stored) == ["123-0000002-7654321:SHIP2:0"]
    assert stored["123-0000002-7654321:SHIP2:0"]["short_status"] == "DELIVERED"
    assert mock_amazon.get_tracking.call_count == 2

    assert await hass.config_entries.async_unload(entry.entry_id)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.LOADED
    # After the restart only the package in transit is read again.
    assert mock_amazon.get_tracking.call_count == 3
    last = entry.runtime_data.data.last_delivered
    assert last.order_id == "123-0000002-7654321"
    assert last.tracking.promise_message == "Consegnato 4 ottobre"
    assert last.last_event.message == "Consegnato"


async def test_packages_gone_from_the_list_are_forgotten(
    hass: HomeAssistant, mock_amazon, hass_storage
) -> None:
    mock_amazon.get_orders.return_value = [make_order(1)]
    mock_amazon.trackings[tracking_url(1)] = make_tracking()
    entry = await _setup(hass)
    key = f"{DOMAIN}.{entry.entry_id}"
    assert len(hass_storage[key]["data"]["delivered"]) == 1

    mock_amazon.get_orders.return_value = []
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()

    assert hass_storage[key]["data"]["delivered"] == {}
    assert entry.runtime_data.data.last_delivered is None


def test_shipment_key() -> None:
    order = make_order(7)
    assert shipment_key(order, order.shipments[0]) == "123-0000007-7654321:SHIP7:0"

    odd = Shipment(
        "x", None, "https://www.amazon.it/progress-tracker/package/abc?ref=1"
    )
    assert shipment_key(Order("123-1", None, None, None, (odd,)), odd) == (
        "123-1:/progress-tracker/package/abc"
    )


@pytest.mark.parametrize(
    ("status", "steps", "stretch", "expected"),
    [
        # Just ordered: a third of the way to "shipped".
        ("ORDER_PLACED", 1, 32, 11),
        # Shipped, almost out for delivery (seen on a real package: 92).
        ("IN_TRANSIT", 2, 92, 64),
        ("IN_TRANSIT", 2, 32, 44),
        ("OUT_FOR_DELIVERY", 3, 0, 67),
        ("DELIVERED", 4, 100, 100),
        # Values outside the range are clamped.
        ("IN_TRANSIT", 2, 250, 67),
        ("IN_TRANSIT", 2, None, None),
    ],
)
def test_package_progress(
    status: str, steps: int, stretch: int | None, expected: int | None
) -> None:
    tracking = replace(make_tracking(status, steps=steps), percent_complete=stretch)
    package = Package(
        key="k",
        order_id="123-0000001-7654321",
        placed_text=None,
        status_text=None,
        status_detail=None,
        tracking_url=tracking_url(1),
        item_titles=(),
        tracking=tracking,
    )
    assert package.progress == expected


def test_package_progress_without_steps() -> None:
    """Without the list of steps on the page, a delivery has four of them."""
    tracking = replace(
        make_tracking("IN_TRANSIT", steps=2), milestones=(), percent_complete=50
    )
    package = Package(
        key="k",
        order_id="123-0000001-7654321",
        placed_text=None,
        status_text=None,
        status_detail=None,
        tracking_url=tracking_url(1),
        item_titles=(),
        tracking=tracking,
    )
    assert package.progress == 50

async def test_locker_picked_up_is_not_in_transit(
    hass: HomeAssistant, mock_amazon
) -> None:
    """A collected Locker package is a completed shipment."""
    mock_amazon.get_orders.return_value = [make_order(1)]
    mock_amazon.trackings[tracking_url(1)] = make_tracking(
        "PICKED_UP",
        steps=4,
        progress=100,
        expected="Ritirato 25 settembre",
        message="Spedizione ritirata dall'Amazon Locker",
    )

    coordinator = (await _setup(hass)).runtime_data

    assert coordinator.data.in_transit == ()
    assert coordinator.data.next_package is None
    assert coordinator.data.last_delivered is not None
    assert coordinator.data.last_delivered.status == "picked_up"
    assert coordinator.data.last_delivered.progress == 100
    assert coordinator.update_interval == IDLE

    await coordinator.async_refresh()

    # Completed packages are not tracked again.
    assert mock_amazon.get_tracking.call_count == 1


async def test_locker_pickup_generates_completion_event(
    hass: HomeAssistant, mock_amazon
) -> None:
    """A pickup generates a distinct event, not a delivery event."""
    events = async_capture_events(hass, EVENT_PACKAGE_UPDATE)

    mock_amazon.get_orders.return_value = [make_order(1)]
    mock_amazon.trackings[tracking_url(1)] = on_its_way()

    entry = await _setup(hass)
    coordinator = entry.runtime_data
    await hass.async_block_till_done()

    assert len(coordinator.data.in_transit) == 1
    assert events == []

    mock_amazon.trackings[tracking_url(1)] = make_tracking(
        "PICKED_UP",
        steps=4,
        progress=100,
        expected="Ritirato 25 settembre",
        message="Spedizione ritirata dall'Amazon Locker",
    )

    await coordinator.async_refresh()
    await hass.async_block_till_done()

    assert coordinator.data.in_transit == ()
    assert len(events) == 1

    event = events[0].data

    assert event["type"] == "picked_up"
    assert event["status"] == "picked_up"
    assert event["previous_status"] == "test_on_its_way"
    assert event["is_delivered"] is False
    assert event["progress"] == 100

    # No repeated completion events.
    await coordinator.async_refresh()
    await hass.async_block_till_done()

    assert len(events) == 1
    assert mock_amazon.get_tracking.call_count == 2


async def test_locker_pickup_persists_across_restart(
    hass: HomeAssistant, mock_amazon, hass_storage
) -> None:
    """A collected package remains completed after HA restarts."""
    mock_amazon.get_orders.return_value = [make_order(1)]
    mock_amazon.trackings[tracking_url(1)] = make_tracking(
        "PICKED_UP",
        steps=4,
        progress=100,
        expected="Ritirato 25 settembre",
        message="Spedizione ritirata dall'Amazon Locker",
    )

    entry = await _setup(hass)

    key = f"{DOMAIN}.{entry.entry_id}"
    stored = hass_storage[key]["data"]["delivered"]

    assert len(stored) == 1
    assert list(stored.values())[0]["short_status"] == "PICKED_UP"
    assert mock_amazon.get_tracking.call_count == 1

    assert await hass.config_entries.async_unload(entry.entry_id)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.LOADED

    coordinator = entry.runtime_data

    assert coordinator.data.in_transit == ()
    assert coordinator.data.last_delivered is not None
    assert coordinator.data.last_delivered.status == "picked_up"
    assert coordinator.data.last_delivered.progress == 100

    # No additional tracking request after restart.
    assert mock_amazon.get_tracking.call_count == 1


def test_locker_picked_up_is_terminal() -> None:
    """PICKED_UP is complete without being a home delivery."""
    tracking = make_tracking(
        "PICKED_UP",
        steps=4,
        progress=100,
    )

    assert tracking.is_complete is True
    assert tracking.is_delivered is False

    package = Package(
        key="locker",
        order_id="123-0000001-7654321",
        placed_text=None,
        status_text="Ritirato 25 settembre",
        status_detail=None,
        tracking_url=tracking_url(1),
        item_titles=(),
        tracking=tracking,
    )

    assert package.status == "picked_up"
    assert package.progress == 100
