"""Atodosol board (ATS-DOM): API client, config flow, entities and changes, against a simulated board."""
import asyncio
import json
from copy import deepcopy
from datetime import timedelta
from ipaddress import ip_address
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlencode

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers.service_info.dhcp import DhcpServiceInfo
from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo
from pytest_homeassistant_custom_component.common import MockConfigEntry, MockModule, mock_integration

from custom_components.atodosol_solarman.board import KIND, KIND_BOARD, board_client, board_title
from custom_components.atodosol_solarman.board import api
from custom_components.atodosol_solarman.board.api import (BoardConnectionError, BoardInvalidResponse, BoardState,
                                                           BoardWriteUnknown, format_kw, normalize_host,
                                                           settings_fields, valid_host)
from custom_components.atodosol_solarman.board.sensor import charge_state, regulation, settings_state
from custom_components.atodosol_solarman.const import DOMAIN
from custom_components.atodosol_solarman.diagnostics import async_get_config_entry_diagnostics

from fake_board import BOARD_ID, LAB, FakeBoard, writable_state

pytestmark = pytest.mark.usefixtures("socket_enabled")

ROOT = Path(__file__).parents[1] / 'custom_components' / DOMAIN


async def no_discovery(*args):
    async def empty():
        return
        yield
    return empty()


@pytest.fixture(autouse=True)
def mocks(hass, monkeypatch):
    mock_integration(hass, MockModule('dhcp'))
    mock_integration(hass, MockModule('network'))
    monkeypatch.setattr(api, 'MIN_GAP', 0)
    monkeypatch.setattr(api, 'RETRY_DELAY', 0)
    with patch('custom_components.atodosol_solarman.discover', no_discovery):
        yield


@pytest.fixture
async def board(hass):
    fake = await FakeBoard().start()
    yield fake
    # Unload what the test left loaded or waiting to retry, so no timer lingers.
    for entry in hass.config_entries.async_entries(DOMAIN):
        if entry.state in (ConfigEntryState.LOADED, ConfigEntryState.SETUP_RETRY):
            await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    await fake.stop()
    # Never the PIN endpoint, never an Origin header.
    assert not [r for r in fake.requests if r[1] == '/api/pin']
    assert not [r for r in fake.requests if 'Origin' in r[2]]


def board_entry(hass, host, unique_id=BOARD_ID, **options):
    entry = MockConfigEntry(domain=DOMAIN, title=board_title(unique_id), unique_id=unique_id, data={KIND: KIND_BOARD},
                            options={'host': host, 'scan_interval': 15, **options}, version=2, minor_version=0)
    entry.add_to_hass(hass)
    return entry


async def setup_board(hass, fake, **options):
    entry = board_entry(hass, fake.host, **options)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


def eid(hass, platform, key):
    return er.async_get(hass).async_get_entity_id(platform, DOMAIN, f'{BOARD_ID}_{key}')


def state_of(hass, platform, key):
    return hass.states.get(eid(hass, platform, key))


def zeroconf_info(port, host='127.0.0.1', hostname='atodosol.local.'):
    return ZeroconfServiceInfo(ip_address=ip_address(host), ip_addresses=[ip_address(host)], port=port,
                               hostname=hostname, type='_http._tcp.local.', name='ats-dom._http._tcp.local.',
                               properties={'version': '2026.2.4'})


# --- Parsing and form bodies ---------------------------------------------------------------------------------


def test_lab_board_state_parses_as_not_configured():
    state = BoardState.parse(LAB)
    assert (state.id, state.fw, state.mac) == (BOARD_ID, '0.5.193', '0a:1b:2c:3d:4e:5f')
    assert not state.configured and not state.writable and state.notice
    assert state.soc is None and state.load_w is None and state.grid_w is None
    assert state.charger.charge_state is None and state.charger.age_s is None and state.charger.power_w is None
    assert len(state.chargers) == 4 and state.alarms == []
    assert charge_state(state) is None and regulation(state) == 'active'
    assert settings_state(state, '', False) == 'unconfigured'


def test_nulls_wrong_types_and_foreign_answers():
    state = BoardState.parse({'id': BOARD_ID, 'soc': 'x', 'load_w': None, 'minimum': True, 'charger': None})
    assert state.charger is None and state.chargers is None and state.soc is None and state.minimum == 0
    assert charge_state(state) is None and regulation(state) == 'no_charger'
    assert state.pin_ok  # absent means fine
    for payload in ([], 'atsdom_x', {'id': 'esp32_0a1b2c3d4e5f'}, {'id': 'atsdom_'}, {}):
        with pytest.raises(BoardInvalidResponse):
            BoardState.parse(payload)


