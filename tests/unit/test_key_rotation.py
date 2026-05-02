"""
tests/unit/test_key_rotation.py — Tests de rotación de API keys de Twelve Data

Cubre:
  - _next_td_key devuelve (None, None) cuando todas las keys están en cooldown
  - _next_td_key NO devuelve key1 si está en cooldown (bug original)
  - _get_twelve_data pone cooldown cuando 'values' viene vacío
  - El loop de get_ohlcv se detiene (no itera key1 infinitamente) cuando todas fallan
  - La garantía de cooldown en el loop actúa cuando _get_twelve_data no pone cooldown
"""
import itertools
import time
import threading
from unittest.mock import patch, MagicMock
import pytest
import pandas as pd


# ── Helpers para parchear el estado interno del módulo ─────────────────────

def _reset_module_state(dp):
    """Limpia cooldowns y contadores del módulo data_provider."""
    with dp._cooldown_lock:
        dp._key_cooldown.clear()
    with dp._minute_lock:
        dp._key_minute_window.clear()


def _set_cooldown(dp, alias: str, seconds: int = 60):
    """Pone una key en cooldown desde el exterior."""
    with dp._cooldown_lock:
        dp._key_cooldown[alias] = time.time() + seconds


# ── Fixture: módulo con keys de prueba ──────────────────────────────────────

@pytest.fixture()
def dp():
    """Importa data_provider y lo deja con un estado limpio."""
    import adapters.data_provider as _dp
    _reset_module_state(_dp)
    # Asegurarse de que el ciclo interno está en sync con _td_keys
    if _dp._td_keys:
        _dp._td_cycle = itertools.cycle(_dp._td_keys)
    return _dp


@pytest.fixture()
def dp_fake_keys(dp):
    """
    Reemplaza temporalmente _td_keys con 3 keys ficticias (key1, key2, key3)
    para que los tests sean independientes de las keys reales del .env.
    """
    fake_keys = [('key1', 'FAKE_K1'), ('key2', 'FAKE_K2'), ('key3', 'FAKE_K3')]
    original_keys  = dp._td_keys
    original_cycle = dp._td_cycle
    original_lock  = dp._td_cycle_lock

    dp._td_keys      = fake_keys
    dp._td_cycle     = itertools.cycle(fake_keys)
    dp._td_cycle_lock = threading.Lock()
    _reset_module_state(dp)

    yield dp

    # Restaurar estado original
    dp._td_keys      = original_keys
    dp._td_cycle     = original_cycle
    dp._td_cycle_lock = original_lock
    _reset_module_state(dp)


# ════════════════════════════════════════════════════════════════════════════
# 1. _next_td_key — comportamiento básico
# ════════════════════════════════════════════════════════════════════════════

class TestNextTdKey:

    def test_devuelve_key1_si_no_hay_cooldown(self, dp_fake_keys):
        alias, key = dp_fake_keys._next_td_key()
        assert alias == 'key1'
        assert key   == 'FAKE_K1'

    def test_devuelve_none_cuando_todas_en_cooldown(self, dp_fake_keys):
        """BUG ORIGINAL: antes devolvía key1 aunque estuviera en cooldown."""
        dp = dp_fake_keys
        _set_cooldown(dp, 'key1', 60)
        _set_cooldown(dp, 'key2', 60)
        _set_cooldown(dp, 'key3', 60)

        alias, key = dp._next_td_key()
        assert alias is None, (
            f"Se esperaba None pero se recibió '{alias}' — "
            "el bug de bucle infinito en key1 sigue presente"
        )
        assert key is None

    def test_NO_devuelve_key1_si_solo_key1_en_cooldown(self, dp_fake_keys):
        """Cuando key1 está en cooldown debe rotarse a key2/key3."""
        dp = dp_fake_keys
        _set_cooldown(dp, 'key1', 60)

        alias, key = dp._next_td_key()
        assert alias in ('key2', 'key3'), (
            f"key1 estaba en cooldown pero se devolvió '{alias}'"
        )

    def test_devuelve_key2_o_key3_cuando_key1_en_cooldown(self, dp_fake_keys):
        dp = dp_fake_keys
        _set_cooldown(dp, 'key1', 60)

        vistos = set()
        for _ in range(10):
            alias, _ = dp._next_td_key()
            if alias:
                vistos.add(alias)

        assert 'key1' not in vistos, "key1 apareció siendo que estaba en cooldown"
        assert vistos.issubset({'key2', 'key3'})

    def test_devuelve_key1_cuando_expira_cooldown(self, dp_fake_keys):
        dp = dp_fake_keys
        # Cooldown muy corto (ya expirado en cuanto lo ponemos)
        with dp._cooldown_lock:
            dp._key_cooldown['key1'] = time.time() - 1  # expirado

        alias, key = dp._next_td_key()
        assert alias == 'key1'

    def test_sin_keys_configuradas_devuelve_none(self, dp):
        original = dp._td_keys
        dp._td_keys = []
        try:
            alias, key = dp._next_td_key()
            assert alias is None
            assert key   is None
        finally:
            dp._td_keys = original


