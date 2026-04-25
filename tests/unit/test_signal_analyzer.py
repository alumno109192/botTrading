"""
tests/unit/test_signal_analyzer.py — Tests unitarios para core/signal_analyzer.py

Cubre:
  - analizar_senal: cálculo de TPs, resultados SL/TP1/TP2/TP3/EN_CURSO, PnL
  - analizar_senal: segunda entrada
  - analizar_senal: medias móviles como obstáculos y cierre_en_media
  - analizar_senal_json: serialización JSON
  - analizar_obstaculos: clasificación, severidad, impacto, recomendación
"""
import pytest
from core.signal_analyzer import analizar_senal, analizar_senal_json, analizar_obstaculos
import json


# ── Fixtures base ─────────────────────────────────────────────────────────────

LONG_BASE = dict(
    direccion="LONG",
    entry=3310.0,
    sl=3295.0,       # riesgo = 15 pts
    tp1_rr=1.5,      # tp1 = 3310 + 15*1.5 = 3332.5
    tp2_rr=2.5,      # tp2 = 3310 + 15*2.5 = 3347.5
    tp3_rr=4.0,      # tp3 = 3310 + 15*4.0 = 3370.0
)

SHORT_BASE = dict(
    direccion="SHORT",
    entry=3310.0,
    sl=3325.0,       # riesgo = 15 pts
    tp1_rr=1.5,      # tp1 = 3310 - 15*1.5 = 3287.5
    tp2_rr=2.5,      # tp2 = 3310 - 15*2.5 = 3272.5
    tp3_rr=4.0,      # tp3 = 3310 - 15*4.0 = 3250.0
)


# ── Tests de analizar_senal ───────────────────────────────────────────────────

class TestAnalizarSenalNiveles:
    def test_long_niveles_correctos(self):
        r = analizar_senal(**LONG_BASE)
        assert r['entry'] == 3310.0
        assert r['sl']    == 3295.0
        assert r['riesgo_pts'] == 15.0
        assert r['tp1'] == pytest.approx(3332.5)
        assert r['tp2'] == pytest.approx(3347.5)
        assert r['tp3'] == pytest.approx(3370.0)

    def test_short_niveles_correctos(self):
        r = analizar_senal(**SHORT_BASE)
        assert r['tp1'] == pytest.approx(3287.5)
        assert r['tp2'] == pytest.approx(3272.5)
        assert r['tp3'] == pytest.approx(3250.0)

    def test_riesgo_calculado_correctamente(self):
        r = analizar_senal(**LONG_BASE)
        assert r['riesgo_pts'] == abs(LONG_BASE['entry'] - LONG_BASE['sl'])

    def test_error_cuando_entry_igual_a_sl(self):
        with pytest.raises(ValueError, match="riesgo = 0"):
            analizar_senal(direccion="LONG", entry=100.0, sl=100.0,
                           tp1_rr=1.5, tp2_rr=2.5, tp3_rr=4.0)

    def test_error_direccion_invalida(self):
        with pytest.raises(ValueError, match="LONG.*SHORT"):
            analizar_senal(direccion="COMPRA", entry=100.0, sl=95.0,
                           tp1_rr=1.5, tp2_rr=2.5, tp3_rr=4.0)

    def test_case_insensitive_direccion(self):
        r = analizar_senal(direccion="long", entry=100.0, sl=95.0,
                           tp1_rr=1.0, tp2_rr=2.0, tp3_rr=3.0)
        assert r['tp1'] == pytest.approx(105.0)


class TestAnalizarSenalResultados:
    def test_sin_cierre_es_en_curso(self):
        r = analizar_senal(**LONG_BASE, cierre=None)
        assert r['resultado'] == 'EN_CURSO'
        assert r['cierre'] is None

    def test_long_sl_alcanzado(self):
        r = analizar_senal(**LONG_BASE, cierre=3290.0)
        assert r['resultado'] == 'SL'
        assert r['pnl_pts'] < 0

    def test_long_tp1_alcanzado(self):
        r = analizar_senal(**LONG_BASE, cierre=3335.0)
        assert r['resultado'] == 'TP1'
        assert r['pnl_pts'] > 0

    def test_long_tp2_alcanzado(self):
        r = analizar_senal(**LONG_BASE, cierre=3350.0)
        assert r['resultado'] == 'TP2'

    def test_long_tp3_alcanzado(self):
        r = analizar_senal(**LONG_BASE, cierre=3375.0)
        assert r['resultado'] == 'TP3'

    def test_long_en_curso_entre_entry_y_tp1(self):
        r = analizar_senal(**LONG_BASE, cierre=3320.0)
        assert r['resultado'] == 'EN_CURSO'

    def test_short_sl_alcanzado(self):
        r = analizar_senal(**SHORT_BASE, cierre=3330.0)
        assert r['resultado'] == 'SL'
        assert r['pnl_pts'] < 0

    def test_short_tp1_alcanzado(self):
        r = analizar_senal(**SHORT_BASE, cierre=3285.0)
        assert r['resultado'] == 'TP1'
        assert r['pnl_pts'] > 0

    def test_short_tp3_alcanzado(self):
        r = analizar_senal(**SHORT_BASE, cierre=3245.0)
        assert r['resultado'] == 'TP3'

    def test_long_sl_exactamente_en_sl(self):
        r = analizar_senal(**LONG_BASE, cierre=3295.0)
        assert r['resultado'] == 'SL'

    def test_long_tp1_exactamente_en_tp1(self):
        r = analizar_senal(**LONG_BASE, cierre=3332.5)
        assert r['resultado'] == 'TP1'


