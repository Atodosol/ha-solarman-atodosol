"""Framed Modbus TCP transport with bounded, cancellable read retries."""
from __future__ import annotations

import asyncio
import struct

from .pysolarman.umodbus.client import tcp


class ModbusTCPClient:
    """One request per connection at a time; TCP chunks are not protocol frames."""

    def __init__(self, host: str, port: int = 502, slave: int = 1, timeout: float = 5):
        self.host, self.port, self.slave, self.timeout = host, port, slave, timeout
        self._reader = self._writer = None
        self._lock = asyncio.Lock()
        self._failures = 0

    async def _disconnect(self):
        writer, self._writer, self._reader = self._writer, None, None
        if writer is not None:
            writer.close()
            try:
                async with asyncio.timeout(1):
                    await writer.wait_closed()
            except (OSError, TimeoutError):
                pass

    async def close(self):
        async with self._lock:
            await self._disconnect()

    async def execute(self, code: int, address: int, **kwargs) -> list[int]:
        request = tcp.function_code_to_function_map[code](self.slave, address, **kwargs)
        # Never blindly repeat a write whose acknowledgement was lost.
        attempts = 2 if code in (1, 2, 3, 4) else 1
        async with self._lock:
            for attempt in range(attempts):
                try:
                    if self._failures:
                        await asyncio.sleep(min(0.2 * 2 ** min(self._failures - 1, 5), 5))
                    async with asyncio.timeout(self.timeout):
                        if self._writer is None:
                            self._reader, self._writer = await asyncio.open_connection(self.host, self.port)
                        self._writer.write(request)
                        await self._writer.drain()
                        header = await self._reader.readexactly(7)
                        transaction, protocol, length, slave = struct.unpack('>HHHB', header)
                        if protocol or not 2 <= length <= 254:
                            raise ValueError('Invalid Modbus TCP header')
                        if header[:2] != request[:2] or slave != self.slave:
                            raise ValueError('Modbus TCP response does not match request')
                        response = header + await self._reader.readexactly(length - 1)
                        result = tcp.parse_response_adu(response, request)
                        self._failures = 0
                        return result
                except asyncio.CancelledError:
                    await self._disconnect()
                    raise
                except (OSError, TimeoutError, asyncio.IncompleteReadError, ValueError):
                    self._failures += 1
                    await self._disconnect()
                    if attempt + 1 == attempts:
                        raise
                except Exception:
                    # A well-formed Modbus exception is not a transient network error.
                    await self._disconnect()
                    raise
