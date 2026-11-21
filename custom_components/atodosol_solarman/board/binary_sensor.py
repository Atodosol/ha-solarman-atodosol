from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .api import BoardState
from .entity import BoardEntity


async def async_setup_entry(hass: HomeAssistant, config_entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> bool:
    coordinator = config_entry.runtime_data
    async_add_entities([ChargerLocked(coordinator), ChangePending(coordinator), ChargerAlarm(coordinator)])
    return True


class ChargerLocked(BoardEntity, BinarySensorEntity):
    # Not the LOCK device class: for it "on" means unlocked.
    _attr_icon = "mdi:ev-plug-type2"

    def __init__(self, coordinator):
        super().__init__(coordinator, "charger_locked")

    def available_on(self, state: BoardState) -> bool:
        return state.charger is not None

    @property
    def is_on(self) -> bool:
        return self.board.charger.locked


class ChangePending(BoardEntity, BinarySensorEntity):
    """A change sent to the charger that the board is still confirming."""

    _attr_icon = "mdi:progress-clock"

    def __init__(self, coordinator):
        super().__init__(coordinator, "change_pending")

    @property
    def is_on(self) -> bool:
        return bool(self.board.charger and self.board.charger.pending)


class ChargerAlarm(BoardEntity, BinarySensorEntity):
    """Alarm of any charger the board sees (`chargers`, recent firmware)."""

    _attr_device_class = BinarySensorDeviceClass.PROBLEM

    def __init__(self, coordinator):
        super().__init__(coordinator, "charger_alarm")

    def available_on(self, state: BoardState) -> bool:
        return state.chargers is not None

    @property
    def is_on(self) -> bool:
        return bool(self.board.alarms)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        if self.coordinator.data is None or self.board.chargers is None:
            return None
        return {"alarms": [{"charger_id": c.get("id", ""), "fault": c.get("fault", ""),
                            "fault_label": c.get("fault_label", "")} for c in self.board.alarms]}
