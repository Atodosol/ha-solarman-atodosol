from __future__ import annotations

import asyncio
import voluptuous as vol
from homeassistant.components import network
from .lan import adapter_networks, scan_lan, scan_network

from typing import Any
from logging import getLogger
from socket import getaddrinfo, herror, gaierror, timeout

from homeassistant.const import CONF_NAME
from homeassistant.core import HomeAssistant, callback
from homeassistant.data_entry_flow import section, AbortFlow
from homeassistant.config_entries import DEFAULT_DISCOVERY_UNIQUE_ID, ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.helpers import config_validation as cv, device_registry as dr
from homeassistant.helpers.service_info.dhcp import DhcpServiceInfo
from homeassistant.helpers.typing import DiscoveryInfoType
from homeassistant.helpers.selector import selector

from .const import *
from .common import *
from .discovery import discover

_LOGGER = getLogger(__name__)

CREATION_SCHEMA = {
    vol.Required(CONF_NAME, default = DEFAULT_[CONF_NAME]): str
}

CONFIGURATION_SCHEMA = {
    vol.Optional("model", default=""): str,
    vol.Required(CONF_HOST, default = DEFAULT_[CONF_HOST], description = {SUGGESTED_VALUE: DEFAULT_[CONF_HOST]}): str,
    vol.Optional(CONF_PORT, default = DEFAULT_[CONF_PORT], description = {SUGGESTED_VALUE: DEFAULT_[CONF_PORT]}): cv.port,
    vol.Optional(CONF_TRANSPORT, default = DEFAULT_[CONF_TRANSPORT], description = {SUGGESTED_VALUE: DEFAULT_[CONF_TRANSPORT]}):
        selector({ "select": {"mode": "dropdown", "options": ["tcp", "modbus_rtu", "modbus_tcp"], "translation_key": "transport"}}),
    vol.Optional(CONF_LOOKUP_FILE, default = DEFAULT_[CONF_LOOKUP_FILE], description = {SUGGESTED_VALUE: DEFAULT_[CONF_LOOKUP_FILE]}): str,
    vol.Required(CONF_ADDITIONAL_OPTIONS):
        section(vol.Schema({
            vol.Optional(CONF_MOD, default = DEFAULT_[CONF_MOD], description = {SUGGESTED_VALUE: DEFAULT_[CONF_MOD]}): vol.All(vol.Coerce(int), vol.Range(min = 0, max = 2)),
            vol.Optional(CONF_MPPT, default = DEFAULT_[CONF_MPPT], description = {SUGGESTED_VALUE: DEFAULT_[CONF_MPPT]}): vol.All(vol.Coerce(int), vol.Range(min = 1, max = 12)),
            vol.Optional(CONF_PHASE, default = DEFAULT_[CONF_PHASE], description = {SUGGESTED_VALUE: DEFAULT_[CONF_PHASE]}): vol.All(vol.Coerce(int), vol.Range(min = 1, max = 3)),
            vol.Optional(CONF_PACK, default = DEFAULT_[CONF_PACK], description = {SUGGESTED_VALUE: DEFAULT_[CONF_PACK]}): vol.All(vol.Coerce(int), vol.Range(min = -1, max = 20)),
            vol.Optional(CONF_BATTERY_NOMINAL_VOLTAGE, default = DEFAULT_[CONF_BATTERY_NOMINAL_VOLTAGE], description = {SUGGESTED_VALUE: DEFAULT_[CONF_BATTERY_NOMINAL_VOLTAGE]}): cv.positive_int,
            vol.Optional(CONF_BATTERY_LIFE_CYCLE_RATING, default = DEFAULT_[CONF_BATTERY_LIFE_CYCLE_RATING], description = {SUGGESTED_VALUE: DEFAULT_[CONF_BATTERY_LIFE_CYCLE_RATING]}): cv.positive_int,
            vol.Optional(CONF_MB_SLAVE_ID, default = DEFAULT_[CONF_MB_SLAVE_ID], description = {SUGGESTED_VALUE: DEFAULT_[CONF_MB_SLAVE_ID]}): cv.positive_int
        }),
        {"collapsed": True}
    )
}

