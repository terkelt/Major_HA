"""Sensors for HLTV Event integration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .const import DATA_COORDINATOR, DOMAIN
from .coordinator import HLTVEventCoordinator


@dataclass(frozen=True, kw_only=True)
class HLTVEventSensorDescription(SensorEntityDescription):
    value_fn: Callable[[dict[str, Any]], Any]
    attrs_fn: Callable[[dict[str, Any]], dict[str, Any]] | None = None


SENSOR_DESCRIPTIONS: tuple[HLTVEventSensorDescription, ...] = (
    HLTVEventSensorDescription(
        key="status",
        name="HLTV Event Status",
        icon="mdi:trophy-outline",
        value_fn=lambda d: d.get("event_name", "Unknown"),
        attrs_fn=lambda d: {
            "event_url": d.get("event_url"),
            "event_id": d.get("event_id"),
            "start_date": d.get("start_date"),
            "end_date": d.get("end_date"),
            "location": d.get("location"),
            "prize_pool": d.get("prize_pool"),
            "format": d.get("format"),
            "map_pool": d.get("map_pool", []),
            "updated_at": d.get("updated_at"),
            "sources": d.get("sources", {}),
        },
    ),
    HLTVEventSensorDescription(
        key="teams",
        name="HLTV Event Teams",
        icon="mdi:account-group",
        value_fn=lambda d: len(d.get("teams", [])),
        attrs_fn=lambda d: {
            "teams": d.get("teams", []),
            "rosters": d.get("rosters", {}),
        },
    ),
    HLTVEventSensorDescription(
        key="results",
        name="HLTV Event Results",
        icon="mdi:scoreboard",
        value_fn=lambda d: len(d.get("results", [])),
        attrs_fn=lambda d: {
            "results": d.get("results", []),
        },
    ),
    HLTVEventSensorDescription(
        key="matches",
        name="HLTV Event Matches",
        icon="mdi:calendar-clock",
        value_fn=lambda d: len(d.get("live_matches", [])),
        attrs_fn=lambda d: {
            "live_matches": d.get("live_matches", []),
            "upcoming_matches": d.get("upcoming_matches", []),
        },
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: HLTVEventCoordinator = hass.data[DOMAIN][entry.entry_id][DATA_COORDINATOR]
    async_add_entities(
        HLTVEventSensor(coordinator=coordinator, description=desc)
        for desc in SENSOR_DESCRIPTIONS
    )


class HLTVEventSensor(CoordinatorEntity[HLTVEventCoordinator], SensorEntity):
    entity_description: HLTVEventSensorDescription

    def __init__(self, coordinator: HLTVEventCoordinator, description: HLTVEventSensorDescription) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{coordinator.config_entry.entry_id}_{description.key}"
        self._attr_has_entity_name = True

    @property
    def native_value(self) -> Any:
        return self.entity_description.value_fn(self.coordinator.data or {})

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        fn = self.entity_description.attrs_fn
        if not fn:
            return None
        return fn(self.coordinator.data or {})