# ════════════════════════════════════════════════════════════════════════════
# 2. _get_twelve_data — cooldown en values vacío
# ════════════════════════════════════════════════════════════════════════════

class TestGetTwelveDataCooldown:

    def _mock_response(self, payload: dict, status_code: int = 200):
        resp = MagicMock()
        resp.status_code = status_code
        resp.json.return_value = payload
        resp.text = str(payload)
        return resp

    def test_pone_cooldown_cuando_values_vacio(self, dp_fake_keys):
        dp = dp_fake_keys
        payload = {'status': 'ok', 'values': [], 'meta': {}}

        with patch('requests.get', return_value=self._mock_response(payload)):
            _, ok = dp._get_twelve_data('GC=F', '1d', '1d', 'FAKE_K1', alias='key1')

        assert ok is False
        assert dp._is_on_cooldown('key1'), (
            "Debe poner cooldown cuando 'values' viene vacío"
        )

    def test_pone_cooldown_en_error_limite_minuto(self, dp_fake_keys):
        dp = dp_fake_keys
        payload = {'status': 'error', 'message': 'You have exceeded the API limit per current minute'}

        with patch('requests.get', return_value=self._mock_response(payload)):
            _, ok = dp._get_twelve_data('GC=F', '1d', '1d', 'FAKE_K1', alias='key1')

        assert ok is False
        assert dp._is_on_cooldown('key1')
        # El cooldown del límite de minuto debe ser ≥ 60s
        remaining = dp._key_cooldown.get('key1', 0) - time.time()
        assert remaining >= 60, f"Cooldown demasiado corto: {remaining:.1f}s"

    def test_pone_cooldown_en_error_limite_diario(self, dp_fake_keys):
        dp = dp_fake_keys
        payload = {'status': 'error', 'message': 'You have exceeded the daily API limit'}

        with patch('requests.get', return_value=self._mock_response(payload)):
            _, ok = dp._get_twelve_data('GC=F', '1d', '1d', 'FAKE_K1', alias='key1')

        assert ok is False
        remaining = dp._key_cooldown.get('key1', 0) - time.time()
        assert remaining >= 3500, f"Cooldown diario demasiado corto: {remaining:.0f}s"

    def test_pone_cooldown_en_http_error(self, dp_fake_keys):
        dp = dp_fake_keys
        with patch('requests.get', return_value=self._mock_response({}, status_code=429)):
            _, ok = dp._get_twelve_data('GC=F', '1d', '1d', 'FAKE_K1', alias='key1')

        assert ok is False
        assert dp._is_on_cooldown('key1')

    def test_no_pone_cooldown_en_exito(self, dp_fake_keys):
        dp = dp_fake_keys
        values = [
            {'datetime': f'2026-03-{d:02d} 00:00:00', 'open': '3000', 'high': '3010',
             'low': '2990', 'close': '3005', 'volume': '100'}
            for d in range(1, 32)  # marzo tiene 31 días
        ]
        payload = {'status': 'ok', 'values': values}

        with patch('requests.get', return_value=self._mock_response(payload)):
            df, ok = dp._get_twelve_data('GC=F', '1mo', '1d', 'FAKE_K1', alias='key1')

        assert ok is True
        assert not df.empty
        assert not dp._is_on_cooldown('key1')


