from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .api import BoardState
from .entity import BoardControl


async def async_setup_entry(hass: HomeAssistant, config_entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> bool:
    async_add_entities([SocProtect(config_entry.runtime_data)])
    return True


class SocProtect(BoardControl, SwitchEntity):
    """«Detener la carga por SOC mínimo» of «Carga máxima».

    In «Solo solar» the board keeps the battery at 95 % by itself and the page
    hides this setting, so it is available only in «Carga máxima» (not in a
    mode this version does not know either).
    """

    _attr_icon = "mdi:battery-lock"

    def __init__(self, coordinator):
        super().__init__(coordinator, "soc_protect")

    def control_available(self, state: BoardState) -> bool:
        return state.max_mode

    @property
    def is_on(self) -> bool:
        return self.board.soc_protect

    async def async_turn_on(self, **kwargs: Any) -> None:
        # With the last minimum the board had, or 20 % as the board page proposes.
        await self.coordinator.async_change(soc_protect=True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.async_change(soc_protect=False)
