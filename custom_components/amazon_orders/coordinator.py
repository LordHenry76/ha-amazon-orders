"""Data update coordinator for Amazon Orders, with adaptive polling."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timedelta
from typing import Any
from urllib.parse import parse_qs, urlsplit

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import (
    AmazonAuth,
    AmazonAuthError,
    AmazonCaptchaError,
    AmazonError,
    AmazonOrdersClient,
    Order,
    Shipment,
    Tracking,
    TrackingEvent,
)
from .const import (
    CAPTCHA_BACKOFF,
    CONF_ACTIVE_INTERVAL,
    CONF_IDLE_INTERVAL,
    DEFAULT_ACTIVE_INTERVAL,
    DEFAULT_IDLE_INTERVAL,
    DOMAIN,
    EVENT_PACKAGE_UPDATE,
    MAX_NEW_TRACKING_PER_UPDATE,
    OUT_FOR_DELIVERY_MIN_STEPS,
    STORAGE_VERSION,
)

_LOGGER = logging.getLogger(__name__)

type AmazonOrdersConfigEntry = ConfigEntry[AmazonOrdersCoordinator]


@dataclass(frozen=True, slots=True)
class Package:
    """A shipment Amazon offers tracking for, with its latest state."""

    key: str
    order_id: str
    placed_text: str | None
    status_text: str | None
    status_detail: str | None
    tracking_url: str
    item_titles: tuple[str, ...]
    tracking: Tracking

    @property
    def status(self) -> str | None:
        """Return Amazon's state code in lower case, e.g. ``delivered``."""
        code = self.tracking.short_status or self.tracking.last_milestone
        return code.lower() if code else None

    @property
    def step_label(self) -> str | None:
        """Return the name of the current step, in the language of the website."""
        if self.tracking.status_label:
            return self.tracking.status_label
        reached = [step.label for step in self.tracking.milestones if step.reached]
        return reached[-1] if reached else None

    @property
    def step_labels(self) -> list[str]:
        """Return the names of all the steps of the delivery."""
        return [step.label for step in self.tracking.milestones if step.label]

    @property
    def last_event(self) -> TrackingEvent | None:
        """Return the most recent event of the tracking history."""
        return self.tracking.events[0] if self.tracking.events else None


@dataclass(frozen=True, slots=True)
class AmazonOrdersData:
    """What the entities show."""

    # Packages not delivered yet, in the order of the orders list (newest first).
    in_transit: tuple[Package, ...] = ()
    last_delivered: Package | None = None
    # Shipments whose tracking page has not been read yet.
    pending: int = 0

    @property
    def next_package(self) -> Package | None:
        """Return the package closest to its delivery."""
        if not self.in_transit:
            return None
        return max(
            self.in_transit,
            key=lambda package: (
                package.tracking.milestones_reached or 0,
                package.tracking.percent_complete or 0,
            ),
        )


def shipment_key(order: Order, shipment: Shipment) -> str:
    """Return what identifies a shipment across updates."""
    query = parse_qs(urlsplit(shipment.tracking_url or "").query)
    shipment_id = (query.get("shipmentId") or [""])[0]
    package_index = (query.get("packageIndex") or ["0"])[0]
    if shipment_id:
        return f"{order.order_id}:{shipment_id}:{package_index}"
    return f"{order.order_id}:{urlsplit(shipment.tracking_url or '').path}"


def _tracking_to_dict(tracking: Tracking) -> dict[str, Any]:
    return {
        "order_id": tracking.order_id,
        "shipment_id": tracking.shipment_id,
        "tracking_id": tracking.tracking_id,
        "short_status": tracking.short_status,
        "last_milestone": tracking.last_milestone,
        "milestones_reached": tracking.milestones_reached,
        "percent_complete": tracking.percent_complete,
        "promise_message": tracking.promise_message,
        "carrier": tracking.carrier,
        "timezone": tracking.timezone,
        "seller_fulfilled": tracking.seller_fulfilled,
        "events": [
            [event.date_text, event.time_text, event.message, event.location]
            for event in tracking.events
        ],
    }


def _tracking_from_dict(data: dict[str, Any]) -> Tracking:
    return Tracking(
        order_id=data.get("order_id"),
        shipment_id=data.get("shipment_id"),
        tracking_id=data.get("tracking_id"),
        short_status=data.get("short_status"),
        last_milestone=data.get("last_milestone"),
        milestones_reached=data.get("milestones_reached"),
        percent_complete=data.get("percent_complete"),
        promise_message=data.get("promise_message"),
        carrier=data.get("carrier"),
        timezone=data.get("timezone"),
        seller_fulfilled=data.get("seller_fulfilled"),
        events=tuple(TrackingEvent(*event) for event in data.get("events") or ()),
    )


def _signature(tracking: Tracking) -> tuple[Any, ...]:
    """Return what, when it changes, is worth an event."""
    return (
        tracking.short_status,
        tracking.last_milestone,
        tracking.milestones_reached,
        tracking.percent_complete,
        tracking.promise_message,
    )


