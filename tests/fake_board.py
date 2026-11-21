"""Atodosol board simulated on localhost, with the rules of firmware local_web.h/local_portal.h."""
import asyncio
import json
from copy import deepcopy
from pathlib import Path
from urllib.parse import parse_qsl

from aiohttp import web

# Real GET /api/state of the lab board (fw 0.5.193, not configured), with the
# MAC, IP, Wi-Fi name and inverter addresses replaced.
LAB = json.loads((Path(__file__).parent / 'fixtures' / 'board_state_lab.json').read_text())
BOARD_ID = LAB['id']
FORM = 'application/x-www-form-urlencoded'


def writable_state(**changes):
    """The lab board once configured from Miledy, with a car charging."""
    state = deepcopy(LAB)
    state.update(configured=True, writable=True, notice='', contracted_w=4600, soc_protect=True, minimum=30,
                 solar=False, solar_ok=True, soc=64, soc_age_s=3, load_w=812, grid_w=-1250)
    state['charger'].update(power_w=3680, charge_state=2, limit_a=16, age_s=2, reason='active')
    state.update(changes)
    return state


def parse_kw(text):
    """Firmware parse_kw: up to 3 integer digits and 3 decimals, '.' or ','."""
    whole, _, frac = text.strip().replace(',', '.').partition('.')
    if not whole.isdigit() or len(whole) > 3 or (frac and (not frac.isdigit() or len(frac) > 3)) or text.strip().endswith(('.', ',')):
        return None
    watts = int(whole) * 1000 + (int(frac.ljust(3, '0')) if frac else 0)
    return watts if 500 <= watts <= 100000 else None


class FakeBoard:
    def __init__(self, state=None, pin=''):
        self.state = deepcopy(state if state is not None else writable_state())
        self.pin = pin
        self.failures = 0
        self.locked = False
        self.requests = []      # (method, path, headers, body)
        self.posts = []         # bodies of POST /api/settings, as sent
        self.active = 0
        self.max_active = 0
        self.delay = 0
        self.drop = 0           # next N requests: connection closed without an answer
        self.slow = 0           # next N requests: answered after 0.5 s
        self.reply = None       # (status, payload) for the next POST
        self.host = ''
        self.port = 0
        self.allowed = None     # Host headers accepted (default: its own address)

    async def start(self):
        app = web.Application()
        app.router.add_route('*', '/api/state', self._wrap(self._state))
        app.router.add_route('*', '/api/settings', self._wrap(self._settings))
        app.router.add_route('*', '/api/pin', self._wrap(self._pin))
        self._runner = web.AppRunner(app, access_log=None, handle_signals=False)
        await self._runner.setup()
        site = web.TCPSite(self._runner, '127.0.0.1', 0)
        await site.start()
        self.port = self._runner.addresses[0][1]
        self.host = f'127.0.0.1:{self.port}'
        return self

    async def stop(self):
        await self._runner.cleanup()

    @property
    def gets(self):
        return sum(1 for r in self.requests if r[0] == 'GET')

    @staticmethod
    def _json(status, payload):
        return web.json_response(payload, status=status, headers={'Connection': 'close'})

    def _wrap(self, handler):
        async def wrapped(request):
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            try:
                body = await request.read()
                self.requests.append((request.method, request.path, dict(request.headers), body))
                if self.delay:
                    await asyncio.sleep(self.delay)
                if self.slow:
                    self.slow -= 1
                    await asyncio.sleep(0.5)
                if self.drop:
                    self.drop -= 1
                    request.transport.close()
                    return web.Response()
                if len(body) > 256:
                    return self._json(413, {'error': 'Petición demasiado grande'})
                if request.headers.get('Host') not in (self.allowed or {self.host}):
                    return self._json(403, {'error': 'Nombre de host no permitido'})
                return await handler(request, body)
            finally:
                self.active -= 1
        return wrapped

    async def _state(self, request, body):
        if request.method != 'GET':
            return self._json(405, {'error': 'Método no permitido'})
        return self._json(200, self.state)

    async def _pin(self, request, body):
        return self._json(500, {'error': 'Home Assistant must never call /api/pin'})

    async def _settings(self, request, body):
        if request.method != 'POST':
            return self._json(405, {'error': 'Método no permitido'})
        origin = request.headers.get('Origin')
        if origin and origin != f'http://{self.host}':
            return self._json(403, {'error': 'Origen no permitido'})
        if request.headers.get('Content-Type', '').split(';')[0] != FORM:
            return self._json(415, {'error': 'Formulario no válido'})
        self.posts.append(body.decode())
        if self.reply:
            (status, payload), self.reply = self.reply, None
            return self._json(status, payload)
        form = {}
        for key, value in parse_qsl(body.decode(), keep_blank_values=True):
            form.setdefault(key, value)
        if self.pin:
            if self.locked:
                return self._json(423, {'error': 'Demasiados PIN incorrectos; espera 5 min', 'attempts_left': 0, 'locked_s': 300})
            if form.get('pin') != self.pin:
                self.failures += 1
                if self.failures >= 5:
                    self.locked = True
                    return self._json(423, {'error': 'Demasiados PIN incorrectos; espera 5 min', 'attempts_left': 0, 'locked_s': 300})
                return self._json(403, {'error': 'PIN incorrecto', 'attempts_left': 5 - self.failures, 'locked_s': 0})
            self.failures = 0
        contracted = None
        if form.get('contracted_kw'):
            if (contracted := parse_kw(form['contracted_kw'])) is None:
                return self._json(400, {'error': 'Potencia contratada fuera de rango (0,5 a 100 kW)'})
        if 'mode' in form and form['mode'] not in ('max', 'solar'):
            return self._json(400, {'error': 'Modo de carga no válido'})
        if form.get('soc_protect') not in ('0', '1'):
            return self._json(400, {'error': 'Indica si se detiene la carga por SOC mínimo'})
        minimum = 0
        if form['soc_protect'] == '1':
            if not form.get('minimum', '').isdigit() or not 1 <= int(form['minimum']) <= 95:
                return self._json(400, {'error': 'SOC mínimo fuera de rango (1 a 95 %)'})
            minimum = int(form['minimum'])
        if not self.state['writable']:
            return self._json(409, {'error': self.state['notice'] or 'La regulación aún no está configurada desde Miledy'})
        if self.state['offgrid'] and contracted is not None:
            return self._json(400, {'error': 'Instalación aislada: la potencia contratada no aplica'})
        solar = form['mode'] == 'solar' if 'mode' in form else self.state['solar']
        if solar and not self.state['solar_ok']:
            return self._json(400, {'error': 'El inversor no informa de la potencia de la batería; «Solo solar» no está disponible'})
        # The V2C worker saves it after confirming with the charger: here, at once and still pending.
        if contracted is not None:
            self.state['contracted_w'] = contracted
        self.state.update(solar=solar, soc_protect=minimum > 0, minimum=minimum)
        if self.state.get('charger'):
            self.state['charger']['pending'] = True
        return self._json(202, {'ok': True, 'pending': True, 'message': 'Cambio enviado; la placa lo está comprobando'})
