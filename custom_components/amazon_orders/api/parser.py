"""Parsers turning Amazon's HTML and embedded JSON into models."""

from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import urljoin

from bs4 import BeautifulSoup, SoupStrainer, Tag

from .exceptions import AmazonParseError
from .models import (
    Address,
    Milestone,
    Order,
    OrderItem,
    Shipment,
    Tracking,
    TrackingEvent,
)

# Marker of the orders list. It is present even when there are no orders.
_ORDERS_PAGE_MARKER = "your-orders-content-container"
_ORDER_ID_RE = re.compile(r"\b\d{3}-\d{7}-\d{7}\b")
# While parsing, a filter sees the class attribute as one string
# ("order-card js-order-card"): a plain string would have to equal all of it.
_ORDER_CARD_CLASS_RE = re.compile(r"(?:^|\s)js-order-card(?:\s|$)")
_STATUS_CARD_CLASS_RE = re.compile(r"(?:^|\s)status-card(?:\s|$)")
_SHIPPING_ADDRESS_CLASS_RE = re.compile(r"(?:^|\s)shippingAddress(?:\s|$)")
_PAGE_STATE_RE = re.compile(
    r'<script\b[^>]*\bdata-a-state="[^"]*page-state[^"]*"[^>]*>(.*?)</script>',
    re.DOTALL,
)
_CAPTCHA_MARKERS = ("/errors/validateCaptcha", "auth-captcha-image")


def is_captcha_page(html: str) -> bool:
    """Return True if the page is a CAPTCHA / anti-bot challenge."""
    return any(marker in html for marker in _CAPTCHA_MARKERS)


def _text(element: Tag | None) -> str | None:
    """Return the text of an element with whitespace collapsed, or None if empty."""
    if element is None:
        return None
    return " ".join(element.get_text(" ").split()) or None


def _href(element: Tag | None, base_url: str) -> str | None:
    """Return the absolute URL of a link, or None."""
    if element is None:
        return None
    href = element.get("href")
    if not isinstance(href, str) or not href or href.startswith(("#", "javascript:")):
        return None
    return urljoin(base_url, href)


def parse_orders(html: str, base_url: str) -> list[Order]:
    """Parse the orders list page.

    Raise AmazonParseError if the page is not the orders list. An orders list
    without orders returns an empty list.
    """
    if _ORDERS_PAGE_MARKER not in html:
        raise AmazonParseError("The page is not the orders list")

    soup = BeautifulSoup(
        html, "html.parser", parse_only=SoupStrainer(class_=_ORDER_CARD_CLASS_RE)
    )
    orders: list[Order] = []
    for card in soup.find_all(class_="js-order-card"):
        order = _parse_order_card(card, base_url)
        if order is not None:
            orders.append(order)
    return orders


def _parse_order_card(card: Tag, base_url: str) -> Order | None:
    match = _ORDER_ID_RE.search(_text(card.select_one(".yohtmlc-order-id")) or "")
    if match is None:
        return None

    # The header shows, in this order, the date the order was placed and its
    # total. Their labels are localized, so they are found by position.
    values = [
        _text(item.select_one(".aok-break-word"))
        for item in card.select(
            ".order-header .a-col-left .order-header__header-list-item"
        )
    ]
    values += [None, None]

    details = card.select_one(
        '.yohtmlc-order-level-connections a[href*="order-details"]'
    )

    return Order(
        order_id=match.group(0),
        placed_text=values[0],
        total_text=values[1],
        details_url=_href(details, base_url),
        shipments=tuple(
            _parse_delivery_box(box, base_url) for box in card.select(".delivery-box")
        ),
        address=_parse_order_address(card),
    )


def _address(lines: list[str | None]) -> Address | None:
    clean = tuple(line for line in lines if line)
    return Address(lines=clean) if clean else None


def _parse_order_address(card: Tag) -> Address | None:
    """Read the address of the "Ship to" popover.

    The page keeps it in a template script that its own code copies into the
    header, so it is parsed on its own.
    """
    template = card.select_one('script[id^="shipToData-"]')
    if template is None or not template.string:
        return None
    popover = BeautifulSoup(template.string, "html.parser").select_one(
        ".a-popover-preload"
    )
    if popover is None:
        return None
    return _address(
        [" ".join(line.split()) for line in popover.get_text("\n").splitlines()]
    )