def test_offgrid_hides_grid_and_contracted_power():
    state = BoardState.parse(writable_state(offgrid=True, contracted_w=4600, grid_w=300))
    assert state.offgrid and state.contracted_w == 0 and state.grid_w is None
    with pytest.raises(ValueError):
        settings_fields(state, contracted_kw=4.6)


def body(state, **change):
    return urlencode(settings_fields(BoardState.parse(state), **change))


def test_exact_form_bodies():
    saved = writable_state()  # soc_protect on, minimum 30, «Carga máxima», no PIN
    assert body(saved, mode='solar') == 'mode=solar&soc_protect=1&minimum=30'
    assert body(saved, mode='max') == 'mode=max&soc_protect=1&minimum=30'
    assert body(saved, soc_protect=False) == 'soc_protect=0'
    assert body(saved, soc_protect=True, minimum=45) == 'soc_protect=1&minimum=45'
    assert body(saved, contracted_kw=4.6) == 'contracted_kw=4.6&soc_protect=1&minimum=30'
    assert body(saved, pin='1234', mode='max') == 'mode=max&soc_protect=1&minimum=30'  # no PIN set: never sent
    with_pin = writable_state(pin_set=True)
    assert body(with_pin, pin='1234', mode='max') == 'pin=1234&mode=max&soc_protect=1&minimum=30'
    off = writable_state(soc_protect=False, minimum=0)
    assert body(off, mode='solar') == 'mode=solar&soc_protect=0'
    assert body(off, soc_protect=True, fallback_minimum=25) == 'soc_protect=1&minimum=25'
    for change in ({'soc_protect': True, 'minimum': 96}, {'soc_protect': True, 'minimum': 0}, {'mode': 'eco'},
                   {'contracted_kw': 0.4}, {'contracted_kw': 100.5}):
        with pytest.raises(ValueError):
            body(saved, **change)


@pytest.mark.parametrize('kw,text', [(4.6, '4.6'), (10, '10'), (5.75, '5.75'), (3.465, '3.465'), (0.5, '0.5'),
                                     (100, '100'), (4.6000001, '4.6'), (13.856, '13.856')])
def test_contracted_power_text_matches_the_board_parser(kw, text):
    assert format_kw(kw) == text


def test_hosts():
    assert normalize_host(' http://Atodosol.local/ ') == 'atodosol.local'
    assert normalize_host('atodosol-2.local.') == 'atodosol-2.local'
    assert normalize_host('192.168.1.50:80') == '192.168.1.50'
    assert normalize_host('192.168.1.50:8080') == '192.168.1.50:8080'
    assert valid_host('atodosol.local') and valid_host('192.168.1.50') and valid_host('127.0.0.1:8080')
    for bad in ('', 'bad host', 'atodosol..local', '-x.local', 'user@atodosol.local', 'atodosol.local:'):
        assert not valid_host(normalize_host(bad)), bad


# --- Client --------------------------------------------------------------------------------------------------


async def test_client_headers(hass, board):
    await board_client(hass, board.host).get_state()
    method, path, headers, _ = board.requests[-1]
    assert (method, path) == ('GET', '/api/state')
    assert headers['Host'] == board.host and headers['Accept'] == 'application/json' and 'Origin' not in headers


async def test_reads_are_retried_once(hass, board, monkeypatch):
    monkeypatch.setattr(api, 'REQUEST_TIMEOUT', 0.2)
    board.slow = 1
    assert (await board_client(hass, board.host).get_state()).id == BOARD_ID
    assert board.gets == 2
    board.slow = 2
    with pytest.raises(BoardConnectionError):
        await board_client(hass, board.host).get_state()
    assert board.gets == 4
    # A dropped connection: aiohttp repeats the GET once by itself, and that is all.
    board.drop = 1
    assert (await board_client(hass, board.host).get_state()).id == BOARD_ID
    assert board.gets == 6
    board.drop = 2
    with pytest.raises(BoardConnectionError):
        await board_client(hass, board.host).get_state()
    assert board.gets == 8
    await asyncio.sleep(0.6)  # let the slow answers finish


