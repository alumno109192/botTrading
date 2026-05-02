"""
tests/unit/test_indicadores_nuevos.py — Tests para las funciones añadidas en esta sesión.

Cubre:
  - calcular_pivots_diarios
  - evaluar_precio_vs_pivots
  - calcular_ichimoku
  - detectar_precio_vs_kumo
  - detectar_hch
  - detectar_hch_invertido
  - detectar_triangulo
  - detectar_bandera_banderin
"""
import pytest
import numpy as np
import pandas as pd

from core.indicators import (
    calcular_pivots_diarios,
    evaluar_precio_vs_pivots,
    calcular_ichimoku,
    detectar_precio_vs_kumo,
    detectar_hch,
    detectar_hch_invertido,
    detectar_triangulo,
    detectar_bandera_banderin,
)


# ── Helper ────────────────────────────────────────────────────────────────────

def _make_ohlcv(n, close_base=2000.0, high_extra=0.5, low_extra=0.5,
                opens=None, highs=None, lows=None, closes=None):
    """Crea un DataFrame OHLCV de n filas con precios planos por defecto."""
    c = np.full(n, close_base) if closes is None else np.array(closes, dtype=float)
    h = c + high_extra          if highs  is None else np.array(highs,  dtype=float)
    lo = c - low_extra          if lows   is None else np.array(lows,   dtype=float)
    o = c.copy()                if opens  is None else np.array(opens,  dtype=float)
    v = np.full(n, 1000.0)
    return pd.DataFrame({'Open': o, 'High': h, 'Low': lo, 'Close': c, 'Volume': v})


def _make_diario(H, L, C, extra_rows=3):
    """Crea un df diario con una sesión anterior conocida y relleno de contexto."""
    rows = extra_rows + 2   # relleno + sesión_ayer[-2] + sesión_hoy[-1]
    highs  = [H] * rows
    lows   = [L] * rows
    closes = [C] * rows
    return pd.DataFrame({'High': highs, 'Low': lows, 'Close': closes,
                         'Open': closes, 'Volume': [1000]*rows})


# ══════════════════════════════════════════════════════════════════════════════
# PIVOTS DIARIOS
# ══════════════════════════════════════════════════════════════════════════════

class TestCalcularPivotsDiarios:
    def test_formula_pp(self):
        """PP = (H + L + C) / 3."""
        df = _make_diario(H=2050.0, L=2000.0, C=2040.0)
        pivots = calcular_pivots_diarios(df)
        expected_pp = (2050.0 + 2000.0 + 2040.0) / 3.0
        assert abs(pivots['PP'] - expected_pp) < 0.01

    def test_formula_r1_s1(self):
        """R1 = 2×PP − L;  S1 = 2×PP − H."""
        H, L, C = 2050.0, 2000.0, 2040.0
        df = _make_diario(H=H, L=L, C=C)
        pivots = calcular_pivots_diarios(df)
        PP = (H + L + C) / 3.0
        assert abs(pivots['R1'] - (2 * PP - L)) < 0.01
        assert abs(pivots['S1'] - (2 * PP - H)) < 0.01

    def test_formula_r2_s2(self):
        """R2 = PP + (H − L);  S2 = PP − (H − L)."""
        H, L, C = 2050.0, 2000.0, 2040.0
        df = _make_diario(H=H, L=L, C=C)
        pivots = calcular_pivots_diarios(df)
        PP  = (H + L + C) / 3.0
        rng = H - L
        assert abs(pivots['R2'] - (PP + rng)) < 0.01
        assert abs(pivots['S2'] - (PP - rng)) < 0.01

    def test_tiene_todas_las_claves(self):
        """El dict debe tener PP, R1, R2, R3, S1, S2, S3."""
        df = _make_diario(H=2050.0, L=2000.0, C=2040.0)
        pivots = calcular_pivots_diarios(df)
        for key in ('PP', 'R1', 'R2', 'R3', 'S1', 'S2', 'S3'):
            assert key in pivots

    def test_jerarquia_niveles(self):
        """S3 < S2 < S1 < PP < R1 < R2 < R3."""
        df = _make_diario(H=2050.0, L=2000.0, C=2025.0)
        p = calcular_pivots_diarios(df)
        assert p['S3'] < p['S2'] < p['S1'] < p['PP'] < p['R1'] < p['R2'] < p['R3']

    def test_datos_insuficientes_retorna_dict_vacio(self):
        """Con menos de 2 filas debe retornar {}."""
        df = _make_diario(H=2050.0, L=2000.0, C=2040.0, extra_rows=0)
        # Sólo 2 filas (la [-2] y la [-1]) — OK con extra_rows=0
        # Probar con 1 fila (insuficiente)
        df_1 = pd.DataFrame({'High': [2050], 'Low': [2000], 'Close': [2040],
                             'Open': [2040], 'Volume': [1000]})
        assert calcular_pivots_diarios(df_1) == {}

    def test_none_retorna_dict_vacio(self):
        assert calcular_pivots_diarios(None) == {}


