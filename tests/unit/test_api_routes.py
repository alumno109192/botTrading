"""
tests/unit/test_api_routes.py — Tests unitarios para api/routes.py

Cubre:
  - GET /         → home, devuelve estado_sistema
  - GET /health   → 200 {"status": "healthy"}
  - GET /status   → 401 sin token | 200 con token correcto
  - GET /cron     → 401 sin token | estado sin threads | estado con threads
"""
import pytest
import os
import json

# Asegurar que CRON_TOKEN esté disponible antes de importar
os.environ['CRON_TOKEN'] = 'test-cron-token'

from api.routes import create_app


# ── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture
def estado_base():
    return {
        'iniciado': '2026-01-01T00:00:00',
        'ultima_actividad_cron': None,
        'detectores': {},
    }


@pytest.fixture
def threads_vacios():
    return {}


@pytest.fixture
def threads_activos():
    """Simula un dict de threads con objetos mock cuyo is_alive() devuelve True."""
    from unittest.mock import MagicMock
    thread_vivo = MagicMock()
    thread_vivo.is_alive.return_value = True
    thread_muerto = MagicMock()
    thread_muerto.is_alive.return_value = False
    return {'gold_4h': thread_vivo, 'gold_1h': thread_muerto}


@pytest.fixture
def client(estado_base, threads_vacios):
    app = create_app(estado_base, threads_vacios)
    app.config['TESTING'] = True
    with app.test_client() as c:
        yield c


@pytest.fixture
def client_con_threads(estado_base, threads_activos):
    """Client con threads mixtos (un vivo, uno muerto).

    Moquea reiniciar_detector para que los tests no intenten arrancar threads reales.
    """
    from unittest.mock import patch
    app = create_app(estado_base, threads_activos)
    app.config['TESTING'] = True
    with patch('services.orchestrator.reiniciar_detector'):
        with app.test_client() as c:
            yield c


CRON_TOKEN = 'test-cron-token'


# ── GET / ─────────────────────────────────────────────────────────────────────

class TestHomeEndpoint:
    def test_devuelve_200(self, client):
        r = client.get('/')
        assert r.status_code == 200

    def test_contiene_status_online(self, client):
        data = r = client.get('/')
        body = json.loads(data.data)
        assert body['status'] == 'online'

    def test_contiene_iniciado(self, client):
        body = json.loads(client.get('/').data)
        assert 'iniciado' in body

    def test_contiene_detectores(self, client):
        body = json.loads(client.get('/').data)
        assert 'detectores' in body


# ── GET /health ───────────────────────────────────────────────────────────────

class TestHealthEndpoint:
    def test_devuelve_200(self, client):
        r = client.get('/health')
        assert r.status_code == 200

    def test_status_healthy(self, client):
        body = json.loads(client.get('/health').data)
        assert body['status'] == 'healthy'


# ── GET /status ───────────────────────────────────────────────────────────────

class TestStatusEndpoint:
    def test_sin_token_devuelve_401(self, client):
        r = client.get('/status')
        assert r.status_code == 401

    def test_token_incorrecto_devuelve_401(self, client):
        r = client.get('/status', headers={'X-Cron-Token': 'wrong-token'})
        assert r.status_code == 401

    def test_token_correcto_devuelve_200(self, client):
        r = client.get('/status', headers={'X-Cron-Token': CRON_TOKEN})
        assert r.status_code == 200

    def test_token_correcto_devuelve_estado(self, client):
        r = client.get('/status', headers={'X-Cron-Token': CRON_TOKEN})
        body = json.loads(r.data)
        assert 'iniciado' in body


# ── GET /cron ─────────────────────────────────────────────────────────────────

class TestCronEndpoint:
    def test_sin_token_devuelve_401(self, client):
        r = client.get('/cron')
        assert r.status_code == 401

    def test_token_incorrecto_devuelve_401(self, client):
        r = client.get('/cron', headers={'X-Cron-Token': 'wrong-token'})
        assert r.status_code == 401

    def test_sin_threads_devuelve_alive_sin_detectores(self, client):
        """Sin threads registrados el status debe ser 'alive_sin_detectores'."""
        r = client.get('/cron', headers={'X-Cron-Token': CRON_TOKEN})
        assert r.status_code == 200
        body = json.loads(r.data)
        assert body['status'] == 'alive_sin_detectores'

    def test_con_threads_devuelve_alive(self, client_con_threads):
        r = client_con_threads.get('/cron', headers={'X-Cron-Token': CRON_TOKEN})
        assert r.status_code == 200
        body = json.loads(r.data)
        assert body['status'] == 'alive'

    def test_con_threads_informa_count(self, client_con_threads):
        r = client_con_threads.get('/cron', headers={'X-Cron-Token': CRON_TOKEN})
        body = json.loads(r.data)
        assert body['threads_totales'] == 2

    def test_alerta_cuando_hay_threads_muertos(self, client_con_threads):
        r = client_con_threads.get('/cron', headers={'X-Cron-Token': CRON_TOKEN})
        body = json.loads(r.data)
        assert body['alerta'] is not None

    def test_sin_alerta_cuando_todos_viven(self, estado_base):
        """Todos los threads vivos → alerta=None."""
        from unittest.mock import MagicMock
        threads = {}
        for name in ['a', 'b']:
            t = MagicMock()
            t.is_alive.return_value = True
            threads[name] = t

        app = create_app(estado_base, threads)
        app.config['TESTING'] = True
        with app.test_client() as c:
            r = c.get('/cron', headers={'X-Cron-Token': CRON_TOKEN})
            body = json.loads(r.data)
        assert body['alerta'] is None

    def test_cron_actualiza_ultima_actividad(self, estado_base):
        """Después de llamar /cron, ultima_actividad_cron no debe ser None."""
        from unittest.mock import MagicMock
        t = MagicMock()
        t.is_alive.return_value = True
        threads = {'gold_4h': t}

        app = create_app(estado_base, threads)
        app.config['TESTING'] = True
        with app.test_client() as c:
            c.get('/cron', headers={'X-Cron-Token': CRON_TOKEN})
        assert estado_base['ultima_actividad_cron'] is not None

    def test_cron_sin_token_configurado_permite_acceso(self, estado_base):
        """Cuando CRON_TOKEN no está configurado, /cron debe ser accesible sin token."""
        orig = os.environ.pop('CRON_TOKEN', None)
        try:
            app_libre = create_app(estado_base, {})
            app_libre.config['TESTING'] = True
            with app_libre.test_client() as c:
                r = c.get('/cron')
                assert r.status_code == 200
        finally:
            if orig is not None:
                os.environ['CRON_TOKEN'] = orig

    def test_threads_muertos_se_reinician_automaticamente(self, estado_base):
        """Cuando /cron detecta un thread muerto, intenta reiniciarlo."""
        from unittest.mock import MagicMock, patch
        thread_muerto = MagicMock()
        thread_muerto.is_alive.return_value = False
        threads = {'gold_4h': thread_muerto}

        app = create_app(estado_base, threads)
        app.config['TESTING'] = True

        with patch('services.orchestrator.reiniciar_detector') as mock_restart:
            with app.test_client() as c:
                r = c.get('/cron', headers={'X-Cron-Token': CRON_TOKEN})
                assert r.status_code == 200
            mock_restart.assert_called_once_with('gold_4h', estado_base, threads)
