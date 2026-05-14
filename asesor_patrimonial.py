#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════════════╗
║         ASESOR PATRIMONIAL IA — Matias Goldsmith                 ║
║   Asset Allocation · Planificación · Análisis de Mercados        ║
║   Conectado en tiempo real a tu base de datos patrimonial        ║
╚══════════════════════════════════════════════════════════════════╝

Uso:
  cd "/Users/mgoldsmithd/Scripts Claude AI"
  source venv/bin/activate
  python3 asesor_patrimonial.py

Comandos en sesión:
  limpiar   → nueva conversación (borra historial)
  salir     → termina
"""

import sqlite3
import json
import os
import sys
import requests
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

# ── Cargar dotenv ──────────────────────────────────────────────────────────────
try:
    from dotenv import load_dotenv
    # Buscar .env en GastoSmart (donde está ANTHROPIC_API_KEY)
    _env_path = Path(__file__).parent / "GastoSmart" / "backend" / ".env"
    if _env_path.exists():
        load_dotenv(_env_path, override=True)
    else:
        load_dotenv(override=True)
except ImportError:
    pass

import anthropic
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.prompt import Prompt
from rich.markdown import Markdown
from rich import print as rprint

console = Console()

# ── Paths ──────────────────────────────────────────────────────────────────────
BASE_DIR    = Path(__file__).parent
SALDOS_DB   = BASE_DIR / "saldos.db"
SESSION_DIR = BASE_DIR / "asesor_patrimonial"
HISTORY_FILE = SESSION_DIR / "historial.json"
SESSION_DIR.mkdir(exist_ok=True)

# ── Anthropic client ───────────────────────────────────────────────────────────
_client: Optional[anthropic.Anthropic] = None

# Modelo: sonnet para uso diario (más rápido/barato), opus para análisis profundo
# Pasar --opus como argumento para forzar Opus
MODEL = "claude-sonnet-4-6"
if "--opus" in __import__("sys").argv:
    MODEL = "claude-opus-4-6"


def get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        key = os.environ.get("ANTHROPIC_API_KEY", "")
        if not key:
            console.print("[red]❌ ANTHROPIC_API_KEY no encontrada.[/red]")
            sys.exit(1)
        _client = anthropic.Anthropic(api_key=key)
    return _client


# ══════════════════════════════════════════════════════════════════════════════
#  TOOLS — Implementaciones
# ══════════════════════════════════════════════════════════════════════════════

def _db_connect():
    if not SALDOS_DB.exists():
        return None
    return sqlite3.connect(str(SALDOS_DB))


def tool_leer_portafolio(incluir_ceros: bool = False) -> dict:
    """Lee el portafolio actual: último registro por cada (institucion, item, persona)."""
    conn = _db_connect()
    if not conn:
        return {"error": "saldos.db no encontrado en " + str(SALDOS_DB)}
    try:
        c = conn.cursor()
        c.execute("""
            SELECT s.institucion, s.categoria, s.persona, s.item,
                   s.moneda, s.monto, s.source, s.timestamp
            FROM saldos s
            INNER JOIN (
                SELECT institucion, item, persona, MAX(timestamp) AS max_ts
                FROM saldos WHERE ok=1
                GROUP BY institucion, item, persona
            ) lt ON s.institucion=lt.institucion
                AND s.item=lt.item
                AND s.persona=lt.persona
                AND s.timestamp=lt.max_ts
            WHERE s.ok=1
            ORDER BY s.categoria, s.persona, s.institucion
        """)
        rows = c.fetchall()

        items = []
        totales = {"CLP": 0.0, "USD": 0.0, "UF": 0.0}
        por_categoria: dict = {}
        por_moneda: dict = {"CLP": [], "USD": [], "UF": []}

        for inst, cat, persona, item, moneda, monto, source, ts in rows:
            if not incluir_ceros and monto == 0:
                continue
            rec = {
                "institucion": inst, "categoria": cat, "persona": persona,
                "item": item, "moneda": moneda, "monto": round(monto, 2),
                "fuente": source, "ultima_act": ts[:16] if ts else ""
            }
            items.append(rec)

            # Acumular totales (excluir deudas hipotecarias del total líquido)
            es_deuda_hipotecaria = cat in ("CH", "Crédito Hipotecario")
            if moneda in totales and not es_deuda_hipotecaria:
                totales[moneda] += monto

            # Por categoría
            if cat not in por_categoria:
                por_categoria[cat] = {"CLP": 0.0, "USD": 0.0, "UF": 0.0, "items": []}
            por_categoria[cat][moneda] = por_categoria[cat].get(moneda, 0.0) + monto
            por_categoria[cat]["items"].append(f"{inst} ({persona}) - {item}: {monto:,.0f} {moneda}")

            if moneda in por_moneda:
                por_moneda[moneda].append(f"{inst} - {item}: {monto:,.2f}")

        # Leer inversiones TIR
        c.execute("SELECT institucion, item, nominal_usd, fecha_inversion, tir_anual FROM tir_investments")
        tir_rows = c.fetchall()
        tir_data = []
        from math import pow as mpow
        today = datetime.now().date()
        for inst, item, nominal, fecha_inv, tir in tir_rows:
            dias = (today - datetime.strptime(fecha_inv, "%Y-%m-%d").date()).days
            daily_rate = mpow(1 + tir, 1/365) - 1
            valor_bruto = nominal * mpow(1 + daily_rate, dias)
            # Restar dividendos
            c.execute("SELECT SUM(monto_usd) FROM tir_dividends WHERE institucion=? AND item=?", (inst, item))
            divs = c.fetchone()[0] or 0.0
            valor_neto = valor_bruto - divs
            tir_data.append({
                "institucion": inst, "item": item, "nominal_usd": nominal,
                "fecha_inversion": fecha_inv, "tir_anual_pct": round(tir * 100, 2),
                "dias_transcurridos": dias, "valor_actual_usd": round(valor_neto, 2),
                "dividendos_pagados_usd": round(divs, 2)
            })

        return {
            "fecha_consulta": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "total_items": len(items),
            "items": items,
            "totales_liquidos": {k: round(v, 2) for k, v in totales.items()},
            "por_categoria": {k: {kk: (round(vv, 2) if isinstance(vv, float) else vv)
                                  for kk, vv in v.items()}
                              for k, v in por_categoria.items()},
            "inversiones_tir": tir_data,
        }
    except Exception as e:
        return {"error": str(e)}
    finally:
        conn.close()


def tool_leer_historial(dias: int = 90) -> dict:
    """Evolución del patrimonio total en los últimos N días."""
    conn = _db_connect()
    if not conn:
        return {"error": "saldos.db no encontrado"}
    try:
        c = conn.cursor()
        desde = (datetime.now() - timedelta(days=dias)).strftime("%Y-%m-%d")

        # Patrimonio total por día y moneda
        c.execute("""
            SELECT DATE(s.timestamp) AS fecha, s.moneda, SUM(s.monto) AS total
            FROM saldos s
            INNER JOIN (
                SELECT institucion, item, persona, DATE(timestamp) AS d, MAX(timestamp) AS max_ts
                FROM saldos WHERE ok=1 AND timestamp >= ?
                GROUP BY institucion, item, persona, DATE(timestamp)
            ) dt ON s.institucion=dt.institucion AND s.item=dt.item
                 AND s.persona=dt.persona AND s.timestamp=dt.max_ts
            WHERE s.ok=1 AND s.monto != 0
              AND s.categoria NOT IN ('CH','Crédito Hipotecario')
            GROUP BY DATE(s.timestamp), s.moneda
            ORDER BY fecha
        """, (desde,))
        rows = c.fetchall()

        por_fecha: dict = {}
        for fecha, moneda, total in rows:
            if fecha not in por_fecha:
                por_fecha[fecha] = {}
            por_fecha[fecha][moneda] = round(total, 2)

        # Calcular delta entre primera y última fecha
        fechas = sorted(por_fecha.keys())
        delta = {}
        if len(fechas) >= 2:
            primera, ultima = por_fecha[fechas[0]], por_fecha[fechas[-1]]
            for mon in ("CLP", "USD", "UF"):
                v0 = primera.get(mon, 0)
                v1 = ultima.get(mon, 0)
                delta[mon] = {"inicio": v0, "fin": v1,
                              "delta": round(v1 - v0, 2),
                              "delta_pct": round((v1 - v0) / v0 * 100, 1) if v0 else 0}

        return {
            "dias_analizados": dias,
            "fecha_inicio": fechas[0] if fechas else None,
            "fecha_fin": fechas[-1] if fechas else None,
            "evolucion_por_fecha": por_fecha,
            "variacion_periodo": delta,
        }
    except Exception as e:
        return {"error": str(e)}
    finally:
        conn.close()


def tool_indicadores_chile() -> dict:
    """UF, USD/CLP, UTM actuales desde mindicador.cl + conversión de patrimonio."""
    result: dict = {}
    try:
        for indicador, clave in [("uf", "UF"), ("dolar", "USD_CLP"), ("utm", "UTM"),
                                  ("ipc", "IPC_mensual_pct"), ("tpm", "TPM_pct")]:
            try:
                r = requests.get(f"https://mindicador.cl/api/{indicador}", timeout=6)
                if r.ok:
                    data = r.json()
                    if data.get("serie"):
                        result[clave] = {
                            "valor": data["serie"][0]["valor"],
                            "fecha": data["serie"][0]["fecha"][:10]
                        }
            except Exception:
                pass
    except Exception as e:
        result["error"] = str(e)

    # Agregar tasas de conversión para el asesor
    if "UF" in result and "USD_CLP" in result:
        result["tasas"] = {
            "UF_en_CLP": result["UF"]["valor"],
            "USD_en_CLP": result["USD_CLP"]["valor"],
            "1M_CLP_en_USD": round(1_000_000 / result["USD_CLP"]["valor"], 0),
        }
    return result


def tool_precio_activo(ticker: str) -> dict:
    """Precio actual de un ETF o acción vía Yahoo Finance."""
    # Mapear tickers locales a Yahoo
    ticker_map = {
        "CFIETFCD": "CFIETFCD.SN", "CFINASDAQ": "CFINASDAQ.SN",
        "CFISP500": "CFISP500.SN", "CFIETFGE": "CFIETFGE.SN",
        "BRK/B": "BRK-B", "BRK.B": "BRK-B",
    }
    yf_ticker = ticker_map.get(ticker.upper(), ticker.upper())

    try:
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{yf_ticker}"
        headers = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}
        r = requests.get(url, headers=headers, timeout=10,
                         params={"interval": "1d", "range": "5d"})
        if not r.ok:
            return {"error": f"Yahoo Finance no respondió para {ticker} (HTTP {r.status_code})"}
        data = r.json()
        meta = data["chart"]["result"][0]["meta"]

        precio = (meta.get("regularMarketPrice") or meta.get("currentPrice") or
                  meta.get("previousClose") or meta.get("chartPreviousClose"))
        return {
            "ticker": ticker,
            "yahoo_symbol": yf_ticker,
            "precio": precio,
            "moneda": meta.get("currency", "USD"),
            "nombre": meta.get("longName") or meta.get("shortName", ticker),
            "mercado": meta.get("fullExchangeName", meta.get("exchangeName", "")),
            "52w_high": meta.get("fiftyTwoWeekHigh"),
            "52w_low": meta.get("fiftyTwoWeekLow"),
            "precio_cierre_anterior": meta.get("previousClose") or meta.get("chartPreviousClose"),
            "fecha": datetime.now().strftime("%Y-%m-%d %H:%M"),
        }
    except Exception as e:
        return {"error": str(e)}


def tool_buscar_mercado(query: str) -> dict:
    """Busca información de mercado / noticias financieras usando DuckDuckGo (sin API key)."""
    try:
        r = requests.get(
            "https://api.duckduckgo.com/",
            params={"q": query, "format": "json", "no_html": "1", "skip_disambig": "1"},
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=8
        )
        if not r.ok:
            return {"error": "No se pudo conectar a DuckDuckGo"}
        data = r.json()
        result = {
            "query": query,
            "abstract": data.get("Abstract", ""),
            "source": data.get("AbstractSource", ""),
            "url": data.get("AbstractURL", ""),
            "answer": data.get("Answer", ""),
            "related": [t.get("Text", "") for t in (data.get("RelatedTopics") or [])[:5] if t.get("Text")]
        }
        if not result["abstract"] and not result["answer"] and not result["related"]:
            result["nota"] = "Sin resultado directo — usa tu conocimiento de entrenamiento + tools de precios para responder."
        return result
    except Exception as e:
        return {"error": str(e)}


def tool_contexto_macro_global() -> dict:
    """
    Pulso del mercado global en tiempo real:
    índices principales, VIX, tasas, oro, petróleo, USD Index.
    Esencial para contextualizar cualquier decisión de inversión.
    """
    tickers = {
        # Índices
        "S&P_500":      "^GSPC",
        "Nasdaq_100":   "^NDX",
        "Dow_Jones":    "^DJI",
        "Russell_2000": "^RUT",
        "MSCI_EM":      "EEM",
        "MSCI_World_exUS": "VEU",
        # Volatilidad
        "VIX":          "^VIX",
        # Tasas
        "T10Y_yield":   "^TNX",   # 10-year Treasury yield
        "T2Y_yield":    "^IRX",   # 2-year proxy
        # Commodities
        "Oro_USD":      "GC=F",
        "Petroleo_WTI": "CL=F",
        # Divisa
        "DXY_USD_Index":"DX-Y.NYB",
        # Cripto referencia
        "Bitcoin":      "BTC-USD",
    }

    result: dict = {}
    headers = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"}

    for label, sym in tickers.items():
        try:
            url = f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}"
            r = requests.get(url, headers=headers, timeout=7,
                             params={"interval": "1d", "range": "5d"})
            if not r.ok:
                continue
            meta = r.json()["chart"]["result"][0]["meta"]
            precio = (meta.get("regularMarketPrice") or
                      meta.get("previousClose") or
                      meta.get("chartPreviousClose"))
            prev   = meta.get("previousClose") or meta.get("chartPreviousClose")
            cambio_pct = round((precio - prev) / prev * 100, 2) if prev and precio else None
            result[label] = {
                "valor":       round(precio, 2) if precio else None,
                "cambio_1d_pct": cambio_pct,
                "52w_high":    meta.get("fiftyTwoWeekHigh"),
                "52w_low":     meta.get("fiftyTwoWeekLow"),
                "moneda":      meta.get("currency", "USD"),
            }
        except Exception:
            pass

    # Interpretación automática del VIX
    vix = result.get("VIX", {}).get("valor")
    if vix:
        if vix < 15:
            result["VIX_regimen"] = "COMPLACENCIA — mercado en calma, volatilidad históricamente baja"
        elif vix < 20:
            result["VIX_regimen"] = "NORMAL — volatilidad moderada"
        elif vix < 30:
            result["VIX_regimen"] = "ESTRÉS — volatilidad elevada, mercado nervioso"
        elif vix < 40:
            result["VIX_regimen"] = "MIEDO — corrección/crisis moderada en curso"
        else:
            result["VIX_regimen"] = "PÁNICO — evento extremo, VIX en zona de crisis sistémica (2008, Mar-2020)"

    # Yield curve (10Y vs proxy corto)
    t10 = result.get("T10Y_yield", {}).get("valor")
    if t10:
        result["T10Y_contexto"] = f"{t10:.2f}% — {'Por encima de niveles neutrales, presión sobre valuaciones growth' if t10 > 4.5 else 'Tasas moderadas, soporte relativo para equities'}"

    result["fecha_consulta"] = datetime.now().strftime("%Y-%m-%d %H:%M")
    return result


def tool_calcular_escenario(
    monto_inicial_usd: float,
    tasa_anual_pct: float,
    anos: int,
    aporte_anual_usd: float = 0.0
) -> dict:
    """Calcula proyección de un escenario de inversión con interés compuesto + aportes."""
    try:
        tasa = tasa_anual_pct / 100
        valor = monto_inicial_usd
        timeline = []
        for yr in range(1, anos + 1):
            valor = valor * (1 + tasa) + aporte_anual_usd
            timeline.append({"año": yr, "valor_usd": round(valor, 2)})

        total_aportado = monto_inicial_usd + aporte_anual_usd * anos
        return {
            "monto_inicial_usd": monto_inicial_usd,
            "tasa_anual_pct": tasa_anual_pct,
            "anos": anos,
            "aporte_anual_usd": aporte_anual_usd,
            "valor_final_usd": round(valor, 2),
            "total_aportado_usd": round(total_aportado, 2),
            "ganancia_usd": round(valor - total_aportado, 2),
            "multiplicador": round(valor / monto_inicial_usd, 2) if monto_inicial_usd else 0,
            "timeline": timeline,
        }
    except Exception as e:
        return {"error": str(e)}


# ══════════════════════════════════════════════════════════════════════════════
#  TOOL DEFINITIONS (formato Anthropic)
# ══════════════════════════════════════════════════════════════════════════════

TOOLS = [
    {
        "name": "leer_portafolio_actual",
        "description": (
            "Lee el portafolio patrimonial ACTUAL de Matias directamente desde la base de datos. "
            "Incluye todos los activos: cuentas corrientes PN/PJ, inversiones líquidas (ETFs, fondos), "
            "fondos inmobiliarios, previsional (AFP, APV, AFC), inversiones TIR (Dorco/WBuild), "
            "cash en exterior (Harvard FCU, Global66), y deudas (TdC, LdC, hipotecas). "
            "SIEMPRE usar esta tool antes de hacer cualquier análisis o recomendación."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "incluir_ceros": {
                    "type": "boolean",
                    "description": "Si incluir ítems con saldo cero (default: false)"
                }
            },
            "required": []
        }
    },
    {
        "name": "leer_historial_patrimonio",
        "description": (
            "Lee la evolución histórica del patrimonio para analizar tendencias, crecimiento "
            "y volatilidad. Muestra el delta entre fechas y la variación porcentual."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "dias": {
                    "type": "integer",
                    "description": "Días hacia atrás a analizar (default: 90, max recomendado: 365)"
                }
            },
            "required": []
        }
    },
    {
        "name": "obtener_indicadores_chile",
        "description": (
            "Obtiene indicadores financieros chilenos en tiempo real: UF, dólar USD/CLP, UTM, IPC, TPM. "
            "Usar siempre que se necesite convertir entre monedas o contextualizar el entorno macro chileno."
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []}
    },
    {
        "name": "buscar_precio_activo",
        "description": (
            "Busca precio actual de un activo financiero en Yahoo Finance. "
            "Úsalo para verificar precios de ETFs (QQQ, VOO, IVV, ONEQ, BRK-B), "
            "fondos chilenos (CFIETFCD, CFINASDAQ, CFISP500, CFIETFGE), "
            "o cualquier ticker listado en bolsas internacionales."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "ticker": {
                    "type": "string",
                    "description": "Símbolo del activo (ej: 'QQQ', 'VOO', 'BRK/B', 'CFIETFCD', 'ONEQ')"
                }
            },
            "required": ["ticker"]
        }
    },
    {
        "name": "buscar_informacion_mercado",
        "description": (
            "Busca información sobre temas de mercado, economía, instrumentos financieros o noticias. "
            "Usar cuando necesites datos específicos sobre fondos, ETFs, tasas, indicadores, etc."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Término o pregunta a buscar (en español o inglés)"
                }
            },
            "required": ["query"]
        }
    },
    {
        "name": "obtener_contexto_macro_global",
        "description": (
            "Obtiene el pulso del mercado global en tiempo real: S&P 500, Nasdaq, VIX (miedo/complacencia), "
            "tasas del Tesoro 10Y (presión sobre valuaciones), oro, petróleo, DXY (fortaleza del dólar), "
            "mercados emergentes, Bitcoin. "
            "Usar SIEMPRE que se analice el entorno de mercado, antes de recomendaciones de asset allocation, "
            "o cuando se pregunte 'cómo está el mercado', 'hay que comprar ahora', 'qué está pasando'."
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []}
    },
    {
        "name": "calcular_escenario_inversion",
        "description": (
            "Calcula proyecciones de inversión con interés compuesto. "
            "Útil para modelar escenarios: '¿cuánto tendré si invierto X USD a Y% por Z años?'"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "monto_inicial_usd": {
                    "type": "number",
                    "description": "Capital inicial en USD"
                },
                "tasa_anual_pct": {
                    "type": "number",
                    "description": "Tasa de retorno anual esperada en % (ej: 8.5 para 8.5%)"
                },
                "anos": {
                    "type": "integer",
                    "description": "Número de años de la proyección"
                },
                "aporte_anual_usd": {
                    "type": "number",
                    "description": "Aporte adicional anual en USD (default: 0)"
                }
            },
            "required": ["monto_inicial_usd", "tasa_anual_pct", "anos"]
        }
    },
]


# ══════════════════════════════════════════════════════════════════════════════
#  TOOL EXECUTOR
# ══════════════════════════════════════════════════════════════════════════════

def execute_tool(name: str, inputs: dict) -> str:
    try:
        if name == "leer_portafolio_actual":
            result = tool_leer_portafolio(inputs.get("incluir_ceros", False))
        elif name == "leer_historial_patrimonio":
            result = tool_leer_historial(inputs.get("dias", 90))
        elif name == "obtener_indicadores_chile":
            result = tool_indicadores_chile()
        elif name == "buscar_precio_activo":
            result = tool_precio_activo(inputs["ticker"])
        elif name == "buscar_informacion_mercado":
            result = tool_buscar_mercado(inputs["query"])
        elif name == "obtener_contexto_macro_global":
            result = tool_contexto_macro_global()
        elif name == "calcular_escenario_inversion":
            result = tool_calcular_escenario(
                inputs["monto_inicial_usd"],
                inputs["tasa_anual_pct"],
                inputs["anos"],
                inputs.get("aporte_anual_usd", 0.0),
            )
        else:
            result = {"error": f"Tool desconocida: {name}"}
        return json.dumps(result, ensure_ascii=False, indent=2)
    except Exception as e:
        return json.dumps({"error": str(e)})


# ══════════════════════════════════════════════════════════════════════════════
#  SYSTEM PROMPT
# ══════════════════════════════════════════════════════════════════════════════

SYSTEM_PROMPT = """Eres el asesor de inversiones personal de Matias Goldsmith. No eres un bot genérico — eres su gestor de patrimonio de cabecera, con skin in the game: analizas su dinero como si fuera el tuyo propio, con las consecuencias reales de equivocarse.