def _parse_delivery_box(box: Tag, base_url: str) -> Shipment:
    tracking = box.select_one('a[href*="/progress-tracker/"]')
    items = []
    for item in box.select(".item-box"):
        title_link = item.select_one(".yohtmlc-product-title a")
        image = item.select_one(".product-image img")
        image_url = image.get("src") if image is not None else None
        items.append(
            OrderItem(
                title=_text(item.select_one(".yohtmlc-product-title")),
                product_url=_href(title_link, base_url),
                image_url=image_url if isinstance(image_url, str) else None,
            )
        )
    return Shipment(
        status_text=_text(box.select_one(".yohtmlc-shipment-status-primaryText")),
        status_detail=_text(box.select_one(".yohtmlc-shipment-status-secondaryText")),
        tracking_url=_href(tracking, base_url),
        items=tuple(items),
    )


def _as_int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return int(value)


def _as_str(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def parse_tracking(html: str) -> Tracking:
    """Parse a "Track package" page.

    The state comes from the JSON the page embeds for its own scripts, the
    history of events from the HTML. Raise AmazonParseError if the JSON is missing.
    """
    match = _PAGE_STATE_RE.search(html)
    if match is None:
        raise AmazonParseError("The page is not a package tracking page")
    try:
        state = json.loads(match.group(1))
    except ValueError as err:
        raise AmazonParseError("The tracking state is not valid JSON") from err
    if not isinstance(state, dict):
        raise AmazonParseError("The tracking state has an unexpected format")

    promise = state.get("promise")
    promise = promise if isinstance(promise, dict) else {}
    progress = state.get("progressTracker")
    progress = progress if isinstance(progress, dict) else {}
    is_mfn = state.get("isMfn")

    soup = BeautifulSoup(
        html, "html.parser", parse_only=SoupStrainer(id="tracking-events-container")
    )
    container = soup.find(id="tracking-events-container")

    carrier = None
    events: list[TrackingEvent] = []
    if isinstance(container, Tag):
        carrier = _text(container.select_one(".tracking-event-carrier-header"))
        events = _parse_events(container)

    # The card with the steps of the delivery is there until the package arrives.
    status_card = BeautifulSoup(
        html, "html.parser", parse_only=SoupStrainer(class_=_STATUS_CARD_CLASS_RE)
    )
    shipping_address = BeautifulSoup(
        html,
        "html.parser",
        parse_only=SoupStrainer(class_=_SHIPPING_ADDRESS_CLASS_RE),
    )

    return Tracking(
        order_id=_as_str(state.get("orderId")),
        shipment_id=_as_str(state.get("shipmentId")),
        tracking_id=_as_str(state.get("trackingId")),
        short_status=_as_str(state.get("shortStatus")),
        last_milestone=_as_str(progress.get("lastReachedMilestone")),
        milestones_reached=_as_int(progress.get("numberOfReachedMilestones")),
        percent_complete=_as_int(progress.get("lastTransitionPercentComplete")),
        promise_message=_as_str(promise.get("promiseMessage")),
        carrier=carrier,
        timezone=_as_str(state.get("timezone")),
        seller_fulfilled=is_mfn if isinstance(is_mfn, bool) else None,
        events=tuple(events),
        status_label=_text(status_card.select_one(".pt-status-main-status")),
        milestones=tuple(
            _parse_milestone(element)
            for element in status_card.select(".pt-status-milestone")
        ),
        address=_address(
            [_text(line) for line in shipping_address.select(".shippingAddress p")]
        ),
        raw_state=state,
    )


def _parse_milestone(element: Tag) -> Milestone:
    percent = element.get("data-percent-complete")
    return Milestone(
        label=_text(element.select_one(".pt-status-milestone-label")),
        reached=element.get("data-reached") == "true",
        percent_complete=int(percent)
        if isinstance(percent, str) and percent.isdigit()
        else None,
    )


def _parse_events(container: Tag) -> list[TrackingEvent]:
    """Read the history: a date header, then one row per event of that day."""
    events: list[TrackingEvent] = []
    date_text: str | None = None
    current: dict[str, str | None] | None = None

    def flush() -> None:
        if current is not None and (current["message"] or current["time"]):
            events.append(
                TrackingEvent(
                    date_text=current["date"],
                    time_text=current["time"],
                    message=current["message"],
                    location=current["location"],
                )
            )

    for element in container.select(
        ".tracking-event-date, .tracking-event-time,"
        " .tracking-event-message, .tracking-event-location"
    ):
        classes = element.get("class") or []
        if "tracking-event-date" in classes:
            date_text = _text(element)
        elif "tracking-event-time" in classes:
            flush()
            current = {
                "date": date_text,
                "time": _text(element),
                "message": None,
                "location": None,
            }
        elif current is not None and "tracking-event-message" in classes:
            current["message"] = _text(element)
        elif current is not None:
            # Couriers other than Amazon leave a dangling comma ("Hub IT, ").
            current["location"] = (_text(element) or "").rstrip(" ,") or None
    flush()
    return events