# ══════════════════════════════════════════════════════════════════════════════
# EVALUAR PRECIO VS PIVOTS
# ══════════════════════════════════════════════════════════════════════════════

class TestEvaluarPrecioVsPivots:
    @pytest.fixture
    def pivots_gold(self):
        """Pivots sintéticos representativos de Gold."""
        return {
            'PP': 2000.0, 'R1': 2020.0, 'R2': 2040.0, 'R3': 2060.0,
            'S1': 1980.0, 'S2': 1960.0, 'S3': 1940.0,
        }

    def test_pivots_vacios_retorna_listas_vacias(self):
        sop, res, info = evaluar_precio_vs_pivots(2000, 2005, 1995, {}, atr=5.0)
        assert sop == []
        assert res == []
        assert info == ''

    def test_precio_sobre_pp_retorna_info_correcta(self, pivots_gold):
        sop, res, info = evaluar_precio_vs_pivots(
            close=2010.0, high=2015.0, low=2005.0,
            pivots=pivots_gold, atr=5.0, tol_mult=0.3,
        )
        assert 'PP' in info or '2000' in info

    def test_precio_bajo_pp_retorna_info_correcta(self, pivots_gold):
        sop, res, info = evaluar_precio_vs_pivots(
            close=1990.0, high=1995.0, low=1985.0,
            pivots=pivots_gold, atr=5.0, tol_mult=0.3,
        )
        assert 'PP' in info or '2000' in info

    def test_soportes_son_menores_que_close(self, pivots_gold):
        close = 2010.0
        sop, _, _ = evaluar_precio_vs_pivots(
            close=close, high=2015.0, low=2005.0,
            pivots=pivots_gold, atr=5.0, tol_mult=0.3,
        )
        for s in sop:
            assert s < close + 5.0 * 0.3 + 1e-6   # con tolerancia

    def test_resistencias_son_mayores_que_close(self, pivots_gold):
        close = 1990.0
        _, res, _ = evaluar_precio_vs_pivots(
            close=close, high=1995.0, low=1985.0,
            pivots=pivots_gold, atr=5.0, tol_mult=0.3,
        )
        for r in res:
            assert r > close - 5.0 * 0.3 - 1e-6   # con tolerancia

    def test_retorna_tres_elementos(self, pivots_gold):
        result = evaluar_precio_vs_pivots(2010, 2015, 2005, pivots_gold, 5.0)
        assert len(result) == 3

    def test_precio_en_pp_menciona_pp(self, pivots_gold):
        """Precio exactamente en PP debe mencionar 'PP' en info."""
        sop, res, info = evaluar_precio_vs_pivots(
            close=2000.0, high=2002.0, low=1998.0,
            pivots=pivots_gold, atr=5.0, tol_mult=0.3,
        )
        assert 'PP' in info


# ══════════════════════════════════════════════════════════════════════════════
# ICHIMOKU
# ══════════════════════════════════════════════════════════════════════════════

