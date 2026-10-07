"""Base entity for Amazon Orders."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import AmazonOrdersCoordinator


class AmazonOrdersEntity(CoordinatorEntity[AmazonOrdersCoordinator]):
    """Common attributes for Amazon Orders entities."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: AmazonOrdersCoordinator, key: str) -> None:
        """Initialise the entity."""
        super().__init__(coordinator)
        entry = coordinator.config_entry
        self._attr_unique_id = f"{entry.unique_id}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.unique_id or entry.entry_id)},
            name="Amazon",
            manufacturer="Amazon (unofficial)",
            entry_type=DeviceEntryType.SERVICE,
        )
