import os

import pytest

os.environ['CRON_TOKEN'] = 'test-cron-token'

from api.routes import create_app


@pytest.fixture
def client():
    estado = {'iniciado': '2026-01-01T00:00:00', 'detectores': {}, 'ultima_actividad_cron': None}
    app = create_app(estado, {})
    app.config['TESTING'] = True
    with app.test_client() as c:
        yield c


def test_value_investing_page_render(client):
    r = client.get('/value-investing')
    assert r.status_code == 200
    assert 'El Ladrón de los Mercados' in r.data.decode('utf-8')


def test_value_investing_empresa_page_render(client):
    r = client.get('/value-investing/empresa/NVDA')
    assert r.status_code == 200
    html = r.data.decode('utf-8')
    assert "'empresa'" in html
    assert 'NVDA' in html


def test_vi_api_calendario(client, mocker):
    mocker.patch('api.value_investing_routes.get_calendario_semanal', return_value=[
        {'ticker': 'NVDA', 'semana_label': 'Esta semana', 'categoria': 'Microchips Occidentales', 'categoria_emoji': '💡', 'categoria_peso': 5},
        {'ticker': 'MSFT', 'semana_label': 'Próxima semana', 'categoria': 'Software Industrial', 'categoria_emoji': '⚙️', 'categoria_peso': 5},
    ])

    r = client.get('/api/v1/vi/calendario')
    assert r.status_code == 200
    data = r.get_json()
    assert data['total_empresas'] == 2
    assert data['stats']['esta_semana'] == 1
    assert data['stats']['proxima_semana'] == 1
    assert data['semanas'][0]['label'] == 'Esta semana'


def test_vi_api_empresa(client, mocker):
    mocker.patch('api.value_investing_routes.get_earnings_info', return_value={'ticker': 'NVDA', 'error': None})
    r = client.get('/api/v1/vi/empresa/nvda')
    assert r.status_code == 200
    assert r.get_json()['ticker'] == 'NVDA'


def test_vi_api_refresh(client, mocker):
    inv = mocker.patch('api.value_investing_routes.invalidar_cache')
    get_info = mocker.patch('api.value_investing_routes.get_earnings_info', return_value={'ticker': 'MSFT'})

    r = client.post('/api/v1/vi/refresh/msft')
    assert r.status_code == 200
    inv.assert_called_once_with('MSFT')
    get_info.assert_called_once_with('MSFT')


def test_vi_api_watchlist(client):
    r = client.get('/api/v1/vi/watchlist')
    assert r.status_code == 200
    data = r.get_json()
    assert data['total_categorias'] == 8
    assert len(data['categorias']) == 8
