from pathlib import Path
from unittest.mock import patch

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.helpers import device_registry as dr, entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry, MockModule, mock_integration
from custom_components.atodosol_solarman.const import DOMAIN
from custom_components.atodosol_solarman.provider import ConfigurationProvider

PROFILES=str(Path(__file__).parents[1]/'custom_components'/DOMAIN/'inverter_definitions')+'/'

OLD='1234567890'
NEW='2401010001'


class FakeInverter:
    """Deye hybrid answering zeros except device type and serial number."""
    serial_number=OLD
    serial=0  # Modbus TCP has no logger serial

    def __init__(self,*args):
        pass

    async def execute(self,code,address,count=1,**kwargs):
        sn=FakeInverter.serial_number.encode()
        regs=[0]*count
        for i in range(count):
            a=address+i
            if a==0:regs[i]=3
            elif 3<=a<=7:regs[i]=int.from_bytes(sn[(a-3)*2:(a-3)*2+2],'big')
        return regs

    async def close(self):
        pass



async def no_discovery(*args):
    async def empty():
        return
        yield
    return empty()


@pytest.fixture(autouse=True)
def mocks(hass):
    mock_integration(hass,MockModule('dhcp'))
    mock_integration(hass,MockModule('network'))
    FakeInverter.serial_number=OLD
    with patch('custom_components.atodosol_solarman.device.Solarman',FakeInverter), patch('custom_components.atodosol_solarman.discover',no_discovery), patch.object(ConfigurationProvider,'directory',PROFILES):
        yield


def entry_for(hass):
    entry=MockConfigEntry(domain=DOMAIN,title='Test solar',unique_id='deye_'+OLD,options={'host':'192.168.50.10','port':502,'transport':'modbus_tcp','inverter_serial':OLD},version=2,minor_version=0)
    entry.add_to_hass(hass)
    return entry


def snapshot(hass,entry):
    devices=dr.async_entries_for_config_entry(dr.async_get(hass),entry.entry_id)
    entities={e.unique_id:(e.entity_id,e.device_id) for e in er.async_entries_for_config_entry(er.async_get(hass),entry.entry_id)}
    return devices,entities


async def test_hot_serial_change_moves_device_without_duplicates(hass):
    entry=entry_for(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    devices,entities=snapshot(hass,entry)
    assert len(devices)==1 and entities
    assert devices[0].serial_number==OLD
    assert devices[0].identifiers=={(DOMAIN,entry.entry_id),(DOMAIN,OLD)}

    # The serial is read with the hourly identification group; force a full read.
    FakeInverter.serial_number=NEW
    coordinator=entry.runtime_data
    coordinator.counter=coordinator._update_interval_seconds
    await coordinator.async_refresh()
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.LOADED
    assert entry.runtime_data is not coordinator
    assert entry.unique_id=='deye_'+NEW
    assert entry.options['inverter_serial']==NEW
    after,after_entities=snapshot(hass,entry)
    assert [d.id for d in after]==[devices[0].id]
    assert after[0].serial_number==NEW
    assert after[0].identifiers=={(DOMAIN,entry.entry_id),(DOMAIN,NEW)}
    assert after_entities==entities
    assert len(dr.async_get(hass).devices)==1


async def test_replacement_while_stopped_is_applied_on_setup(hass):
    entry=entry_for(hass)
    device=dr.async_get(hass).async_get_or_create(config_entry_id=entry.entry_id,identifiers={(DOMAIN,entry.entry_id),(DOMAIN,OLD)},serial_number=OLD,name='Test solar')
    FakeInverter.serial_number=NEW
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.unique_id=='deye_'+NEW and entry.options['inverter_serial']==NEW
    devices,_=snapshot(hass,entry)
    assert [d.id for d in devices]==[device.id]
    assert devices[0].identifiers=={(DOMAIN,entry.entry_id),(DOMAIN,NEW)}


async def test_serial_owned_by_another_entry_is_not_taken(hass):
    entry=entry_for(hass)
    MockConfigEntry(domain=DOMAIN,unique_id='deye_'+NEW,options={'host':'192.168.50.30','inverter_serial':NEW},version=2,minor_version=0).add_to_hass(hass)
    FakeInverter.serial_number=NEW
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.unique_id=='deye_'+OLD and entry.options['inverter_serial']==OLD
