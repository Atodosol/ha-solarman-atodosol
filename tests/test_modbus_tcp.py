import asyncio
import struct
from contextlib import asynccontextmanager

import pytest
from custom_components.atodosol_solarman.modbus_tcp import ModbusTCPClient
from custom_components.atodosol_solarman.pysolarman import Solarman


@asynccontextmanager
async def server_for(handler):
    tasks = set()
    async def serve(reader, writer):
        task = asyncio.current_task(); tasks.add(task)
        try:
            await handler(reader, writer)
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            writer.close(); tasks.discard(task)
    server = await asyncio.start_server(serve, '127.0.0.1', 0)
    try:
        yield server.sockets[0].getsockname()[1]
    finally:
        server.close(); await server.wait_closed()
        for task in tasks: task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


def response(req, values):
    body = bytes((3, len(values)*2)) + struct.pack('>'+'H'*len(values), *values)
    return req[:2] + struct.pack('>HHB', 0, len(body)+1, req[6]) + body


@pytest.mark.enable_socket
@pytest.mark.parametrize('split', [1, 5, 7, 9, 1000])
async def test_fragmented_full_block_and_connection_reuse(split):
    async def handler(reader, writer):
        for _ in range(2):
            req=await reader.readexactly(12)
            wire=response(req, list(range(125)))
            writer.write(wire[:split]); await writer.drain()
            await asyncio.sleep(.005)
            writer.write(wire[split:]); await writer.drain()
    async with server_for(handler) as port:
        client=Solarman('127.0.0.1',port,'modbus_tcp',0,1,1)
        try:
            assert await client.execute(3,150,count=125)==list(range(125))
            assert await client.execute(3,150,count=125)==list(range(125))
        finally: await client.close()


@pytest.mark.enable_socket
async def test_reconnect_after_truncated_frame():
    calls=0
    async def handler(reader,writer):
        nonlocal calls
        req=await reader.readexactly(12); calls+=1
        wire=response(req,[2405])
        writer.write(wire[:5] if calls==1 else wire);await writer.drain()
    async with server_for(handler) as port:
        client=ModbusTCPClient('127.0.0.1',port,timeout=1)
        try: assert await client.execute(3,150,count=1)==[2405]
        finally: await client.close()
    assert calls==2


@pytest.mark.enable_socket
async def test_write_is_not_repeated_after_lost_acknowledgement():
    calls=0
    async def handler(reader,writer):
        nonlocal calls
        req=await reader.readexactly(12);calls+=1
        assert req[7]==6
    async with server_for(handler) as port:
        client=ModbusTCPClient('127.0.0.1',port,timeout=.2)
        try:
            with pytest.raises(asyncio.IncompleteReadError): await client.execute(6,100,data=1)
        finally: await client.close()
    assert calls==1


@pytest.mark.enable_socket
@pytest.mark.parametrize('field,value', [('length',65535),('unit',2),('transaction',12345),('protocol',1)])
async def test_rejects_invalid_headers(field,value):
    async def handler(reader,writer):
        req=await reader.readexactly(12)
        tx,protocol,length,unit=struct.unpack('>HHHB',response(req,[1])[:7])
        fields=dict(transaction=tx,protocol=protocol,length=length,unit=unit);fields[field]=value
        writer.write(struct.pack('>HHHB',*(fields[k] for k in ['transaction','protocol','length','unit'])));await writer.drain()
    async with server_for(handler) as port:
        client=ModbusTCPClient('127.0.0.1',port,timeout=.3)
        try:
            with pytest.raises(ValueError): await client.execute(3,0,count=1)
        finally: await client.close()


@pytest.mark.enable_socket
async def test_cancellation_closes_socket_and_reconnects():
    ready=asyncio.Event();disconnected=asyncio.Event()
    async def handler(reader,writer):
        await reader.readexactly(12);ready.set()
        assert await reader.read()==b'';disconnected.set()
    async with server_for(handler) as port:
        client=ModbusTCPClient('127.0.0.1',port,timeout=10)
        task=asyncio.create_task(client.execute(3,0,count=1))
        await ready.wait();task.cancel()
        with pytest.raises(asyncio.CancelledError):await task
        await asyncio.wait_for(disconnected.wait(),1)
        await client.close()
        assert client._writer is None