# ════════════════════════════════════════════════════════════════════════════
# 3. Loop de get_ohlcv — no cicla infinitamente sobre key1
# ════════════════════════════════════════════════════════════════════════════

class TestGetOhlcvLoop:

    def _empty_response(self):
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {'status': 'ok', 'values': []}
        resp.text = '{}'
        return resp

    def test_no_llama_api_mas_veces_que_keys_disponibles(self, dp_fake_keys):
        """El loop debe terminar en ≤ N iteraciones (N = número de keys)."""
        dp = dp_fake_keys
        n_keys = len(dp._td_keys)  # 3

        call_count = 0
        original_get = dp._get_twelve_data

        def counting_get(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            return pd.DataFrame(), False

        with patch.object(dp, '_get_twelve_data', side_effect=counting_get), \
             patch.object(dp, 'DIRECT_FETCH_MODE', True), \
             patch.object(dp, '_reserve_minute_slot', return_value=True):
            dp.get_ohlcv('GC=F', '1d', '1d')

        assert call_count <= n_keys, (
            f"El loop llamó a la API {call_count} veces con solo {n_keys} keys — "
            "sigue ciclando sobre key1"
        )

    def test_loop_se_detiene_cuando_todas_en_cooldown(self, dp_fake_keys):
        """Con todas las keys en cooldown, el loop debe detenerse sin llamar a la API."""
        dp = dp_fake_keys
        _set_cooldown(dp, 'key1', 60)
        _set_cooldown(dp, 'key2', 60)
        _set_cooldown(dp, 'key3', 60)

        api_llamada = False

        def never_called(*args, **kwargs):
            nonlocal api_llamada
            api_llamada = True
            return pd.DataFrame(), False

        with patch.object(dp, '_get_twelve_data', side_effect=never_called), \
             patch.object(dp, 'DIRECT_FETCH_MODE', True), \
             patch.object(dp, '_reserve_minute_slot', return_value=True):
            result_df, _ = dp.get_ohlcv('GC=F', '1d', '1d')

        assert not api_llamada, "La API fue llamada aunque todas las keys estaban en cooldown"
        assert result_df.empty

    def test_key1_en_cooldown_usa_key2(self, dp_fake_keys):
        """Con key1 en cooldown, el loop debe usar key2 o key3."""
        dp = dp_fake_keys
        _set_cooldown(dp, 'key1', 60)

        keys_usadas = []
        values = [
            {'datetime': f'2026-03-{d:02d} 00:00:00', 'open': '3000', 'high': '3010',
             'low': '2990', 'close': '3005', 'volume': '0'}
            for d in range(1, 32)  # marzo tiene 31 días
        ]

        def fake_get(ticker, period, interval, api_key, alias=''):
            keys_usadas.append(alias)
            if alias == 'key1':
                return pd.DataFrame(), False
            resp_payload = {'status': 'ok', 'values': values}
            df = pd.DataFrame(values)
            df['datetime'] = pd.to_datetime(df['datetime'], utc=True)
            df = df.set_index('datetime')
            df = df.rename(columns={'open': 'Open', 'high': 'High',
                                    'low': 'Low', 'close': 'Close', 'volume': 'Volume'})
            df = df.astype(float)
            return df, True

        with patch.object(dp, '_get_twelve_data', side_effect=fake_get), \
             patch.object(dp, 'DIRECT_FETCH_MODE', True), \
             patch.object(dp, '_reserve_minute_slot', return_value=True), \
             patch.object(dp, '_registrar_uso_key'):
            dp.get_ohlcv('GC=F', '1d', '1d')

        assert 'key1' not in keys_usadas, (
            f"Se usó key1 estando en cooldown. Keys usadas: {keys_usadas}"
        )
        assert any(k in ('key2', 'key3') for k in keys_usadas)
