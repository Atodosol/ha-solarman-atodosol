from __future__ import annotations

from typing import Any

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .api import MODE_MAX, MODE_SOLAR
from .entity import BoardControl


async def async_setup_entry(hass: HomeAssistant, config_entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> bool:
    async_add_entities([ChargeMode(config_entry.runtime_data)])
    return True


class ChargeMode(BoardControl, SelectEntity):
    """«Carga máxima» / «Solo solar».

    «Solo solar» is offered only when the inverter reports the battery power
    (`solar_ok`), as on the board page; if it is already on it can be left.
    """

    _attr_icon = "mdi:car-electric"

    def __init__(self, coordinator):
        super().__init__(coordinator, "charge_mode")

    @property
    def options(self) -> list[str]:
        state = self.coordinator.data
        return [MODE_MAX, MODE_SOLAR] if state is None or state.solar_selectable else [MODE_MAX]

    @property
    def current_option(self) -> str | None:
        # None (unknown) for a mode this version does not know, never «max».
        return self.board.charge_mode

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        if self.coordinator.data is None:
            return None
        attributes = {"solar_available": self.board.solar_ok, "solar_floor": self.board.solar_floor}
        if self.board.mode_reported:
            attributes["board_mode"] = self.board.mode
        return attributes

    async def async_select_option(self, option: str) -> None:
        await self.coordinator.async_change(mode=option)
