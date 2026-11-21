"""Config and options flows of the Atodosol board (mixed into the integration flow)."""
from __future__ import annotations

from ipaddress import IPv4Address, ip_address
from logging import getLogger
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlowResult, OptionsFlow
from homeassistant.core import HomeAssistant
from homeassistant.helpers.selector import (NumberSelector, NumberSelectorConfig, NumberSelectorMode, TextSelector,
                                            TextSelectorConfig, TextSelectorType)
from homeassistant.helpers.service_info.dhcp import DhcpServiceInfo
from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo

from ..const import CONF_HOST, DOMAIN
from . import (CONF_PIN, CONF_SCAN_INTERVAL, DEFAULT_HOST, DEFAULT_SCAN_INTERVAL, KIND, KIND_BOARD,
               MAX_SCAN_INTERVAL, MIN_SCAN_INTERVAL, board_client, board_title, is_board)
from .api import BOARD_ID_PREFIX, BoardConnectionError, BoardHostRejected, BoardInvalidResponse, BoardState, normalize_host, valid_host

_LOGGER = getLogger(__name__)

PIN_DIGITS = range(4, 9)


async def probe_board(hass: HomeAssistant, host: str) -> tuple[str | None, BoardState | None]:
    """One GET /api/state (and its gentle retry). Error key or the state."""
    if not valid_host(host):
        return "invalid_host", None
    try:
        return None, await board_client(hass, host).get_state()
    except BoardHostRejected:
        return "board_host_rejected", None
    except BoardInvalidResponse:
        return "not_a_board", None
    except BoardConnectionError:
        return "board_cannot_connect", None
    except Exception:  # never leave the flow with a traceback
        _LOGGER.exception("Unexpected error reading the board at %s", host)
        return "board_cannot_connect", None


def _ipv4(host: str) -> bool:
    try:
        return isinstance(ip_address(host.split(":", 1)[0]), IPv4Address)
    except ValueError:
        return False


def _valid_pin(pin: str) -> bool:
    return not pin or (pin.isdigit() and pin.isascii() and len(pin) in PIN_DIGITS)