async def data_schema(hass: HomeAssistant, data_schema: dict[str, Any]) -> vol.Schema:
    lookup_files = [DEFAULT_[CONF_LOOKUP_FILE]] + await async_listdir(hass.config.path(LOOKUP_DIRECTORY_PATH)) + await async_listdir(hass.config.path(LOOKUP_CUSTOM_DIRECTORY_PATH), "custom/")
    _LOGGER.debug(f"step_user_data_schema: {LOOKUP_DIRECTORY_PATH}: {lookup_files}")
    data_schema[CONF_LOOKUP_FILE] = vol.In(lookup_files)
    _LOGGER.debug(f"step_user_data_schema: data_schema: {data_schema}")
    return vol.Schema(data_schema)

async def validate_connection(hass, user_input):
    """Validate a real read before saving, without changing device settings."""
    from .pysolarman import Solarman
    from .parser import ParameterParser
    from .common import get_request_code
    from .lan import probe_inverter

    host = user_input.get(CONF_HOST, "").strip()
    if not host:
        return {"base": "invalid_host"}, None
    port = user_input.get(CONF_PORT, DEFAULT_[CONF_PORT])
    transport = user_input.get(CONF_TRANSPORT, DEFAULT_[CONF_TRANSPORT])
    slave = user_input.get(CONF_ADDITIONAL_OPTIONS, {}).get(CONF_MB_SLAVE_ID, 1)
    profile = user_input.get(CONF_LOOKUP_FILE, "Auto")
    client = None
    try:
        async with asyncio.timeout(10):
            if transport == "modbus_tcp" and profile in AUTODETECTION_REDIRECT:
                identity = await probe_inverter(host, port, slave, timeout=5)
                return None, identity
            client = Solarman(host, port, transport, 0, slave, 3)
            code, address, count = 3, 0, 23
            if profile not in AUTODETECTION_REDIRECT:
                parameters = {PARAM_[k]: user_input.get(CONF_ADDITIONAL_OPTIONS, {}).get(k, DEFAULT_[k]) for k in PARAM_}
                parser = await ParameterParser().init(hass.config.path(LOOKUP_DIRECTORY_PATH) + "/", profile, parameters)
                request = parser.schedule_requests(0)[0]
                code, address, count = get_request_code(request), request[REQUEST_START], request[REQUEST_COUNT]
            if code not in (1, 2, 3, 4):
                raise ValueError("Profile must start with a read")
            await client.execute(code, address, count=count)
            return None, None
    except (TimeoutError, OSError):
        return {"base": "cannot_connect"}, None
    except Exception:
        return {"base": "invalid_response"}, None
    finally:
        if client is not None:
            await client.close()

def remove_defaults(user_input: dict[str, Any]):
    for k in list(user_input.keys()):
        if k == CONF_ADDITIONAL_OPTIONS:
            for l in list(user_input[k].keys()):
                if user_input[k][l] == DEFAULT_.get(l):
                    del user_input[k][l]
            if not user_input[k]:
                del user_input[k]
        elif user_input[k] == DEFAULT_.get(k):
            del user_input[k]
    return user_input

