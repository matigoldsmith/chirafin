import requests
import datetime
import os

def get_exchange_rate(moneda, fecha_iso=None):
    """
    Obtiene el tipo de cambio para una moneda y fecha específica.
    Usa mindicador.cl para CLP/USD/EUR/UF y open.er-api.com como fallback para BRL y otras.
    """
    if not moneda or moneda == "CLP":
        return 1.0
    
    moneda = moneda.upper()
    if not fecha_iso:
        fecha_iso = datetime.datetime.now().strftime("%Y-%m-%d")

    # 1. Intentar con mindicador.cl (Muy preciso para Chile)
    mapa_mindicador = {
        "USD": "dolar",
        "EUR": "euro",
        "UF": "uf"
    }
    
    if moneda in mapa_mindicador:
        indicador = mapa_mindicador[moneda]
        try:
            dt = datetime.datetime.strptime(fecha_iso, "%Y-%m-%d")
            fecha_api = dt.strftime("%d-%m-%Y")
        except:
            fecha_api = datetime.datetime.now().strftime("%d-%m-%Y")
            
        url = f"https://mindicador.cl/api/{indicador}/{fecha_api}"
        
        try:
            response = requests.get(url, timeout=5)
            if response.status_code == 200:
                data = response.json()
                if data.get("serie"):
                    return float(data["serie"][0]["valor"])
        except:
            pass

    # 2. Fallback para BRL o si mindicador falló (API Internacional)
    # open.er-api.com es gratuita y no requiere API Key para consultas básicas
    try:
        # Nota: Trabajamos con la moneda base y buscamos su valor en CLP
        url_er = f"https://open.er-api.com/v6/latest/{moneda}"
        response = requests.get(url_er, timeout=5)
        if response.status_code == 200:
            data = response.json()
            if data.get("rates") and "CLP" in data["rates"]:
                return float(data["rates"]["CLP"])
    except Exception as e:
        print(f"⚠️ Error en API Internacional ({moneda}): {e}")

    # 3. Hardcoded fallbacks (promedios históricos) por si todo falla
    fallbacks = {
        "USD": 950.0,
        "BRL": 170.0,
        "EUR": 1020.0
    }
    return fallbacks.get(moneda, 1.0)