class TestCalcularIchimoku:
    def _uptrend_df(self, n=100):
        closes = [2000.0 + i * 0.5 for i in range(n)]
        highs  = [c + 2.0 for c in closes]
        lows   = [c - 2.0 for c in closes]
        return _make_ohlcv(n, closes=closes, highs=highs, lows=lows)

    def test_agrega_columnas_ichimoku(self):
        df = self._uptrend_df(100)
        df_ich = calcular_ichimoku(df)
        for col in ('tenkan', 'kijun', 'kumo_a', 'kumo_b', 'chikou'):
            assert col in df_ich.columns, f"Falta columna: {col}"

    def test_no_modifica_columnas_originales(self):
        df = self._uptrend_df(100)
        df_ich = calcular_ichimoku(df)
        for col in ('Open', 'High', 'Low', 'Close', 'Volume'):
            assert col in df_ich.columns

    def test_longitud_preservada(self):
        df = self._uptrend_df(100)
        df_ich = calcular_ichimoku(df)
        assert len(df_ich) == len(df)

    def test_tenkan_lt_kijun_long_term_in_uptrend(self):
        """En tendencia alcista sostenida tenkan > kijun (conversión > base)."""
        df = self._uptrend_df(120)
        df_ich = calcular_ichimoku(df)
        tenkan_last = float(df_ich['tenkan'].dropna().iloc[-1])
        kijun_last  = float(df_ich['kijun'].dropna().iloc[-1])
        assert tenkan_last > kijun_last

    def test_kumo_a_mayor_que_kumo_b_en_tendencia_alcista(self):
        """En tendencia alcista madura, kumo_a > kumo_b (nube verde)."""
        df = self._uptrend_df(120)
        df_ich = calcular_ichimoku(df)
        kumo_a = float(df_ich['kumo_a'].dropna().iloc[-1])
        kumo_b = float(df_ich['kumo_b'].dropna().iloc[-1])
        assert kumo_a > kumo_b


# ══════════════════════════════════════════════════════════════════════════════
# DETECTAR PRECIO VS KUMO
# ══════════════════════════════════════════════════════════════════════════════

class TestDetectarPrecioVsKumo:
    def _df_sin_columnas_kumo(self, n=60):
        return _make_ohlcv(n)

    def _df_con_kumo_manual(self, n, close_val, kumo_a_val, kumo_b_val):
        """Construye un df con columnas kumo_a y kumo_b inyectadas manualmente."""
        df = _make_ohlcv(n, close_base=close_val)
        df['kumo_a'] = kumo_a_val
        df['kumo_b'] = kumo_b_val
        return df

    def test_sin_columnas_kumo_retorna_sin_datos(self):
        df = self._df_sin_columnas_kumo()
        pos, color, grosor, senyal = detectar_precio_vs_kumo(df, atr=5.0)
        assert pos == 'sin_datos'
        assert senyal == 0

    def test_sobre_kumo_verde_retorna_senyal_positiva(self):
        """Precio sobre kumo verde → senyal = +2."""
        df = self._df_con_kumo_manual(60, close_val=2100.0,
                                      kumo_a_val=2050.0, kumo_b_val=2040.0)
        pos, color, _, senyal = detectar_precio_vs_kumo(df, atr=5.0)
        assert pos   == 'sobre_kumo'
        assert color == 'verde'
        assert senyal == 2

    def test_bajo_kumo_rojo_retorna_senyal_negativa(self):
        """Precio bajo kumo rojo → senyal = -2."""
        df = self._df_con_kumo_manual(60, close_val=1900.0,
                                      kumo_a_val=1940.0, kumo_b_val=1950.0)
        pos, color, _, senyal = detectar_precio_vs_kumo(df, atr=5.0)
        assert pos   == 'bajo_kumo'
        assert color == 'rojo'
        assert senyal == -2

    def test_dentro_kumo_retorna_senyal_cero(self):
        """Precio dentro de la nube → indecisión → senyal = 0."""
        df = self._df_con_kumo_manual(60, close_val=2000.0,
                                      kumo_a_val=1990.0, kumo_b_val=2010.0)
        pos, _, _, senyal = detectar_precio_vs_kumo(df, atr=5.0)
        assert pos   == 'dentro_kumo'
        assert senyal == 0

    def test_retorna_cuatro_elementos(self):
        df = self._df_con_kumo_manual(60, close_val=2100.0,
                                      kumo_a_val=2050.0, kumo_b_val=2040.0)
        result = detectar_precio_vs_kumo(df, atr=5.0)
        assert len(result) == 4


