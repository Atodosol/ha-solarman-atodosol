"""Client of the local API of the Atodosol board (firmware local_web.h).

No Home Assistant imports: parsing and form bodies are tested on their own.

- GET /api/state: public state, never the PIN.
- POST /api/settings, application/x-www-form-urlencoded. `soc_protect` is
  always required and `minimum` too when it is 1, so every change resends the
  saved value of both; `mode` and `contracted_kw` are optional and are sent
  only when they change, so the board keeps what it has.
- POST /api/pin is never used here: the PIN is changed on the board page.

The board accepts its IP or its mDNS name in the Host header and refuses any
Origin other than its own, so no Origin header is sent.
"""
from __future__ import annotations

import asyncio
import json
import re
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlencode

import aiohttp

BOARD_ID_PREFIX = "atsdom_"

REQUEST_TIMEOUT = 5.0   # the board closes a connection after 3 s
RETRY_DELAY = 2.0       # one gentle retry for reads only
MIN_GAP = 0.5           # pause between two requests to the same board

FORM = "application/x-www-form-urlencoded"

MODE_MAX = "max"
MODE_SOLAR = "solar"
MODES = (MODE_MAX, MODE_SOLAR)

SOC_MIN, SOC_MAX = 1, 95
DEFAULT_MINIMUM = 20    # what the board page proposes when there is none
CONTRACTED_MIN_W, CONTRACTED_MAX_W = 500, 100000
AGE_NEVER = 999         # age_s when the charger never answered

_HOST = re.compile(r"^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]*[a-z0-9])?)*(?::[0-9]{1,5})?$")


class BoardError(Exception):
    """Base error talking to the board."""


class BoardConnectionError(BoardError):
    """The board did not answer; nothing was sent or applied."""


class BoardHostRejected(BoardError):
    """403 to a read: the board does not accept this name in the Host header."""


class BoardInvalidResponse(BoardError):
    """The answer is not the state of an Atodosol board."""


class BoardWriteUnknown(BoardError):
    """The change was sent but no answer came: it may have been applied."""


class BoardRejected(BoardError):
    """The board answered a change with an error."""

    def __init__(self, status: int, message: str, payload: dict[str, Any]):
        super().__init__(message)
        self.status = status
        self.message = message
        self.payload = payload

    @property
    def pin_denied(self) -> bool:
        """Wrong PIN (403) or locked after too many (423): only then the board counts attempts."""
        return "attempts_left" in self.payload

    @property
    def attempts_left(self) -> int | None:
        return _int(self.payload.get("attempts_left"), None)

    @property
    def locked_s(self) -> int:
        return _int(self.payload.get("locked_s"), 0) or 0


def normalize_host(value: str) -> str:
    """`http://Atodosol.local/` -> `atodosol.local`; `atodosol-2.local.` -> `atodosol-2.local`."""
    host = (value or "").strip().lower()
    for prefix in ("http://", "https://"):
        if host.startswith(prefix):
            host = host[len(prefix):]
    host = host.split("/", 1)[0]
    if host.endswith(":80"):
        host = host[:-3]
    return host.rstrip(".")


def valid_host(host: str) -> bool:
    return bool(_HOST.match(host))


def _bool(value: Any) -> bool:
    return value is True


def _int(value: Any, default: int | None = 0) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    return int(value)


def _num(value: Any) -> int | float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value


def _str(value: Any) -> str:
    return value if isinstance(value, str) else ""


@dataclass(frozen=True)
class ChargerState:
    """`charger`: the charger regulated by the board."""

    id: str
    power_w: int | float | None
    charge_state: int | None   # None: no data (-1)
    locked: bool
    limit_a: int
    age_s: int | None          # None: never answered
    reason: str
    message: str
    pending: bool

    @classmethod
    def parse(cls, data: dict[str, Any]) -> ChargerState:
        state = _int(data.get("charge_state"), -1)
        age = _int(data.get("age_s"), None)
        return cls(
            id=_str(data.get("id")),
            power_w=_num(data.get("power_w")),
            charge_state=None if state is None or state < 0 else state,
            locked=_bool(data.get("locked")),
            limit_a=_int(data.get("limit_a")) or 0,
            age_s=None if age is None or age == AGE_NEVER else age,
            reason=_str(data.get("reason")),
            message=_str(data.get("message")),
            pending=_bool(data.get("pending")),
        )


