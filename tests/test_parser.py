"""Tests for the parsers, on synthetic pages reproducing Amazon's structure."""

from __future__ import annotations

import pytest

from custom_components.amazon_orders.api.exceptions import AmazonParseError
from custom_components.amazon_orders.api.parser import (
    is_captcha_page,
    parse_orders,
    parse_tracking,
)

BASE_URL = "https://www.amazon.it"


def test_orders_list(load_fixture) -> None:
    orders = parse_orders(load_fixture("orders.html"), BASE_URL)

    assert [order.order_id for order in orders] == [
        "123-1234567-1234567",
        "123-7654321-7654321",
    ]

    first = orders[0]
    assert first.placed_text == "3 ottobre 2026"
    assert first.total_text == "10,98 €"
    assert first.details_url == (
        "https://www.amazon.it/your-orders/order-details"
        "?orderID=123-1234567-1234567&ref=ppx_yo2ov_dt_b_fed_order_details"
    )
    assert len(first.shipments) == 1

    shipment = first.shipments[0]
    assert shipment.status_text == "Consegnato 4 ottobre"
    assert shipment.status_detail == (
        "Il pacco è stato consegnato presso il domicilio selezionato"
    )
    assert shipment.tracking_url is not None
    assert shipment.tracking_url.startswith(
        "https://www.amazon.it/progress-tracker/package?"
    )
    assert "shipmentId=TESTSHIP1" in shipment.tracking_url

    assert len(shipment.items) == 1
    item = shipment.items[0]
    assert item.title == "Example product one"
    assert item.product_url == (
        "https://www.amazon.it/dp/B000TEST01?ref=ppx_yo2ov_dt_b_fed_asin_title"
    )
    assert item.image_url == (
        "https://m.media-amazon.com/images/I/test-B000TEST01._SS142_.jpg"
    )


def test_order_without_tracking_link(load_fixture) -> None:
    order = parse_orders(load_fixture("orders.html"), BASE_URL)[1]

    assert order.shipments[0].status_text == "Reso completo"
    assert order.shipments[0].tracking_url is None


def test_orders_list_without_orders(load_fixture) -> None:
    assert parse_orders(load_fixture("orders_empty.html"), BASE_URL) == []


@pytest.mark.parametrize("page", ["signin.html", "tracking_delivered.html"])
def test_orders_rejects_other_pages(load_fixture, page: str) -> None:
    with pytest.raises(AmazonParseError):
        parse_orders(load_fixture(page), BASE_URL)


def test_tracking_delivered(load_fixture) -> None:
    tracking = parse_tracking(load_fixture("tracking_delivered.html"))

    assert tracking.order_id == "123-1234567-1234567"
    assert tracking.shipment_id == "TESTSHIP1"
    assert tracking.tracking_id == "TESTTRACKING123"
    assert tracking.short_status == "DELIVERED"
    assert tracking.last_milestone == "DELIVERED"
    assert tracking.milestones_reached == 4
    assert tracking.percent_complete == 100
    assert tracking.promise_message == "Consegnato 4 ottobre"
    assert tracking.carrier == "Consegna da Amazon"
    assert tracking.timezone == "Europe/Rome"
    assert tracking.seller_fulfilled is False
    assert tracking.is_delivered is True


def test_tracking_order_just_placed(load_fixture) -> None:
    tracking = parse_tracking(load_fixture("tracking_ordered.html"))

    assert tracking.short_status == "ORDER_PLACED"
    assert tracking.last_milestone == "ORDERED"
    assert tracking.milestones_reached == 1
    assert tracking.percent_complete == 32
    assert tracking.promise_message == "In arrivo domani"
    assert tracking.is_delivered is False
    # Nothing has been shipped yet: no carrier, no shipment, no history.
    assert tracking.carrier is None
    assert tracking.shipment_id is None
    assert tracking.tracking_id is None
    assert tracking.events == ()

    assert tracking.status_label == "Ordinato"
    assert [step.label for step in tracking.milestones] == [
        "Ordinato",
        "Spedito",
        "In consegna",
        "Consegnato",
    ]
    assert [step.reached for step in tracking.milestones] == [True, False, False, False]
    assert [step.percent_complete for step in tracking.milestones] == [100, 10, 0, 0]


def test_tracking_delivered_has_no_steps(load_fixture) -> None:
    tracking = parse_tracking(load_fixture("tracking_delivered.html"))

    assert tracking.status_label is None
    assert tracking.milestones == ()


def test_tracking_events(load_fixture) -> None:
    events = parse_tracking(load_fixture("tracking_delivered.html")).events

    assert [event.message for event in events] == [
        "Consegnato",
        "In consegna",
        "Il pacco è arrivato presso la stazione di consegna finale",
        "Il pacco ha lasciato la struttura del mittente",
    ]
    assert {event.date_text for event in events} == {"domenica 4 ottobre"}
    assert events[0].time_text == "16:07"
    assert events[0].location == "ROMA, IT"
    # The first event of a shipment has neither a time nor a place.
    assert events[-1].time_text is None
    assert events[-1].location is None


@pytest.mark.parametrize("page", ["signin.html", "orders.html"])
def test_tracking_rejects_other_pages(load_fixture, page: str) -> None:
    with pytest.raises(AmazonParseError):
        parse_tracking(load_fixture(page))


def test_tracking_with_missing_fields() -> None:
    html = (
        '<script type="a-state" data-a-state="{&quot;key&quot;:&quot;page-state&quot;}">'
        '{"shortStatus":"NOT_A_REAL_CODE","progressTracker":"unexpected"}</script>'
    )

    tracking = parse_tracking(html)

    assert tracking.short_status == "NOT_A_REAL_CODE"
    assert tracking.last_milestone is None
    assert tracking.milestones_reached is None
    assert tracking.events == ()
    assert tracking.is_delivered is False


def test_tracking_with_broken_json() -> None:
    html = (
        '<script type="a-state" data-a-state="{&quot;key&quot;:&quot;page-state&quot;}">'
        "{not json</script>"
    )

    with pytest.raises(AmazonParseError):
        parse_tracking(html)


def test_captcha_detection(load_fixture) -> None:
    assert is_captcha_page('<form action="/errors/validateCaptcha">') is True
    assert is_captcha_page(load_fixture("orders.html")) is False