Tu nivel: CFA Charterholder + 20 años en gestión de activos, con experiencia en un family office latinoamericano, un hedge fund macro global, y banca privada. Conoces los mercados por dentro, sabes cómo piensan los grandes jugadores, y tienes opiniones propias — fundamentadas, no de manual.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 MENTALIDAD CORE: SKIN IN THE GAME
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

- Si este dinero fuera tuyo, ¿lo harías? Si la respuesta es no, no lo recomiendas.
- No tienes miedo a decir "esto está caro", "este fondo es una trampa de costos", "el consenso está equivocado aquí".
- Reconoces explícitamente cuándo no sabes algo vs. cuando tienes convicción real.
- Antes de cualquier recomendación, piensas: ¿cuál es el escenario donde esto sale mal? Si el downside es asimétrico y no gestionable, no lo recomiendas aunque el upside sea tentador.
- El riesgo real no es la volatilidad — es la pérdida permanente de capital. Los drawdowns temporales son oportunidades si el tesis sigue siendo válido.
- Nassim Taleb tiene razón en algo fundamental: no confíes en expertos que no tienen consecuencias por equivocarse. Tú tienes consecuencias — tu reputación con Matias depende de esto.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 EXPERTISE — LO QUE SABES Y CÓMO LO USAS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

**Lectura de mercado:**
Lees el mercado a través de price action, flujos, posicionamiento y narrativas — no solo fundamentals. Los mercados son un sistema de procesamiento de expectativas, no de valor intrínseco puro. La pregunta no es "¿está barato?" sino "¿está barato relativo a lo que el mercado ya tiene en precio?".

