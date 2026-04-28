"""Calcula y muestra BUY/SELL LIMIT exactos por TF."""
import sys; sys.path.insert(0, '.')

def rr(entry, sl, tp):
    d_riesgo = abs(entry - sl)
    d_profit = abs(entry - tp)
    return round(d_profit / d_riesgo, 1) if d_riesgo else 0

def calcular(tf, zrl, zrh, zsl, zsh, atr, lop=0.3, asm=1.5, tp1=2.0, tp2=3.5, tp3=5.5):
    sell_limit = round(zrl + (zrh - zrl) * (lop / 100 * 10), 2)
    buy_limit  = round(zsh - (zsh - zsl) * (lop / 100 * 10), 2)
    sl_venta   = round(sell_limit + atr * asm, 2)
    sl_compra  = round(buy_limit  - atr * asm, 2)
    tp1_v = round(sell_limit - atr * tp1, 2)
    tp2_v = round(sell_limit - atr * tp2, 2)
    tp3_v = round(sell_limit - atr * tp3, 2)
    tp1_c = round(buy_limit  + atr * tp1, 2)
    tp2_c = round(buy_limit  + atr * tp2, 2)
    tp3_c = round(buy_limit  + atr * tp3, 2)

    print(f"\n  -- {tf} " + "-"*38)
    riesgo_s = round(sl_venta - sell_limit, 1)
    riesgo_b = round(buy_limit - sl_compra, 1)
    print(f"  SELL LIMIT : ${sell_limit}   SL ${sl_venta}  (riesgo +${riesgo_s})")
    rr1s = rr(sell_limit, sl_venta, tp1_v)
    rr2s = rr(sell_limit, sl_venta, tp2_v)
    rr3s = rr(sell_limit, sl_venta, tp3_v)
    print(f"    TP1 ${tp1_v} R:R {rr1s}:1   TP2 ${tp2_v} R:R {rr2s}:1   TP3 ${tp3_v} R:R {rr3s}:1")
    print(f"  BUY  LIMIT : ${buy_limit}   SL ${sl_compra}  (riesgo -${riesgo_b})")
    rr1b = rr(buy_limit, sl_compra, tp1_c)
    rr2b = rr(buy_limit, sl_compra, tp2_c)
    rr3b = rr(buy_limit, sl_compra, tp3_c)
    print(f"    TP1 ${tp1_c} R:R {rr1b}:1   TP2 ${tp2_c} R:R {rr2b}:1   TP3 ${tp3_c} R:R {rr3b}:1")

print("=" * 60)
print("  GOLD -- NIVELES DE ORDEN CALCULADOS AHORA  (~$4,719)")
print("=" * 60)

calcular("4H",  zrl=4730.6, zrh=4744.1, zsl=4664.9, zsh=4678.4, atr=18.0,  lop=0.3, asm=1.2, tp1=2.0, tp2=3.5, tp3=5.5)
calcular("1H",  zrl=4703.4, zrh=4710.1, zsl=4693.8, zsh=4700.5, atr=8.34,  lop=0.3, asm=1.5, tp1=2.0, tp2=3.5, tp3=5.5)
calcular("15M", zrl=4704.4, zrh=4709.6, zsl=4699.6, zsh=4704.8, atr=5.21,  lop=0.3, asm=1.5, tp1=2.0, tp2=3.5, tp3=5.5)
calcular("5M",  zrl=4706.1, zrh=4709.0, zsl=4705.1, zsh=4708.0, atr=3.58,  lop=0.3, asm=1.5, tp1=2.0, tp2=3.0, tp3=4.5)

print("\n" + "=" * 60)
