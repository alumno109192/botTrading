"""Rutas Flask para la sección Value Investing."""
from __future__ import annotations

import re
from collections import defaultdict

from flask import Blueprint, jsonify, render_template

from services.value_investing import (
    WATCHLIST,
    get_calendario_semanal,
    get_earnings_info,
    invalidar_cache,
)

vi_bp = Blueprint('value_investing', __name__)


def _orden_semana(label: str):
    if label == 'Esta semana':
        return (0, 0)
    if label == 'Próxima semana':
        return (1, 0)
    match = re.match(r'^En (\d+) semanas$', label or '')
    if match:
        return (2, int(match.group(1)))
    if label == 'Semana pasada':
        return (3, 0)
    return (4, 0)


@vi_bp.route('/value-investing')
@vi_bp.route('/value-investing/calendario')
def vi_calendario_page():
    return render_template('value_investing.html', vista_inicial='calendario')


@vi_bp.route('/value-investing/empresa/<ticker>')
def vi_empresa_page(ticker: str):
    return render_template('value_investing.html', vista_inicial='empresa', ticker_inicial=ticker.upper())


@vi_bp.route('/api/v1/vi/calendario')
def vi_api_calendario():
    empresas = get_calendario_semanal()

    grouped = defaultdict(list)
    for row in empresas:
        grouped[row.get('semana_label', 'Sin fecha confirmada')].append(row)

    semanas = []
    for label in sorted(grouped.keys(), key=_orden_semana):
        semanas.append({
            'label': label,
            'total': len(grouped[label]),
            'empresas': grouped[label],
        })

    categorias = [
        {
            'nombre': cat,
            'emoji': data['emoji'],
            'peso': data['peso'],
            'tickers': data['tickers'],
        }
        for cat, data in WATCHLIST.items()
    ]

    esta_semana = len([x for x in empresas if x.get('semana_label') == 'Esta semana'])
    proxima_semana = len([x for x in empresas if x.get('semana_label') == 'Próxima semana'])

    return jsonify({
        'semanas': semanas,
        'total_empresas': len(empresas),
        'categorias': categorias,
        'stats': {
            'total': len(empresas),
            'esta_semana': esta_semana,
            'proxima_semana': proxima_semana,
        },
    })


@vi_bp.route('/api/v1/vi/empresa/<ticker>')
def vi_api_empresa(ticker: str):
    data = get_earnings_info(ticker.upper())
    return jsonify(data)


@vi_bp.route('/api/v1/vi/refresh/<ticker>', methods=['POST'])
def vi_api_refresh(ticker: str):
    tk = ticker.upper()
    invalidar_cache(tk)
    data = get_earnings_info(tk)
    return jsonify(data)


@vi_bp.route('/api/v1/vi/watchlist')
def vi_api_watchlist():
    categorias = [
        {
            'nombre': cat,
            'emoji': data['emoji'],
            'peso': data['peso'],
            'tickers': data['tickers'],
        }
        for cat, data in WATCHLIST.items()
    ]
    return jsonify({'categorias': categorias, 'total_categorias': len(categorias)})
