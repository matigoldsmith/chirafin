#!/usr/bin/env python3
"""
FX Helper - Tipo de cambio automático para conversión a CLP
Fuentes:
  1. mindicador.cl  → USD y EUR (tasa oficial chilena, primario)
  2. open.er-api.com → BRL y otras monedas, respaldo para USD/EUR
Sin fallback hardcodeado: si ambas APIs fallan, lanza excepción.
"""
import requests
import time

# Cache en memoria: {cache_key: (rate, timestamp)}
_CACHE = {}
_CACHE_TTL = 3600  # 1 hora

# Mapa mindicador.cl: moneda → endpoint
_MINDICADOR = {
    'USD': 'https://mindicador.cl/api/dolar',
    'EUR': 'https://mindicador.cl/api/euro',
}


def _get_mindicador(currency: str):
    """Consulta mindicador.cl para USD o EUR → CLP. Retorna None si falla."""
    url = _MINDICADOR.get(currency.upper())
    if not url:
        return None
    try:
        r = requests.get(url, timeout=10)
        if r.status_code == 200:
            valor = r.json()['serie'][0]['valor']
            if valor and valor > 0:
                return float(valor)
    except Exception as e:
        print(f"⚠️ mindicador.cl error {currency}: {e}")
    return None


def _get_open_er(from_currency: str):
    """Consulta open.er-api.com para cualquier moneda → CLP. Retorna None si falla."""
    try:
        r = requests.get(
            f'https://open.er-api.com/v6/latest/{from_currency.upper()}',
            timeout=10
        )
        if r.status_code == 200:
            data = r.json()
            if data.get('result') == 'success':
                clp_rate = data.get('rates', {}).get('CLP')
                if clp_rate and clp_rate > 0:
                    return float(clp_rate)
    except Exception as e:
        print(f"⚠️ open.er-api error {from_currency}: {e}")
    return None


def get_fx_rate(from_currency: str, fecha=None):
    """
    Devuelve tipo de cambio from_currency → CLP.
    Lanza ValueError si no se puede obtener de ninguna fuente.
    fecha: ignorado (siempre tasa actual)
    """
    currency = from_currency.upper()
    if currency == 'CLP':
        return 1.0

    cache_key = f"{currency}_CLP"
    now = time.time()
    if cache_key in _CACHE:
        rate, ts = _CACHE[cache_key]
        if now - ts < _CACHE_TTL:
            return rate

    rate = None

    # 1. mindicador.cl (primario para USD y EUR)
    if currency in _MINDICADOR:
        rate = _get_mindicador(currency)
        if rate:
            print(f"✓ FX {currency}→CLP: {rate} (mindicador.cl)")

    # 2. open.er-api.com (primario para BRL/otros, respaldo para USD/EUR)
    if rate is None:
        rate = _get_open_er(currency)
        if rate:
            print(f"✓ FX {currency}→CLP: {rate} (open.er-api.com)")

    if rate is None:
        raise ValueError(
            f"No se pudo obtener tipo de cambio {currency}→CLP. "
            f"Ambas APIs fallaron (mindicador.cl + open.er-api.com)."
        )

    _CACHE[cache_key] = (rate, now)
    return rate


def convert_to_clp(monto: float, currency: str, fecha=None):
    """
    Convierte monto a CLP. Retorna (monto_clp, fx_rate).
    Lanza ValueError si no se puede obtener el tipo de cambio.
    """
    if not currency or currency.upper() == 'CLP':
        return int(round(monto)), 1.0

    fx = get_fx_rate(currency, fecha)
    monto_clp = int(round(monto * fx))
    return monto_clp, fx