@dataclass(frozen=True)
class BoardState:
    """GET /api/state."""

    id: str
    fw: str
    host: str
    ip: str
    configured: bool
    writable: bool
    notice: str
    pin_set: bool
    pin_ok: bool
    offgrid: bool
    contracted_w: int
    ceiling_a: int
    soc_protect: bool
    minimum: int
    solar: bool
    solar_ok: bool
    solar_floor: int
    mode: str | None            # `mode` as reported, when the firmware sends it
    mode_reported: bool
    soc: int | float | None
    soc_age_s: int
    load_w: int | float | None
    grid_w: int | float | None
    locked_s: int
    charger: ChargerState | None
    chargers: tuple[dict[str, Any], ...] | None = None   # None: firmware without `chargers`
    raw: dict[str, Any] = field(default_factory=dict, compare=False, repr=False)

    @classmethod
    def parse(cls, payload: Any) -> BoardState:
        if not isinstance(payload, dict):
            raise BoardInvalidResponse("The answer is not a JSON object")
        board_id = _str(payload.get("id"))
        if not board_id.startswith(BOARD_ID_PREFIX) or len(board_id) <= len(BOARD_ID_PREFIX):
            raise BoardInvalidResponse("The answer is not from an Atodosol board")
        charger = payload.get("charger")
        chargers = payload.get("chargers")
        offgrid = _bool(payload.get("offgrid"))
        return cls(
            id=board_id,
            fw=_str(payload.get("fw")),
            host=_str(payload.get("host")),
            ip=_str(payload.get("ip")),
            configured=_bool(payload.get("configured")),
            writable=_bool(payload.get("writable")),
            notice=_str(payload.get("notice")),
            pin_set=_bool(payload.get("pin_set")),
            pin_ok=payload.get("pin_ok") is not False,
            offgrid=offgrid,
            contracted_w=0 if offgrid else _int(payload.get("contracted_w")) or 0,
            ceiling_a=_int(payload.get("ceiling_a")) or 0,
            soc_protect=_bool(payload.get("soc_protect")),
            minimum=_int(payload.get("minimum")) or 0,
            solar=_bool(payload.get("solar")),
            solar_ok=_bool(payload.get("solar_ok")),
            solar_floor=_int(payload.get("solar_floor")) or 0,
            mode=payload["mode"] if isinstance(payload.get("mode"), str) else None,
            mode_reported="mode" in payload,
            soc=_num(payload.get("soc")),
            soc_age_s=_int(payload.get("soc_age_s")) or 0,
            load_w=_num(payload.get("load_w")),
            grid_w=None if offgrid else _num(payload.get("grid_w")),
            locked_s=_int(payload.get("locked_s")) or 0,
            charger=ChargerState.parse(charger) if isinstance(charger, dict) else None,
            chargers=tuple(c for c in chargers if isinstance(c, dict)) if isinstance(chargers, list) else None,
            raw=payload,
        )

    @property
    def mac(self) -> str:
        """`atsdom_0a1b2c3d4e5f` -> `0a:1b:2c:3d:4e:5f` (empty if the id has another form)."""
        hexa = self.id[len(BOARD_ID_PREFIX):]
        if len(hexa) != 12 or any(c not in "0123456789abcdef" for c in hexa):
            return ""
        return ":".join(hexa[i:i + 2] for i in range(0, 12, 2))

    @property
    def charge_mode(self) -> str | None:
        """Charge mode: `mode` when the board reports it, else derived from `solar`.

        A mode this version does not know (a later firmware may add one) is
        None, never «max»: it is shown as unknown and never sent back.
        """
        if self.mode_reported:
            return self.mode if self.mode in MODES else None
        return MODE_SOLAR if self.solar else MODE_MAX

    @property
    def solar_mode(self) -> bool:
        """«Solo solar» is the effective mode."""
        return self.charge_mode == MODE_SOLAR

    @property
    def max_mode(self) -> bool:
        """«Carga máxima» is the effective mode (not in an unknown mode)."""
        return self.charge_mode == MODE_MAX

    @property
    def solar_selectable(self) -> bool:
        """«Solo solar» can be chosen (or is already on and can be left)."""
        return self.solar_ok or self.solar_mode

    @property
    def alarms(self) -> list[dict[str, Any]]:
        return [c for c in self.chargers or () if c.get("alarm") is True]


def format_kw(kw: float) -> str:
    """Contracted power for `contracted_kw`: integer part and up to 3 decimals, as `parse_kw` reads it."""
    watts = round(float(kw) * 1000)
    if not CONTRACTED_MIN_W <= watts <= CONTRACTED_MAX_W:
        raise ValueError("Contracted power out of range (0.5 to 100 kW)")
    whole, frac = divmod(watts, 1000)
    return str(whole) if not frac else f"{whole}.{frac:03d}".rstrip("0")