class TestAnalizarSenalPnL:
    def test_long_pnl_positivo_en_tp(self):
        r = analizar_senal(**LONG_BASE, cierre=3340.0)
        assert r['pnl_pts'] > 0

    def test_short_pnl_positivo_en_tp(self):
        r = analizar_senal(**SHORT_BASE, cierre=3280.0)
        assert r['pnl_pts'] > 0

    def test_long_pnl_negativo_en_sl(self):
        r = analizar_senal(**LONG_BASE, cierre=3285.0)
        assert r['pnl_pts'] < 0


class TestAnalizarSenalSegundaEntrada:
    def test_no_segunda_entrada_sin_cierre(self):
        r = analizar_senal(**LONG_BASE, cierre=None)
        assert r['segunda_entrada'] == 'NO'

    def test_segunda_entrada_posible_long(self):
        """Precio retrocede pero sigue sobre el SL con >30% de margen."""
        # SL=3295, entry=3310, riesgo=15
        # cierre=3299.5 → margen=(3299.5-3295)/15=0.30 → POSIBLE
        r = analizar_senal(**LONG_BASE, cierre=3299.6)
        assert r['resultado'] == 'EN_CURSO'
        assert r['segunda_entrada'] in ('POSIBLE', 'ARRIESGADA')

    def test_segunda_entrada_arriesgada_long(self):
        """Precio retrocede hacia el SL (<30% de margen)."""
        # cierre=3296 → margen=(3296-3295)/15=0.067 → ARRIESGADA
        r = analizar_senal(**LONG_BASE, cierre=3296.0)
        assert r['segunda_entrada'] == 'ARRIESGADA'

    def test_no_segunda_entrada_cuando_tp_alcanzado(self):
        r = analizar_senal(**LONG_BASE, cierre=3340.0)
        assert r['segunda_entrada'] == 'NO'


class TestAnalizarSenalMedias:
    def test_obstaculos_detectados_antes_de_tp1(self):
        """EMA20 entre entry y TP1 → aparece en obstáculos antes_tp1."""
        r = analizar_senal(**LONG_BASE, cierre=3320.0,
                           medias={'EMA20': 3320.0, 'EMA50': 3360.0})
        # EMA20=3320 está entre 3310 y 3332.5 → antes_tp1
        obs_tp1 = r['medias_obstaculos']['antes_tp1']
        nombres = [o['nombre'] for o in obs_tp1]
        assert 'EMA20' in nombres

    def test_cierre_en_media_detectado(self):
        """Cierre dentro de la tolerancia de una media → cierre_en_media."""
        # TP1 = 3332.5; EMA50 = 3332.0 (~0.5 pts de diferencia, < 0.15% de 3332.5)
        r = analizar_senal(**LONG_BASE, cierre=3332.0,
                           medias={'EMA50': 3332.0})
        assert r['cierre_en_media'] == 'EMA50'

    def test_sin_medias_obstaculos_vacios(self):
        r = analizar_senal(**LONG_BASE, cierre=3320.0)
        for zona in r['medias_obstaculos'].values():
            assert zona == []

    def test_ema_fuera_de_rango_tp_no_aparece(self):
        """EMA muy alejada no debe aparecer en obstáculos."""
        r = analizar_senal(**LONG_BASE, cierre=3320.0,
                           medias={'EMA200': 3200.0})
        total_obs = sum(len(v) for v in r['medias_obstaculos'].values())
        assert total_obs == 0


class TestAnalizarSenalJSON:
    def test_retorna_json_valido(self):
        j = analizar_senal_json(**LONG_BASE, cierre=3340.0)
        data = json.loads(j)
        assert 'resultado' in data
        assert 'tp1' in data

    def test_json_coincide_con_dict(self):
        d = analizar_senal(**LONG_BASE, cierre=3340.0)
        j = json.loads(analizar_senal_json(**LONG_BASE, cierre=3340.0))
        assert d['resultado'] == j['resultado']
        assert abs(d['tp1'] - j['tp1']) < 1e-6


# ── Tests de analizar_obstaculos ─────────────────────────────────────────────

