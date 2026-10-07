"""Data models: orders, shipments and tracking events.

Texts ending in ``_text`` are shown as Amazon wrote them, in the language of the
website (e.g. "Consegnato 4 ottobre"). The codes in :class:`Tracking`
(``short_status``, ``last_milestone``) do not depend on the language.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

STATUS_DELIVERED = "DELIVERED"


@dataclass(frozen=True, slots=True)
class OrderItem:
    """A product inside a shipment."""

    title: str | None
    product_url: str | None
    image_url: str | None


@dataclass(frozen=True, slots=True)
class Shipment:
    """One delivery box of an order, as shown in the orders list."""

    status_text: str | None
    status_detail: str | None
    # Absolute URL of the "Track package" page. None when Amazon offers no
    # tracking for this shipment (seen on orders fulfilled by third-party sellers).
    tracking_url: str | None
    items: tuple[OrderItem, ...] = ()


@dataclass(frozen=True, slots=True)
class Order:
    """An order card from the orders list."""

    order_id: str
    placed_text: str | None
    total_text: str | None
    details_url: str | None
    shipments: tuple[Shipment, ...] = ()


@dataclass(frozen=True, slots=True)
class TrackingEvent:
    """A row of the tracking history, newest first as on the page."""

    date_text: str | None
    time_text: str | None
    message: str | None
    location: str | None


@dataclass(frozen=True, slots=True)
class Milestone:
    """One of the steps of a delivery, as shown by the progress bar of the page."""

    label: str | None
    reached: bool
    # How far the stretch leading to this step has been covered, in percent.
    percent_complete: int | None


@dataclass(frozen=True, slots=True)
class Tracking:
    """The state of a package, from its "Track package" page."""

    order_id: str | None
    shipment_id: str | None
    tracking_id: str | None
    short_status: str | None
    last_milestone: str | None
    milestones_reached: int | None
    percent_complete: int | None
    promise_message: str | None
    carrier: str | None
    timezone: str | None
    seller_fulfilled: bool | None
    events: tuple[TrackingEvent, ...] = ()
    # The steps of the delivery and the name of the current one, in the language
    # of the website. Amazon shows them only until the package is delivered.
    status_label: str | None = None
    milestones: tuple[Milestone, ...] = ()
    # The whole JSON the page embeds, as Amazon sent it. It holds identifiers of
    # the order and of the customer: never expose it as it is.
    raw_state: dict[str, Any] = field(default_factory=dict, compare=False, repr=False)

    @property
    def is_delivered(self) -> bool:
        """Return True when the package has been delivered."""
        return STATUS_DELIVERED in (self.short_status, self.last_milestone)
