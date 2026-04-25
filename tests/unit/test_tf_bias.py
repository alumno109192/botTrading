"""
tests/unit/test_tf_bias.py — Tests unitarios para services/tf_bias.py

Cubre:
  - publicar_sesgo / obtener_sesgo
  - verificar_confluencia (permite / bloquea según sesgos publicados)
  - estado_completo
  - publicar_canal_* / obtener_canal_* (solo memoria, sin BD)
"""
import time
import pytest

# Importar el módulo completo para acceder a los stores internos si hace falta
import services.tf_bias as tf_bias
from services.tf_bias import (
    publicar_sesgo,
    obtener_sesgo,
    verificar_confluencia,
    estado_completo,
    BIAS_BULLISH,
    BIAS_BEARISH,
    BIAS_NEUTRAL,
    publicar_canal_4h,
    obtener_canal_4h,
    publicar_canal_1h,
    obtener_canal_1h,
)


# ── Fixture: limpiar el store global entre tests ──────────────────────────────

@pytest.fixture(autouse=True)
def limpiar_stores():
    """Limpia los stores globales antes de cada test para evitar contaminación."""
    tf_bias._bias_store.clear()
    tf_bias._canal_store.clear()
    tf_bias._canal_1h_store.clear()
    tf_bias._canal_1d_store.clear()
    tf_bias._canal_1w_store.clear()
    yield
    tf_bias._bias_store.clear()
    tf_bias._canal_store.clear()
    tf_bias._canal_1h_store.clear()
    tf_bias._canal_1d_store.clear()
    tf_bias._canal_1w_store.clear()


# ── publicar_sesgo / obtener_sesgo ────────────────────────────────────────────

class TestPublicarObtenerSesgo:
    def test_sesgo_publicado_puede_obtenerse(self):
        publicar_sesgo('XAUUSD', '1D', BIAS_BULLISH, score=10)
        sesgo = obtener_sesgo('XAUUSD', '1D')
        assert sesgo is not None
        assert sesgo['bias']  == BIAS_BULLISH
        assert sesgo['score'] == 10

    def test_sesgo_sobrescribe_anterior(self):
        publicar_sesgo('XAUUSD', '1D', BIAS_BULLISH, score=10)
        publicar_sesgo('XAUUSD', '1D', BIAS_BEARISH, score=7)
        sesgo = obtener_sesgo('XAUUSD', '1D')
        assert sesgo['bias'] == BIAS_BEARISH
        assert sesgo['score'] == 7

    def test_obtener_sesgo_inexistente_retorna_none(self):
        assert obtener_sesgo('BTCUSD', '1D') is None

    def test_sesgos_de_distintos_simbolos_son_independientes(self):
        publicar_sesgo('XAUUSD', '4H', BIAS_BULLISH, score=8)
        publicar_sesgo('BTCUSD', '4H', BIAS_BEARISH, score=5)
        assert obtener_sesgo('XAUUSD', '4H')['bias'] == BIAS_BULLISH
        assert obtener_sesgo('BTCUSD', '4H')['bias'] == BIAS_BEARISH

    def test_sesgos_de_distintos_tfs_son_independientes(self):
        publicar_sesgo('XAUUSD', '1D', BIAS_BULLISH, score=10)
        publicar_sesgo('XAUUSD', '4H', BIAS_BEARISH, score=6)
        assert obtener_sesgo('XAUUSD', '1D')['bias'] == BIAS_BULLISH
        assert obtener_sesgo('XAUUSD', '4H')['bias'] == BIAS_BEARISH

    def test_sesgo_incluye_timestamp(self):
        publicar_sesgo('XAUUSD', '1D', BIAS_BULLISH, score=10)
        sesgo = obtener_sesgo('XAUUSD', '1D')
        assert 'ts' in sesgo


# ── verificar_confluencia ────────────────────────────────────────────────────