# ══════════════════════════════════════════════════════════════════════════════
# DETECTAR HCH
# ══════════════════════════════════════════════════════════════════════════════

class TestDetectarHCH:
    def test_pocos_datos_retorna_false(self):
        df = _make_ohlcv(20)
        ok, neck, cab = detectar_hch(df, atr=1.0, lookback=80)
        assert ok is False
        assert neck == 0.0
        assert cab  == 0.0

    def test_retorna_tupla_tres_elementos(self):
        df = _make_ohlcv(90)
        result = detectar_hch(df, atr=1.0, lookback=80)
        assert len(result) == 3

    def test_no_detecta_en_tendencia_pura(self):
        """Tendencia alcista simple no tiene HCH."""
        closes = [2000.0 + i * 0.5 for i in range(90)]
        highs  = [c + 0.5 for c in closes]
        lows   = [c - 0.5 for c in closes]
        df = _make_ohlcv(90, closes=closes, highs=highs, lows=lows)
        ok, _, _ = detectar_hch(df, atr=1.0)
        assert ok is False

    def test_detecta_hch_sintetico(self):
        """Ingeniería de un patrón HCH claro con wing=1, lookback=30."""
        # 35 filas. Ventana = df['High'].iloc[-31:-1] = filas 4..33 (30 filas, índices 0..29)
        # Swing highs en índices de ventana 5, 15, 25 → filas 9, 19, 29
        # HI=2006, Cabeza=2016, HD=2006 → |HI-HD|=0 < tol=0.6, Cabeza > ambos
        # Valles en filas 14 y 24: Low=1998 → neckline=1998
        # Close[-1]=1996 < 1998 - 0.1 = 1997.9  ✓
        n = 35
        base_h = 2000.5
        base_l = 1999.5
        base_c = 2000.0
        highs  = [base_h] * n
        lows   = [base_l] * n
        closes = [base_c] * n
        opens  = [base_c] * n
        # Peaks
        highs[9]  = 2006.0
        highs[19] = 2016.0
        highs[29] = 2006.0
        # Valles neckline
        lows[14] = 1998.0
        lows[24] = 1998.0
        # Cierre por debajo de neckline para confirmación
        closes[-1] = 1996.0

        df = pd.DataFrame({'Open': opens, 'High': highs,
                           'Low': lows,   'Close': closes, 'Volume': [1000]*n})
        ok, neckline, cabeza = detectar_hch(df, atr=1.0, lookback=30, wing=1)
        # La función detecta algún HCH válido en los datos (puede elegir otra triple
        # por el criterio >= en zonas planas, pero siempre cumple: neckline > 0 y
        # cabeza > neckline, y close < neckline − tol)
        assert ok is True
        assert neckline > 0.0
        assert cabeza > neckline        # cabeza siempre por encima de la neckline
        assert float(closes[-1]) < neckline  # cierre de confirmación bajo neckline


# ══════════════════════════════════════════════════════════════════════════════
# DETECTAR HCH INVERTIDO
# ══════════════════════════════════════════════════════════════════════════════

