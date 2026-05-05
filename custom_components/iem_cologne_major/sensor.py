"""Sensors for IEM Cologne Major."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .const import DATA_COORDINATOR, DOMAIN
from .coordinator import IEMCologneDataUpdateCoordinator


@dataclass(frozen=True, kw_only=True)
class IEMCologneSensorDescription(SensorEntityDescription):
    """IEM Cologne sensor description."""

    value_fn: Callable[[dict[str, Any]], Any]
    attrs_fn: Callable[[dict[str, Any]], dict[str, Any]] | None = None


SENSOR_DESCRIPTIONS: tuple[IEMCologneSensorDescription, ...] = (
    IEMCologneSensorDescription(
        key="phase",
        name="IEM Cologne Phase",
        icon="mdi:tournament",
        value_fn=lambda data: data.get("active_stage", "Unknown"),
        attrs_fn=lambda data: {
            "stage_windows": data.get("stage_windows", []),
            "tournament": data.get("tournament", {}),
            "stage_info": data.get("stage_info", {}),
        },
    ),
    IEMCologneSensorDescription(
        key="score_signal_count",
        name="IEM Cologne Score Signals",
        icon="mdi:scoreboard",
        value_fn=lambda data: len(data.get("score_signal_lines", [])),
        attrs_fn=lambda data: {
            "score_signal_lines": data.get("score_signal_lines", []),
            "score_change_detected": data.get("score_change_detected", False),
        },
    ),
    IEMCologneSensorDescription(
        key="last_score_change",
        name="IEM Cologne Last Score Change",
        icon="mdi:clock-alert-outline",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda data: _last_score_change_dt(data),
    ),
    IEMCologneSensorDescription(
        key="next_match",
        name="IEM Cologne Next Match",
        icon="mdi:calendar-clock",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda data: _next_match_dt(data),
        attrs_fn=lambda data: {
            "next_match": data.get("next_match"),
            "upcoming_matches": data.get("upcoming_matches", []),
        },
    ),
    IEMCologneSensorDescription(
        key="matches_today",
        name="IEM Cologne Matches Today",
        icon="mdi:calendar-today",
        value_fn=lambda data: data.get("matches_today", 0),
    ),
    IEMCologneSensorDescription(
        key="participants",
        name="IEM Cologne Participants",
        icon="mdi:account-group",
        value_fn=lambda data: _participants_count(data),
        attrs_fn=lambda data: {
            **data.get("participants", {}),
            "team_rosters": data.get("team_rosters", {}),
            "bracket_lines": data.get("bracket_lines", []),
            "swiss_standings": data.get("swiss_standings", []),
            "roster_status": data.get("roster_status", {}),
        },
    ),
    IEMCologneSensorDescription(
        key="all_data",
        name="IEM Cologne All Data",
        icon="mdi:database",
        value_fn=lambda data: data.get("updated_at"),
        attrs_fn=lambda data: data,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up sensors for a config entry."""
    coordinator: IEMCologneDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id][DATA_COORDINATOR]

    async_add_entities(
        IEMCologneSensor(coordinator=coordinator, description=description)
        for description in SENSOR_DESCRIPTIONS
    )


class IEMCologneSensor(CoordinatorEntity[IEMCologneDataUpdateCoordinator], SensorEntity):
    """Representation of a IEM Cologne sensor."""

    entity_description: IEMCologneSensorDescription

    def __init__(
        self,
        coordinator: IEMCologneDataUpdateCoordinator,
        description: IEMCologneSensorDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{coordinator.config_entry.entry_id}_{description.key}"
        self._attr_has_entity_name = True

    @property
    def native_value(self) -> Any:
        data = self.coordinator.data or {}
        return self.entity_description.value_fn(data)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        attrs_fn = self.entity_description.attrs_fn
        if not attrs_fn:
            return None
        data = self.coordinator.data or {}
        return attrs_fn(data)


def _next_match_dt(data: dict[str, Any]) -> datetime | None:
    match = data.get("next_match") or {}
    begin_at = match.get("begin_at")
    if not begin_at:
        return None
    parsed = dt_util.parse_datetime(begin_at)
    if parsed is None:
        return None
    return parsed


def _participants_count(data: dict[str, Any]) -> int:
    participants = data.get("participants", {})
    return sum(len(v) for v in participants.values() if isinstance(v, list))


def _last_score_change_dt(data: dict[str, Any]) -> datetime | None:
    value = data.get("last_score_change")
    if not value:
        return None
    parsed = dt_util.parse_datetime(value)
    if parsed is None:
        return None
    return parsed
