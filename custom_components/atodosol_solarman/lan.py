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
# Finding port 502 is quick; identifying a Wi-Fi dongle is not. A DYDA on weak
# Wi-Fi may need ARP, a retransmitted SYN and a slow first Modbus answer: the
# single 1.5 s budget missed real inverters that manual entry (5 s) found.
PORT_TIMEOUT = 1.5
IDENTIFY_TIMEOUT = 6
IDENTIFY_ATTEMPTS = 2
IDENTIFY_CONCURRENCY = 4
RETRY_DELAY = 0.5
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
    # Hosts with port 502 open that did not identify as a Deye inverter, with the reason.
    unidentified: list[tuple[str, str]] = field(default_factory=list)


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
        # Some firmwares pad short serial numbers with trailing NUL or spaces.
        serial = b''.join(v.to_bytes(2, 'big') for v in registers[3:8]).rstrip(b'\x00 ').decode('ascii')
        if not 6 <= len(serial) <= 10 or not serial.isalnum() or set(serial) <= {'0'}:
            raise ValueError('Invalid inverter serial number')
        return Inverter(host, serial, registers[0], port, slave)
    finally:
        await client.close()


async def port_open(host: str, port: int = 502, timeout: float = PORT_TIMEOUT) -> bool:
    """TCP connect only: nothing is sent to hosts that are not Modbus servers."""
    try:
        async with asyncio.timeout(timeout):
            _, writer = await asyncio.open_connection(host, port)
    except (OSError, TimeoutError):
        return False
    writer.close()
    try:
        async with asyncio.timeout(1):
            await writer.wait_closed()
    except (OSError, TimeoutError):
        pass
    return True


REASONS = {
    'en': {'timeout': 'no Modbus answer in time', 'not_deye': 'answers Modbus but is not a supported Deye inverter',
           'closed': 'closed the connection while answering', 'error': 'Modbus error'},
    'es': {'timeout': 'no responde a Modbus a tiempo', 'not_deye': 'responde a Modbus pero no es un inversor Deye compatible',
           'closed': 'cerró la conexión al responder', 'error': 'error Modbus'},
}


def _reason(error: BaseException) -> str:
    if isinstance(error, TimeoutError):
        return 'timeout'
    if isinstance(error, asyncio.IncompleteReadError):
        return 'closed'
    if isinstance(error, (OSError, UnicodeError)):
        return 'error'
    return 'not_deye'  # a Modbus answer (or exception) from another kind of device


def reason_text(code: str, language: str | None) -> str:
    texts = REASONS.get((language or 'en').split('-')[0], REASONS['en'])
    return texts.get(code, code)


async def identify(host: str) -> Inverter:
    """Generous, retried identification for a host that accepted a connection."""
    error: BaseException = TimeoutError()
    for attempt in range(IDENTIFY_ATTEMPTS):
        try:
            return await probe_inverter(host, timeout=IDENTIFY_TIMEOUT)
        except ValueError:
            raise  # a real answer that is not a Deye inverter: do not insist
        except (OSError, TimeoutError, UnicodeError, asyncio.IncompleteReadError) as exc:
            error = exc
            if attempt + 1 < IDENTIFY_ATTEMPTS:
                await asyncio.sleep(RETRY_DELAY)
    raise error


async def _run(queue: asyncio.Queue, workers: int, job) -> None:
    async def worker():
        while not queue.empty():
            item = queue.get_nowait()
            try:
                await job(item)
            except Exception:
                pass
            finally:
                queue.task_done()

    tasks = [asyncio.create_task(worker()) for _ in range(min(workers, queue.qsize()))]
    try:
        await asyncio.gather(*tasks)
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


async def scan_lan(networks: list[str], *, exclude: set[str] | None = None) -> ScanResult:
    """No shell, ARP cache, credentials, port sweep beyond 502, or configuration writes."""
    excluded = exclude or set()
    # Deduplicate before scheduling, cap allocation as well as concurrency.
    hosts = sorted({str(h) for n in networks for h in scan_network(n).hosts() if str(h) not in excluded}, key=IPv4Address)
    result = ScanResult(limited=len(hosts) > MAX_HOSTS)
    open_hosts: list[str] = []

    async def sweep(host):
        if await port_open(host):
            open_hosts.append(host)

    async def check(host):
        try:
            result.devices.append(await identify(host))
        except Exception as exc:
            result.unidentified.append((host, _reason(exc)))

    queue = asyncio.Queue()
    for host in hosts[:MAX_HOSTS]:
        queue.put_nowait(host)
    try:
        async with asyncio.timeout(SCAN_TIMEOUT):
            await _run(queue, MAX_CONCURRENCY, sweep)
            candidates = asyncio.Queue()
            for host in sorted(open_hosts, key=IPv4Address):
                candidates.put_nowait(host)
            await _run(candidates, IDENTIFY_CONCURRENCY, check)
    except TimeoutError:
        result.limited = True
    result.devices.sort(key=lambda d: IPv4Address(d.host))
    result.unidentified.sort(key=lambda item: IPv4Address(item[0]))
    return result