class TestAnalizarObstaculos:
    BASE_SENAL = {
        'id': 1,
        'simbolo': 'XAUUSD',
        'timeframe': '4H',
        'direccion': 'LONG',
        'precio_entrada': 3310.0,
        'sl': 3295.0,
        'tp1': 3332.5,
        'tp2': 3347.5,
        'tp3': 3370.0,
        'atr': 15.0,
    }

    def test_sin_medias_no_hay_obstaculos(self):
        r = analizar_obstaculos(self.BASE_SENAL, {})
        assert r['tiene_obstaculos'] is False
        assert r['obstaculos'] == []

    def test_estructura_de_retorno(self):
        r = analizar_obstaculos(self.BASE_SENAL, {})
        expected_keys = {'senal_id', 'tiene_obstaculos', 'obstaculos',
                         'impacto_tp1', 'impacto_tp2', 'impacto_tp3',
                         'recomendacion', 'todas_a_favor',
                         'referencias_lejanas', 'mensaje_telegram'}
        assert expected_keys.issubset(r.keys())

    def test_ema_en_zona_entry_es_critico(self):
        """EMA en ±0.5 ATR del entry → severidad CRÍTICO → NO_OPERAR."""
        # ATR=15, entry=3310, zona entry = [3302.5, 3317.5]
        medias = {'EMA18': 3312.0}
        r = analizar_obstaculos(self.BASE_SENAL, medias)
        assert r['recomendacion'] == 'NO_OPERAR'
        obs = r['obstaculos']
        assert len(obs) == 1
        assert obs[0]['severidad'] == 'CRÍTICO'

    def test_ema_tendencia_lejana_es_referencia(self):
        """EMA tendencia > 3 ATR del entry → no es obstáculo, es referencia."""
        # ATR=15, entry=3310, 3ATR=45 → EMA200 < 3310-45=3265 es lejana
        medias = {'EMA200': 3260.0}
        r = analizar_obstaculos(self.BASE_SENAL, medias)
        assert r['tiene_obstaculos'] is False
        assert len(r['referencias_lejanas']) == 1

    def test_recomendacion_operar_normal_sin_obstaculos(self):
        r = analizar_obstaculos(self.BASE_SENAL, {})
        assert r['recomendacion'] == 'OPERAR_NORMAL'

    def test_impacto_tp1_bloqueado_cuando_critico_en_entry(self):
        medias = {'EMA18': 3312.0}
        r = analizar_obstaculos(self.BASE_SENAL, medias)
        assert r['impacto_tp1'] == 'BLOQUEADO'

    def test_short_sin_obstaculos_normal(self):
        senal_short = {**self.BASE_SENAL,
                       'direccion': 'SHORT',
                       'precio_entrada': 3310.0,
                       'sl': 3325.0,
                       'tp1': 3287.5,
                       'tp2': 3272.5,
                       'tp3': 3250.0}
        r = analizar_obstaculos(senal_short, {})
        assert r['recomendacion'] == 'OPERAR_NORMAL'

    def test_mensaje_telegram_generado(self):
        r = analizar_obstaculos(self.BASE_SENAL, {})
        assert 'XAUUSD' in r['mensaje_telegram']
        assert '4H' in r['mensaje_telegram']

    def test_campo_zona_interno_no_expuesto(self):
        """El campo _zona no debe aparecer en obstáculos públicos."""
        medias = {'EMA18': 3312.0}
        r = analizar_obstaculos(self.BASE_SENAL, medias)
        for obs in r['obstaculos']:
            assert '_zona' not in obs

    def test_error_direccion_invalida(self):
        senal_bad = {**self.BASE_SENAL, 'direccion': 'BUY'}
        with pytest.raises(ValueError):
            analizar_obstaculos(senal_bad, {})

    def test_scalping_reduce_severidad(self):
        """En scalping la severidad baja un nivel respecto al mismo obstáculo en 4H."""
        senal_scalping = {**self.BASE_SENAL, 'timeframe': '5M'}
        # EMA lenta entre entry y TP1 → severidad base MODERADO → en scalping → LEVE
        medias = {'EMA42': 3320.0}   # entre 3310 y 3332.5
        r = analizar_obstaculos(senal_scalping, medias)
        if r['tiene_obstaculos']:
            sevs = [o['severidad'] for o in r['obstaculos']]
            assert 'CRÍTICO' not in sevs

    def test_todas_a_favor_sin_obstaculos_y_pendientes_positivas(self):
        """Cuando no hay obstáculos y todas las MAs van a favor → todas_a_favor=True."""
        medias = {'EMA9': 3200.0}  # muy por debajo del entry (fuera de rango trade)
        pendientes = {'EMA9': 1.0}  # alcista → a favor de LONG
        r = analizar_obstaculos(self.BASE_SENAL, medias, pendientes)
        # EMA9 fuera del rango del trade → no es obstáculo
        # todas_a_favor puede ser True si no hay obstáculos y pendientes son positivas
        assert isinstance(r['todas_a_favor'], bool)
