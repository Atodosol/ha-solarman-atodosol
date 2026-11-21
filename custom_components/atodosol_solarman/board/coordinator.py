from __future__ import annotations

from datetime import timedelta
from logging import getLogger
from math import ceil
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import CONNECTION_NETWORK_MAC, DeviceInfo
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from ..const import DOMAIN
from . import (CONF_PIN, CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL, MAX_SCAN_INTERVAL, MIN_SCAN_INTERVAL,
               PENDING_SCAN_INTERVAL, board_client)
from .api import (DEFAULT_MINIMUM, MODE_SOLAR, SOC_MAX, SOC_MIN, BoardClient, BoardConnectionError, BoardError,
                  BoardHostRejected, BoardRejected, BoardState, BoardWriteUnknown, settings_fields)

_LOGGER = getLogger(__name__)


def _error(key: str, **placeholders: Any) -> HomeAssistantError:
    return HomeAssistantError(translation_domain=DOMAIN, translation_key=key,
                              translation_placeholders={k: str(v) for k, v in placeholders.items()} or None)


def _minutes(seconds: int) -> int:
    return max(1, ceil(seconds / 60))


class BoardCoordinator(DataUpdateCoordinator[BoardState]):
    """Polls GET /api/state and sends the changes of the entities."""

    def __init__(self, hass: HomeAssistant, config_entry: ConfigEntry, client: BoardClient | None = None):
        seconds = int(config_entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL))
        self._interval = timedelta(seconds=min(MAX_SCAN_INTERVAL, max(MIN_SCAN_INTERVAL, seconds)))
        super().__init__(hass, _LOGGER, config_entry=config_entry, name=config_entry.title,
                         update_interval=self._interval, always_update=False)
        self.client = client or board_client(hass, config_entry.options["host"])
        # The board only tells whether a PIN is set; a PIN it refused is not sent again
        # until the options change (each wrong PIN counts toward a 5-minute lock).
        self.pin_rejected = False
        # Last minimum the board had, to turn the protection on again with it.
        self.last_minimum = DEFAULT_MINIMUM

    @property
    def pin(self) -> str:
        return self.config_entry.options.get(CONF_PIN, "") or ""

    @property
    def device_info(self) -> DeviceInfo:
        state = self.data
        info = DeviceInfo(
            identifiers={(DOMAIN, self.config_entry.unique_id)},
            manufacturer="Atodosol",
            model="Placa domótica ATS-DOM",
            name=self.config_entry.title,
            configuration_url=self.client.url,
        )
        if state is not None:
            info["sw_version"] = state.fw
            if state.mac:
                info["connections"] = {(CONNECTION_NETWORK_MAC, state.mac)}
        return info

    async def _async_update_data(self) -> BoardState:
        host = self.client.host
        try:
            state = await self.client.get_state()
        except BoardHostRejected as err:
            raise UpdateFailed(translation_domain=DOMAIN, translation_key="board_host_rejected",
                               translation_placeholders={"host": host}) from err
        except BoardError as err:
            raise UpdateFailed(translation_domain=DOMAIN, translation_key="board_read_failed",
                               translation_placeholders={"host": host, "error": str(err)}) from err
        if state.id != self.config_entry.unique_id:
            # Two boards in one home can swap atodosol.local / atodosol-2.local on reboot:
            # never show or change another board as if it were this one.
            raise UpdateFailed(translation_domain=DOMAIN, translation_key="board_wrong_board",
                               translation_placeholders={"host": host, "found": state.id})
        if state.soc_protect and SOC_MIN <= state.minimum <= SOC_MAX:
            self.last_minimum = state.minimum
        # A change is applied by the board after the answer: follow it closely.
        pending = state.charger is not None and state.charger.pending
        self.update_interval = timedelta(seconds=PENDING_SCAN_INTERVAL) if pending else self._interval
        self._update_firmware(state)
        return state

    def _update_firmware(self, state: BoardState) -> None:
        registry = dr.async_get(self.hass)
        # async_get_device_by_identifier is recent; hacs.json allows Home Assistant 2025.2.
        if get_device := getattr(registry, "async_get_device_by_identifier", None):
            device = get_device((DOMAIN, state.id), self.config_entry.entry_id)
        else:
            device = registry.async_get_device(identifiers={(DOMAIN, state.id)})
        if device and device.sw_version != state.fw:
            registry.async_update_device(device.id, sw_version=state.fw)

    async def async_change(self, **change: Any) -> None:
        """Send one change (mode, soc_protect, minimum or contracted_kw) and refresh."""
        state = self.data
        if not self.last_update_success or state is None:
            raise _error("board_unavailable")
        if not state.writable:
            raise _error("not_writable", notice=state.notice or "—")
        if state.locked_s > 0:
            raise _error("pin_locked", minutes=_minutes(state.locked_s))
        if state.pin_set and not self.pin:
            raise _error("pin_missing")
        if state.pin_set and self.pin_rejected:
            raise _error("pin_rejected")
        if change.get("mode") == MODE_SOLAR and not state.solar_selectable:
            raise _error("solar_unavailable")
        if change.get("contracted_kw") is not None and state.offgrid:
            raise _error("offgrid_contracted")
        fields = settings_fields(state, pin=self.pin, fallback_minimum=self.last_minimum, **change)
        try:
            reply = await self.client.post_settings(fields)
        except BoardRejected as err:
            if err.status == 423 or (err.pin_denied and err.locked_s > 0):
                raise _error("pin_locked", minutes=_minutes(err.locked_s or 300)) from err
            if err.pin_denied:
                await self.async_request_refresh()
                if not self.pin:
                    raise _error("pin_missing") from err
                if not state.pin_set:
                    # The PIN was set on the board after the last read, so it was not sent:
                    # with the new read the next change carries it.
                    raise _error("write_rejected", message=err.message) from err
                self.pin_rejected = True
                self.async_update_listeners()  # «Ajustes» shows it even if the state did not change
                raise _error("pin_wrong", attempts=err.attempts_left if err.attempts_left is not None else "?") from err
            await self.async_request_refresh()  # e.g. 409: the controls follow what the board says now
            raise _error("write_rejected", message=err.message) from err
        except BoardConnectionError as err:
            raise _error("write_failed") from err
        except BoardWriteUnknown as err:
            await self.async_request_refresh()
            raise _error("write_unknown") from err
        _LOGGER.info("%s: %s (%s)", self.name, reply.get("message") or "Cambio aceptado",
                     ", ".join(k for k, _ in fields if k != "pin"))
        if reply.get("pending"):
            self.update_interval = timedelta(seconds=PENDING_SCAN_INTERVAL)
        await self.async_request_refresh()
