"""On-demand, read-only discovery of Deye-compatible Modbus TCP inverters."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from ipaddress import IPv4Address, IPv4Network, ip_network

from .const import AUTODETECTION_DEYE
from .modbus_tcp import ModbusTCPClient

MAX_HOSTS = 1024
MAX_CONCURRENCY = 24
SCAN_TIMEOUT = 45
PRIVATE_NETWORKS = tuple(ip_network(n) for n in ('10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16'))


@dataclass(frozen=True)
class Inverter:
    host: str
    serial: str
    device_type: int
    port: int = 502
    slave: int = 1

    @property
    def label(self):
        return f'Deye · {self.serial} · {self.host}'


@dataclass
class ScanResult:
    devices: list[Inverter] = field(default_factory=list)
    limited: bool = False


def scan_network(value: str) -> IPv4Network:
    """Only explicitly local RFC1918 ranges, bounded even on enterprise LANs."""
    network = ip_network(value, strict=False)
    if not isinstance(network, IPv4Network) or not any(network.subnet_of(n) for n in PRIVATE_NETWORKS):
        raise ValueError('Choose a private IPv4 LAN')
    if network.num_addresses > MAX_HOSTS:
        raise ValueError('Choose a range of /22 or smaller')
    return network


def adapter_networks(adapters: list[dict]) -> tuple[list[str], bool]:
    networks = set()
    limited = False
    for adapter in adapters:
        if not adapter.get('enabled'):
            continue
        for address in adapter.get('ipv4', []):
            try:
                net = ip_network(f"{address['address']}/{address['network_prefix']}", strict=False)
                if not isinstance(net, IPv4Network) or not any(net.subnet_of(n) for n in PRIVATE_NETWORKS):
                    continue
                if net.num_addresses > MAX_HOSTS:
                    net = ip_network(f"{address['address']}/24", strict=False)
                    limited = True
                networks.add(str(net))
            except (KeyError, ValueError):
                continue
    return sorted(networks), limited


async def probe_inverter(host: str, port: int = 502, slave: int = 1, timeout: float = 1.5) -> Inverter:
    client = ModbusTCPClient(host, port, slave, timeout)
    try:
        # Bound the whole probe, including any retry and cleanup.
        async with asyncio.timeout(timeout):
            registers = await client.execute(3, 0, count=23)
        known = {t for types in AUTODETECTION_DEYE for t in types}
        if len(registers) != 23 or registers[0] not in known:
            raise ValueError('Not a supported Deye inverter')
        serial = b''.join(v.to_bytes(2, 'big') for v in registers[3:8]).decode('ascii')
        if len(serial) != 10 or not serial.isalnum() or set(serial) <= {'0'}:
            raise ValueError('Invalid inverter serial number')
        return Inverter(host, serial, registers[0], port, slave)
    finally:
        await client.close()


async def scan_lan(networks: list[str], *, exclude: set[str] | None = None) -> ScanResult:
    """No shell, ARP cache, credentials, port sweep, or configuration writes."""
    excluded = exclude or set()
    # Deduplicate before scheduling, cap allocation as well as concurrency.
    hosts = sorted({str(h) for n in networks for h in scan_network(n).hosts() if str(h) not in excluded}, key=IPv4Address)
    result = ScanResult(limited=len(hosts) > MAX_HOSTS)
    queue = asyncio.Queue()
    for host in hosts[:MAX_HOSTS]:
        queue.put_nowait(host)

    async def worker():
        while not queue.empty():
            host = queue.get_nowait()
            try:
                result.devices.append(await probe_inverter(host))
            except (OSError, TimeoutError, ValueError, UnicodeError, asyncio.IncompleteReadError):
                pass
            except Exception:
                # Other Modbus servers can return application-level exceptions.
                pass
            finally:
                queue.task_done()

    tasks = [asyncio.create_task(worker()) for _ in range(min(MAX_CONCURRENCY, queue.qsize()))]
    try:
        async with asyncio.timeout(SCAN_TIMEOUT):
            await asyncio.gather(*tasks)
    except TimeoutError:
        result.limited = True
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
    result.devices.sort(key=lambda d: IPv4Address(d.host))
    return result
