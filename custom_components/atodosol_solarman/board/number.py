from __future__ import annotations

from homeassistant.components.number import NumberDeviceClass, NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, EntityCategory, UnitOfPower
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .api import CONTRACTED_MAX_W, CONTRACTED_MIN_W, SOC_MAX, SOC_MIN, BoardState
from .entity import BoardControl


async def async_setup_entry(hass: HomeAssistant, config_entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> bool:
    coordinator = config_entry.runtime_data
    async_add_entities([SocMinimum(coordinator), ContractedPower(coordinator)])
    return True


class SocMinimum(BoardControl, NumberEntity):
    """Battery level at which «Carga máxima» stops the car (1-95 %)."""

    _attr_device_class = NumberDeviceClass.BATTERY
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_native_min_value = SOC_MIN
    _attr_native_max_value = SOC_MAX
    _attr_native_step = 1
    _attr_mode = NumberMode.BOX

    def __init__(self, coordinator):
        super().__init__(coordinator, "soc_minimum")

    def control_available(self, state: BoardState) -> bool:
        # As on the board page: only with the protection on and in «Carga máxima».
        return state.soc_protect and state.max_mode

    @property
    def native_value(self) -> int | None:
        return self.board.minimum or None

    async def async_set_native_value(self, value: float) -> None:
        await self.coordinator.async_change(soc_protect=True, minimum=int(round(value)))


class ContractedPower(BoardControl, NumberEntity):
    """Contracted power the charger must respect. Not in off-grid homes."""

    _attr_device_class = NumberDeviceClass.POWER
    _attr_native_unit_of_measurement = UnitOfPower.KILO_WATT
    _attr_native_min_value = CONTRACTED_MIN_W / 1000
    _attr_native_max_value = CONTRACTED_MAX_W / 1000
    _attr_native_step = 0.01
    _attr_mode = NumberMode.BOX
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator):
        super().__init__(coordinator, "contracted_power")

    def control_available(self, state: BoardState) -> bool:
        return not state.offgrid

    @property
    def native_value(self) -> float | None:
        return self.board.contracted_w / 1000 if self.board.contracted_w else None

    async def async_set_native_value(self, value: float) -> None:
        await self.coordinator.async_change(contracted_kw=value)