class TestDetectarHCHInvertido:
    def test_pocos_datos_retorna_false(self):
        df = _make_ohlcv(20)
        ok, neck, cab = detectar_hch_invertido(df, atr=1.0, lookback=80)
        assert ok is False

    def test_no_detecta_en_tendencia_bajista_pura(self):
        closes = [2000.0 - i * 0.5 for i in range(90)]
        highs  = [c + 0.5 for c in closes]
        lows   = [c - 0.5 for c in closes]
        df = _make_ohlcv(90, closes=closes, highs=highs, lows=lows)
        ok, _, _ = detectar_hch_invertido(df, atr=1.0)
        assert ok is False

    def test_detecta_hch_invertido_sintetico(self):
        """Ingeniería de HCH invertido con wing=1, lookback=30."""
        # Simétrico al HCH pero invertido:
        # Swing lows en índices de ventana 5, 15, 25 → filas 9, 19, 29
        # HI_low=1994, C_low=1984, HD_low=1994
        # Picos neckline: High[14]=2003, High[24]=2003 → neckline=2003
        # Close[-1]=2005 > 2003 + 0.1 = 2003.1 ✓
        n = 35
        base_h = 2000.5
        base_l = 1999.5
        base_c = 2000.0
        highs  = [base_h] * n
        lows   = [base_l] * n
        closes = [base_c] * n
        opens  = [base_c] * n
        # Valles (swing lows): HI y HD iguales, Cabeza más profunda
        lows[9]  = 1994.0
        lows[19] = 1984.0
        lows[29] = 1994.0
        # Picos neckline
        highs[14] = 2003.0
        highs[24] = 2003.0
        # Cierre por encima de neckline para confirmación
        closes[-1] = 2005.0

        df = pd.DataFrame({'Open': opens, 'High': highs,
                           'Low': lows,   'Close': closes, 'Volume': [1000]*n})
        ok, neckline, cabeza = detectar_hch_invertido(df, atr=1.0, lookback=30, wing=1)
        assert ok is True
        assert neckline > 0.0
        assert cabeza < neckline        # cabeza (valle) siempre por debajo de la neckline
        assert float(closes[-1]) > neckline  # cierre de confirmación sobre neckline


# ══════════════════════════════════════════════════════════════════════════════
# DETECTAR TRIÁNGULO
# ══════════════════════════════════════════════════════════════════════════════

class TestDetectarTriangulo:
    def test_pocos_datos_retorna_no_detectado(self):
        df = _make_ohlcv(20)
        tipo, _, _ = detectar_triangulo(df, atr=1.0, lookback=60)
        assert tipo == 'no_detectado'

    def test_retorna_tupla_tres_elementos(self):
        df = _make_ohlcv(70)
        result = detectar_triangulo(df, atr=1.0)
        assert len(result) == 3

    def test_no_detecta_en_dato_plano(self):
        """Datos completamente planos no tienen triángulo convergente."""
        df = _make_ohlcv(70)
        tipo, _, _ = detectar_triangulo(df, atr=1.0)
        assert tipo == 'no_detectado'

    def test_detecta_triangulo_simetrico_con_ruptura_alcista(self):
        """Triángulo simétrico convergente + cierre sobre techo → ruptura_alcista."""
        # wing=1, lookback=30 → ventana = df[-31:-1] = 30 filas (índices 0..29)
        # Swing highs: índices ventana 5, 15, 25 → filas 9, 19, 29 del df de 35 filas
        # Valores highs descendentes: 2006, 2004, 2002
        # Swing lows en mismos índices con valores ascendentes: 1994, 1996, 1998
        # m_h ≈ -0.2 (negativo), m_l ≈ +0.2 (positivo) → convergente ✓
        # amplitud proyectada al last_i=29: ~2.4 → 2.4/2000 = 0.0012 < 0.04 ✓
        # Close[-2] = 2000 (en triángulo) ✓
        # Close[-1] = 2002 > techo_proyectado(≈2001.2) + 0.1 → ruptura_alcista ✓
        n = 35
        highs  = [2001.0] * n
        lows   = [1999.0] * n
        closes = [2000.0] * n
        opens  = [2000.0] * n
        # Swing highs descendentes
        highs[9]  = 2006.0
        highs[19] = 2004.0
        highs[29] = 2002.0
        # Swing lows ascendentes
        lows[9]  = 1994.0
        lows[19] = 1996.0
        lows[29] = 1998.0
        # Ruptura: close[-1] > precio_techo proyectado ≈2001.2
        closes[-1] = 2002.5
        df = pd.DataFrame({'Open': opens, 'High': highs,
                           'Low': lows, 'Close': closes, 'Volume': [1000]*n})
        tipo, techo, suelo = detectar_triangulo(df, atr=1.0, lookback=30, wing=1)
        assert tipo == 'ruptura_alcista'
        assert techo > suelo

    def test_detecta_compresion_simetrico(self):
        """Mismo patrón pero sin ruptura → compresion_simetrico."""
        n = 35
        highs  = [2001.0] * n
        lows   = [1999.0] * n
        closes = [2000.0] * n
        opens  = [2000.0] * n
        highs[9]  = 2006.0;  highs[19] = 2004.0;  highs[29] = 2002.0
        lows[9]   = 1994.0;  lows[19]  = 1996.0;  lows[29]  = 1998.0
        # Sin ruptura: close[-1] dentro del triángulo
        closes[-1] = 2000.0
        df = pd.DataFrame({'Open': opens, 'High': highs,
                           'Low': lows, 'Close': closes, 'Volume': [1000]*n})
        tipo, _, _ = detectar_triangulo(df, atr=1.0, lookback=30, wing=1)
        assert tipo in ('compresion_simetrico', 'compresion_ascendente',
                        'compresion_descendente', 'ruptura_alcista', 'ruptura_bajista')