async def test_changes_are_never_retried(hass, board, monkeypatch):
    monkeypatch.setattr(api, 'REQUEST_TIMEOUT', 0.2)
    board.drop = 1
    with pytest.raises(BoardWriteUnknown):
        await board_client(hass, board.host).post_settings([('soc_protect', '0')])
    board.slow = 1
    with pytest.raises(BoardWriteUnknown):
        await board_client(hass, board.host).post_settings([('soc_protect', '0')])
    await asyncio.sleep(0.6)
    assert [r[0] for r in board.requests] == ['POST', 'POST']


async def test_requests_to_one_board_never_overlap(hass, board):
    board.delay = 0.05
    one, two = board_client(hass, board.host), board_client(hass, f'http://{board.host}/')
    await asyncio.gather(*(c.get_state() for c in (one, two, one, two)),
                         one.post_settings([('soc_protect', '0')]), two.post_settings([('soc_protect', '0')]))
    assert board.max_active == 1 and len(board.requests) == 6


# --- Config flow ---------------------------------------------------------------------------------------------


async def start_board_flow(hass):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={'source': 'user'})
    assert result['type'] is FlowResultType.MENU
    assert result['menu_options'] == ['automatic', 'manual', 'board']
    result = await hass.config_entries.flow.async_configure(result['flow_id'], {'next_step_id': 'board'})
    assert result['type'] is FlowResultType.FORM and result['step_id'] == 'board'
    return result


async def test_manual_board(hass, board):
    result = await start_board_flow(hass)
    result = await hass.config_entries.flow.async_configure(result['flow_id'], {'host': f'http://{board.host}/'})
    assert result['type'] is FlowResultType.CREATE_ENTRY
    assert result['title'] == 'Placa domótica Atodosol 4e5f'
    assert result['data'] == {KIND: KIND_BOARD}
    assert result['options'] == {'host': board.host, 'scan_interval': 15}
    entry = result['result']
    assert entry.unique_id == BOARD_ID
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED


async def test_manual_board_errors(hass, board):
    result = await start_board_flow(hass)
    flow = result['flow_id']
    for host, error in (('bad host', 'invalid_host'), ('127.0.0.1:1', 'board_cannot_connect')):
        result = await hass.config_entries.flow.async_configure(flow, {'host': host})
        assert result['type'] is FlowResultType.FORM and result['errors'] == {'base': error}
    board.allowed = {'atodosol.local'}
    result = await hass.config_entries.flow.async_configure(flow, {'host': board.host})
    assert result['errors'] == {'base': 'board_host_rejected'}
    board.allowed, board.state = None, {'id': 'esp32_0a1b2c3d4e5f'}
    result = await hass.config_entries.flow.async_configure(flow, {'host': board.host})
    assert result['errors'] == {'base': 'not_a_board'}
    assert not hass.config_entries.async_entries(DOMAIN)


async def test_same_board_twice(hass, board):
    board_entry(hass, 'atodosol.local')
    result = await start_board_flow(hass)
    result = await hass.config_entries.flow.async_configure(result['flow_id'], {'host': board.host})
    assert result['type'] is FlowResultType.ABORT and result['reason'] == 'board_already_configured'
    assert len(hass.config_entries.async_entries(DOMAIN)) == 1


async def test_zeroconf_discovery_is_confirmed(hass, board):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={'source': 'zeroconf'},
                                                       data=zeroconf_info(board.port))
    assert result['type'] is FlowResultType.FORM and result['step_id'] == 'board_discovered'
    assert result['description_placeholders'] == {'name': 'atodosol.local', 'host': board.host}
    result = await hass.config_entries.flow.async_configure(result['flow_id'], {})
    assert result['type'] is FlowResultType.CREATE_ENTRY
    assert result['result'].unique_id == BOARD_ID and result['options']['host'] == board.host


async def test_zeroconf_of_a_known_address_sends_nothing(hass, board):
    board_entry(hass, board.host)
    result = await hass.config_entries.flow.async_init(DOMAIN, context={'source': 'zeroconf'},
                                                       data=zeroconf_info(board.port))
    assert result['type'] is FlowResultType.ABORT and result['reason'] == 'board_already_configured'
    assert board.requests == []


