import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from custom_components.atodosol_solarman.lan import adapter_networks, scan_network, scan_lan, probe_inverter, Inverter


def test_adapter_ranges_exclude_public_and_disabled():
    networks,limited=adapter_networks([
        {'enabled':True,'ipv4':[{'address':'192.168.50.8','network_prefix':24}]},
        {'enabled':True,'ipv4':[{'address':'10.20.30.2','network_prefix':8}]},
        {'enabled':True,'ipv4':[{'address':'8.8.8.8','network_prefix':24}]},
        {'enabled':False,'ipv4':[{'address':'172.16.1.2','network_prefix':24}]},
    ])
    assert networks==['10.20.30.0/24','192.168.50.0/24']
    assert limited


@pytest.mark.parametrize('network',['8.8.8.0/24','10.0.0.0/8','127.0.0.0/24','169.254.0.0/24','::1/128','192.168.0.0/16'])
def test_reject_invalid_scan_ranges(network):
    with pytest.raises(ValueError):scan_network(network)


def identity_registers():
    regs=[0]*23;regs[0]=3
    regs[3:8]=[int.from_bytes(b'1234567890'[i:i+2],'big') for i in range(0,10,2)]
    return regs


async def test_probe_verifies_identity_and_only_reads():
    with patch('custom_components.atodosol_solarman.lan.ModbusTCPClient') as cls:
        cls.return_value.execute=AsyncMock(return_value=identity_registers())
        cls.return_value.close=AsyncMock()
        found=await probe_inverter('192.168.50.10')
        assert found.serial=='1234567890'
        cls.return_value.execute.assert_awaited_once_with(3,0,count=23)
        cls.return_value.close.assert_awaited_once()


@pytest.mark.parametrize('kind',['other_modbus','zeros','invalid_serial'])
async def test_probe_rejects_non_inverters(kind):
    regs=identity_registers()
    if kind=='other_modbus':regs[0]=99
    if kind=='zeros':regs=[0]*23
    if kind=='invalid_serial':regs[3]=0
    with patch('custom_components.atodosol_solarman.lan.ModbusTCPClient') as cls:
        cls.return_value.execute=AsyncMock(return_value=regs);cls.return_value.close=AsyncMock()
        with pytest.raises(ValueError):await probe_inverter('192.168.50.10')
        cls.return_value.close.assert_awaited_once()


async def test_scan_multiple_devices_dedup_and_concurrency():
    active=peak=0;calls=[]
    async def probe(host,timeout=1.5):
        nonlocal active,peak
        active+=1;peak=max(active,peak);calls.append(host)
        try:
            await asyncio.sleep(.001)
            if host.endswith(('.10','.12')):return Inverter(host,host,3)
            raise TimeoutError
        finally:active-=1
    swept=[]
    async def port(host):
        swept.append(host)
        return host.endswith(('.10','.12','.20'))
    with patch('custom_components.atodosol_solarman.lan.port_open',side_effect=port), \
         patch('custom_components.atodosol_solarman.lan.probe_inverter',side_effect=probe), \
         patch('custom_components.atodosol_solarman.lan.RETRY_DELAY',0):
        result=await scan_lan(['192.168.50.0/26','192.168.50.0/27'],exclude={'192.168.50.1'})
    assert [d.host for d in result.devices]==['192.168.50.10','192.168.50.12']
    assert len(swept)==len(set(swept))==61
    # Only hosts with 502 open are identified; a silent one is retried and reported.
    assert sorted(calls)==['192.168.50.10','192.168.50.12','192.168.50.20','192.168.50.20']
    assert result.unidentified==[('192.168.50.20','timeout')]
    assert 1<=peak<=4


async def test_scan_cancellation_cleans_workers():
    started=asyncio.Event();active=0
    async def probe(host,timeout=1.5):
        nonlocal active
        active+=1;started.set()
        try:await asyncio.Event().wait()
        finally:active-=1
    with patch('custom_components.atodosol_solarman.lan.port_open',AsyncMock(return_value=True)), \
         patch('custom_components.atodosol_solarman.lan.probe_inverter',side_effect=probe):
        task=asyncio.create_task(scan_lan(['192.168.50.0/26']))
        await started.wait();task.cancel()
        with pytest.raises(asyncio.CancelledError):await task
    assert active==0


async def test_slow_wifi_dongle_is_identified_with_a_generous_budget():
    """David 23/09: a DYDA on Wi-Fi answered after the old 1.5 s scan budget."""
    from custom_components.atodosol_solarman import lan
    budgets=[]
    async def probe(host,timeout=1.5):
        budgets.append(timeout)
        if len(budgets)==1:raise TimeoutError
        return Inverter(host,'2604070503',3)
    with patch.object(lan,'probe_inverter',side_effect=probe),patch.object(lan,'RETRY_DELAY',0):
        found=await lan.identify('192.168.0.57')
    assert found.host=='192.168.0.57' and budgets==[lan.IDENTIFY_TIMEOUT]*2 and lan.IDENTIFY_TIMEOUT>=5


async def test_non_deye_modbus_answer_is_not_retried():
    from custom_components.atodosol_solarman import lan
    probe=AsyncMock(side_effect=ValueError('Not a supported Deye inverter'))
    with patch.object(lan,'probe_inverter',probe):
        with pytest.raises(ValueError):await lan.identify('192.168.0.9')
    probe.assert_awaited_once()


async def test_padded_serial_is_accepted():
    regs=identity_registers()
    regs[3:8]=[int.from_bytes(b'23051781\x00\x00'[i:i+2],'big') for i in range(0,10,2)]
    with patch('custom_components.atodosol_solarman.lan.ModbusTCPClient') as cls:
        cls.return_value.execute=AsyncMock(return_value=regs);cls.return_value.close=AsyncMock()
        found=await probe_inverter('192.168.50.10')
    assert found.serial=='23051781'


async def test_port_open_sends_nothing():
    from unittest.mock import MagicMock
    from custom_components.atodosol_solarman import lan
    writer=MagicMock();writer.wait_closed=AsyncMock()
    with patch.object(lan.asyncio,'open_connection',AsyncMock(return_value=(MagicMock(),writer))) as conn:
        assert await lan.port_open('192.168.0.57')
    conn.assert_awaited_once_with('192.168.0.57',502)
    writer.write.assert_not_called();writer.close.assert_called_once()
    with patch.object(lan.asyncio,'open_connection',AsyncMock(side_effect=ConnectionRefusedError)):
        assert not await lan.port_open('192.168.0.58')


def test_reasons_are_localized():
    from custom_components.atodosol_solarman.lan import reason_text
    assert reason_text('timeout','es')=='no responde a Modbus a tiempo'
    assert reason_text('not_deye','es-ES').startswith('responde a Modbus')
    assert reason_text('timeout','de')=='no Modbus answer in time'
