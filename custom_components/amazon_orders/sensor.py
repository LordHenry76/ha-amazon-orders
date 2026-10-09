"""Sensors for Amazon Orders."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import PERCENTAGE
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import AmazonOrdersConfigEntry, AmazonOrdersData, Package
from .entity import AmazonOrdersEntity

PARALLEL_UPDATES = 0

# State of the status sensor while no package is on its way.
STATUS_IDLE = "idle"
MAX_STATE_LENGTH = 255


def package_attributes(package: Package | None) -> dict[str, Any]:
    """Return the details of a package, as attributes of an entity."""
    if package is None:
        return {}
    event = package.last_event
    address = package.address
    return {
        "order_id": package.order_id,
        "order_placed": package.placed_text,
        "items": list(package.item_titles),
        "status": package.status,
        "milestone": package.tracking.last_milestone.lower()
        if package.tracking.last_milestone
        else None,
        "step": package.tracking.milestones_reached,
        "step_label": package.step_label,
        "progress": package.progress,
        "step_progress": package.tracking.percent_complete,
        "expected": package.tracking.promise_message,
        "carrier": package.tracking.carrier,
        "status_text": package.status_text,
        "status_detail": package.status_detail,
        "last_event": event.message if event else None,
        "last_event_date": event.date_text if event else None,
        "last_event_time": event.time_text if event else None,
        "last_event_location": event.location if event else None,
        "tracking_url": package.tracking_url,
        "recipient": address.name if address else None,
        "delivery_city": address.city if address else None,
        "delivery_address": address.street if address else None,
    }


def _text(value: str | None) -> str | None:
    return value[:MAX_STATE_LENGTH] if value else None


def _step_attributes(package: Package | None) -> dict[str, Any]:
    if package is None:
        return {}
    return {
        "step_index": package.tracking.milestones_reached,
        "step_count": len(package.step_labels) or None,
        "steps": package.step_labels,
    }


def _last_event_attributes(package: Package | None) -> dict[str, Any]:
    event = package.last_event if package else None
    if event is None:
        return {}
    return {
        "date": event.date_text,
        "time": event.time_text,
        "location": event.location,
    }


@dataclass(frozen=True, kw_only=True)
class AmazonOrdersSensorDescription(SensorEntityDescription):
    """Describes an Amazon Orders sensor."""

    value_fn: Callable[[AmazonOrdersData], Any]
    attributes_fn: Callable[[AmazonOrdersData], dict[str, Any]] | None = None


def _next(
    value: Callable[[Package], Any], idle: Any = None
) -> Callable[[AmazonOrdersData], Any]:
    """Build a value function reading the package closest to its delivery."""

    def _value(data: AmazonOrdersData) -> Any:
        package = data.next_package
        return idle if package is None else value(package)

    return _value


SENSORS: tuple[AmazonOrdersSensorDescription, ...] = (
    AmazonOrdersSensorDescription(
        key="packages_in_transit",
        translation_key="packages_in_transit",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: len(data.in_transit),
        attributes_fn=lambda data: {
            "packages": [package_attributes(package) for package in data.in_transit],
            "pending": data.pending,
        },
    ),
    AmazonOrdersSensorDescription(
        key="next_package_status",
        translation_key="next_package_status",
        value_fn=_next(lambda package: _text(package.status), idle=STATUS_IDLE),
        attributes_fn=lambda data: package_attributes(data.next_package),
    ),
    AmazonOrdersSensorDescription(
        key="next_package_step",
        translation_key="next_package_step",
        value_fn=_next(lambda package: _text(package.step_label)),
        attributes_fn=lambda data: _step_attributes(data.next_package),
    ),
    AmazonOrdersSensorDescription(
        key="next_package_progress",
        translation_key="next_package_progress",
        native_unit_of_measurement=PERCENTAGE,
        value_fn=_next(lambda package: package.progress),
        attributes_fn=lambda data: (
            {"step_progress": data.next_package.tracking.percent_complete}
            if data.next_package
            else {}
        ),
    ),
    AmazonOrdersSensorDescription(
        key="next_package_expected",
        translation_key="next_package_expected",
        value_fn=_next(lambda package: _text(package.tracking.promise_message)),
    ),
    AmazonOrdersSensorDescription(
        key="next_package_last_event",
        translation_key="next_package_last_event",
        value_fn=_next(
            lambda package: (
                _text(package.last_event.message) if package.last_event else None
            )
        ),
        attributes_fn=lambda data: _last_event_attributes(data.next_package),
    ),
    AmazonOrdersSensorDescription(
        key="last_delivered",
        translation_key="last_delivered",
        value_fn=lambda data: (
            _text(data.last_delivered.tracking.promise_message)
            if data.last_delivered
            else None
        ),
        attributes_fn=lambda data: package_attributes(data.last_delivered),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: AmazonOrdersConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Amazon Orders sensors."""
    async_add_entities(
        AmazonOrdersSensor(entry.runtime_data, description) for description in SENSORS
    )


class AmazonOrdersSensor(AmazonOrdersEntity, SensorEntity):
    """A sensor reading the data of the coordinator."""

    entity_description: AmazonOrdersSensorDescription

    def __init__(self, coordinator, description: AmazonOrdersSensorDescription) -> None:
        """Initialise the sensor."""
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> Any:
        """Return the state of the sensor."""
        return self.entity_description.value_fn(self.coordinator.data)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return the attributes of the sensor."""
        if self.entity_description.attributes_fn is None:
            return None
        return self.entity_description.attributes_fn(self.coordinator.data)
