from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorEntityDescription, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, EntityCategory, UnitOfElectricCurrent, UnitOfPower, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import StateType

from .api import BoardState
from .entity import BoardEntity

# V2C Trydan ChargeState: IEC 61851 states A-F. Only 0-2 are seen in the field.
CHARGE_STATES = {0: "no_vehicle", 1: "connected", 2: "charging", 3: "ventilation", 4: "error", 5: "error"}
CHARGE_STATE_OPTIONS = ["no_vehicle", "connected", "charging", "ventilation", "error"]

# `charger.reason`, as the board page names them (local_web.h). «Solo solar»
# words two of them differently; an unknown one is «other» with its code.
REASONS = [
    "active", "no_vehicle", "manual_stop", "soc_low", "soc_stale", "soc_recovering", "recovering", "comm_hold",
    "power_insufficient", "inverter_stale", "charger_stale", "limit_unconfirmed", "power_units_unknown",
    "storage_error", "ota_preparing", "dyda_maintenance", "charger_missing", "offline", "unconfigured",
    "cloud_window", "cloud_prepare", "cloud_window_charging", "solar_wait", "solar_grid",
]
SOLAR_REASONS = {"soc_low": "solar_soc_low", "soc_recovering": "solar_soc_recovering"}
REASON_OPTIONS = REASONS + list(SOLAR_REASONS.values()) + ["no_charger", "other"]

# `notice` from local_portal.h access_message(): known ones get a translated state.
NOTICES = {
    "Atodosol debe revisar el inversor asignado antes de cambiar ajustes": "needs_sources",
    "Esta versión admite un solo inversor": "unsupported",
    "Error de almacenamiento en la placa; contacta con Atodosol": "storage_error",
}
SETTINGS_OPTIONS = ["ready", "pin_missing", "pin_rejected", "pin_locked", "unconfigured", "needs_sources",
                    "unsupported", "storage_error", "blocked"]


def charge_state(state: BoardState) -> str | None:
    value = state.charger.charge_state if state.charger else None
    return CHARGE_STATES.get(value) if value is not None else None


def regulation(state: BoardState) -> str:
    if state.charger is None:
        return "no_charger"
    reason = state.charger.reason
    if state.solar_mode and reason in SOLAR_REASONS:
        return SOLAR_REASONS[reason]
    return reason if reason in REASONS else "other"


def settings_state(state: BoardState, pin: str, pin_rejected: bool) -> str:
    if state.writable:
        if state.locked_s > 0:
            return "pin_locked"
        if state.pin_set and not pin:
            return "pin_missing"
        if state.pin_set and pin_rejected:
            return "pin_rejected"
        return "ready"
    if not state.configured:
        return "unconfigured"
    if not state.pin_ok:
        return "storage_error"
    return NOTICES.get(state.notice, "blocked")


@dataclass(frozen=True, kw_only=True)
class BoardSensorDescription(SensorEntityDescription):
    value_fn: Callable[[BoardState], StateType]
    available_fn: Callable[[BoardState], bool] = lambda state: True
    attrs_fn: Callable[[BoardState], dict[str, Any]] | None = None


def _charger(state: BoardState) -> bool:
    return state.charger is not None


SENSORS = (
    BoardSensorDescription(
        key="battery", device_class=SensorDeviceClass.BATTERY, state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=PERCENTAGE, suggested_display_precision=0,
        value_fn=lambda s: s.soc, attrs_fn=lambda s: {"soc_age_s": s.soc_age_s}),
    BoardSensorDescription(
        key="house_load", device_class=SensorDeviceClass.POWER, state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT, suggested_display_precision=0, value_fn=lambda s: s.load_w),
    # + buying from the grid, - feeding into it. Not in off-grid homes.
    BoardSensorDescription(
        key="grid_power", device_class=SensorDeviceClass.POWER, state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT, suggested_display_precision=0,
        value_fn=lambda s: s.grid_w, available_fn=lambda s: not s.offgrid),
    BoardSensorDescription(
        key="charger_power", device_class=SensorDeviceClass.POWER, state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT, suggested_display_precision=0,
        value_fn=lambda s: s.charger.power_w, available_fn=_charger),
    BoardSensorDescription(
        key="charger_state", device_class=SensorDeviceClass.ENUM, options=CHARGE_STATE_OPTIONS,
        value_fn=charge_state, available_fn=_charger,
        attrs_fn=lambda s: {"charge_state": s.charger.charge_state, "charger_id": s.charger.id} if s.charger else {}),
    BoardSensorDescription(
        key="charger_limit", device_class=SensorDeviceClass.CURRENT, state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE, value_fn=lambda s: s.charger.limit_a,
        available_fn=_charger, attrs_fn=lambda s: {"ceiling_a": s.ceiling_a}),
    BoardSensorDescription(
        key="regulation", device_class=SensorDeviceClass.ENUM, options=REASON_OPTIONS, value_fn=regulation,
        attrs_fn=lambda s: {"reason": s.charger.reason, "message": s.charger.message} if s.charger else {}),
    BoardSensorDescription(
        key="charger_data_age", device_class=SensorDeviceClass.DURATION, native_unit_of_measurement=UnitOfTime.SECONDS,
        entity_category=EntityCategory.DIAGNOSTIC, entity_registry_enabled_default=False,
        value_fn=lambda s: s.charger.age_s, available_fn=_charger),
)


async def async_setup_entry(hass: HomeAssistant, config_entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> bool:
    coordinator = config_entry.runtime_data
    async_add_entities([BoardSensor(coordinator, d) for d in SENSORS] + [BoardSettingsSensor(coordinator)])
    return True


class BoardSensor(BoardEntity, SensorEntity):
    entity_description: BoardSensorDescription

    def __init__(self, coordinator, description: BoardSensorDescription):
        super().__init__(coordinator, description.key)
        self.entity_description = description

    def available_on(self, state: BoardState) -> bool:
        return self.entity_description.available_fn(state)

    @property
    def native_value(self) -> StateType:
        return self.entity_description.value_fn(self.board)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        if self.entity_description.attrs_fn is None or self.coordinator.data is None:
            return None
        return self.entity_description.attrs_fn(self.board)


class BoardSettingsSensor(BoardEntity, SensorEntity):
    """Whether the settings can be changed from Home Assistant, and why not."""

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = SETTINGS_OPTIONS

    def __init__(self, coordinator):
        super().__init__(coordinator, "settings")

    @property
    def native_value(self) -> str:
        return settings_state(self.board, self.coordinator.pin, self.coordinator.pin_rejected)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        state = self.board
        return {"notice": state.notice, "locked_s": state.locked_s, "pin_set": state.pin_set, "offgrid": state.offgrid}
