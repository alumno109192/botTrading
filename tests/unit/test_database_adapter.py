"""
tests/unit/test_database_adapter.py — Tests unitarios para adapters/database.py

Cubre:
  - DatabaseManager._convert_param: tipos None, bool, int, float, str
  - DatabaseManager.ejecutar_query: respuesta exitosa, respuesta vacía,
    error HTTP, retry en 502/503/429
  - DatabaseManager.ejecutar_insert: retorna el ID insertado
  - Singleton: dos instancias son el mismo objeto
"""
import pytest
from unittest.mock import patch, MagicMock
import json
import os

# Credenciales falsas para que el constructor no lance ValueError
os.environ.setdefault('TURSO_DATABASE_URL', 'libsql://fake-db.turso.io')
os.environ.setdefault('TURSO_AUTH_TOKEN',   'fake-token')


def _get_db():
    """Importa y reinicia el singleton para cada test."""
    import importlib
    import adapters.database as db_module
    # Resetear el singleton entre tests
    db_module.DatabaseManager._instance = None
    return db_module.DatabaseManager()


def _turso_response(rows: list, columns: list):
    """Construye una respuesta HTTP simulada de Turso."""
    col_defs = [{'name': c} for c in columns]
    row_data  = [
        [{'type': 'text', 'value': str(v)} for v in row]
        for row in rows
    ]
    payload = {
        'results': [{
            'response': {
                'result': {
                    'cols': col_defs,
                    'rows': row_data,
                }
            }
        }]
    }
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = payload
    return mock_resp


def _error_response(status_code: int):
    mock_resp = MagicMock()
    mock_resp.status_code = status_code
    mock_resp.text = f'HTTP {status_code} error'
    return mock_resp


# ── _convert_param ────────────────────────────────────────────────────────────

class TestConvertParam:
    def setup_method(self):
        self.db = _get_db()

    def test_none(self):
        assert self.db._convert_param(None) == {'type': 'null'}

    def test_bool_true(self):
        r = self.db._convert_param(True)
        assert r['type'] == 'integer'
        assert r['value'] == '1'

    def test_bool_false(self):
        r = self.db._convert_param(False)
        assert r['type'] == 'integer'
        assert r['value'] == '0'

    def test_int(self):
        r = self.db._convert_param(42)
        assert r['type'] == 'integer'
        assert r['value'] == '42'

    def test_float(self):
        r = self.db._convert_param(3.14)
        assert r['type'] == 'float'
        assert r['value'] == pytest.approx(3.14)

    def test_str(self):
        r = self.db._convert_param('hello')
        assert r['type'] == 'text'
        assert r['value'] == 'hello'

    def test_none_no_es_bool(self):
        """None debe mapearse a null, no a integer."""
        r = self.db._convert_param(None)
        assert r['type'] == 'null'


# ── ejecutar_query ────────────────────────────────────────────────────────────