class AmazonOrdersCoordinator(DataUpdateCoordinator[AmazonOrdersData]):
    """Reads the orders list and follows each package until it is delivered.

    A delivered package never changes again, so its tracking page is read only
    once: what it said is kept, also across restarts.
    """

    config_entry: AmazonOrdersConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        entry: AmazonOrdersConfigEntry,
        auth: AmazonAuth,
        client: AmazonOrdersClient,
    ) -> None:
        """Initialize."""
        self.auth = auth
        self.client = client
        self.idle_interval = timedelta(
            seconds=entry.options.get(CONF_IDLE_INTERVAL, DEFAULT_IDLE_INTERVAL)
        )
        self.active_interval = timedelta(
            seconds=entry.options.get(CONF_ACTIVE_INTERVAL, DEFAULT_ACTIVE_INTERVAL)
        )
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=self.idle_interval,
        )
        self._store: Store[dict[str, Any]] = Store(
            hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}"
        )
        self._delivered: dict[str, Tracking] = {}
        self._in_transit: dict[str, Tracking] = {}
        self._first_update_done = False

    async def _async_setup(self) -> None:
        """Load what is known about the packages already delivered."""
        stored = await self._store.async_load() or {}
        for key, data in (stored.get("delivered") or {}).items():
            try:
                self._delivered[key] = _tracking_from_dict(data)
            except (TypeError, AttributeError):
                continue

    async def async_remove_store(self) -> None:
        """Delete what was stored, when the config entry is removed."""
        await self._store.async_remove()

    async def _async_update_data(self) -> AmazonOrdersData:
        try:
            orders = await self.client.async_get_orders()
            data = await self._async_read_packages(orders)
        except AmazonAuthError as err:
            raise ConfigEntryAuthFailed(
                f"Amazon no longer accepts the stored session: {err}"
            ) from err
        except AmazonCaptchaError as err:
            self.update_interval = max(self.idle_interval, CAPTCHA_BACKOFF)
            raise UpdateFailed(f"Amazon answered with a CAPTCHA: {err}") from err
        except AmazonError as err:
            raise UpdateFailed(f"Cannot read the orders from Amazon: {err}") from err

        out_for_delivery = any(
            (package.tracking.milestones_reached or 0) >= OUT_FOR_DELIVERY_MIN_STEPS
            for package in data.in_transit
        )
        self.update_interval = (
            self.active_interval
            if out_for_delivery or data.pending
            else self.idle_interval
        )
        return data

    async def _async_read_packages(self, orders: list[Order]) -> AmazonOrdersData:
        in_transit: list[Package] = []
        last_delivered: Package | None = None
        still_in_transit: dict[str, Tracking] = {}
        seen: set[str] = set()
        pending = 0
        new_pages = 0
        store_changed = False

        for order in orders:
            for shipment in order.shipments:
                if shipment.tracking_url is None:
                    continue
                key = shipment_key(order, shipment)
                if key in seen:
                    continue
                seen.add(key)

                tracking = self._delivered.get(key)
                previous = self._in_transit.get(key)
                if tracking is None:
                    if previous is None and new_pages >= MAX_NEW_TRACKING_PER_UPDATE:
                        pending += 1
                        continue
                    try:
                        tracking = await self.client.async_get_tracking(
                            shipment.tracking_url
                        )
                    except (AmazonAuthError, AmazonCaptchaError):
                        raise
                    except AmazonError as err:
                        _LOGGER.debug("Cannot read a tracking page: %s", err)
                        if previous is None:
                            pending += 1
                            continue
                        tracking = previous
                    if previous is None:
                        new_pages += 1

                package = Package(
                    key=key,
                    order_id=order.order_id,
                    placed_text=order.placed_text,
                    status_text=shipment.status_text,
                    status_detail=shipment.status_detail,
                    tracking_url=shipment.tracking_url,
                    item_titles=tuple(
                        item.title for item in shipment.items if item.title
                    ),
                    tracking=tracking,
                )
                self._fire_event_if_changed(package, previous)

                if tracking.is_delivered:
                    if key not in self._delivered:
                        self._delivered[key] = tracking
                        store_changed = True
                    if last_delivered is None:
                        last_delivered = package
                else:
                    still_in_transit[key] = tracking
                    in_transit.append(package)

        # Forget the packages that left the orders list.
        for key in [key for key in self._delivered if key not in seen]:
            del self._delivered[key]
            store_changed = True
        self._in_transit = still_in_transit
        self._first_update_done = True

        if store_changed:
            await self._store.async_save(
                {
                    "delivered": {
                        key: _tracking_to_dict(tracking)
                        for key, tracking in self._delivered.items()
                    }
                }
            )

        return AmazonOrdersData(
            in_transit=tuple(in_transit),
            last_delivered=last_delivered,
            pending=pending,
        )

    def _fire_event_if_changed(
        self, package: Package, previous: Tracking | None
    ) -> None:
        """Fire an event for a new package, a change of state or a delivery.

        Nothing is fired for what the first update finds, nor for packages
        that were already delivered when they were first seen.
        """
        if not self._first_update_done or package.key in self._delivered:
            return
        if previous is None:
            if package.tracking.is_delivered:
                return
            kind = "new"
        elif package.tracking.is_delivered:
            kind = "delivered"
        elif _signature(previous) != _signature(package.tracking):
            kind = "update"
        else:
            return

        previous_code = (
            (previous.short_status or previous.last_milestone) if previous else None
        )
        event = package.last_event
        self.hass.bus.async_fire(
            EVENT_PACKAGE_UPDATE,
            {
                "type": kind,
                "order_id": package.order_id,
                "status": package.status,
                "previous_status": previous_code.lower() if previous_code else None,
                "step": package.tracking.milestones_reached,
                "step_label": package.step_label,
                "progress": package.tracking.percent_complete,
                "expected": package.tracking.promise_message,
                "carrier": package.tracking.carrier,
                "items": list(package.item_titles),
                "last_event": event.message if event else None,
                "is_delivered": package.tracking.is_delivered,
                "config_entry_id": self.config_entry.entry_id,
            },
        )