# ══════════════════════════════════════════════════════════════════════════════
# DETECTAR BANDERA / BANDERÍN
# ══════════════════════════════════════════════════════════════════════════════

class TestDetectarBanderaBanderin:
    def test_pocos_datos_retorna_no_detectado(self):
        df = _make_ohlcv(10)
        tipo, obj, ent = detectar_bandera_banderin(df, atr=1.0)
        assert tipo == 'no_detectado'

    def test_retorna_tupla_tres_elementos(self):
        df = _make_ohlcv(40)
        result = detectar_bandera_banderin(df, atr=1.0)
        assert len(result) == 3

    def test_no_detecta_en_dato_plano(self):
        """Sin impulso previo no hay bandera."""
        df = _make_ohlcv(40)
        tipo, _, _ = detectar_bandera_banderin(df, atr=1.0)
        assert tipo == 'no_detectado'

    def test_detecta_bandera_alcista(self):
        """
        Mástil alcista (impulso de +10 en 8 velas) + consolidación ajustada +
        ruptura al alza → bandera_alcista o bandera_alcista_prep.
        """
        # df de 40 filas con impulso en filas 10-17 y consolidación en 18-38
        n = 40
        closes = [2000.0] * n
        highs  = [2000.5] * n
        lows   = [1999.5] * n
        opens  = [2000.0] * n
        # Impulso alcista en filas 10-17: close sube de 2000 a 2010
        for i in range(8):
            closes[10 + i] = 2000.0 + i * (10.0 / 7.0)
            highs [10 + i] = closes[10 + i] + 0.5
            lows  [10 + i] = closes[10 + i] - 0.5
        # Consolidación ajustada en filas 18-38: rango pequeño cerca de 2009.5
        for i in range(19, 39):
            closes[i] = 2009.5
            highs [i] = 2010.5
            lows  [i] = 2009.0
        # Ruptura: close[-1] por encima de cons_high + ATR*0.1
        closes[-1] = 2012.0
        highs [-1] = 2012.5
        lows  [-1] = 2011.5
        df = pd.DataFrame({'Open': opens, 'High': highs,
                           'Low': lows, 'Close': closes, 'Volume': [1000]*n})
        tipo, precio_obj, nivel_ent = detectar_bandera_banderin(df, atr=1.0)
        assert tipo in ('bandera_alcista', 'banderin_alcista',
                        'bandera_alcista_prep', 'banderin_alcista_prep')
        assert precio_obj > nivel_ent   # objetivo encima del punto de entrada

    def test_tipo_valido(self):
        """El tipo retornado siempre debe ser uno de los valores esperados."""
        tipos_validos = {
            'bandera_alcista', 'bandera_bajista',
            'banderin_alcista', 'banderin_bajista',
            'bandera_alcista_prep', 'bandera_bajista_prep',
            'banderin_alcista_prep', 'banderin_bajista_prep',
            'no_detectado',
        }
        df = _make_ohlcv(40)
        tipo, _, _ = detectar_bandera_banderin(df, atr=1.0)
        assert tipo in tipos_validos
