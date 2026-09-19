import asyncio
from unittest.mock import AsyncMock, patch
import pytest
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry, MockModule, mock_integration
from custom_components.atodosol_solarman.const import DOMAIN
from custom_components.atodosol_solarman.lan import Inverter, ScanResult

MANUAL={'name':'Test solar','host':'192.168.50.10','port':502,'transport':'modbus_tcp','lookup_file':'Auto','additional_options':{}}
DEVICE=Inverter('192.168.50.10','1234567890',3)

@pytest.fixture(autouse=True)
def mocks(hass):
    mock_integration(hass, MockModule("dhcp"))
    with patch('custom_components.atodosol_solarman.async_setup',return_value=True), patch('custom_components.atodosol_solarman.async_setup_entry',return_value=True), patch('custom_components.atodosol_solarman.config_flow.async_listdir',return_value=['deye_hybrid.yaml']), patch('custom_components.atodosol_solarman.config_flow.network.async_get_adapters',return_value=[{'enabled':True,'ipv4':[{'address':'192.168.50.2','network_prefix':24}]}]):
        yield

async def test_manual_and_auto_menu(hass):
    result=await hass.config_entries.flow.async_init(DOMAIN,context={'source':'user'})
    assert result['type']==FlowResultType.MENU
    assert result['menu_options']==['automatic','manual']
    result=await hass.config_entries.flow.async_configure(result['flow_id'],{'next_step_id':'manual'})
    assert result['type']==FlowResultType.FORM and result['step_id']=='manual'
    with patch('custom_components.atodosol_solarman.config_flow.validate_connection',return_value=(None,DEVICE)):
        result=await hass.config_entries.flow.async_configure(result['flow_id'],MANUAL)
    assert result['type']==FlowResultType.CREATE_ENTRY
    assert result['options']['host']==DEVICE.host
    assert result['options']['inverter_serial']==DEVICE.serial
    assert result['result'].unique_id=='deye_'+DEVICE.serial

async def test_manual_errors_preserve_form(hass):
    result=await hass.config_entries.flow.async_init(DOMAIN,context={'source':'user'})
    result=await hass.config_entries.flow.async_configure(result['flow_id'],{'next_step_id':'manual'})
    with patch('custom_components.atodosol_solarman.config_flow.validate_connection',return_value=({'base':'cannot_connect'},None)):
        result=await hass.config_entries.flow.async_configure(result['flow_id'],MANUAL)
    assert result['step_id']=='manual' and result['errors']=={'base':'cannot_connect'}

async def test_duplicate_serial_at_new_address(hass):
    MockConfigEntry(domain=DOMAIN,unique_id='deye_'+DEVICE.serial,options={'host':'192.168.50.11'},version=2).add_to_hass(hass)
    result=await hass.config_entries.flow.async_init(DOMAIN,context={'source':'user'})
    result=await hass.config_entries.flow.async_configure(result['flow_id'],{'next_step_id':'manual'})
    with patch('custom_components.atodosol_solarman.config_flow.validate_connection',return_value=(None,DEVICE)):
        result=await hass.config_entries.flow.async_configure(result['flow_id'],MANUAL)
    assert result['type']==FlowResultType.ABORT and result['reason']=='already_configured'

async def test_automatic_discovery_and_selection(hass):
    result=await hass.config_entries.flow.async_init(DOMAIN,context={'source':'user'})
    result=await hass.config_entries.flow.async_configure(result['flow_id'],{'next_step_id':'automatic'})
    assert result['step_id']=='automatic'
    ready = asyncio.Event()
    async def scan(*args):
        await ready.wait()
        return ScanResult([DEVICE])
    with patch('custom_components.atodosol_solarman.config_flow.scan_lan',side_effect=scan):
        result=await hass.config_entries.flow.async_configure(result['flow_id'],{'network':'192.168.50.0/24'})
        assert result['type']==FlowResultType.SHOW_PROGRESS
        ready.set()
        await hass.async_block_till_done()
    result=await hass.config_entries.flow.async_configure(result['flow_id'])
    # Home Assistant auto-advances the progress task; asking again shows the selection.
    if result.get('type')==FlowResultType.SHOW_PROGRESS_DONE:
        result=await hass.config_entries.flow.async_configure(result['flow_id'])
    assert result['step_id']=='choose'
    result=await hass.config_entries.flow.async_configure(result['flow_id'],{'device':DEVICE.host})
    assert result['step_id']=='manual'
    assert any((getattr(k,'description',None) or {}).get('suggested_value')==DEVICE.host for k in result['data_schema'].schema)

async def test_automatic_rejects_public_network(hass):
    result=await hass.config_entries.flow.async_init(DOMAIN,context={'source':'user'})
    result=await hass.config_entries.flow.async_configure(result['flow_id'],{'next_step_id':'automatic'})
    result=await hass.config_entries.flow.async_configure(result['flow_id'],{'network':'8.8.8.0/24'})
    assert result['errors']=={'network':'invalid_network'}

async def test_empty_scan_keeps_manual_alternative(hass):
    result=await hass.config_entries.flow.async_init(DOMAIN,context={'source':'user'})
    result=await hass.config_entries.flow.async_configure(result['flow_id'],{'next_step_id':'automatic'})
    with patch('custom_components.atodosol_solarman.config_flow.scan_lan',return_value=ScanResult()):
        result=await hass.config_entries.flow.async_configure(result['flow_id'],{'network':'192.168.50.0/24'})
        await hass.async_block_till_done()
    result=await hass.config_entries.flow.async_configure(result['flow_id'])
    if result.get('type')==FlowResultType.SHOW_PROGRESS_DONE:
        result=await hass.config_entries.flow.async_configure(result['flow_id'])
    assert result['step_id']=='not_found'
    assert 'manual' in result['menu_options']

async def test_cancel_scan_cancels_worker(hass):
    started=asyncio.Event();cancelled=asyncio.Event()
    async def scan(*args):
        started.set()
        try: await asyncio.Event().wait()
        finally:cancelled.set()
    result=await hass.config_entries.flow.async_init(DOMAIN,context={'source':'user'})
    result=await hass.config_entries.flow.async_configure(result['flow_id'],{'next_step_id':'automatic'})
    with patch('custom_components.atodosol_solarman.config_flow.scan_lan',side_effect=scan):
        result=await hass.config_entries.flow.async_configure(result['flow_id'],{'network':'192.168.50.0/24'})
        await started.wait()
        hass.config_entries.flow.async_abort(result['flow_id'])
        await hass.async_block_till_done()
    assert cancelled.is_set()

async def test_options_reject_different_inverter(hass):
    entry=MockConfigEntry(domain=DOMAIN,unique_id='deye_'+DEVICE.serial,options=MANUAL | {'inverter_serial':DEVICE.serial},version=2)
    entry.add_to_hass(hass)
    result=await hass.config_entries.options.async_init(entry.entry_id)
    with patch('custom_components.atodosol_solarman.config_flow.validate_connection',return_value=(None,Inverter(DEVICE.host,'9999999999',3))):
        result=await hass.config_entries.options.async_configure(result['flow_id'],{k:v for k,v in MANUAL.items() if k!='name'})
    assert result['errors']=={'base':'wrong_device'}