async def test_zeroconf_follows_a_board_to_its_new_address(hass, board):
    entry = board_entry(hass, '127.0.0.1:1')
    with patch.object(hass.config_entries, 'async_schedule_reload') as reload:
        result = await hass.config_entries.flow.async_init(DOMAIN, context={'source': 'zeroconf'},
                                                           data=zeroconf_info(board.port, hostname='ats-dom.local.'))
    assert result['type'] is FlowResultType.ABORT and result['reason'] == 'board_already_configured'
    assert entry.options['host'] == board.host
    reload.assert_called_once_with(entry.entry_id)


async def test_zeroconf_of_something_else(hass, board):
    board.state = {'id': 'shelly_1234'}
    result = await hass.config_entries.flow.async_init(DOMAIN, context={'source': 'zeroconf'},
                                                       data=zeroconf_info(board.port))
    assert result['type'] is FlowResultType.ABORT and result['reason'] == 'not_a_board'
    info = zeroconf_info(board.port, host='fe80::1')
    result = await hass.config_entries.flow.async_init(DOMAIN, context={'source': 'zeroconf'}, data=info)
    assert result['type'] is FlowResultType.ABORT and result['reason'] == 'not_a_board'


async def test_dhcp_of_a_board(hass):
    by_ip = board_entry(hass, '10.9.9.9')
    with patch.object(hass.config_entries, 'async_schedule_reload') as reload:
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={'source': 'dhcp'}, data=DhcpServiceInfo(ip='10.9.9.10', hostname='ats-dom', macaddress='0a1b2c3d4e5f'))
    assert result['type'] is FlowResultType.ABORT and result['reason'] == 'board_already_configured'
    assert by_ip.options['host'] == '10.9.9.10'
    reload.assert_called_once_with(by_ip.entry_id)


async def test_dhcp_keeps_a_board_name_and_leaves_inverters_alone(hass):
    by_name = board_entry(hass, 'atodosol.local')
    with patch.object(hass.config_entries, 'async_schedule_reload') as reload:
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={'source': 'dhcp'}, data=DhcpServiceInfo(ip='10.9.9.10', hostname='ats-dom', macaddress='0a1b2c3d4e5f'))
    assert result['reason'] == 'board_already_configured' and by_name.options['host'] == 'atodosol.local'
    reload.assert_not_called()
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={'source': 'dhcp'}, data=DhcpServiceInfo(ip='10.9.9.20', hostname='dongle', macaddress='e8fdf8001122'))
    assert result['type'] is FlowResultType.FORM and result['step_id'] == 'manual'


async def test_options_same_board_pin_and_interval(hass, board):
    entry = await setup_board(hass, board)
    gets = board.gets
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result['type'] is FlowResultType.FORM and result['step_id'] == 'board'
    result = await hass.config_entries.options.async_configure(result['flow_id'], {'host': f'HTTP://{board.host}/', 'pin': '12ab', 'scan_interval': 30})
    assert result['errors'] == {'pin': 'invalid_pin'}
    result = await hass.config_entries.options.async_configure(result['flow_id'], {'host': board.host, 'pin': '1234', 'scan_interval': 30})
    assert result['type'] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert dict(entry.options) == {'host': board.host, 'pin': '1234', 'scan_interval': 30}
    assert entry.state is ConfigEntryState.LOADED and entry.runtime_data.update_interval == timedelta(seconds=30)
    assert board.gets == gets + 1  # same address: no check, only the read after the reload


async def test_options_refuse_another_board(hass, board):
    entry = await setup_board(hass, board)
    other = await FakeBoard(writable_state(id='atsdom_ffeeddccbbaa')).start()
    try:
        result = await hass.config_entries.options.async_init(entry.entry_id)
        result = await hass.config_entries.options.async_configure(result['flow_id'], {'host': other.host, 'scan_interval': 15})
        assert result['type'] is FlowResultType.FORM and result['errors'] == {'base': 'wrong_board'}
    finally:
        await other.stop()
    assert entry.options['host'] == board.host


# --- Entities ------------------------------------------------------------------------------------------------


