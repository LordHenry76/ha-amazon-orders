"""Binary sensors for Amazon Orders."""

from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import AmazonOrdersConfigEntry
from .entity import AmazonOrdersEntity

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: AmazonOrdersConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Amazon Orders binary sensors."""
    async_add_entities(
        [AmazonOrdersPackageInTransit(entry.runtime_data, "package_in_transit")]
    )


class AmazonOrdersPackageInTransit(AmazonOrdersEntity, BinarySensorEntity):
    """On while at least one package is on its way."""

    _attr_translation_key = "package_in_transit"

    @property
    def is_on(self) -> bool:
        """Return True if a package has not been delivered yet."""
        return bool(self.coordinator.data.in_transit)
