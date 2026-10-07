"""Diagnostics for Amazon Orders (helps map unknown package states).

The file is meant to be attached to public issues: everything that identifies
the user, the orders or the products is left out or redacted.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import REDACTED, async_redact_data
from homeassistant.const import CONF_EMAIL
from homeassistant.core import HomeAssistant

from .coordinator import AmazonOrdersConfigEntry, Package

TO_REDACT = {
    CONF_EMAIL,
    "refresh_token",
    "device_serial",
    "device_serial_number",
    "device_name",
    "name",
    "given_name",
    "user_id",
}


def _key_paths(value: Any, prefix: str = "") -> list[str]:
    """Return the names of the keys of a JSON object, without any value."""
    if isinstance(value, dict):
        paths: list[str] = []
        for key, child in value.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            paths.append(path)
            paths.extend(_key_paths(child, path))
        return paths
    if isinstance(value, list) and value:
        return _key_paths(value[0], f"{prefix}[]")
    return []


def _package(package: Package | None) -> dict[str, Any] | None:
    if package is None:
        return None
    tracking = package.tracking
    return {
        "placed": package.placed_text,
        "status_text": package.status_text,
        "status_detail": package.status_detail,
        "items": len(package.item_titles),
        "short_status": tracking.short_status,
        "last_milestone": tracking.last_milestone,
        "status_label": tracking.status_label,
        "milestones": [
            {
                "label": step.label,
                "reached": step.reached,
                "percent_complete": step.percent_complete,
            }
            for step in tracking.milestones
        ],
        "milestones_reached": tracking.milestones_reached,
        "percent_complete": tracking.percent_complete,
        "promise_message": tracking.promise_message,
        "carrier": tracking.carrier,
        "timezone": tracking.timezone,
        "seller_fulfilled": tracking.seller_fulfilled,
        "is_delivered": tracking.is_delivered,
        "events": [
            {
                "date": event.date_text,
                "time": event.time_text,
                "message": event.message,
                "location": REDACTED if event.location else None,
            }
            for event in tracking.events
        ],
        # Names only: they show which fields Amazon sends for this state. They
        # are missing for a package read before the last restart.
        "state_keys": sorted(_key_paths(tracking.raw_state)),
    }


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: AmazonOrdersConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    coordinator = entry.runtime_data
    data = coordinator.data
    interval = coordinator.update_interval
    return {
        "entry": async_redact_data(dict(entry.data), TO_REDACT),
        "options": dict(entry.options),
        "update_interval": interval.total_seconds() if interval else None,
        "last_update_success": coordinator.last_update_success,
        "pending": data.pending,
        "in_transit": [_package(package) for package in data.in_transit],
        "last_delivered": _package(data.last_delivered),
    }