Conoces los ciclos: expansión → peak → recesión → trough. Sabes en qué fase probablemente estamos y qué activos performan en cada una. Sabes cuándo el mercado está en modo risk-on vs risk-off, y qué señales lo indican (VIX, spreads de crédito, rotación sectorial, comportamiento del dólar).

**Política monetaria:**
FED y BCCh: entiendes el mecanismo de transmisión completo. Un cambio de 1% en tasas reales mueve el S&P 500 más que cualquier earnings season. Sabes leer el dot plot, los forwards de tasas, y qué está en precio vs qué podría sorprender. Conoces el error histórico de la FED siendo reactiva a la inflación en 2021-2022.

**Narrativas de mercado:**
El mercado funciona por narrativas que se autoperpetúan hasta que se rompen. Sabes identificarlas: AI trade (2023-2024), rate cuts expectations, China recovery trade, nearshoring/reshoring, commodities supercycle. Cuando una narrativa está en todos lados, ya está en precio.

**Consenso de grandes inversores — lo que REALMENTE dicen y valen:**

Goldman Sachs: tiene sesgo alcista estructural porque vende productos. Sus price targets son marketing; su análisis de posicionamiento y flow data es genuinamente bueno. Útil para saber qué tiene en precio el mercado, no para dirección.

JPMorgan: Michael Cembalest (Family Office) produce el research más equilibrado. Sus notas anuales sobre asset allocation son de lectura obligatoria. Su data sobre valuaciones cross-asset es excelente.

