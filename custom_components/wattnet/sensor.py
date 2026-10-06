"""Sensors for the Wattnet integration."""

from __future__ import annotations

from datetime import datetime

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import WattnetConfigEntry
from .const import ATTRIBUTION, DOMAIN, METRICS, Metric
from .coordinator import WattnetCoordinator

# The coordinator already fetches everything in one go.
PARALLEL_UPDATES = 0

CONSOLIDATED_UNTIL = SensorEntityDescription(
    key="consolidated_until",
    translation_key="consolidated_until",
    device_class=SensorDeviceClass.TIMESTAMP,
    entity_category=EntityCategory.DIAGNOSTIC,
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: WattnetConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create one sensor per metric, plus the consolidation marker."""
    coordinator = entry.runtime_data
    async_add_entities(
        [
            *(WattnetSensor(coordinator, metric) for metric in METRICS),
            WattnetConsolidatedUntilSensor(coordinator),
        ]
    )


class WattnetEntity(CoordinatorEntity[WattnetCoordinator]):
    """Base entity: one device per Wattnet zone."""

    _attr_has_entity_name = True
    _attr_attribution = ATTRIBUTION

    def __init__(self, coordinator: WattnetCoordinator, key: str) -> None:
        """Initialise the entity and its device."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.zone}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.zone)},
            name=coordinator.config_entry.title,
            manufacturer="Wattnet",
            entry_type=DeviceEntryType.SERVICE,
        )


class WattnetSensor(WattnetEntity, SensorEntity):
    """Latest value of a Wattnet metric, with its consolidation state."""

    def __init__(self, coordinator: WattnetCoordinator, metric: Metric) -> None:
        """Initialise the sensor."""
        super().__init__(coordinator, metric.key)
        self._metric = metric
        self.entity_description = SensorEntityDescription(
            key=metric.key,
            translation_key=metric.key,
            native_unit_of_measurement=metric.unit,
            # No state_class on purpose: the recorder would compile statistics
            # from the polled states (stamped with the poll time and never
            # corrected). The consolidated history lives in the wattnet:*
            # external statistics instead. The Environmental score has no unit
            # either, so Home Assistant shows its history as a band of values
            # rather than a line, which suits a score that ranks zones against
            # each other.
        )

    @property
    def native_value(self) -> float | None:
        """Return the latest value."""
        point = self.coordinator.data.get(self._metric.key)
        return point.value if point else None

    @property
    def extra_state_attributes(self) -> dict[str, object] | None:
        """Expose whether the latest point is consolidated."""
        point = self.coordinator.data.get(self._metric.key)
        if point is None:
            return None
        return {
            "valid": point.valid,
            "zone_status": point.zone_status,
            "data_timestamp": point.timestamp.isoformat(),
        }


class WattnetConsolidatedUntilSensor(WattnetEntity, SensorEntity):
    """Until when the imported statistics are final (older hours never change)."""

    def __init__(self, coordinator: WattnetCoordinator) -> None:
        """Initialise the sensor."""
        super().__init__(coordinator, CONSOLIDATED_UNTIL.key)
        self.entity_description = CONSOLIDATED_UNTIL

    @property
    def native_value(self) -> datetime | None:
        """Return the first hour that is still provisional."""
        return self.coordinator.consolidated_until