async def test_entities_of_a_writable_board(hass, board):
    entry = await setup_board(hass, board)
    assert entry.state is ConfigEntryState.LOADED
    values = {
        ('sensor', 'battery'): '64', ('sensor', 'house_load'): '812', ('sensor', 'grid_power'): '-1250',
        ('sensor', 'charger_power'): '3680', ('sensor', 'charger_state'): 'charging', ('sensor', 'charger_limit'): '16',
        ('sensor', 'regulation'): 'active', ('sensor', 'settings'): 'ready',
        ('binary_sensor', 'charger_locked'): 'off', ('binary_sensor', 'change_pending'): 'off',
        ('binary_sensor', 'charger_alarm'): 'off', ('select', 'charge_mode'): 'max', ('switch', 'soc_protect'): 'on',
        ('number', 'soc_minimum'): '30', ('number', 'contracted_power'): '4.6',
    }
    assert {k: state_of(hass, *k).state for k in values} == values
    assert state_of(hass, 'select', 'charge_mode').attributes['options'] == ['max', 'solar']
    assert state_of(hass, 'sensor', 'charger_limit').attributes['ceiling_a'] == 32
    age = er.async_get(hass).async_get(eid(hass, 'sensor', 'charger_data_age'))
    assert age.disabled_by is er.RegistryEntryDisabler.INTEGRATION
    device, = dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)
    assert (DOMAIN, BOARD_ID) in device.identifiers
    assert (device.manufacturer, device.sw_version) == ('Atodosol', '0.5.193')
    assert (dr.CONNECTION_NETWORK_MAC, '0a:1b:2c:3d:4e:5f') in device.connections
    assert device.configuration_url == f'http://{board.host}/'


async def test_lab_board_shows_the_house_and_blocks_the_controls(hass, board):
    board.state = deepcopy(LAB)
    await setup_board(hass, board)
    assert state_of(hass, 'sensor', 'battery').state == STATE_UNKNOWN
    settings = state_of(hass, 'sensor', 'settings')
    assert settings.state == 'unconfigured' and settings.attributes['notice'] == LAB['notice']
    for platform, key in (('select', 'charge_mode'), ('switch', 'soc_protect'), ('number', 'soc_minimum'),
                          ('number', 'contracted_power')):
        assert state_of(hass, platform, key).state == STATE_UNAVAILABLE
    assert state_of(hass, 'sensor', 'charger_state').state == STATE_UNKNOWN
    assert state_of(hass, 'binary_sensor', 'charger_alarm').state == 'off'
    with pytest.raises(HomeAssistantError) as err:
        await hass.config_entries.async_entries(DOMAIN)[0].runtime_data.async_change(mode='max')
    assert err.value.translation_key == 'not_writable'
    assert board.posts == []


async def test_offgrid_and_solar_mode(hass, board):
    board.state = writable_state(offgrid=True, solar=True, solar_ok=False, grid_w=None)
    entry = await setup_board(hass, board)
    assert state_of(hass, 'sensor', 'grid_power').state == STATE_UNAVAILABLE
    assert state_of(hass, 'number', 'contracted_power').state == STATE_UNAVAILABLE
    assert state_of(hass, 'switch', 'soc_protect').state == STATE_UNAVAILABLE
    assert state_of(hass, 'number', 'soc_minimum').state == STATE_UNAVAILABLE
    mode = state_of(hass, 'select', 'charge_mode')
    assert mode.state == 'solar' and mode.attributes['options'] == ['max', 'solar']  # it can be left
    board.state.update(solar=False)
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    assert state_of(hass, 'select', 'charge_mode').attributes['options'] == ['max']
    with pytest.raises(HomeAssistantError) as err:
        await entry.runtime_data.async_change(mode='solar')
    assert err.value.translation_key == 'solar_unavailable' and board.posts == []


async def test_charger_alarm(hass, board):
    board.state['chargers'][1].update(alarm=True, fault='E12', fault_label='Fallo de tierra')
    await setup_board(hass, board)
    alarm = state_of(hass, 'binary_sensor', 'charger_alarm')
    assert alarm.state == 'on' and alarm.attributes['alarms'] == [{'charger_id': board.state['chargers'][1]['id'], 'fault': 'E12', 'fault_label': 'Fallo de tierra'}]


async def test_wrong_board_is_never_shown_as_this_one(hass, board):
    entry = board_entry(hass, board.host, unique_id='atsdom_ffeeddccbbaa')
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.SETUP_RETRY


async def test_unreachable_board_retries_setup(hass):
    entry = board_entry(hass, '127.0.0.1:1')
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.SETUP_RETRY
    await hass.config_entries.async_unload(entry.entry_id)


# --- Changes -------------------------------------------------------------------------------------------------


