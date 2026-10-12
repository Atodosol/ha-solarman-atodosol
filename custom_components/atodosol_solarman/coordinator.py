from __future__ import annotations

from logging import getLogger
from itertools import count
from datetime import timedelta

from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import *
from .common import *
from .device import Device
from .provider import ConfigurationProvider

_LOGGER = getLogger(__name__)

class Coordinator(DataUpdateCoordinator[dict[str, tuple[int | float | str | list, int | float | None]]]):
    def __init__(self, hass: HomeAssistant, config_entry: ConfigEntry[Coordinator]):
        self.device = Device(ConfigurationProvider(hass, config_entry))
        self.inverter_serial: str | None = None
        self._replacing = False
        super().__init__(hass, _LOGGER, config_entry = config_entry, name = "", update_interval = TIMINGS_UPDATE_INTERVAL, always_update = False)

    @DataUpdateCoordinator.update_interval.setter
    def update_interval(self, value: timedelta | None):
        DataUpdateCoordinator.update_interval.fset(self, value)
        self.counter = self._update_interval_seconds

    @property
    def name(self):
        return self.device.config.name

    @name.setter
    def name(self, _: str):
        pass

    @property
    def counter(self):
        return self._counter_value

    @counter.setter
    def counter(self, value: int | float):
        self._counter = count(0, int(value))
        self._counter_value = next(self._counter)

    async def _async_setup(self):
        await super()._async_setup()
        try:
            await self.device.setup()
        except TimeoutError:
            raise
        except Exception as e:
            raise UpdateFailed(strepr(e)) from e

    async def _async_update_data(self):
        try:
            data = await self.device.get(self.counter)
        except TimeoutError:
            raise
        except Exception as e:
            raise UpdateFailed(strepr(e)) from e
        self._check_inverter_serial(data)
        return data

    def _check_inverter_serial(self, data):
        # Same connection, different inverter: reload to confirm it with a full read,
        # detect the profile again and move the device (see init).
        if (serial := observed_inverter_serial(data)) is None or serial == self.inverter_serial or self._replacing:
            return
        if self.inverter_serial is None:
            self.inverter_serial = serial
            return
        self._replacing = True
        _LOGGER.info("%s: inverter serial number changed from %s to %s, reloading", self.name, self.inverter_serial, serial)
        self.hass.config_entries.async_schedule_reload(self.config_entry.entry_id)

    def _async_refresh_finished(self):
        super()._async_refresh_finished()
        if self.data:
            self._counter_value = next(self._counter)
        elif not self.last_update_success:
            self.counter = self._update_interval_seconds

    async def init(self):
        await super().async_config_entry_first_refresh()
        logger_serial = str(self.device.modbus.serial) if self.device.modbus.serial else None
        if (serial := observed_inverter_serial(self.data)) and (stored := stored_inverter_serial(self.hass, self.config_entry, logger_serial)) and serial != stored:
            # Inverter replaced behind the same dongle/address, also while Home Assistant was off.
            replace_inverter(self.hass, self.config_entry, stored, serial)
        self.inverter_serial = serial
        device_info = build_device_info(self.config_entry.entry_id, logger_serial, self.device.endpoint.mac, self.device.endpoint.host, self.device.profile.info, self.device.config.name)
        device_info = describe_inverter(device_info, self.data or {}, self.config_entry.options.get("model", ""))
        if self.device.config.transport == "modbus_tcp":
            device_info["configuration_url"] = f"http://{self.device.endpoint.host}/"
        self.device.info[self.config_entry.entry_id] = device_info
        _LOGGER.debug(device_info)
        return self

    async def async_shutdown(self):
        await super().async_shutdown()
        try:
            await self.device.shutdown()
        except Exception as e:
            _LOGGER.exception(f"Unexpected error shutting down {self.name}")