class ConfigFlowHandler(ConfigFlow, domain=DOMAIN):
    VERSION = 2
    MINOR_VERSION = 0

    def __init__(self):
        self._scan_task = None
        self._networks = None
        self._network_limited = False
        self._scan_result = None
        self._suggested = {}

    async def async_step_user(self, user_input=None):
        return self.async_show_menu(step_id="user", menu_options=["automatic", "manual"])

    async def async_step_automatic(self, user_input=None):
        errors = {}
        if self._networks is None:
            adapters = await network.async_get_adapters(self.hass)
            self._networks, self._network_limited = adapter_networks(adapters)
        if user_input is not None:
            try:
                selected = str(scan_network(user_input["network"]))
            except ValueError:
                errors["network"] = "invalid_network"
            else:
                self._networks = [selected]
                self._scan_task = None
                return await self.async_step_scan()
        suggested = self._networks[0] if self._networks else ""
        return self.async_show_form(
            step_id="automatic",
            data_schema=vol.Schema({vol.Required("network", default=suggested): str}),
            errors=errors,
            description_placeholders={"networks": ", ".join(self._networks) or "—"},
        )

    async def async_step_scan(self, user_input=None):
        if self._scan_task is None:
            self._scan_task = self.hass.async_create_task(scan_lan(self._networks), "Atodosol LAN discovery")
        if not self._scan_task.done():
            return self.async_show_progress(step_id="scan", progress_action="scan", progress_task=self._scan_task)
        try:
            self._scan_result = self._scan_task.result()
        except Exception:
            return self.async_show_progress_done(next_step_id="scan_failed")
        return self.async_show_progress_done(next_step_id="choose" if self._scan_result.devices else "not_found")

    async def async_step_choose(self, user_input=None):
        if user_input is not None:
            device = next((d for d in self._scan_result.devices if d.host == user_input.get("device")), None)
            if device is not None:
                self._suggested = {
                    CONF_NAME: DEFAULT_[CONF_NAME], CONF_HOST: device.host,
                    CONF_PORT: device.port, CONF_TRANSPORT: "modbus_tcp",
                    CONF_LOOKUP_FILE: "Auto", CONF_ADDITIONAL_OPTIONS: {CONF_MB_SLAVE_ID: device.slave},
                }
                return await self.async_step_manual()
        return self.async_show_form(
            step_id="choose",
            data_schema=vol.Schema({vol.Required("device"): vol.In({d.host: d.label for d in self._scan_result.devices})}),
        )

    async def async_step_not_found(self, user_input=None):
        return self.async_show_menu(step_id="not_found", menu_options=["automatic", "manual"])

    async def async_step_scan_failed(self, user_input=None):
        return self.async_show_menu(step_id="scan_failed", menu_options=["automatic", "manual"])

    async def async_step_manual(self, user_input=None):
        errors = {}
        if user_input is not None:
            user_input = dict(user_input)
            user_input[CONF_HOST] = user_input[CONF_HOST].strip()
            errors, identity = await validate_connection(self.hass, user_input)
            if not errors:
                if identity is not None:
                    await self.async_set_unique_id(f"deye_{identity.serial}")
                    self._abort_if_unique_id_configured()
                for entry in self._async_current_entries():
                    if (entry.options.get(CONF_HOST) == user_input[CONF_HOST]
                        and entry.options.get(CONF_PORT, DEFAULT_[CONF_PORT]) == user_input.get(CONF_PORT, DEFAULT_[CONF_PORT])
                        and entry.options.get(CONF_ADDITIONAL_OPTIONS, {}).get(CONF_MB_SLAVE_ID, 1) == user_input.get(CONF_ADDITIONAL_OPTIONS, {}).get(CONF_MB_SLAVE_ID, 1)):
                        return self.async_abort(reason="already_configured")
                options = remove_defaults(filter_by_keys(user_input, CONFIGURATION_SCHEMA))
                if identity is not None:
                    options["inverter_serial"] = identity.serial
                return self.async_create_entry(title=user_input[CONF_NAME], data={}, options=options)
        return self.async_show_form(
            step_id="manual",
            data_schema=self.add_suggested_values_to_schema(await data_schema(self.hass, CREATION_SCHEMA | CONFIGURATION_SCHEMA), user_input or self._suggested),
            errors=errors or {},
        )

    async def async_step_dhcp(self, discovery_info):
        # DHCP is a hint only; the manual confirmation still performs a real read.
        self._suggested = {CONF_HOST: discovery_info.ip, CONF_NAME: DEFAULT_[CONF_NAME]}
        return await self.async_step_manual()

    async def async_step_integration_discovery(self, discovery_info):
        self._suggested = {CONF_HOST: discovery_info["ip"], CONF_NAME: DEFAULT_[CONF_NAME], CONF_TRANSPORT: "tcp", CONF_PORT: 8899}
        return await self.async_step_manual()

    @staticmethod
    @callback
    def async_get_options_flow(entry):
        return OptionsFlowHandler(entry)


class OptionsFlowHandler(OptionsFlow):
    def __init__(self, entry):
        self.entry = entry

    async def async_step_init(self, user_input=None):
        errors = {}
        if user_input is not None:
            errors, identity = await validate_connection(self.hass, user_input)
            expected = self.entry.options.get("inverter_serial")
            if not errors and expected and (identity is None or identity.serial != expected):
                errors = {"base": "wrong_device"}
            if not errors:
                options = remove_defaults(dict(user_input))
                if expected:
                    options["inverter_serial"] = expected
                return self.async_create_entry(data=options)
        return self.async_show_form(
            step_id="init",
            data_schema=self.add_suggested_values_to_schema(await data_schema(self.hass, CONFIGURATION_SCHEMA), user_input or self.entry.options),
            errors=errors or {},
        )
