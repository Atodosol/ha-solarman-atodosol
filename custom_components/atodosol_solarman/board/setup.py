from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntry

from ..const import DOMAIN
from .coordinator import BoardCoordinator

PLATFORMS: list[Platform] = [Platform.SENSOR, Platform.BINARY_SENSOR, Platform.SELECT, Platform.SWITCH, Platform.NUMBER]

# The PIN never leaves Home Assistant; addresses, MACs, charger serials and the
# Wi-Fi name are hidden as well, so the file can be attached to an issue.
TO_REDACT = {"pin", "host", "ip", "id", "unique_id", "title", "ssid", "sources", "grid_source", "battery_source",
             "charger_id", "identifiers", "connections"}


async def async_setup_board_entry(hass: HomeAssistant, config_entry: ConfigEntry) -> bool:
    coordinator = BoardCoordinator(hass, config_entry)
    await coordinator.async_config_entry_first_refresh()
    config_entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(config_entry, PLATFORMS)

    async def reload(hass: HomeAssistant, config_entry: ConfigEntry) -> None:
        await hass.config_entries.async_reload(config_entry.entry_id)

    config_entry.async_on_unload(config_entry.add_update_listener(reload))
    return True


async def async_unload_board_entry(hass: HomeAssistant, config_entry: ConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(config_entry, PLATFORMS)


def board_device_removable(config_entry: ConfigEntry, device_entry: DeviceEntry) -> bool:
    return (DOMAIN, config_entry.unique_id) not in device_entry.identifiers


async def async_board_diagnostics(hass: HomeAssistant, config_entry: ConfigEntry) -> dict[str, Any]:
    coordinator: BoardCoordinator = config_entry.runtime_data
    state = coordinator.data
    return {
        "config": async_redact_data({"title": config_entry.title, "unique_id": config_entry.unique_id,
                                     "data": dict(config_entry.data), "options": dict(config_entry.options)}, TO_REDACT),
        "pin_configured": bool(coordinator.pin),
        "pin_rejected": coordinator.pin_rejected,
        "last_update_success": coordinator.last_update_success,
        "update_interval_s": coordinator.update_interval.total_seconds() if coordinator.update_interval else None,
        "state": async_redact_data(state.raw, TO_REDACT) if state is not None else None,
    }