class TestVerificarConfluencia:
    def test_permite_cuando_no_hay_datos_de_tfs_superiores(self):
        """Sin datos en TFs superiores, la señal debe permitirse."""
        ok, desc = verificar_confluencia('XAUUSD', '4H', BIAS_BULLISH)
        assert ok is True

    def test_permite_cuando_tf_no_esta_en_jerarquia(self):
        ok, desc = verificar_confluencia('XAUUSD', '999H', BIAS_BULLISH)
        assert ok is True

    def test_permite_cuando_1d_confirma_direccion_long(self):
        publicar_sesgo('XAUUSD', '1D', BIAS_BULLISH, score=10)
        ok, desc = verificar_confluencia('XAUUSD', '4H', BIAS_BULLISH)
        assert ok is True
        assert isinstance(desc, str)

    def test_bloquea_cuando_1d_es_contrario(self):
        publicar_sesgo('XAUUSD', '1D', BIAS_BEARISH, score=10)
        ok, desc = verificar_confluencia('XAUUSD', '4H', BIAS_BULLISH)
        assert ok is False

    def test_permite_cuando_mayoria_confirma(self):
        publicar_sesgo('XAUUSD', '1D', BIAS_BULLISH,  score=10)
        publicar_sesgo('XAUUSD', '1W', BIAS_BULLISH,  score=9)
        ok, _ = verificar_confluencia('XAUUSD', '4H', BIAS_BULLISH)
        assert ok is True

    def test_bloquea_cuando_mayoria_es_contraria(self):
        publicar_sesgo('XAUUSD', '1D', BIAS_BEARISH, score=10)
        publicar_sesgo('XAUUSD', '1W', BIAS_BEARISH, score=9)
        ok, _ = verificar_confluencia('XAUUSD', '4H', BIAS_BULLISH)
        assert ok is False

    def test_descripcion_es_string(self):
        publicar_sesgo('XAUUSD', '1D', BIAS_BULLISH, score=8)
        _, desc = verificar_confluencia('XAUUSD', '4H', BIAS_BULLISH)
        assert isinstance(desc, str)
        assert len(desc) > 0

    def test_neutral_no_cuenta_como_contrario(self):
        """NEUTRAL no bloquea la señal."""
        publicar_sesgo('XAUUSD', '1D', BIAS_NEUTRAL, score=5)
        ok, _ = verificar_confluencia('XAUUSD', '4H', BIAS_BULLISH)
        # Neutral no se suma a confirmados ni a contrarios → debe permitir
        assert ok is True

    def test_permite_tf_mas_alto_en_jerarquia(self):
        """1W no tiene TFs superiores → siempre permite."""
        ok, _ = verificar_confluencia('XAUUSD', '1W', BIAS_BULLISH)
        assert ok is True


# ── estado_completo ───────────────────────────────────────────────────────────

class TestEstadoCompleto:
    def test_retorna_dict(self):
        assert isinstance(estado_completo(), dict)

    def test_contiene_sesgos_publicados(self):
        publicar_sesgo('XAUUSD', '1D', BIAS_BULLISH, score=10)
        estado = estado_completo()
        assert 'XAUUSD' in estado
        assert '1D' in estado['XAUUSD']

    def test_es_copia_del_primer_nivel(self):
        """estado_completo copia el nivel símbolo→TF; el símbolo es independiente."""
        publicar_sesgo('XAUUSD', '1D', BIAS_BULLISH, score=10)
        estado = estado_completo()
        # Añadir un nuevo símbolo al resultado no debe afectar el store interno
        estado['NUEVO_SIMBOLO'] = {}
        assert 'NUEVO_SIMBOLO' not in tf_bias._bias_store


# ── Canales ───────────────────────────────────────────────────────────────────

class TestCanales:
    def test_canal_4h_guardado_y_recuperado(self):
        publicar_canal_4h('XAUUSD',
                          alcista_roto=True, bajista_roto=False,
                          linea_soporte=3280.0, linea_resist=3350.0)
        canal = obtener_canal_4h('XAUUSD')
        assert canal is not None
        assert canal['alcista_roto'] is True
        assert canal['bajista_roto'] is False
        assert canal['linea_soporte'] == pytest.approx(3280.0)
        assert canal['linea_resist']  == pytest.approx(3350.0)

    def test_canal_1h_guardado_y_recuperado(self):
        publicar_canal_1h('XAUUSD',
                          alcista_roto=False, bajista_roto=True,
                          linea_soporte=3290.0, linea_resist=3340.0)
        canal = obtener_canal_1h('XAUUSD')
        assert canal is not None
        assert canal['bajista_roto'] is True

    def test_canal_inexistente_retorna_none(self):
        assert obtener_canal_4h('INEXISTENTE') is None
        assert obtener_canal_1h('INEXISTENTE') is None

    def test_canal_sobrescribe_anterior(self):
        publicar_canal_4h('XAUUSD', True, False, 3280.0, 3350.0)
        publicar_canal_4h('XAUUSD', False, True, 3270.0, 3360.0)
        canal = obtener_canal_4h('XAUUSD')
        assert canal['alcista_roto'] is False
        assert canal['bajista_roto'] is True

    def test_canales_simbolos_independientes(self):
        publicar_canal_4h('XAUUSD', True,  False, 3280.0, 3350.0)
        publicar_canal_4h('BTCUSD', False, True,  90000.0, 100000.0)
        canal_gold  = obtener_canal_4h('XAUUSD')
        canal_btc   = obtener_canal_4h('BTCUSD')
        assert canal_gold['alcista_roto'] is True
        assert canal_btc['alcista_roto']  is False
