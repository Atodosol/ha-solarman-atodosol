"""Placa domótica Atodosol (ATS-DOM): local HTTP API of the board, port 80.

The board is an ESP32 that serves one client at a time from its main loop,
with a 3 s budget per connection and requests of at most 1 KB. Every request
to a board goes through one lock per address and none is sent in parallel.
"""
from __future__ import annotations

import asyncio

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from ..const import DOMAIN
from .api import BoardClient, normalize_host

KIND = "kind"
KIND_BOARD = "ats_dom"

CONF_PIN = "pin"
CONF_SCAN_INTERVAL = "scan_interval"

DEFAULT_HOST = "atodosol.local"
DEFAULT_TITLE = "Placa domótica Atodosol"
DEFAULT_SCAN_INTERVAL = 15
MIN_SCAN_INTERVAL = 10
MAX_SCAN_INTERVAL = 300
PENDING_SCAN_INTERVAL = 5

_LOCKS = f"{DOMAIN}_board_locks"


def is_board(entry: ConfigEntry) -> bool:
    return entry.data.get(KIND) == KIND_BOARD


def board_title(board_id: str) -> str:
    """Title with the end of the MAC, to tell two boards of the same home apart."""
    return f"{DEFAULT_TITLE} {board_id[-4:]}"


def board_client(hass: HomeAssistant, host: str) -> BoardClient:
    """Client for a board address; flows and the coordinator share its lock."""
    host = normalize_host(host)
    locks: dict[str, asyncio.Lock] = hass.data.setdefault(_LOCKS, {})
    return BoardClient(async_get_clientsession(hass), host, locks.setdefault(host, asyncio.Lock()))