async def call(hass, platform, service, key, **data):
    await hass.services.async_call(platform, service, {'entity_id': eid(hass, platform, key), **data}, blocking=True)
    await hass.async_block_till_done()


async def test_charge_mode_change(hass, board):
    entry = await setup_board(hass, board)
    await call(hass, 'select', 'select_option', 'charge_mode', option='solar')
    assert board.posts == ['mode=solar&soc_protect=1&minimum=30']
    assert state_of(hass, 'select', 'charge_mode').state == 'solar'
    assert state_of(hass, 'binary_sensor', 'change_pending').state == 'on'
    assert entry.runtime_data.update_interval == timedelta(seconds=5)
    board.state['charger']['pending'] = False
    await entry.runtime_data.async_refresh()
    assert entry.runtime_data.update_interval == timedelta(seconds=15)


async def test_soc_protection_off_and_on_again(hass, board):
    entry = await setup_board(hass, board)
    await call(hass, 'switch', 'turn_off', 'soc_protect')
    assert state_of(hass, 'switch', 'soc_protect').state == 'off'
    assert state_of(hass, 'number', 'soc_minimum').state == STATE_UNAVAILABLE
    await call(hass, 'switch', 'turn_on', 'soc_protect')
    assert board.posts == ['soc_protect=0', 'soc_protect=1&minimum=30']  # back with the minimum it had
    await entry.runtime_data.async_refresh()
    await call(hass, 'number', 'set_value', 'soc_minimum', value=45)
    await entry.runtime_data.async_refresh()
    await call(hass, 'number', 'set_value', 'contracted_power', value=5.75)
    assert board.posts[2:] == ['soc_protect=1&minimum=45', 'contracted_kw=5.75&soc_protect=1&minimum=45']
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    assert state_of(hass, 'number', 'contracted_power').state == '5.75'


async def change_error(hass, platform, service, key, **data):
    with pytest.raises(HomeAssistantError) as err:
        await call(hass, platform, service, key, **data)
    return err.value


async def test_board_with_pin_and_no_pin_saved(hass, board):
    board.state, board.pin = writable_state(pin_set=True), '1234'
    await setup_board(hass, board)
    assert state_of(hass, 'sensor', 'settings').state == 'pin_missing'
    err = await change_error(hass, 'select', 'select_option', 'charge_mode', option='solar')
    assert err.translation_key == 'pin_missing' and board.posts == [] and board.failures == 0


async def test_good_pin(hass, board):
    board.state, board.pin = writable_state(pin_set=True), '1234'
    await setup_board(hass, board, pin='1234')
    assert state_of(hass, 'sensor', 'settings').state == 'ready'
    await call(hass, 'select', 'select_option', 'charge_mode', option='solar')
    assert board.posts == ['pin=1234&mode=solar&soc_protect=1&minimum=30'] and board.failures == 0
    assert state_of(hass, 'select', 'charge_mode').state == 'solar'


async def test_wrong_pin_is_sent_only_once(hass, board):
    board.state, board.pin = writable_state(pin_set=True), '1234'
    await setup_board(hass, board, pin='9999')
    err = await change_error(hass, 'select', 'select_option', 'charge_mode', option='solar')
    assert err.translation_key == 'pin_wrong' and err.translation_placeholders == {'attempts': '4'}
    assert state_of(hass, 'sensor', 'settings').state == 'pin_rejected'
    err = await change_error(hass, 'switch', 'turn_off', 'soc_protect')
    assert err.translation_key == 'pin_rejected'
    assert len(board.posts) == 1 and board.failures == 1


async def test_locked_board(hass, board):
    board.state, board.pin = writable_state(pin_set=True), '1234'
    entry = await setup_board(hass, board, pin='1234')
    board.reply = (423, {'error': 'Demasiados PIN incorrectos; espera 5 min', 'attempts_left': 0, 'locked_s': 300})
    err = await change_error(hass, 'switch', 'turn_off', 'soc_protect')
    assert err.translation_key == 'pin_locked' and err.translation_placeholders == {'minutes': '5'}
    board.state['locked_s'] = 240
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    assert state_of(hass, 'sensor', 'settings').state == 'pin_locked'
    err = await change_error(hass, 'switch', 'turn_off', 'soc_protect')
    assert err.translation_key == 'pin_locked' and err.translation_placeholders == {'minutes': '4'}
    assert len(board.posts) == 1