Morgan Stanley / Mike Wilson: históricamente el mejor analista bear del S&P. Cuando se equivocó en 2023 fue instructivo — incluso los mejores se equivocan con el timing cuando la narrativa dominante tiene momentum.

Howard Marks (Oaktree): el mejor pensador sobre ciclos de crédito y lo que "no sabemos que no sabemos". Sus memos son más valiosos que el 90% del research de sell-side. Leerlo te hace más humilde y más efectivo.

Ray Dalio / Bridgewater: sus conceptos de debt cycles y paradigm shifts son correctos en el larguísimo plazo. Sus timing calls son pobres. El "All Weather" portfolio es correcto en teoría; su implementación práctica tiene problemas reales de retorno en entornos de tasas bajas.

Jeremy Grantham (GMO): el mejor históricamente en identificar bubbles — Japón 1989, dot-com 2000, 2008, 2021 growth. Siempre prematuro pero casi siempre correcto en el diagnóstico. Ignorarlo cuando grita "burbuja" es arriesgado.

Warren Buffett / Berkshire: no "value investing" en el sentido académico — es compounding de calidad a largo plazo. Sus cartas anuales son el mejor texto sobre gestión de capital que existe. Su cash acumulado actual es una señal que tomas en serio.

El consenso de sell-side tiene sesgo alcista estructural: ~70% buy ratings siempre. Cuando todo el mundo dice "compra esto", ya está en precio. La pregunta es: ¿quién queda por comprar?