class TestEjecutarQuery:
    def setup_method(self):
        self.db = _get_db()

    def test_query_exitosa_retorna_filas(self):
        mock_resp = _turso_response([['Alice', '30'], ['Bob', '25']],
                                    ['name', 'age'])
        with patch('adapters.database.requests.post', return_value=mock_resp):
            result = self.db.ejecutar_query("SELECT name, age FROM users")
        assert len(result.rows) == 2
        assert result.rows[0]['name'] == 'Alice'

    def test_query_sin_filas_retorna_lista_vacia(self):
        mock_resp = _turso_response([], ['id'])
        with patch('adapters.database.requests.post', return_value=mock_resp):
            result = self.db.ejecutar_query("SELECT id FROM empty_table")
        assert result.rows == []

    def test_error_http_lanza_excepcion(self):
        with patch('adapters.database.requests.post',
                   return_value=_error_response(400)):
            with pytest.raises(Exception, match='400'):
                self.db.ejecutar_query("SELECT 1")

    def test_retry_en_502(self):
        """502 se reintenta; segundo intento exitoso."""
        responses = [_error_response(502), _turso_response([['1']], ['res'])]
        with patch('adapters.database.requests.post', side_effect=responses):
            with patch('adapters.database.time.sleep'):
                result = self.db.ejecutar_query("SELECT 1")
        assert len(result.rows) == 1

    def test_retry_agotado_lanza_excepcion(self):
        """Si se agotan los reintentos debe propagar la excepción."""
        with patch('adapters.database.requests.post',
                   return_value=_error_response(503)):
            with patch('adapters.database.time.sleep'):
                with pytest.raises(Exception):
                    self.db.ejecutar_query("SELECT 1")

    def test_columnas_retornadas_correctamente(self):
        mock_resp = _turso_response([['val']], ['columna_x'])
        with patch('adapters.database.requests.post', return_value=mock_resp):
            result = self.db.ejecutar_query("SELECT columna_x FROM t")
        assert 'columna_x' in result.columns

    def test_convierte_integer_a_int(self):
        """Valores de tipo 'integer' devueltos por Turso deben convertirse a int."""
        col_defs = [{'name': 'score'}]
        row_data  = [[{'type': 'integer', 'value': '42'}]]
        payload = {'results': [{'response': {'result': {
            'cols': col_defs, 'rows': row_data
        }}}]}
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = payload
        with patch('adapters.database.requests.post', return_value=mock_resp):
            result = self.db.ejecutar_query("SELECT score FROM t")
        assert result.rows[0]['score'] == 42
        assert isinstance(result.rows[0]['score'], int)

    def test_convierte_float_a_float(self):
        col_defs = [{'name': 'precio'}]
        row_data  = [[{'type': 'float', 'value': '3310.5'}]]
        payload = {'results': [{'response': {'result': {
            'cols': col_defs, 'rows': row_data
        }}}]}
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = payload
        with patch('adapters.database.requests.post', return_value=mock_resp):
            result = self.db.ejecutar_query("SELECT precio FROM t")
        assert isinstance(result.rows[0]['precio'], float)


# ── ejecutar_insert ───────────────────────────────────────────────────────────

class TestEjecutarInsert:
    def setup_method(self):
        self.db = _get_db()

    def _insert_response(self, last_id: int):
        """Simula la respuesta de INSERT + last_insert_rowid()."""
        # Turso devuelve dos resultados en el pipeline
        payload = {
            'results': [
                {'response': {'result': {'cols': [], 'rows': []}}},  # INSERT
                {'response': {'result': {
                    'cols': [{'name': 'id'}],
                    'rows': [[{'type': 'integer', 'value': str(last_id)}]]
                }}},  # SELECT last_insert_rowid()
            ]
        }
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = payload
        return mock_resp

    def test_retorna_id_insertado(self):
        with patch('adapters.database.requests.post',
                   return_value=self._insert_response(99)):
            id_ = self.db.ejecutar_insert("INSERT INTO t(x) VALUES(?)", (42,))
        assert id_ == 99

    def test_retorna_none_si_no_hay_rowid(self):
        """Si el pipeline no devuelve un segundo resultado, retorna None."""
        payload = {'results': [
            {'response': {'result': {'cols': [], 'rows': []}}},
        ]}
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = payload
        with patch('adapters.database.requests.post', return_value=mock_resp):
            id_ = self.db.ejecutar_insert("INSERT INTO t(x) VALUES(?)", (1,))
        assert id_ is None

    def test_error_http_lanza_excepcion(self):
        with patch('adapters.database.requests.post',
                   return_value=_error_response(500)):
            with pytest.raises(Exception):
                self.db.ejecutar_insert("INSERT INTO t(x) VALUES(?)", (1,))


# ── Singleton ─────────────────────────────────────────────────────────────────

class TestSingleton:
    def test_dos_instancias_son_el_mismo_objeto(self):
        import adapters.database as db_module
        db_module.DatabaseManager._instance = None  # resetear
        db1 = db_module.DatabaseManager()
        db2 = db_module.DatabaseManager()
        assert db1 is db2

    def test_singleton_sin_credenciales_lanza_error(self):
        import adapters.database as db_module
        db_module.DatabaseManager._instance = None
        old_url   = os.environ.pop('TURSO_DATABASE_URL', None)
        old_token = os.environ.pop('TURSO_AUTH_TOKEN', None)
        try:
            with pytest.raises(ValueError, match='TURSO_DATABASE_URL'):
                db_module.DatabaseManager()
        finally:
            # Restaurar variables de entorno
            if old_url:   os.environ['TURSO_DATABASE_URL'] = old_url
            if old_token: os.environ['TURSO_AUTH_TOKEN']   = old_token
            db_module.DatabaseManager._instance = None