async def test_board_refuses_the_change(hass, board):
    await setup_board(hass, board)
    board.state['writable'] = False  # it stopped accepting changes after the last read
    board.state['notice'] = 'La regulación aún no está configurada desde Miledy'
    err = await change_error(hass, 'switch', 'turn_off', 'soc_protect')
    assert err.translation_key == 'write_rejected'
    assert err.translation_placeholders == {'message': 'La regulación aún no está configurada desde Miledy'}
    assert board.posts == ['soc_protect=0']
    assert state_of(hass, 'switch', 'soc_protect').state == STATE_UNAVAILABLE


async def test_change_without_answer_is_not_repeated(hass, board):
    await setup_board(hass, board)
    posts = len([r for r in board.requests if r[0] == 'POST'])
    board.drop = 1
    err = await change_error(hass, 'switch', 'turn_off', 'soc_protect')
    assert err.translation_key == 'write_unknown'
    assert len([r for r in board.requests if r[0] == 'POST']) == posts + 1


async def test_unreachable_board_on_a_change(hass, board):
    entry = await setup_board(hass, board)
    entry.runtime_data.client = board_client(hass, '127.0.0.1:1')
    err = await change_error(hass, 'switch', 'turn_off', 'soc_protect')
    assert err.translation_key == 'write_failed'


# --- Diagnostics and texts -----------------------------------------------------------------------------------


async def test_diagnostics_hide_pin_and_addresses(hass, board):
    board.state, board.pin = writable_state(pin_set=True), '4321'
    entry = await setup_board(hass, board, pin='4321')
    result = await async_get_config_entry_diagnostics(hass, entry)
    text = json.dumps(result, ensure_ascii=False)
    for secret in ('4321', board.host, BOARD_ID, '192.168.1.50', 'MiWifi', '001122334455', '1199887766'):
        assert secret not in text, secret
    assert result['pin_configured'] is True and result['state']['fw'] == '0.5.193'


def test_texts_exist_in_spanish_and_english():
    import re
    code = '\n'.join(p.read_text() for p in (ROOT / 'board').glob('*.py'))
    keys = set(re.findall(r'_error\("(\w+)"', code)) | set(re.findall(r'translation_key="(\w+)"', code))
    from custom_components.atodosol_solarman.board import sensor
    for lang in ('strings', 'translations/en', 'translations/es'):
        texts = json.loads((ROOT / f'{lang}.json').read_text())
        assert keys <= set(texts['exceptions']), (lang, keys - set(texts['exceptions']))
        for error in ('invalid_host', 'board_cannot_connect', 'board_host_rejected', 'not_a_board'):
            assert error in texts['config']['error'], (lang, error)
        for error in ('board_cannot_connect', 'board_host_rejected', 'not_a_board', 'wrong_board', 'invalid_pin'):
            assert error in texts['options']['error'], (lang, error)
        for reason in ('board_already_configured', 'not_a_board', 'board_cannot_connect', 'invalid_host'):
            assert reason in texts['config']['abort'], (lang, reason)
        entity = texts['entity']
        assert set(entity['sensor']['regulation']['state']) == set(sensor.REASON_OPTIONS)
        assert set(entity['sensor']['settings']['state']) == set(sensor.SETTINGS_OPTIONS)
        assert set(entity['sensor']['charger_state']['state']) == set(sensor.CHARGE_STATE_OPTIONS)
        assert set(entity['select']['charge_mode']['state']) == {'max', 'solar'}
        assert texts['config']['step']['user']['menu_options']['board']


async def test_pin_set_on_the_board_after_the_last_read(hass, board):
    board.pin = '1234'  # the state still says there is no PIN
    entry = await setup_board(hass, board, pin='1234')
    err = await change_error(hass, 'switch', 'turn_off', 'soc_protect')
    assert err.translation_key == 'write_rejected' and board.posts == ['soc_protect=0']
    board.state['pin_set'] = True
    await entry.runtime_data.async_refresh()
    await call(hass, 'switch', 'turn_off', 'soc_protect')
    assert board.posts[-1] == 'pin=1234&soc_protect=0' and board.failures == 0