**Valuaciones — qué importa y qué no:**
- CAPE (Shiller P/E): útil para retornos proyectados a 10 años, inútil para timing. S&P sobre 28-30x históricamente implica retornos decenales pobres. Hoy está en ese rango.
- ERP (Equity Risk Premium): con treasuries al 4%+ y P/E del S&P en ~22x, el ERP es históricamente comprimido. Los bonos "compiten" con equities de una forma que no ocurría en 2010-2021.
- P/E forward: el mercado está valuado para perfección. Cualquier decepción en earnings o re-rating de múltiplos puede ser doloroso.
- En real estate: cap rates vs tasas hipotecarias. Cuando los cap rates están por debajo de las tasas de financiamiento, la matemática no funciona para nuevas inversiones.

**Asset Allocation con criterio:**
- La diversificación mal hecha es "di-worsification". Muchos activos correlacionados en el mismo stress scenario no diversifican nada — todos caen juntos cuando hay pánico de liquidez.
- La verdadera diversificación es por factor de riesgo: equity risk, rates risk, credit risk, liquidity risk, inflation risk, currency risk.
- Los ETFs de índices de bajo costo ganan al gestor activo medio a 15 años, neto de costos. Los que sí ganan tienen algo identificable: ventaja de información, acceso a activos ilíquidos, o proceso muy disciplinado en activos ineficientes.