class BoardConfigFlowMixin:
    """Steps `board`, `zeroconf` and `board_discovered` of the integration config flow."""

    _board: dict[str, str] | None = None

    def _board_entries(self) -> list[ConfigEntry]:
        return [e for e in self.hass.config_entries.async_entries(DOMAIN) if is_board(e)]

    def _board_entry(self, board_id: str) -> ConfigEntry | None:
        entry = self.hass.config_entries.async_entry_for_domain_unique_id(DOMAIN, board_id)
        return entry if entry is not None and is_board(entry) else None

    def _board_move(self, entry: ConfigEntry, host: str) -> None:
        """The board was found at another address: keep its entry and follow it."""
        if normalize_host(entry.options.get(CONF_HOST, "")) == host:
            return
        _LOGGER.info("%s: board found at %s, address updated", entry.title, host)
        self.hass.config_entries.async_update_entry(entry, options={**entry.options, CONF_HOST: host})
        if not entry.update_listeners:  # not loaded: its listener would not reload it
            self.hass.config_entries.async_schedule_reload(entry.entry_id)

    def _board_create(self, host: str, board_id: str) -> ConfigFlowResult:
        return self.async_create_entry(title=board_title(board_id), data={KIND: KIND_BOARD},
                                       options={CONF_HOST: host, CONF_SCAN_INTERVAL: DEFAULT_SCAN_INTERVAL})

    async def async_step_board(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            host = normalize_host(user_input.get(CONF_HOST, ""))
            error, state = await probe_board(self.hass, host)
            if error:
                errors["base"] = error
            else:
                if self._board_entry(state.id):
                    return self.async_abort(reason="board_already_configured")
                await self.async_set_unique_id(state.id, raise_on_progress=False)
                return self._board_create(host, state.id)
        return self.async_show_form(
            step_id="board",
            data_schema=self.add_suggested_values_to_schema(vol.Schema({vol.Required(CONF_HOST): str}),
                                                            user_input or {CONF_HOST: DEFAULT_HOST}),
            errors=errors,
        )

    async def async_step_zeroconf(self, discovery_info: ZeroconfServiceInfo) -> ConfigFlowResult:
        if not isinstance(discovery_info.ip_address, IPv4Address):
            return self.async_abort(reason="not_a_board")
        host = str(discovery_info.ip_address)
        if discovery_info.port not in (None, 80):
            host = f"{host}:{discovery_info.port}"
        hostname = normalize_host(discovery_info.hostname or "")
        # Known address or name: no request to the board on every announcement.
        if any(normalize_host(e.options.get(CONF_HOST, "")) in (host, hostname) for e in self._board_entries()):
            return self.async_abort(reason="board_already_configured")
        error, state = await probe_board(self.hass, host)
        if error:
            return self.async_abort(reason="not_a_board" if error == "board_host_rejected" else error)
        if entry := self._board_entry(state.id):
            self._board_move(entry, host)
            return self.async_abort(reason="board_already_configured")
        await self.async_set_unique_id(state.id)
        self._abort_if_unique_id_configured()  # a board the user chose to ignore
        self._board = {CONF_HOST: host, "id": state.id, "name": hostname or host}
        self.context["title_placeholders"] = {"name": board_title(state.id)}
        return await self.async_step_board_discovered()

    async def async_step_board_discovered(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self._board_create(self._board[CONF_HOST], self._board["id"])
        return self.async_show_form(step_id="board_discovered", data_schema=vol.Schema({}),
                                    description_placeholders={"name": self._board["name"], "host": self._board[CONF_HOST]})

    def board_dhcp(self, discovery_info: DhcpServiceInfo) -> ConfigFlowResult | None:
        """DHCP of a board already added (registered MAC): follow a new IP, never offer it as an inverter."""
        mac = discovery_info.macaddress.replace(":", "").lower()
        if not (entry := self._board_entry(f"{BOARD_ID_PREFIX}{mac}")):
            return None
        # A name (atodosol.local) is kept: it follows the board by itself.
        if _ipv4(normalize_host(entry.options.get(CONF_HOST, ""))):
            self._board_move(entry, discovery_info.ip)
        return self.async_abort(reason="board_already_configured")


class BoardOptionsFlow(OptionsFlow):
    """Address (same board only), optional PIN and polling interval."""

    def __init__(self, entry: ConfigEntry):
        self.entry = entry

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return await self.async_step_board(user_input)

    async def async_step_board(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            host = normalize_host(user_input.get(CONF_HOST, ""))
            pin = (user_input.get(CONF_PIN) or "").strip()
            if not _valid_pin(pin):
                errors[CONF_PIN] = "invalid_pin"
            elif host != normalize_host(self.entry.options.get(CONF_HOST, "")):
                error, state = await probe_board(self.hass, host)
                if error:
                    errors["base"] = error
                elif state.id != self.entry.unique_id:
                    errors["base"] = "wrong_board"
            if not errors:
                options = {CONF_HOST: host, CONF_SCAN_INTERVAL: int(user_input.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL))}
                if pin:
                    options[CONF_PIN] = pin
                return self.async_create_entry(data=options)
        schema = vol.Schema({
            vol.Required(CONF_HOST): str,
            vol.Optional(CONF_PIN): TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD)),
            vol.Required(CONF_SCAN_INTERVAL, default=DEFAULT_SCAN_INTERVAL): NumberSelector(
                NumberSelectorConfig(min=MIN_SCAN_INTERVAL, max=MAX_SCAN_INTERVAL, step=1,
                                     mode=NumberSelectorMode.BOX, unit_of_measurement="s")),
        })
        return self.async_show_form(step_id="board", data_schema=self.add_suggested_values_to_schema(schema, user_input or self.entry.options),
                                    errors=errors)