async def test_pin_never_reaches_the_log(hass, board, caplog):
    caplog.set_level('DEBUG', logger='custom_components.atodosol_solarman')
    board.state, board.pin = writable_state(pin_set=True), '4321'
    entry = await setup_board(hass, board, pin='4321')
    await call(hass, 'select', 'select_option', 'charge_mode', option='solar')
    await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert board.posts == ['pin=4321&mode=solar&soc_protect=1&minimum=30']
    assert 'async_setup' in caplog.text or 'Cambio enviado' in caplog.text
    assert '4321' not in caplog.text


# --- Charge mode reported by the board (`mode`) --------------------------------------------------------------


def test_charge_mode_reads_the_mode_field_first():
    # No `mode` (firmware 0.5.193): derived from `solar`, as before.
    assert BoardState.parse(writable_state()).charge_mode == 'max'
    assert BoardState.parse(writable_state(solar=True)).charge_mode == 'solar'
    # `mode` comes first when the board sends it.
    assert BoardState.parse(writable_state(mode='solar', solar=False)).charge_mode == 'solar'
    assert BoardState.parse(writable_state(mode='max', solar=True)).charge_mode == 'max'
    for unknown in ('cheap', '', None, 2):
        state = BoardState.parse(writable_state(mode=unknown, solar=False))
        assert state.charge_mode is None, unknown  # never «max»
        assert not state.max_mode and not state.solar_mode


def test_unknown_mode_never_sends_mode():
    for change in ({'soc_protect': True, 'minimum': 45}, {'contracted_kw': 5.75}, {'soc_protect': False}):
        assert 'mode=' not in body(writable_state(mode='cheap'), **change), change


async def test_mode_field_drives_the_select(hass, board):
    board.state = writable_state(mode='solar', solar=False)
    await setup_board(hass, board)
    mode = state_of(hass, 'select', 'charge_mode')
    assert mode.state == 'solar' and mode.attributes['board_mode'] == 'solar'
    assert state_of(hass, 'switch', 'soc_protect').state == STATE_UNAVAILABLE
    assert state_of(hass, 'number', 'soc_minimum').state == STATE_UNAVAILABLE


@pytest.mark.parametrize('solar_ok,options', [(True, ['max', 'solar']), (False, ['max'])])
async def test_unknown_mode_is_unknown_and_never_overwritten(hass, board, solar_ok, options):
    board.state = writable_state(mode='cheap', solar_ok=solar_ok)
    entry = await setup_board(hass, board)
    mode = state_of(hass, 'select', 'charge_mode')
    assert mode.state == STATE_UNKNOWN and mode.attributes['board_mode'] == 'cheap'
    assert mode.attributes['options'] == options
    # Switch and minimum belong to «Carga máxima»: not in a mode this version does not know.
    assert state_of(hass, 'switch', 'soc_protect').state == STATE_UNAVAILABLE
    assert state_of(hass, 'number', 'soc_minimum').state == STATE_UNAVAILABLE
    # Contracted power, and a minimum sent by other means, never carry `mode`.
    await call(hass, 'number', 'set_value', 'contracted_power', value=5.75)
    await entry.runtime_data.async_refresh()
    await entry.runtime_data.async_change(soc_protect=True, minimum=45)
    await hass.async_block_till_done()
    assert board.posts == ['contracted_kw=5.75&soc_protect=1&minimum=30', 'soc_protect=1&minimum=45']
    assert board.state['mode'] == 'cheap'
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    assert state_of(hass, 'select', 'charge_mode').state == STATE_UNKNOWN
    # Choosing a known mode is still possible, on purpose.
    await call(hass, 'select', 'select_option', 'charge_mode', option='max')
    assert board.posts[-1] == 'mode=max&soc_protect=1&minimum=45'
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    assert state_of(hass, 'select', 'charge_mode').state == 'max'
    assert state_of(hass, 'switch', 'soc_protect').state == 'on'


async def test_firmware_update_without_the_recent_device_lookup(hass, board, monkeypatch):
    """Home Assistant 2025.2 (hacs.json) has no DeviceRegistry.async_get_device_by_identifier."""
    monkeypatch.delattr(dr.DeviceRegistry, 'async_get_device_by_identifier')
    entry = await setup_board(hass, board)
    assert entry.state is ConfigEntryState.LOADED
    board.state['fw'] = '0.5.194'
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    assert entry.runtime_data.last_update_success
    assert state_of(hass, 'sensor', 'battery').state == '64'
    device, = dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)
    assert device.sw_version == '0.5.194'