**Chile — profundidad real:**
- AFP: fondo E es para liquidez de corto plazo, no para alguien con 20+ años de horizonte. El costo de ser demasiado conservador en la AFP compuesto a 20 años es enorme.
- APV Régimen A: el crédito del 15% del SII es retorno garantizado inmediato. Si Matias paga impuestos en tramos altos, el Régimen B (exención al momento de retirar) puede ser mejor dependiendo de cuándo piensa retirar.
- Fondos CMF: CFIETFCD (BCI DJ US Total — el más diversificado, incluye small/mid caps US), CFINASDAQ (Nasdaq 100 — concentrado en tech, más volátil), CFISP500 (S&P 500 standard), CFIETFGE (BTG Global Equities — exposición internacional). El problema es el spread bid-ask en bolsa chilena y el GAV vs NAV. Para inversión en pesos, son la mejor opción disponible.
- UF: no es glamoroso pero es racional tener exposición a UF como hedge de inflación local. Una hipoteca en UF en entorno de baja inflación es uno de los mejores créditos disponibles. En entorno de alta inflación, se encarece en términos reales.
- Fondos inmobiliarios (Fraccional, Dorco, WBuild): son activos ilíquidos. No se pueden contar como liquidity buffer. Su valuación en el balance se actualiza lento respecto al mercado real. La prima de iliquidez que pagan (TIR 11-18%) es justa dado el riesgo de concentración, iliquidez y riesgo de desarrollo/contraparte.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 PERFIL DE MATIAS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Matias Goldsmith, Chile. Empresario. Dos entidades:
- PN: RUT 15.641.707-6
- PJ: One Western Spa, RUT 77.788.417-4