def settings_fields(state: BoardState, *, mode: str | None = None, soc_protect: bool | None = None,
                    minimum: int | None = None, contracted_kw: float | None = None, pin: str | None = None,
                    fallback_minimum: int = DEFAULT_MINIMUM) -> list[tuple[str, str]]:
    """Form fields for one change, in the order of the board page.

    Only the given values change; the board keeps the mode and the contracted
    power that are not sent, and `soc_protect`/`minimum` are resent as saved.
    `mode` is sent only when the user changes the mode, so a mode this
    version does not know is never overwritten by another change.
    """
    fields: list[tuple[str, str]] = []
    if state.pin_set and pin:
        fields.append(("pin", pin))
    if contracted_kw is not None:
        if state.offgrid:
            raise ValueError("Off-grid installation: the contracted power does not apply")
        fields.append(("contracted_kw", format_kw(contracted_kw)))
    if mode is not None:
        if mode not in MODES:
            raise ValueError(f"Unknown charge mode {mode!r}")
        fields.append(("mode", mode))
    protect = state.soc_protect if soc_protect is None else soc_protect
    fields.append(("soc_protect", "1" if protect else "0"))
    if protect:
        if minimum is None:
            minimum = state.minimum if SOC_MIN <= state.minimum <= SOC_MAX else fallback_minimum
        if not SOC_MIN <= int(minimum) <= SOC_MAX:
            raise ValueError("Minimum SOC out of range (1 to 95 %)")
        fields.append(("minimum", str(int(minimum))))
    return fields


def _json(raw: bytes) -> Any:
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as err:
        raise BoardInvalidResponse("The answer is not valid JSON") from err


def _error_text(raw: bytes, status: int) -> tuple[str, dict[str, Any]]:
    try:
        payload = json.loads(raw.decode("utf-8")) if raw else {}
    except (UnicodeDecodeError, ValueError):
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    return (_str(payload.get("error")) or f"HTTP {status}"), payload


class BoardClient:
    """One board address. Requests go one after another through `lock`."""

    def __init__(self, session: aiohttp.ClientSession, host: str, lock: asyncio.Lock | None = None):
        self.host = normalize_host(host)
        self._session = session
        self._lock = lock or asyncio.Lock()
        self._last = 0.0

    @property
    def url(self) -> str:
        return f"http://{self.host}/"

    async def _request(self, method: str, path: str, body: bytes | None = None) -> tuple[int, bytes]:
        headers = {"Accept": "application/json"}
        if body is not None:
            headers["Content-Type"] = FORM
        async with self._lock:
            if (wait := self._last + MIN_GAP - time.monotonic()) > 0:
                await asyncio.sleep(wait)
            try:
                async with self._session.request(method, f"http://{self.host}{path}", data=body, headers=headers,
                                                 timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT),
                                                 allow_redirects=False) as response:
                    return response.status, await response.read()
            finally:
                self._last = time.monotonic()

    async def get_state(self) -> BoardState:
        for attempt in range(2):
            try:
                status, raw = await self._request("GET", "/api/state")
                break
            except (TimeoutError, aiohttp.ClientError, OSError) as err:
                # aiohttp itself repeats a GET once when the board drops the connection.
                if attempt or isinstance(err, aiohttp.ServerDisconnectedError):
                    raise BoardConnectionError(f"No answer from {self.host}: {err!r}") from err
                await asyncio.sleep(RETRY_DELAY)
        if status == 403:
            raise BoardHostRejected(_error_text(raw, status)[0])
        if status != 200:
            raise BoardInvalidResponse(f"HTTP {status}: {_error_text(raw, status)[0]}")
        return BoardState.parse(_json(raw))

    async def post_settings(self, fields: list[tuple[str, str]]) -> dict[str, Any]:
        """Send one change. Never retried: the board could apply it twice or count a wrong PIN twice."""
        try:
            status, raw = await self._request("POST", "/api/settings", urlencode(fields).encode())
        except aiohttp.ClientConnectorError as err:
            raise BoardConnectionError(f"No connection to {self.host}: {err!r}") from err
        except (TimeoutError, aiohttp.ClientError, OSError) as err:
            raise BoardWriteUnknown(f"No answer from {self.host}: {err!r}") from err
        message, payload = _error_text(raw, status)
        if status in (200, 202) and payload.get("ok") is True:
            return payload
        raise BoardRejected(status, message, payload)