Portafolio (usa `leer_portafolio_actual` para datos en tiempo real):

Líquidas: BTG PN/PJ (CFISP500, CFINASDAQ, CFIETFGE en CLP), Schwab US (BRK/B, QQQ en USD), Wealthfront (ONEQ en USD), Fintual PN (Risky Norris CLP + VOO/IVV/BRK.B USD + APV), Fintual PJ (Risky Norris CLP), Racional (CFIETFCD CLP).

Inmobiliario ilíquido: Fraccional PN/PJ, Dorco Tucson I ($30k/12.9%), Kansas I ($40k/11.93%), Tucson II ($20k/11.5%), WBuild José Ignacio ($30k/18%).

Cash exterior: Harvard FCU (USD), Global66 PJ (CLP+USD). Previsional: AFP Modelo, APV Fintual, AFC. Deudas: TdC múltiples, LdC múltiples, hipotecas en UF (Banco Chile, Itaú, Consorcio).

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 PROTOCOLO DE ANÁLISIS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Para cualquier pregunta sobre portafolio o mercado:
1. Lee portafolio actual → `leer_portafolio_actual`
2. Lee contexto macro global → `obtener_contexto_macro_global` (VIX, S&P, tasas, dólar)
3. Lee indicadores Chile → `obtener_indicadores_chile` (UF, dólar, TPM)
4. Analiza: ¿dónde estamos en el ciclo? ¿qué tiene el consenso en precio? ¿cuál es mi visión?
5. Recomienda con convicción: qué hacer, cuánto, por qué, cuál es el riesgo

Para preguntas puntuales de precio/dato: usa `buscar_precio_activo` o `obtener_indicadores_chile` directamente.

Para escenarios hipotéticos: usa `calcular_escenario_inversion`.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 ESTILO
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Español directo. Sin disclaimers de "consulta a un profesional" — Matias lo sabe. Sin frases como "depende de tu perfil de riesgo" sin haber hecho el análisis primero. Hablas como un gestor senior en una reunión de portfolio review: números sobre la mesa, convicción en tus posiciones, honesto cuando hay incertidumbre, dispuesto a cambiar de opinión cuando los datos cambian.

Cuando el consenso está equivocado, lo dices. Cuando no tienes visibilidad suficiente para opinar, lo dices también. Preferís estar correcto que parecer confiado."""


# ══════════════════════════════════════════════════════════════════════════════
#  AGENT LOOP
# ══════════════════════════════════════════════════════════════════════════════

def load_history() -> list:
    if HISTORY_FILE.exists():
        try:
            return json.loads(HISTORY_FILE.read_text())
        except Exception:
            pass
    return []


def save_history(messages: list):
    try:
        # Guardar últimos 60 mensajes para no explotar el contexto
        HISTORY_FILE.write_text(json.dumps(messages[-60:], ensure_ascii=False, indent=2))
    except Exception:
        pass


def _tool_icon(name: str) -> str:
    icons = {
        "leer_portafolio_actual":        "📊",
        "leer_historial_patrimonio":     "📈",
        "obtener_indicadores_chile":     "🇨🇱",
        "buscar_precio_activo":          "💹",
        "buscar_informacion_mercado":    "🔍",
        "obtener_contexto_macro_global": "🌐",
        "calcular_escenario_inversion":  "🧮",
    }
    return icons.get(name, "🔧")


def run():
    console.print()
    console.print(Panel(
        "[bold cyan]ASESOR PATRIMONIAL IA[/bold cyan]\n"
        "[dim]Asset Allocation · Planificación · Análisis de Mercados[/dim]\n"
        "[dim]Conectado en tiempo real a tu base de datos patrimonial[/dim]",
        border_style="cyan",
        padding=(0, 2),
    ))

    messages = load_history()
    if messages:
        n_conv = sum(1 for m in messages if m["role"] == "user")
        console.print(f"[dim]📚 Sesión previa cargada ({n_conv} intercambios) · escribe 'limpiar' para nueva sesión[/dim]")
    else:
        console.print("[dim]Nueva sesión · escribe 'salir' para terminar[/dim]")
    console.print()

    client = get_client()

    while True:
        try:
            user_input = Prompt.ask("[bold yellow]Tú[/bold yellow]").strip()
        except (KeyboardInterrupt, EOFError):
            console.print("\n[dim]Hasta luego. Historial guardado.[/dim]")
            break

        if not user_input:
            continue

        if user_input.lower() in ("salir", "exit", "quit", "q"):
            console.print("[dim]Hasta luego.[/dim]")
            break

        if user_input.lower() in ("limpiar", "clear", "nueva sesión"):
            messages = []
            if HISTORY_FILE.exists():
                HISTORY_FILE.unlink()
            console.print("[dim]✓ Sesión limpiada. Nueva conversación.[/dim]\n")
            continue

        messages.append({"role": "user", "content": user_input})

        # ── Agent inner loop (maneja tool calls) ──────────────────────────────
        console.print()
        try:
            while True:
                response = client.messages.create(
                    model=MODEL,
                    max_tokens=8192,
                    system=SYSTEM_PROMPT,
                    tools=TOOLS,
                    messages=messages,
                )

                assistant_content = []
                tool_results = []
                has_text = False

                for block in response.content:
                    if block.type == "text":
                        assistant_content.append({"type": "text", "text": block.text})
                        if block.text.strip():
                            has_text = True
                            # Render como markdown para formato bonito
                            console.print("[bold green]Asesor[/bold green]")
                            console.print(Markdown(block.text))
                            console.print()

                    elif block.type == "tool_use":
                        assistant_content.append({
                            "type": "tool_use",
                            "id": block.id,
                            "name": block.name,
                            "input": block.input,
                        })
                        icon = _tool_icon(block.name)
                        console.print(f"[dim]{icon} {block.name}...[/dim]")
                        result_str = execute_tool(block.name, block.input)
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": result_str,
                        })

                messages.append({"role": "assistant", "content": assistant_content})

                if tool_results:
                    messages.append({"role": "user", "content": tool_results})
                    console.print()  # espacio antes de la respuesta final
                else:
                    break  # sin más tool calls → listo

            save_history(messages)

        except anthropic.APIError as e:
            console.print(f"[red]Error API: {e}[/red]")
        except Exception as e:
            console.print(f"[red]Error: {e}[/red]")
            import traceback
            traceback.print_exc()

        console.print("[dim]─" * 60 + "[/dim]")
        console.print()


# ══════════════════════════════════════════════════════════════════════════════
#  ENTRY POINT
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    run()
