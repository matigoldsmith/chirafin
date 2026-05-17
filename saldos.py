import os
import sys

# 1. Deshabilitar CPR (Cursor Position Request) para evitar bloqueos en terminales macOS/zsh
os.environ['PROMPT_TOOLKIT_NO_CPR'] = '1'

# 2. Monkeypatch preventivo para silenciar el warning de CPR que bloquea el terminal
try:
    # Engañamos a prompt_toolkit para que crea que el terminal no soporta CPR en la salida,
    # así evita el intento de petición y el warning consecuente.
    from prompt_toolkit.output.vt100 import Vt100_Output
    Vt100_Output.responds_to_cpr = property(lambda self: False)
except Exception:
    pass

import re
import signal
import string
import sqlite3
import subprocess
import datetime
import argparse
import logging
import questionary
from pathlib import Path
from playwright.sync_api import sync_playwright
from rich.console import Console
from rich.table import Table
from rich.text import Text
from rich.panel import Panel
from rich.rule import Rule
from rich import box as rich_box
import json
import threading
import urllib.request
import webbrowser
import imaplib
import email as _email_lib
import email.utils as _email_utils
from rich import print as rprint

# ── Fraccional (módulo separado) ────────────────────────────────────────────
try:
    from fraccional import (
        menu_actualizar  as _frac_menu_actualizar,
        menu_analizar    as _frac_menu_analizar,
        menu_parametros  as _frac_menu_parametros,
        menu_cuotas      as _frac_menu_cuotas,
    )
    _FRACCIONAL_AVAILABLE = True
except ImportError:
    _FRACCIONAL_AVAILABLE = False

_console = Console()

# ── Supabase Configuration ─────────────────────────────────────
# Mirror local SQLite to Supabase for the upcoming Web Dashboard
SUP_URL = "https://mbovlripktxzjqyerizg.supabase.co"
SUP_KEY = "sb_publishable_AZjgV0FBDO5SCKehvDe6EQ_Tn0vdbWD"

def _sync_supabase(table_name, list_of_dicts):
    """Sync a list of records to Supabase using REST API."""
    import ssl
    import json
    if not list_of_dicts: return

    url = f"{SUP_URL}/rest/v1/{table_name}"
    headers = {
        "apikey": SUP_KEY,
        "Authorization": f"Bearer {SUP_KEY}",
        "Content-Type": "application/json",
        "Prefer": "resolution=merge-duplicates"
    }

    try:
        data = json.dumps(list_of_dicts).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers=headers, method="POST")
        context = ssl._create_unverified_context()
        with urllib.request.urlopen(req, context=context) as response:
            if DEBUG: print(f"[Supabase] Sync successful to {table_name}: {response.getcode()}")
    except Exception as e:
        if DEBUG: print(f"[Supabase] Sync ERROR on {table_name}: {e}")


def _save_pago_tdc(record: dict):
    """Guarda un registro de pagos_tdc SOLO en Supabase (fuente única)."""
    try:
        _sync_supabase("pagos_tdc", [record])
    except Exception as e:
        print(f"[Supabase] pagos_tdc sync error: {e}")


def _migrate_pagos_tdc_to_supabase():
    """
    Migración retroactiva: copia todos los registros de pagos_tdc
    desde SQLite local a Supabase. Solo corre si Supabase está vacío.
    """
    # Verificar si Supabase ya tiene datos
    existing = _read_supabase("pagos_tdc", extra="&limit=1")
    if existing:
        return  # Ya hay datos en Supabase, no migrar

    # Leer desde SQLite
    try:
        conn = init_db()
        rows = conn.execute("""
            SELECT timestamp, institucion, card_number, card_name,
                   periodo_hasta, pagar_hasta, facturado_clp, pagado_clp,
                   facturado_usd, pagado_usd, no_facturado_clp
            FROM pagos_tdc
            ORDER BY timestamp ASC
        """).fetchall()
        conn.close()
    except Exception as e:
        print(f"[Migración] No se pudo leer SQLite pagos_tdc: {e}")
        return

    if not rows:
        return

    cols = ["timestamp", "institucion", "card_number", "card_name",
            "periodo_hasta", "pagar_hasta", "facturado_clp", "pagado_clp",
            "facturado_usd", "pagado_usd", "no_facturado_clp"]
    records = [dict(zip(cols, r)) for r in rows]

    try:
        _sync_supabase("pagos_tdc", records)
        print(f"[Migración] {len(records)} registros de pagos_tdc subidos a Supabase.")
    except Exception as e:
        print(f"[Migración] Error subiendo pagos_tdc a Supabase: {e}")


def _read_supabase(table_name, filters=None, select="*", extra=""):
    """Lee registros desde Supabase via REST API. Retorna lista de dicts.
    filters: dict col→val (eq.). extra: raw query string p.ej. '&order=timestamp.desc&limit=1'
    """
    import ssl, json, urllib.parse
    url = f"{SUP_URL}/rest/v1/{table_name}?select={select}"
    if filters:
        for k, v in filters.items():
            url += f"&{k}=eq.{urllib.parse.quote(str(v))}"
    if extra:
        url += extra
    headers = {
        "apikey": SUP_KEY,
        "Authorization": f"Bearer {SUP_KEY}",
        "Accept": "application/json",
    }
    try:
        req = urllib.request.Request(url, headers=headers, method="GET")
        context = ssl._create_unverified_context()
        with urllib.request.urlopen(req, context=context) as response:
            return json.loads(response.read().decode("utf-8"))
    except Exception as e:
        if DEBUG: print(f"[Supabase] Read ERROR on {table_name}: {e}")
        return []


def _sup_rpc(func_name, params=None):
    """Llama una función RPC de Supabase. Retorna lista de dicts."""
    import ssl, json
    url = f"{SUP_URL}/rest/v1/rpc/{func_name}"
    headers = {
        "apikey": SUP_KEY,
        "Authorization": f"Bearer {SUP_KEY}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    body = json.dumps(params or {}).encode("utf-8")
    try:
        req = urllib.request.Request(url, data=body, headers=headers, method="POST")
        context = ssl._create_unverified_context()
        with urllib.request.urlopen(req, context=context) as response:
            return json.loads(response.read().decode("utf-8"))
    except Exception as e:
        if DEBUG: print(f"[Supabase] RPC ERROR {func_name}: {e}")
        return []

class _ScraperLogBuffer:
    def __init__(self, orig):
        self._orig = orig
        self._lines = []
    def write(self, s):
        self._lines.append(s)
    def flush(self):
        pass
    def show(self):
        self._orig.write("".join(self._lines))
        self._orig.flush()
    def discard(self):
        self._lines.clear()


# ══════════════════════════════════════════════════════════════
# ARGUMENTOS DE LÍNEA DE COMANDOS
# ══════════════════════════════════════════════════════════════
parser = argparse.ArgumentParser(description="Consulta de saldos bancarios automatizada")
parser.add_argument("--debug", "-d", action="store_true", help="Verbose: muestra diagnósticos detallados (selectores, elementos, JS dumps)")
parser.add_argument("--headless", action="store_true", help="Forzar headless=True (por defecto es False)")
parser.add_argument("--no-video", action="store_true", help="No grabar video en caso de error")
parser.add_argument("--banks", "--bank", "-b", nargs="+", help="Ejecutar solo los bancos especificados (ej: santander wealthfront)")
parser.add_argument("--auto", action="store_true", help="Modo automatizado: salta todos los inputs manuales.")
parser.add_argument("--all", action="store_true", help="Ejecutar todos los scrapers automáticos.")
args, _unknown_args = parser.parse_known_args()

# Global flag para controlar interactividad
AUTOMATED = args.auto or not sys.stdin.isatty()

# Modo verbose — usar: if DEBUG: print("[SIGLA] detalle...")
DEBUG = args.debug

# Logger para debugging
logging.basicConfig(level=logging.INFO if DEBUG else logging.WARNING)
logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════
# BITWARDEN
# ══════════════════════════════════════════════════════════════

def bw_env():
    env = os.environ.copy()
    env["NODE_TLS_REJECT_UNAUTHORIZED"] = "0"
    return env

_SAVED_TERM_STATE = None  # Estado exacto del TTY al arrancar el script

def _save_terminal_state():
    """Guarda el estado exacto del terminal ANTES de que Playwright lo modifique.
    Debe llamarse al inicio de main(), antes de cualquier operación de Playwright.
    """
    global _SAVED_TERM_STATE
    try:
        import termios, sys
        _SAVED_TERM_STATE = termios.tcgetattr(sys.stdin.fileno())
    except Exception:
        pass

def _reset_terminal():
    """Restaura el terminal al estado exacto previo a Playwright.
    Usa el snapshot guardado por _save_terminal_state() + stty sane como refuerzo.
    """
    import sys, subprocess as _sp
    # 1. Restaurar estado EXACTO guardado al inicio (más confiable que adivinar flags)
    global _SAVED_TERM_STATE
    if _SAVED_TERM_STATE is not None:
        try:
            import termios
            # TCSAFLUSH descarta el buffer de stdin (evita que teclas acumuladas bloqueen questionary)
            termios.tcsetattr(sys.stdin.fileno(), termios.TCSAFLUSH, _SAVED_TERM_STATE)
        except Exception:
            pass
    # 2. stty sane como refuerzo (sin capture_output para que afecte el TTY real)
    try:
        _sp.run(["stty", "sane"], check=False)
    except Exception:
        pass

def bw_unlock():
    """Desbloquea Bitwarden y establece la sesión sin sincronizar automáticamente."""
    print("[KEY]  Desbloqueando Bitwarden...")
    master = subprocess.run(
        ["security", "find-generic-password", "-a", "bitwarden", "-s", "bitwarden-master", "-w"],
        capture_output=True, text=True
    ).stdout.strip()
    result = subprocess.run(
        ["bw", "unlock", master, "--raw"],
        capture_output=True, text=True, env=bw_env()
    )
    session = result.stdout.strip()
    if session:
        os.environ["BW_SESSION"] = session
        print("[OK]  Bitwarden desbloqueado")
    else:
        raise Exception("No se pudo desbloquear Bitwarden")

def bw_sync_manual():
    """Sincroniza Bitwarden manualmente (ejecutar si hay problemas con credenciales)."""
    print("[SYNC]  Sincronizando Bitwarden...")
    result = subprocess.run(["bw", "sync"], capture_output=True, text=True, env=bw_env())
    if result.returncode == 0:
        print("[OK]  Bitwarden sincronizado")
    else:
        print("[ERROR]  Error al sincronizar Bitwarden")
        print(result.stderr)

def wait_for_whatsapp_otp(timeout=60, poll=1.5):
    """
    Espera OTP de WhatsApp escrito por el Shortcut de iPhone vía iCloud Drive.
    Retorna el código como string, o None si hay timeout.

    Flujo:
      iPhone recibe OTP → Shortcut lo guarda en iCloud Drive → Mac sincroniza → Python lo lee
    """
    import time
    otp_file = Path.home() / "Library" / "Mobile Documents" / "com~apple~CloudDocs" / "otp_g66.txt"

    # Borrar archivo anterior si existe (de una ejecución previa)
    if otp_file.exists():
        otp_file.unlink()

    print(f"[OTP] Esperando código WhatsApp (timeout: {timeout}s)...", flush=True)
    elapsed = 0
    while elapsed < timeout:
        if otp_file.exists():
            code = otp_file.read_text().strip()
            if code:
                otp_file.unlink()  # Limpiar después de leer
                print(f"[OTP] [OK]  Código recibido: {code}", flush=True)
                return code
        time.sleep(poll)
        elapsed += poll

    print("[OTP] [WAIT]   Timeout esperando OTP de WhatsApp.", flush=True)
    return None


def bw_get(field, item_name):
    env = bw_env()
    result = subprocess.run(["bw", "get", field, item_name], capture_output=True, text=True, env=env)
    if "Session key is invalid" in result.stderr or not result.stdout.strip():
        bw_unlock()
        result = subprocess.run(["bw", "get", field, item_name], capture_output=True, text=True, env=bw_env())
    return result.stdout.strip()


# ══════════════════════════════════════════════════════════════
# ESTILOS UI
# ══════════════════════════════════════════════════════════════
# Orden jerárquico solicitado: A) Cash, B) Fondos líquidos, etc.
CAT_ORDER = [
    "Cash", 
    "Fondos líquidos", 
    "Fondos Inmobiliarios", 
    "Propiedades de Inversión", 
    "Inversión en startups", 
    "Fondos previsionales", 
    "Casa",
    "Créditos Hipotecarios"
]

# Globales para tracking de sesión
_LAST_TABLE_MAPPING = []  # Guarda los items en el orden de la última tabla mostrada

QUESTIONARY_STYLE = questionary.Style([
    ('qmark', 'fg:#5f87ff bold'),
    ('question', 'bold'),
    ('answer', 'fg:#5fafff bold'),
    ('pointer', 'fg:#5f87ff bold'),
    ('highlighted', 'fg:#eeeeee bold'),
    ('selected', 'fg:#5f87ff'),
    ('separator', 'fg:#666666'),
    ('instruction', 'fg:#888888'),
])

# Estilo naranja para el menú principal (top-level)
ORANGE_MENU_STYLE = questionary.Style([
    ('qmark',       'fg:#ff8700 bold'),
    ('question',    'bold'),
    ('answer',      'fg:#ff8700 bold'),
    ('pointer',     'fg:#ff8700 bold'),
    ('highlighted', 'fg:#ff8700 bold'),
    ('selected',    'fg:#ff8700'),
    ('separator',   'fg:#444444'),
    ('instruction', 'fg:#888888'),
])

def _clear_terminal_buffer():
    """Limpia el buffer del terminal y restaura el estado sano del TTY para evitar el error de CPR."""
    try:
        import sys
        import time
        import subprocess
        # 1. Flush de todos los buffers de salida
        sys.stdout.flush()
        sys.stderr.flush()
        # 2. Descartar cualquier input acumulado en stdin (teclas de Playwright, etc.)
        if sys.stdin.isatty():
            try:
                import termios
                termios.tcflush(sys.stdin.fileno(), termios.TCIFLUSH)
            except Exception:
                pass
        # 3. Restaurar estado sano del terminal (macOS/Linux)
        if sys.stdin.isatty():
            subprocess.run(["stty", "sane"], check=False)
        # 4. Pequeña pausa para estabilización
        time.sleep(0.3)
        # 5. Reset de atributos ANSI y asegurar que el cursor sea visible
        sys.stdout.write('\033[0m\x1b[?25h')
        sys.stdout.flush()
    except:
        pass

def cat_to_short(cat, inst=None, item=None):
    """Devuelve el nombre de display jerárquico según la solicitud del usuario."""
    cat_str = str(cat)
    # UNIFY ALL CASH VARIATIONS IMMEDIATELY
    if any(x in cat_str.upper() for x in ["CASH", "A) CASH", "CC ", "CC PJ", "CC PN", "CUENTAS CORRIENTES", "SAVINGS", "CHECKING"]):
        return "Cash"
        
    # Strip prefixes like "A) " more robustly for others
    cat_str = re.sub(r'^[A-Z]\)\s*', '', cat_str)
    
    c = cat_str.upper()
    i = str(inst).upper() if inst else ""
    
    # Pre-filtrar acentos para comparaciones más exactas
    c_no_accents = c.replace("Á", "A").replace("É", "E").replace("Í", "I").replace("Ó", "O").replace("Ú", "U")
    i_no_accents = i.replace("Á", "A").replace("É", "E").replace("Í", "I").replace("Ó", "O").replace("Ú", "U")
    item_no_accents = str(item).upper().replace("Á", "A").replace("É", "E").replace("Í", "I").replace("Ó", "O").replace("Ú", "U") if item else ""
    
    # 0. Salidas directas para categorías claras (evitar sobreescrituras)
    if "TAIHU" in item_no_accents or "TAIHU" in c_no_accents or "CASA" in item_no_accents:
        return "Casa"
    if "STARTUP" in c_no_accents or "WOPERTY" in c_no_accents or "WOPERTY" in i_no_accents:
        return "Inversión en startups"
    if "PROPIEDAD" in c_no_accents:
        return "Propiedades de Inversión"
    if "INMOBILIARI" in c_no_accents:
        return "Fondos Inmobiliarios"
        
    # 1. Cash: Unificar todas las variaciones de Cash
    if any(x in c_no_accents for x in ["CASH", "CC ", "CC PJ", "CC PN", "RECIBIR", "DEPOSITAR", "CUENTAS CORRIENTES", "SAVINGS", "CHECKING"]):
        return "Cash"
    
    # 2. Hipotecarios especiales con lógica por institución
    # Evitar atrapar Tarjetas y Líneas de Crédito aquí
    if ("CH" in c_no_accents or "HIPOT" in c_no_accents or "CREDITO" in c_no_accents) and not ("LINEA" in c_no_accents or "TARJETA" in c_no_accents or "LDC" in c_no_accents or "TDC" in c_no_accents):
        if "CONSORCIO" in i_no_accents:
            return "Casa"
        if "ITAU" in i_no_accents or "CHILE" in i_no_accents:
            return "Propiedades de Inversión"
        return "Créditos Hipotecarios"
        
    # 3. Fondos líquidos: Eliminado el 'INV' genérico que atrapaba otras Inversiones
    if ("LIQUID" in c_no_accents) or ("TDC" in c_no_accents) or ("LDC" in c_no_accents) or ("TARJETA" in c_no_accents) or ("LINEA" in c_no_accents):
        return "Fondos líquidos"
        
    # Extra: Atrapa Inversiones genéricas que no fueron a Startup ni a Propiedad
    if "INVERSION" in c_no_accents:
        return "Fondos líquidos"
    
    # 4. Fondos previsionales
    if "PREVISIONAL" in c_no_accents or "AFP" in c_no_accents or "AFC" in c_no_accents:
        return "Fondos previsionales"
    
    # 5. Mi casa
    if "CASA" in c_no_accents:
        return "Casa"
    
    return cat

def cat_to_persona(cat):
    """'CC PN' → 'PN', 'CC PJ' → 'PJ', 'TdC' → 'PN'"""
    return "PJ" if "PJ" in cat else "PN"

def _normalize_cat(cat_raw):
    """Mapea las categorías de la base de datos a los nuevos nombres jerárquicos."""
    return cat_to_short(cat_raw)

def _normalize_persona(cat_raw, persona_raw):
    if persona_raw:
        return persona_raw
    return "PJ" if "PJ" in cat_raw else "PN"

def format_rut(raw):
    """156417076 o 15641707-6 → 15.641.707-6"""
    clean = raw.replace(".", "").replace("-", "").strip().upper()
    if len(clean) < 2:
        return raw
    body, dv = clean[:-1], clean[-1]
    fmt = ""
    for i, ch in enumerate(reversed(body)):
        if i > 0 and i % 3 == 0:
            fmt = "." + fmt
        fmt = ch + fmt
    return fmt + "-" + dv

def parse_int(s):
    try:
        return int(str(s).strip().replace(".", "").replace(",", "").replace(" ", ""))
    except:
        return None

# ══════════════════════════════════════════════════════════════
# FORMATTERS & HELPERS
# ══════════════════════════════════════════════════════════════

def clean_monto(raw, moneda="CLP", is_manual=False):
    """Limpia un string de monto con inteligencia extrema para no multiplicar por 10."""
    if not raw: return "0"
    if isinstance(raw, (int, float)): 
        # Si es un float tipo 4300.0, lo convertimos a "4300" directamente
        f_val = float(raw)
        if f_val == int(f_val):
            return str(int(f_val))
        return str(f_val)
    
    s = str(raw).strip().replace(" ", "").replace("$", "").replace("UF", "")
    
    # 1. Limpieza de decimales de sistema (.0 o .00 al final)
    if "." in s and not "," in s:
        partes = s.split(".")
        if len(partes) == 2 and partes[1] in ("0", "00"):
            s = partes[0]

    # 2. Estilo Chileno (Manual o CLP/UF): Punto es miles, Coma es decimal
    if is_manual or moneda in ("CLP", "UF"):
        # Si tiene coma Y punto, el punto es miles (ej: 1.234,56)
        if "." in s and "," in s:
            return s.replace(".", "").replace(",", ".")
        # Si tiene varios puntos, son miles (ej: 1.000.000)
        if s.count(".") > 1:
            return s.replace(".", "")
        # Si tiene un solo punto (ej: 4.300 o 43.5)
        if "." in s:
            segmentos = s.split(".")
            ultimo_segmento = segmentos[-1]
            # Si hay exactamente 3 dígitos después, es miles chileno (4.300 -> 4300)
            if len(ultimo_segmento) == 3:
                return s.replace(".", "")
            # De lo contrario es decimal (43.5 -> 43.5)
            return s
        
        # Base: borrar puntos, cambiar comas
        return s.replace(".", "").replace(",", ".")
    else:
        # 3. Estilo USA (Automático USD): Coma es miles, Punto es decimal
        return s.replace(",", "")

def fmt_monto(monto):
    """Formatea monto a string con puntos de miles, sin decimales."""
    try:
        if monto is None or monto == 0: return "—"
        val = int(round(float(monto)))
        return f"{val:,}".replace(",", ".")
    except:
        return str(monto)

def _slog(tag: str, verb: str, msg: str = "") -> None:
    """Log estandarizado para scrapers: [TAG] verb     mensaje"""
    print(f"[{tag}] {verb:<8} {msg}", flush=True)


def _fmt_pagos_log(inst, card, fac_clp, pag_clp, fac_usd=None, pag_usd=None, no_fac_clp=None):
    """Log estandarizado para pagos TdC.
    pag_clp  = pago del período anterior (abs, histórico).
    no_fac_clp = pagos ya hechos contra el bill actual (abs) — usado para Δ pendiente.
    """
    def _clp(v):
        if v is None: return "$—"
        ival = int(round(float(v)))
        if ival < 0: return f"-${abs(ival):,}".replace(",", ".")
        return f"${ival:,}".replace(",", ".")
    def _usd(v):
        if v is None: return None
        fv = float(v)
        sign = "-" if fv < 0 else ""
        return f"{sign}USD ${abs(fv):,.2f}"

    # CLP part — Δ = facturado − pagado_efectivo
    # no_fac_clp = pagos ciclo actual (Santander/Ripley); si None, usar pag_clp (demás bancos)
    fac_c   = fac_clp or 0
    pag_c   = abs(pag_clp or 0)
    nofac_c = abs(no_fac_clp or 0)
    delta_c = fac_c - (nofac_c if no_fac_clp is not None else pag_c)
    d_col = "bright_red" if delta_c > 0 else "bright_green"
    if fac_clp is not None:
        nofac_str = f" / {_clp(nofac_c)} pag_nofac" if nofac_c else ""
        clp_str = (f"CLP {_clp(fac_c)} fac / {_clp(pag_c)} pag_ant{nofac_str} → "
                   f"Δ [{d_col}]{_clp(delta_c)}[/{d_col}]")
    else:
        clp_str = f"CLP {_clp(pag_c)} pag"

    # USD part
    usd_str = ""
    fac_u = fac_usd
    pag_u = abs(pag_usd) if pag_usd else None
    if fac_u is not None or pag_u:
        if fac_u is not None and pag_u is not None:
            delta_u = fac_u - pag_u
            u_col = "bright_red" if delta_u > 0 else "bright_green"
            usd_str = (f"  |  {_usd(fac_u)} fac / {_usd(pag_u)} pag → "
                       f"Δ [{u_col}]{_usd(delta_u)}[/{u_col}]")
        elif pag_u:
            usd_str = f"  |  {_usd(pag_u)} pag"
        elif fac_u:
            usd_str = f"  |  {_usd(fac_u)} fac"

    return f"  [green]OK[/green] [dim]Pagos {inst} • TdC {card}[/dim]  {clp_str}{usd_str}"


def fmt_uf(val):
    """Legacy helper."""
    return fmt_monto(val)

# ── Lógica de Divisas ──────────────────────────────────────────

_RATES_BY_DATE = {}  # { 'YYYY-MM-DD': { 'USD': 950, 'UF': 38000 } }


import ssl

def get_rates(target_date=None):
    """Obtiene valores de USD y UF desde mindicador.cl or DB fallback para una fecha específica."""
    global _RATES_BY_DATE
    if not target_date:
        target_date = datetime.datetime.now().strftime("%Y-%m-%d")

    # 1. Check in-memory cache
    if target_date in _RATES_BY_DATE:
        return _RATES_BY_DATE[target_date]

    ctx     = ssl._create_unverified_context()
    headers = {'User-Agent': 'Mozilla/5.0'}
    db_path = _db_path()

    def _prev_usd():
        """Retorna el último USD conocido anterior a target_date."""
        try:
            rows = _read_supabase("currency_rates", select="usd",
                                  extra=f"&date=lt.{target_date}&order=date.desc&limit=1")
            if rows and rows[0].get("usd"):
                return float(rows[0]["usd"])
        except: pass
        try:
            conn = sqlite3.connect(db_path)
            r = conn.execute("SELECT valor FROM rates WHERE date<? AND moneda='USD' ORDER BY date DESC LIMIT 1",
                             (target_date,)).fetchone()
            conn.close()
            if r: return float(r[0])
        except: pass
        return None

    def _is_holiday_or_weekend(date_str):
        """Retorna True si la fecha es fin de semana o feriado chileno."""
        dt = datetime.datetime.strptime(date_str, "%Y-%m-%d")
        if dt.weekday() >= 5:  # sábado=5, domingo=6
            return True
        y, m, d = dt.year, dt.month, dt.day
        # Feriados fijos Chile
        fixed = {(1,1),(5,1),(5,21),(7,16),(8,15),(9,18),(9,19),(10,12),(10,31),(11,1),(12,8),(12,25)}
        if (m, d) in fixed:
            return True
        # Viernes Santo y Sábado Santo (Pascua)
        # Algoritmo Meeus/Jones/Butcher
        a = y % 19; b = y // 100; c = y % 100
        d_ = b // 4; e = b % 4; f = (b + 8) // 25
        g = (b - f + 1) // 3; h = (19*a + b - d_ - g + 15) % 30
        i = c // 4; k = c % 4; l = (32 + 2*e + 2*i - h - k) % 7
        mm = (a + 11*h + 22*l) // 451
        easter_month = (h + l - 7*mm + 114) // 31
        easter_day   = ((h + l - 7*mm + 114) % 31) + 1
        easter = datetime.datetime(y, easter_month, easter_day)
        good_friday   = easter - datetime.timedelta(days=2)
        holy_saturday = easter - datetime.timedelta(days=1)
        if dt.date() in (good_friday.date(), holy_saturday.date()):
            return True
        return False

    def _sanity_ok(usd_val):
        """Verifica el rate solo en fines de semana / feriados (desviación ≤2%).
        Si desvía >2%, intenta corroborar con fuente alternativa.
        Retorna (bool_ok, usd_a_usar).
        """
        if not _is_holiday_or_weekend(target_date):
            return True, usd_val   # día hábil → confiar directamente
        prev = _prev_usd()
        if prev is None:
            return True, usd_val   # sin referencia, aceptar
        deviation = abs(usd_val - prev) / prev
        if deviation <= 0.02:
            return True, usd_val
        # Desviación >3% → corroborar con fuente alternativa
        alt_usd = None
        for alt_url in ["https://api.frankfurter.app/latest?from=USD&to=CLP",
                        "https://open.er-api.com/v6/latest/USD"]:
            try:
                req_alt = urllib.request.Request(alt_url, headers=headers)
                with urllib.request.urlopen(req_alt, timeout=8, context=ctx) as r:
                    d = json.loads(r.read().decode())
                    alt_usd = float(d["rates"]["CLP"])
                if alt_usd: break
            except: continue
        if alt_usd:
            if abs(alt_usd - usd_val) / usd_val < 0.01:
                # Ambas coinciden → movimiento real
                _console.print(f"[dim green][Rates] Feriado/fin de semana — mindicador ${usd_val:.0f} confirmado por fuente alternativa ${alt_usd:.0f} ✓[/dim green]")
                return True, usd_val
            else:
                _console.print(f"[dim yellow][Rates] Feriado/fin de semana — mindicador ${usd_val:.0f} vs fuente alternativa ${alt_usd:.0f}: no coinciden → usando ayer (${prev:.0f})[/dim yellow]")
                return False, prev
        else:
            _console.print(f"[dim yellow][Rates] Feriado/fin de semana — mindicador ${usd_val:.0f} sospechoso (+{deviation:.1%} sobre ayer ${prev:.0f}), fuente alternativa no disponible → usando ayer[/dim yellow]")
            return False, prev

    # 2. Supabase (fuente primaria)
    try:
        rows = _read_supabase("currency_rates", {"date": target_date}, select="usd,uf")
        if rows and rows[0].get("usd") and rows[0].get("uf"):
            usd_raw = float(rows[0]["usd"])
            ok, usd_final = _sanity_ok(usd_raw)
            if ok:
                _RATES_BY_DATE[target_date] = {"USD": usd_final, "UF": float(rows[0]["uf"])}
                return _RATES_BY_DATE[target_date]
            # Rate sospechoso en Supabase — borrarlo y seguir
            try: _read_supabase  # no-op, solo para indicar que continuamos
            except: pass
    except: pass

    # 2b. SQLite local
    try:
        conn = sqlite3.connect(db_path)
        res_usd = conn.execute("SELECT valor FROM rates WHERE date=? AND moneda='USD'", (target_date,)).fetchone()
        res_uf  = conn.execute("SELECT valor FROM rates WHERE date=? AND moneda='UF'",  (target_date,)).fetchone()
        conn.close()
        if res_usd and res_uf:
            usd_raw = float(res_usd[0])
            ok, usd_final = _sanity_ok(usd_raw)
            if ok:
                _RATES_BY_DATE[target_date] = {"USD": usd_final, "UF": float(res_uf[0])}
                try: _sync_supabase("currency_rates", [{"date": target_date, "usd": usd_final, "uf": float(res_uf[0])}])
                except: pass
                return _RATES_BY_DATE[target_date]
            # Rate sospechoso en SQLite — limpiar y seguir a la API
            try:
                conn2 = sqlite3.connect(db_path)
                conn2.execute("DELETE FROM rates WHERE date=? AND moneda='USD'", (target_date,))
                conn2.commit(); conn2.close()
            except: pass
    except: pass

    # 3. Try fetching from API
    try:
        usd_val, uf_val = None, None
        today = datetime.datetime.now().strftime("%Y-%m-%d")
        
        ctx = ssl._create_unverified_context()
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'}
        
        status_msg = f"[bold blue]Buscando rates {target_date}...[/]"
        with _console.status(status_msg):
            if target_date == today:
                req = urllib.request.Request("https://mindicador.cl/api", headers=headers)
                with urllib.request.urlopen(req, timeout=10, context=ctx) as resp:
                    data = json.loads(resp.read().decode())
                    usd_val = float(data["dolar"]["valor"])
                    uf_val = float(data["uf"]["valor"])
            else:
                y, m, d = target_date.split("-")
                dmy = f"{d}-{m}-{y}"
                
                # Fetch USD
                try:
                    req_u = urllib.request.Request(f"https://mindicador.cl/api/dolar/{dmy}", headers=headers)
                    with urllib.request.urlopen(req_u, timeout=10, context=ctx) as resp:
                        data = json.loads(resp.read().decode())
                        if data["serie"]:
                            usd_val = float(data["serie"][0]["valor"])
                except: pass
                
                # Fetch UF
                try:
                    req_f = urllib.request.Request(f"https://mindicador.cl/api/uf/{dmy}", headers=headers)
                    with urllib.request.urlopen(req_f, timeout=10, context=ctx) as resp:
                        data = json.loads(resp.read().decode())
                        if data["serie"]:
                            uf_val = float(data["serie"][0]["valor"])
                except: pass

        if usd_val and uf_val:
            ok, usd_final = _sanity_ok(usd_val)
            _RATES_BY_DATE[target_date] = {"USD": usd_final, "UF": uf_val}
            try:
                conn = sqlite3.connect(db_path)
                conn.execute("INSERT OR REPLACE INTO rates (moneda, valor, date) VALUES (?, ?, ?)", ("USD", usd_final, target_date))
                conn.execute("INSERT OR REPLACE INTO rates (moneda, valor, date) VALUES (?, ?, ?)", ("UF", uf_val, target_date))
                conn.commit()
                conn.close()
            except: pass
            try:
                _sync_supabase("currency_rates", [{"date": target_date, "usd": usd_final, "uf": uf_val}])
            except: pass
            return _RATES_BY_DATE[target_date]

    except Exception as e:
        if DEBUG: print(f"[Error] API failed for {target_date}: {e}")

    # 4. Final Fallback: Latest known in Supabase or SQLite
    try:
        rows = _read_supabase("currency_rates", select="usd,uf", extra="&order=date.desc&limit=1")
        if rows and rows[0].get("usd") and rows[0].get("uf"):
            _RATES_BY_DATE[target_date] = {"USD": float(rows[0]["usd"]), "UF": float(rows[0]["uf"])}
            return _RATES_BY_DATE[target_date]
    except: pass
    try:
        conn = sqlite3.connect(_db_path())
        row_usd = conn.execute("SELECT valor FROM rates WHERE moneda='USD' ORDER BY date DESC LIMIT 1").fetchone()
        row_uf  = conn.execute("SELECT valor FROM rates WHERE moneda='UF' ORDER BY date DESC LIMIT 1").fetchone()
        conn.close()
        fallback_usd = row_usd[0] if row_usd else 950.0
        fallback_uf  = row_uf[0] if row_uf else 38500.0
        _RATES_BY_DATE[target_date] = {"USD": fallback_usd, "UF": fallback_uf}
        return _RATES_BY_DATE[target_date]
    except:
        return {"USD": 950.0, "UF": 38500.0}

def convert_to_all(monto, moneda, target_date=None):
    """Convierte un monto en moneda original a las 3 columnas: CLP MM, USD M, UF M."""
    rates = get_rates(target_date)
    clp = 0.0
    r_usd = float(rates["USD"])
    r_uf = float(rates["UF"])
    
    if moneda == "CLP":
        clp = float(monto or 0)
    elif moneda == "USD":
        clp = float(monto or 0) * r_usd
    elif moneda == "UF":
        clp = float(monto or 0) * r_uf
    
    clp_mm = clp / 1_000_000.0
    usd_m  = (clp / r_usd) / 1_000.0
    uf     = (clp / r_uf)
    
    return clp_mm, usd_m, uf, r_usd, r_uf

# ══════════════════════════════════════════════════════════════
# DATA TABLE RENDERING
# ══════════════════════════════════════════════════════════════

def add_result(resultados, bank_key, inst, cat, item, monto_str, ok=True, manual=False):
    """Agrega resultado de CLP. Usa clean_monto para normalizar."""
    try:
        clean = clean_monto(monto_str, "CLP", is_manual=manual)
        monto_int = int(round(float(clean)))
        if cat in ("TdC", "LdC") and monto_int > 0:
            monto_int = -monto_int
        monto_fmt = fmt_monto(monto_int)
        ok_final = ok
    except Exception:
        monto_int = 0
        monto_fmt = "No obtenido" if not ok else monto_str
        ok_final = False

    resultados.append({
        "bank_key":  bank_key,
        "inst":      inst,
        "cat":       cat,
        "item":      item,
        "moneda":    "CLP",
        "monto":     monto_fmt,
        "monto_int": monto_int,
        "ok":        ok_final,
        "manual":    manual,
    })

def add_result_uf(resultados, bank_key, inst, cat, item, uf_str, ok=True, manual=False):
    """Agrega resultado de UF. Almacena float REAL en DB."""
    try:
        clean = clean_monto(uf_str, "UF", is_manual=manual)
        uf_float = float(clean)
        if cat == "CH" and uf_float > 0:
            uf_float = -uf_float
        # Formato chileno: punto miles, coma decimal
        monto_fmt = f"{uf_float:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
        ok_final = True
    except Exception:
        uf_float  = None
        monto_fmt = "No obtenido" if not ok else uf_str
        ok_final  = False
    resultados.append({
        "bank_key":  bank_key,
        "inst":      inst,
        "cat":       cat,
        "item":      item,
        "moneda":    "UF",
        "monto":     monto_fmt,
        "monto_int": uf_float,
        "ok":        ok_final and uf_float is not None,
        "manual":    manual,
    })

def add_result_usd(resultados, bank_key, inst, cat, item, usd_str, ok=True, manual=False):
    """Agrega resultado de USD."""
    try:
        clean = clean_monto(usd_str, "USD", is_manual=manual)
        usd_float = float(clean)
        # Formato chileno: punto miles, coma decimal
        monto_fmt = f"{usd_float:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
        ok_final = True
    except Exception:
        usd_float = None
        monto_fmt = "No obtenido" if not ok else usd_str
        ok_final  = False
    resultados.append({
        "bank_key":  bank_key,
        "inst":      inst,
        "cat":       cat,
        "item":      item,
        "moneda":    "USD",
        "monto":     monto_fmt,
        "monto_int": usd_float,
        "ok":        ok_final and usd_float is not None,
        "manual":    manual,
    })

def print_preliminary(inst, cat, item, monto_str, ok=True, moneda="CLP", prev_monto=None, last_date=None, manual=False):
    """Impresión en tiempo real con estética premium y contexto completo."""
    if not ok:
        label = f"[bold cyan]{inst}[/bold cyan] [dim]• {item}[/dim]"
        _console.print(f"  [bold red]ERROR[/bold red] {label:<50} [dim]({str(monto_str)[:30]})[/dim]")
        return

    # Si no nos pasan el valor previo, intentamos buscarlo para la comparativa.
    # NO filtramos por categoría: el nombre de categoría que llega aquí puede diferir
    # del que está guardado en la DB (ej. "Previsional" vs "Fondos previsionales").
    if prev_monto is None:
        try:
            persona = cat_to_persona(cat)
            conn = sqlite3.connect(DB_PATH)
            row = conn.execute(
                "SELECT monto, timestamp FROM saldos WHERE institucion=? AND item=? AND persona=? "
                "ORDER BY timestamp DESC LIMIT 1", (inst, item, persona)
            ).fetchone()
            conn.close()
            if row:
                prev_monto, last_date = row[0], row[1]
        except: pass

    # Determinar moneda y formatear New
    s = str(monto_str)
    try:
        # Usar la lógica de limpieza centralizada
        clean_s = clean_monto(s, moneda, is_manual=manual)
        val = float(clean_s)
        
        if moneda in ("USD", "UF"):
            # Formato chileno: punto miles, coma decimal
            monto_disp = f"{val:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
        else: # CLP
            monto_disp = fmt_monto(val)
    except:
        monto_disp = s

    # Formatear Prev
    prev_str = ""
    if prev_monto is not None:
        try:
            p_val = float(prev_monto)
            if moneda in ("USD", "UF"):
                p_fmt = f"{p_val:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
            else:
                p_fmt = fmt_monto(p_val)
            prev_str = f"[dim]{p_fmt}[/dim] → "
        except: pass

    # Formatear Fecha
    date_str = ""
    if last_date:
        fecha_fmt = _fmt_ts(last_date)
        date_str = f" [dim]u. act: {fecha_fmt}[/dim]"

    # Estilo según valor
    color = "bright_green"
    if s.startswith("-") or (cat == "CH" and "val" in locals() and val and val < 0):
        color = "bright_red"

    cat_short = cat_to_short(cat, inst)
    
    # Render final premium: OK CAT INST • ITEM | Prev -> New MONEDA Date
    _console.print(
        f"  [green]OK[/green] [dim]{cat_short} {inst} • {item}[/dim]  "
        f"{prev_str}[{color}]{monto_disp}[/{color}] "
        f"[dim]{moneda} {date_str}[/dim]",
        highlight=False
    )


# ══════════════════════════════════════════════════════════════
# DISPLAY / TABLAS
# Columnas: Categoría | Institución | Persona | Item | Moneda | Monto | Última act.
# ══════════════════════════════════════════════════════════════

_MES = ["Ene","Feb","Mar","Abr","May","Jun","Jul","Ago","Sep","Oct","Nov","Dic"]

# Timestamp de inicio del script — para determinar si un item fue actualizado "ahora"
_SCRIPT_START = datetime.datetime.now()

# Lista de fallos en extracción de pagos TdC — se popula durante scraping
# Cada entrada: {"inst": str, "card": str, "error": str}
_PAGOS_ERRORS: list = []

# Nombres alternativos para mostrar en tabla (no afecta lógica ni DB)
_INST_DISPLAY = {
    # "Itaú Empresas" renombrado a "Itaú CdB" para evitar conflictos con Itaú PN
}

# Mapa: nombre en catálogo → nombre almacenado en DB cuando difieren
# Necesario para la clave compuesta (inst, item) en show_last_saldos
_CATALOG_TO_DB_INST = {
    "Scotiabank PN":       "Scotiabank",
    "Scotiabank Empresas": "Scotiabank",
}


# ══════════════════════════════════════════════════════════════
# FORMATTERS & HELPERS
def _fmt_ts(ts_str):
    """'2026-02-26 09:01' → '26-Feb 09:01', '—' → '—'"""
    if not ts_str or ts_str == "—":
        return "—"
    try:
        dt = datetime.datetime.strptime(ts_str[:16], "%Y-%m-%d %H:%M")
        return f"{dt.day:02d}-{_MES[dt.month-1]} {dt.strftime('%H:%M')}"
    except Exception:
        return ts_str

def _get_source_emoji(item, source, inst=None):
    """
    Retorna el texto que representa la fuente de datos según el item y source.

    - Shares × Price (CFIETFCD, CFINASDAQ de Itaú): "Semiautomática"
    - TIR (Dorco, WBuild): "Semiautomática" (cálculo automático pero con datos ingresados manualmente)
    - Auto (scraping): "Automática" (totalmente automático)
    - Manual: "Manual" (totalmente manual)
    """
    # Shares × Price items: solo para Itaú (manual catalog) — BTG y Racional son scraping automático
    if _is_shares_price_item(item, inst=inst):
        return "Semiautomática"

    # TIR semi-automático (Dorco, WBuild)
    if inst in ("HDZ", "WBuild"):
        return "Semiautomática"

    # Fuentes específicas
    if source == "auto":
        return "Automática"
    elif source == "historial":
        return "Historial"
    else:
        return "Manual"


def _freshness_cell(ts_str):
    """
    ● verde   → ts >= _SCRIPT_START (actualizado en esta ejecución)
    ● amarillo → ts válido y antigüedad ≤3 días
    ● rojo    → antigüedad >3 días | sin dato | 'No obtenido' | nunca capturado
    """
    if not ts_str or ts_str == "—":
        return Text("●", style="bold red")
    try:
        dt = datetime.datetime.strptime(ts_str[:16], "%Y-%m-%d %H:%M")
        if dt >= _SCRIPT_START:
            return Text("●", style="bold green")
        age_days = (datetime.datetime.now() - dt).total_seconds() / 86400
        if age_days <= 3:
            return Text("●", style="bold white")
        else:
            return Text("●", style="bold red")
    except Exception:
        return Text("●", style="bold red")

def _monto_cell(monto_str):
    """Devuelve un Text de rich con color según el valor. '0' se muestra como '—'."""
    if monto_str in ("—", "No se pudo obtener", "No obtenido", None, ""):
        return Text(str(monto_str or "—"), style="dim")
    m_str = str(monto_str).strip()
    try:
        # Check for various zero representations
        clp_f = float(m_str.replace("$", "").replace(".", "").replace(",", ".").strip())
        if clp_f == 0:
            return Text("—", style="dim")
    except ValueError:
        pass
    
    if m_str.startswith("-"):
        return Text(m_str, style="bold red")
    return Text(m_str, style="bold green")


def _print_table_rows(rows, title="RESUMEN DE SALDOS", subtitle=None):
    """Renderiza la tabla de resultados con estética Premium."""
    from rich.box import ROUNDED, DOUBLE_EDGE
    from rich.table import Table

    # Asegurar que tenemos rates actuales
    get_rates()

    title_str = f"\n[bold sky_blue3]{title}[/bold sky_blue3]"
    if subtitle:
        title_str += f"\n[dim]{subtitle}[/dim]"

    table = Table(
        title=title_str,
        box=ROUNDED,
        header_style="bold sky_blue3",
        border_style="dim",
        show_footer=False,
        title_justify="center",
        row_styles=["", "on grey15"],  # zebra sutil para Clear Dark (~#262626 sobre ~#1e2021)
    )
    
    table.add_column("Categoría", no_wrap=True)
    table.add_column("#", justify="center", style="dim")
    table.add_column("Institución")
    table.add_column("Per.", justify="center")
    table.add_column("Item", style="italic")
    table.add_column("Monto Original", justify="right")
    table.add_column("Mon.", justify="center")
    table.add_column("CLP MM", justify="right")
    table.add_column("USD M", justify="right")
    table.add_column("UF", justify="right")
    table.add_column("Última act.", justify="left")
    table.add_column("Tipo Actualización", justify="center")

    global _LAST_TABLE_MAPPING
    _LAST_TABLE_MAPPING = []
    item_counter = 1

    def fmt_val(v, decimals=1, style=None):
        if v is None:
            return Text("—", style="dim")
        if decimals == 0:
            rounded = int(round(v))
            if rounded == 0:
                return Text("0", style=style or "dim")
            res = f"{rounded:,}".replace(",", ".")
        else:
            if abs(v) < 0.001:
                return Text("0", style=style or "dim")
            res = f"{v:,.{decimals}f}".replace(",", "X").replace(".", ",").replace("X", ".")
        if style: return Text(res, style=style)
        color = "bright_green" if v > 0 else "bright_red"
        return Text(res, style=color)

    # 1. ORDENAMIENTO PREVIO (CRÍTICO PARA SUMAS CORRECTAS)
    # Esto asegura que todos los "Cash" terminen en un solo bloque y se sumen juntos.
    rows_sorted = sorted(
        rows,
        key=lambda r: (
            CAT_ORDER.index(cat_to_short(r[0], r[1])) if cat_to_short(r[0], r[1]) in CAT_ORDER else 99,
            r[1], # Institución
            r[3]  # Item
        )
    )

    # 2. AGRUPAR POR CATEGORÍA UNIFICADA
    groups = []
    cur_cat, cur_group = None, []
    for row in rows_sorted:
        row_cat = cat_to_short(row[0], row[1])
        if row_cat != cur_cat:
            if cur_group:
                groups.append((cur_cat, cur_group))
            cur_cat, cur_group = row_cat, [row]
        else:
            cur_group.append(row)
    if cur_group:
        groups.append((cur_cat, cur_group))
    
    # Pre-calcular totales para reporte preciso
    total_clp, total_usd, total_uf = 0.0, 0.0, 0.0
    patri_clp, patri_usd, patri_uf = 0.0, 0.0, 0.0
    
    # Lista para guardar grupos procesados con sus sumas
    processed_groups = []
    
    for cat_name, group_rows in groups:
        cat_clp, cat_usd, cat_uf = 0.0, 0.0, 0.0
        rows_with_vals = []
        for r_data in group_rows:
            # 1. Unpack r_data with variable indices support
            # For show_last_saldos: 12 elements (monto_val at 6, moneda at 4, ts_iso at 9)
            # For print_table: now standardized to 11 elements
            if len(r_data) >= 10:
                monto_val = r_data[6]
                moneda_val = r_data[4]
                ts_iso_val = r_data[9]
            else:
                monto_val = r_data[6]
                moneda_val = r_data[4]
                ts_iso_val = r_data[8] if len(r_data) > 8 else None

            clp_mm, usd_m, uf = 0.0, 0.0, 0.0
            if monto_val is not None:
                # Usar siempre tasa de HOY para consistencia con la tabla resumida
                clp_mm, usd_m, uf, _, _ = convert_to_all(monto_val, moneda_val, target_date=None)
            rows_with_vals.append((r_data, clp_mm, usd_m, uf))
            cat_clp += clp_mm
            cat_usd += usd_m
            cat_uf  += uf
            
        # Ordenar dentro del grupo por CLP MM desc
        rows_with_vals.sort(key=lambda x: x[1], reverse=True)
        processed_groups.append((cat_name, cat_clp, cat_usd, cat_uf, rows_with_vals))
        
        # Patrimonio Inversiones = Todo excepto Mi casa
        if "Casa" not in str(cat_name):
            patri_clp += cat_clp
            patri_usd += cat_usd
            patri_uf  += cat_uf

        total_clp += cat_clp
        total_usd += cat_usd
        total_uf  += cat_uf

    # Calcular el timestamp más reciente para destacarlo en bold
    max_ts_iso = max(
        (r_data[9] for _, _, _, _, rows in processed_groups
         for r_data, _, _, _ in rows if r_data[9]),
        default=None
    )

    patri_shown = False
    last_shown_cat = None
    for g_idx, (cat_name, cat_clp, cat_usd, cat_uf, group_details) in enumerate(processed_groups):
        
        # Si es Mi casa o Créditos Hipotecarios (que suelen venir después de Mi casa),
        # mostramos el Total Patrimonio antes si no se ha mostrado.
        # Por orden CAT_ORDER, Mi casa es el penúltimo.
        if "Casa" in str(cat_name) and not patri_shown:
            patri_shown = True
            table.add_section()
            
            p_clp = f"{int(round(patri_clp)):,}".replace(",", ".")
            p_usd = f"{int(round(patri_usd)):,}".replace(",", ".")
            p_uf  = f"{int(round(patri_uf)):,}".replace(",", ".")

            # High contrast Orange for Patrimonio (No background)
            p_style = "bold orange3"
            table.add_row(
                Text("TOTAL PATRIMONIO INVERSIONES", style=p_style), # Categoría
                "", # #
                "", "", "", "", "", # Inst, Per, Item, Monto, Mon
                fmt_val(patri_clp, 0, p_style), # CLP MM
                fmt_val(patri_usd, 0, p_style), # USD M
                fmt_val(patri_uf,  0, p_style), # UF
                "", # Última act.
                "", # Tipo Actualización
            )
            table.add_section()

        if g_idx > 0 and not ("Casa" in str(cat_name) and patri_shown):
            table.add_section()

        # Filas de datos
        for r_data, clp_mm, usd_m, uf in group_details:
            # Desempaquetado con soporte para campos extra (*_)
            (cat_disp, inst, persona, item, moneda_val, m_str, m_num, ts_fmt, marker, ts_iso, *extra) = r_data

            # Extraer meta-datos si existen (están al final de 'row' en show_last_saldos)
            full_cat = extra[0] if extra else None
            bank_key = extra[1] if len(extra) > 1 else None
            # item_code real (puede diferir de item cuando hay nota de referencia, ej: CxC/CxP)
            real_item_code = extra[2] if len(extra) > 2 else item

            _LAST_TABLE_MAPPING.append({
                'cat': full_cat or cat_disp,
                'inst': inst,
                'item': real_item_code,  # item_code del catálogo, NO el display (nota)
                'moneda': moneda_val,
                'val': m_num,
                'bank_key': bank_key
            })
            table.add_row(
                Text(str(cat_name), style="bold white") if (item_counter == 1 or cat_name != last_shown_cat) else "",
                str(item_counter),
                _INST_DISPLAY.get(inst, inst),
                persona,
                item,
                fmt_val(m_num, 0),
                moneda_val, # MONEDA RECUPERADA (POSICIÓN CORRECTA)
                fmt_val(clp_mm, 0),
                fmt_val(usd_m, 0),
                fmt_val(uf, 0),
                Text(ts_fmt, style="bold" if ts_iso and ts_iso == max_ts_iso else "dim italic"),
                Text(marker, style="dim italic")
            )
            item_counter += 1
            last_shown_cat = cat_name
        
        # Fila de SUBTOTAL por categoría (Bold White, Sin fondo)
        sub_style = "bold white"
        table.add_row(
            Text(f"SUBTOTAL {str(cat_name).upper()}", style=sub_style), # Categoría
            "", # #
            "", "", "", "", "", # Inst, Per, Item, Monto, Mon
            fmt_val(cat_clp, 0, sub_style), # CLP MM
            fmt_val(cat_usd, 0, sub_style), # USD M
            fmt_val(cat_uf,  0, sub_style), # UF
            "", # Última act.
            "", # Tipo Actualización
        )

    # Si terminamos el loop y nunca mostramos el Total Patrimonio, lo mostramos ahora
    if not patri_shown and total_clp != 0:
        table.add_section()
        p_clp = f"{int(round(patri_clp)):,}".replace(",", ".")
        p_usd = f"{int(round(patri_usd)):,}".replace(",", ".")
        p_uf  = f"{int(round(patri_uf)):,}".replace(",", ".")
        p_style = "bold orange3"
        table.add_row(
            Text("TOTAL PATRIMONIO INVERSIONES", style=p_style), # Categoría
            "", # #
            "", "", "", "", "", # Inst, Per, Item, Monto, Mon
            fmt_val(patri_clp, 0, p_style), # CLP MM
            fmt_val(patri_usd, 0, p_style), # USD M
            fmt_val(patri_uf,  0, p_style), # UF
            "", # Última act.
            "", # Tipo Actualización
        )

    # Fila de TOTAL GENERAL (Orange, Sin fondo)
    table.add_section()
    g_style = "bold orange3"
    table.add_row(
        Text("TOTAL GENERAL", style=g_style), # Categoría
        "", # #
        "", "", "", "", "", # Inst, Per, Item, Monto, Mon
        fmt_val(total_clp, 0, g_style), # CLP MM
        fmt_val(total_usd, 0, g_style), # USD M
        fmt_val(total_uf,  0, g_style), # UF
        "", # Última act.
        "", # Tipo Actualización
    )

    _console.print()
    _console.print(table)
    _console.print()

def _render_bullets(text):
    """Convierte texto con bullets '•' en un Group de Rich con hanging indent correcto."""
    from rich.console import Group
    from rich.text import Text
    from rich.padding import Padding

    items = []
    for line in text.strip().split("\n"):
        line = line.strip()
        if not line:
            continue
        if line.startswith("•"):
            body = line[1:].strip()
            t = Text()
            t.append("• ", style="bold sky_blue3")
            t.append(body)
            items.append(Padding(t, pad=(0, 0, 0, 0)))
        else:
            items.append(Text(f"  {line}"))
    return Group(*items)


def _check_supabase_connection():
    """Retorna True si Supabase responde, False si no."""
    try:
        rows = _read_supabase("currency_rates", select="date", extra="&limit=1")
        return isinstance(rows, list)
    except Exception:
        return False

def _print_mini_header():
    """Indicador de fuente de datos — solo al mostrar números/tablas."""
    sup_ok = _check_supabase_connection()
    source = "[bold green]☁ Supabase[/bold green]" if sup_ok else "[bold yellow]💾 Local[/bold yellow]"
    _console.print(f"  {source}", highlight=False)

def _print_table_menu(title, options):
    """Muestra un menú de selección navegable con flechas y Enter."""
    choices = []
    
    for i, opt in enumerate(options, 1):
        clean_opt = opt.strip()
        if clean_opt.startswith("[bold white]"):
            header_raw  = clean_opt.replace("[bold white]", "").replace("[/bold white]", "")
            is_sub      = header_raw.startswith("  ")   # indent = sub-sección
            header_text = header_raw.strip()
            if is_sub:
                choices.append(questionary.Separator(f"       {header_text}"))
            else:
                choices.append(questionary.Separator(f"  ── {header_text} ──"))
        else:
            # Opción seleccionable
            choices.append(questionary.Choice(title=clean_opt.strip(), value=i))

    _console.print(f"\n[bold sky_blue3]  {title.upper()}  [/bold sky_blue3]")
    _clear_terminal_buffer()
    selected = questionary.select(
        "",
        choices=choices,
        style=QUESTIONARY_STYLE,
        pointer="»",
        use_indicator=True,
        qmark=""
    ).ask(patch_stdout=True)
    
    return selected


def print_table(resultados, ts=None):
    """Imprime tabla desde lista de resultados (post-scraping). Fuente siempre [AUTO] (auto)."""
    # Para reporte recién escaneado, usamos siempre 'current' (hoy)
    if ts is None:
        ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    try:
        dt = datetime.datetime.strptime(ts[:16], "%Y-%m-%d %H:%M")
        ts_fmt = f"{dt.day:02d}-{_MES[dt.month-1]} {dt.strftime('%H:%M')}"
    except Exception:
        ts_fmt = ts
    ts_display = f"[AUTO] {ts_fmt}"
    resultados_sorted = sorted(
        resultados,
        key=lambda r: (CAT_ORDER.index(cat_to_short(r["cat"], r["inst"])) if cat_to_short(r["cat"], r["inst"]) in CAT_ORDER else 99, r["inst"])
    )
    iso_date = ts[:10]
    rows = []
    for r in resultados_sorted:
        monto_disp = r["monto"] if r["ok"] else "No obtenido"
        marker = "[MANUAL]" if r.get("manual") else "[AUTO]"
        rows.append((
            cat_to_short(r["cat"], r["inst"]),
            r["inst"],
            cat_to_persona(r["cat"]),
            r["item"],
            r["moneda"],
            monto_disp,
            r.get("monto_int"),  # Index 6: numeric for conversion
            ts_fmt,              # Index 7: display duration/time
            marker,              # Index 8: marker
            iso_date             # Index 9: iso date for rates
        ))

    _print_table_rows(rows)


def get_available_snapshot_dates(limit=365):
    """Retorna lista de fechas distintas (YYYY-MM-DD) con registros ok=1,
    ordenadas de más reciente a más antigua. Máximo `limit` fechas. Fuente: Supabase."""
    # Primary: Supabase via RPC
    try:
        rows = _sup_rpc("get_snapshot_dates")
        dates = [r["fecha"] for r in rows if r.get("fecha")]
        if dates:
            return dates[:limit]
    except Exception:
        pass
    # Secondary: direct Supabase table read
    try:
        rows = _read_supabase("saldos", select="timestamp", extra="&ok=eq.true&order=timestamp.desc&limit=5000")
        if rows:
            dates = sorted({r["timestamp"][:10] for r in rows if r.get("timestamp")}, reverse=True)
            if dates:
                return dates[:limit]
    except Exception:
        pass
    # Final fallback: SQLite local
    db_path = _db_path()
    if not os.path.exists(db_path):
        return []
    conn = init_db()
    try:
        rows = conn.execute("""
            SELECT DISTINCT date(timestamp) AS d FROM saldos
            WHERE ok = 1 ORDER BY d DESC LIMIT ?
        """, (limit,)).fetchall()
        return [r[0] for r in rows]
    except sqlite3.OperationalError:
        return []
    finally:
        conn.close()


def _fetch_snapshot_dict(snapshot_before=None):
    """Retorna {(inst, item, persona): (cat_raw, moneda, monto_float)} para una fecha dada.
    Si snapshot_before es None, retorna el estado más reciente. Fuente: Supabase."""
    result = {}
    try:
        if snapshot_before:
            rows = _sup_rpc("get_snapshot", {"ts_filter": snapshot_before})
            for r in rows:
                if r.get("source") == "historial": continue
                result[(r["institucion"], r["item"], r["persona"])] = (
                    r["categoria"], r["moneda"], float(r["monto"] or 0)
                )
        else:
            rows = _read_supabase("v_latest_saldos")
            for r in rows:
                if r.get("source") == "historial": continue
                result[(r["institucion"], r["item"], r["persona"])] = (
                    r["categoria"], r["moneda"], float(r["monto"] or 0)
                )
        return result
    except Exception:
        pass
    # Fallback: SQLite local
    ts_filter = f"AND timestamp <= '{snapshot_before}'" if snapshot_before else ""
    target_date = snapshot_before[:10] if snapshot_before else ""
    hist_filter = f"AND (COALESCE(s.source, 'auto') != 'historial' OR date(s.timestamp) = '{target_date}')" if target_date else "AND COALESCE(s.source, 'auto') != 'historial'"
    db_path = _db_path()
    if not os.path.exists(db_path): return result
    conn = init_db()
    try:
        rows = conn.execute(f"""
            SELECT s.institucion, s.categoria, s.persona, s.item, s.moneda, s.monto
            FROM saldos s
            INNER JOIN (
                SELECT institucion, item, persona, MAX(timestamp) AS max_ts
                FROM saldos WHERE ok = 1 {ts_filter}
                GROUP BY institucion, item, persona
            ) latest ON s.institucion=latest.institucion AND s.item=latest.item
                     AND s.persona=latest.persona AND s.timestamp=latest.max_ts
            WHERE s.ok = 1 {hist_filter}
        """).fetchall()
        for inst, cat, persona, item, moneda, monto in rows:
            try: m = float(monto) if monto is not None else 0.0
            except: m = 0.0
            result[(inst, item, persona)] = (cat, moneda, m)
    except sqlite3.OperationalError: pass
    conn.close()
    return result


def _pick_snapshot_date(prompt_text):
    """Selector de fecha interactivo premium. Agrupado por mes, con shortcuts y buscador."""
    from datetime import datetime
    import questionary
    from questionary import Choice, Separator

    dates = get_available_snapshot_dates(limit=365)
    if not dates:
        _console.print("[yellow]No hay fechas disponibles en la base de datos.[/]")
        return None

    today_str = datetime.now().strftime("%Y-%m-%d")

    # ── Construcción de la lista de opciones (Choices) ──
    choices = []

    # 1. Shortcuts al principio
    choices.append(Separator("─── ACCESOS RÁPIDOS ───"))
    if today_str in dates:
        choices.append(Choice("📅  Hoy", value=today_str))

    # Buscar el snapshot anterior al de hoy (si existe)
    others = [d for d in dates if d != today_str]
    if others:
        label = datetime.strptime(others[0], "%Y-%m-%d").strftime("%d %b %Y").title()
        choices.append(Choice(f"Snapshot previo ({label})", value=others[0]))
    
    # Inicio del mes actual (si hay data)
    this_month_start = today_str[:7] + "-01"
    if this_month_start in dates and this_month_start != today_str:
        label = datetime.strptime(this_month_start, "%Y-%m-%d").strftime("%d %b").title()
        choices.append(Choice(f"Inicio de mes ({label})", value=this_month_start))

    choices.append(Separator("─── ACCIONES ───"))
    choices.append(Choice("« Volver", value="__back__"))

    # 3. Listado Completo por Mes (Agrupado)
    last_month = None
    first_date = True
    for d in dates:
        dt = datetime.strptime(d, "%Y-%m-%d")
        month_label = dt.strftime("═══ %B %Y ═══").upper()
        
        if month_label != last_month:
            choices.append(Separator(f"   {month_label}"))
            last_month = month_label
        
        day_label = dt.strftime("%a %d %b").title()
        suffix = "  (hoy)" if d == today_str else ""
        choices.append(Choice(f"  {day_label}{suffix}", value=d))

    selected = questionary.select(
        prompt_text, choices=choices, style=QUESTIONARY_STYLE, pointer="»"
    ).ask()

    if selected is None or selected == "__back__":
        return None
            
    return selected


def show_summary_by_category(pause=True, snapshot_before=None, title="RESUMEN POR CATEGORÍA"):
    """Muestra tabla por categoría: una fila por categoría con totales CLP MM / USD M / UF.
    Usa exactamente el mismo cálculo que la tabla completa para que los números coincidan."""
    from rich.table import Table
    from rich.box import ROUNDED
    from rich.text import Text

    # Tipos de cambio (mismo que la tabla completa)
    with _console.status("[bold blue]Consultando tipos de cambio actuales...[/]"):
        rates = get_rates()

    usd_val = rates.get("USD", 0)
    uf_val  = rates.get("UF", 0)
    rates_subtitle = (f"Tipos de Cambio Aplicados:  "
                      f"USD: ${usd_val:,.0f}  UF: ${uf_val:,.0f}").replace(",", ".")

    # ── Reutilizar EXACTAMENTE el mismo flujo que show_last_saldos() ──
    db_data_ok = {}
    ts_filter = f"AND timestamp <= '{snapshot_before}'" if snapshot_before else ""
    target_date = snapshot_before[:10] if snapshot_before else ""
    hist_filter = f"AND (COALESCE(s.source, 'auto') != 'historial' OR date(s.timestamp) = '{target_date}')" if target_date else "AND COALESCE(s.source, 'auto') != 'historial'"
    # Leer desde Supabase (fuente primaria)
    try:
        if snapshot_before:
            rows_raw = _sup_rpc("get_snapshot", {"ts_filter": snapshot_before})
            for r in rows_raw:
                if r.get("source") == "historial": continue
                db_data_ok[(r["institucion"], r["item"], r["persona"])] = (
                    r["categoria"], r["persona"], r["moneda"], r["monto"], r["ts"], r.get("source", "auto")
                )
        else:
            rows_raw = _read_supabase("v_latest_saldos")
            for r in rows_raw:
                if r.get("source") == "historial": continue
                db_data_ok[(r["institucion"], r["item"], r["persona"])] = (
                    r["categoria"], r["persona"], r["moneda"], r["monto"], r["timestamp"], r.get("source", "auto")
                )
    except Exception:
        # Fallback: SQLite local
        db_path = _db_path()
        if os.path.exists(db_path):
            conn = init_db()
            try:
                rows_raw = conn.execute(f"""
                    SELECT s.institucion, s.categoria, s.persona, s.item, s.moneda, s.monto, s.timestamp,
                           COALESCE(s.source, 'auto') AS source
                    FROM saldos s
                    INNER JOIN (
                        SELECT institucion, item, persona, MAX(timestamp) AS max_ts
                        FROM saldos WHERE ok = 1 {ts_filter}
                        GROUP BY institucion, item, persona
                    ) latest ON s.institucion = latest.institucion
                             AND s.item = latest.item
                             AND s.persona = latest.persona
                             AND s.timestamp = latest.max_ts
                    WHERE s.ok = 1 {hist_filter}
                """).fetchall()
                for inst, cat_raw, persona_raw, item, moneda, monto_int, ts, source in rows_raw:
                    db_data_ok[(inst, item, persona_raw)] = (cat_raw, persona_raw, moneda, monto_int, ts, source)
            except sqlite3.OperationalError:
                pass
            conn.close()

    all_catalog = _get_unified_catalog_list()
    all_catalog.sort(key=lambda x: (
        CAT_ORDER.index(cat_to_short(x['cat'], x['inst'], x['item'])) if cat_to_short(x['cat'], x['inst'], x['item']) in CAT_ORDER else 99,
        x['inst']
    ))

    # Acumular totales por categoría usando convert_to_all() — mismo que _print_table_rows()
    cat_totals = {}  # cat_short → {"clp_mm": 0, "usd_m": 0, "uf": 0}
    for item_meta in all_catalog:
        inst_name    = item_meta['inst']
        cat          = item_meta['cat']
        item_code    = item_meta['item']
        catalog_mono = item_meta['moneda']

        db_inst = _CATALOG_TO_DB_INST.get(inst_name, inst_name)
        db_key  = (db_inst, item_code, cat_to_persona(cat))

        if db_key in db_data_ok:
            cat_raw, persona_raw, moneda, monto_int, ts, source = db_data_ok[db_key]
            cat_short = cat_to_short(cat_raw, inst_name, item_code)
            try:
                m_val = float(monto_int) if monto_int is not None else 0.0
                target_date = ts[:10] if ts else None
            except:
                m_val = 0.0
                target_date = None
        else:
            cat_short = cat_to_short(cat, inst_name, item_code)
            moneda    = catalog_mono or "CLP"
            m_val     = 0.0

        if cat_short not in cat_totals:
            cat_totals[cat_short] = {"clp_mm": 0.0, "usd_m": 0.0, "uf": 0.0}

        clp_mm, usd_m, uf, _, _ = convert_to_all(m_val, moneda)
        cat_totals[cat_short]["clp_mm"] += clp_mm
        cat_totals[cat_short]["usd_m"]  += usd_m
        cat_totals[cat_short]["uf"]     += uf

    # ── Pre-calcular totales para los porcentajes ──
    total_clp_pre = 0.0
    patri_clp_pre = 0.0
    for cat in CAT_ORDER:
        if cat not in cat_totals:
            continue
        total_clp_pre += cat_totals[cat]["clp_mm"]
        if "Casa" not in cat:
            patri_clp_pre += cat_totals[cat]["clp_mm"]

    def fmt_pct(v, total, style=None):
        """Formatea porcentaje relativo al total CLP MM."""
        if total == 0:
            return Text("—", style="dim")
        pct = v / total * 100
        s = f"{pct:.1f}%"
        return Text(s, style=style or ("bright_green" if pct >= 0 else "bright_red"))

    # ── Construir tabla Rich simple con 5 columnas ──
    def fmt_val(v):
        """Formatea entero con miles usando puntos; 0 en dim; negativo en rojo."""
        if v is None:
            return Text("—", style="dim")
        rounded = int(round(v))
        if rounded == 0:
            return Text("0", style="dim")
        res = f"{rounded:,}".replace(",", ".")
        color = "bright_green" if rounded > 0 else "bright_red"
        return Text(res, style=color)

    title_str = f"\n[bold sky_blue3]{title}[/bold sky_blue3]\n[dim]{rates_subtitle}[/dim]"
    table = Table(
        title=title_str,
        box=ROUNDED,
        header_style="bold sky_blue3",
        border_style="dim",
        show_footer=False,
        title_justify="center",
        row_styles=["", "on grey15"],
    )
    table.add_column("Categoría", no_wrap=True, style="bold white")
    table.add_column("CLP MM", justify="right")
    table.add_column("USD M",  justify="right")
    table.add_column("UF",     justify="right")
    table.add_column("%",      justify="right")

    total_clp_mm = 0.0
    total_usd_m  = 0.0
    total_uf     = 0.0
    patri_clp_mm = 0.0
    patri_usd_m  = 0.0
    patri_uf     = 0.0

    patri_shown = False

    for cat in CAT_ORDER:
        if cat not in cat_totals:
            continue

        d = cat_totals[cat]
        c_clp = d["clp_mm"]
        c_usd = d["usd_m"]
        c_uf  = d["uf"]

        # Insertar fila TOTAL PATRIMONIO INVERSIONES justo antes de Casa
        if "Casa" in cat and not patri_shown:
            patri_shown = True
            table.add_section()
            p_style = "bold orange3"
            table.add_row(
                Text("TOTAL PATRIMONIO INVERSIONES", style=p_style),
                Text(f"{int(round(patri_clp_mm)):,}".replace(",", "."), style=p_style),
                Text(f"{int(round(patri_usd_m)):,}".replace(",",  "."), style=p_style),
                Text(f"{int(round(patri_uf)):,}".replace(",",     "."), style=p_style),
                fmt_pct(patri_clp_mm, total_clp_pre, p_style),
            )
            table.add_section()

        table.add_row(
            cat,
            fmt_val(c_clp),
            fmt_val(c_usd),
            fmt_val(c_uf),
            fmt_pct(c_clp, total_clp_pre),
        )

        total_clp_mm += c_clp
        total_usd_m  += c_usd
        total_uf     += c_uf
        if "Casa" not in cat:
            patri_clp_mm += c_clp
            patri_usd_m  += c_usd
            patri_uf     += c_uf

    # Si "Casa" no existe en los datos, mostrar TOTAL PATRIMONIO igual
    if not patri_shown:
        table.add_section()
        p_style = "bold orange3"
        table.add_row(
            Text("TOTAL PATRIMONIO INVERSIONES", style=p_style),
            Text(f"{int(round(patri_clp_mm)):,}".replace(",", "."), style=p_style),
            Text(f"{int(round(patri_usd_m)):,}".replace(",",  "."), style=p_style),
            Text(f"{int(round(patri_uf)):,}".replace(",",     "."), style=p_style),
            fmt_pct(patri_clp_mm, total_clp_pre, p_style),
        )

    table.add_section()
    g_style = "bold orange3"
    table.add_row(
        Text("TOTAL GENERAL", style=g_style),
        Text(f"{int(round(total_clp_mm)):,}".replace(",", "."), style=g_style),
        Text(f"{int(round(total_usd_m)):,}".replace(",",  "."), style=g_style),
        Text(f"{int(round(total_uf)):,}".replace(",",     "."), style=g_style),
        Text("100%", style=g_style),
    )

    _console.print(table)

    if pause:
        input("\nPresiona Enter para volver al menú...")

def show_last_saldos(pause=True, title="ÚLTIMO REGISTRO CONOCIDO", by_category=False, snapshot_before=None, hide_zeros=False):
    """Muestra la tabla consolidada con el último valor de todos los items del catálogo.

    Args:
        pause: Si True, espera Enter al final
        title: Título de la tabla
        by_category: Si True, muestra solo resumen por categoría (sin items individuales)
        snapshot_before: Si se proporciona (ej. "2026-03-08 23:59:59"), filtra registros
                         con timestamp <= snapshot_before (foto histórica point-in-time)
    """
    if by_category:
        cat_title = title if title != "ÚLTIMO REGISTRO CONOCIDO" else "RESUMEN POR CATEGORÍA"
        show_summary_by_category(pause=pause, snapshot_before=snapshot_before, title=cat_title)
        return
    # Obtener y mostrar tipos de cambio actuales antes de la tabla
    with _console.status("[bold blue]Consultando tipos de cambio actuales...[/]"):
        rates = get_rates()

    usd_val = rates.get("USD", 0)
    uf_val  = rates.get("UF", 0)
    rates_subtitle = (f"Tipos de Cambio Aplicados:  "
                      f"USD: ${usd_val:,.0f}  UF: ${uf_val:,.0f}").replace(",", ".")

    ts_filter = f"AND timestamp <= '{snapshot_before}'" if snapshot_before else ""
    target_date = snapshot_before[:10] if snapshot_before else ""
    hist_filter = f"AND (COALESCE(s.source, 'auto') != 'historial' OR date(s.timestamp) = '{target_date}')" if target_date else "AND COALESCE(s.source, 'auto') != 'historial'"

    db_data_ok = {}
    _item_notas = {}  # notas de referencia para ítems en _NOTE_REQUIRED_ITEMS
    # Leer desde Supabase (fuente primaria)
    try:
        if snapshot_before:
            rows_raw = _sup_rpc("get_snapshot", {"ts_filter": snapshot_before})
            for r in rows_raw:
                if r.get("source") == "historial": continue
                db_data_ok[(r["institucion"], r["item"], r["persona"])] = (
                    r["categoria"], r["persona"], r["moneda"], r["monto"], r["ts"], r.get("source", "auto")
                )
        else:
            rows_raw = _read_supabase("v_latest_saldos")
            for r in rows_raw:
                if r.get("source") == "historial": continue
                db_data_ok[(r["institucion"], r["item"], r["persona"])] = (
                    r["categoria"], r["persona"], r["moneda"], r["monto"], r["timestamp"], r.get("source", "auto")
                )
        # Obtener notas para ítems que las requieren (extra_data → {"nota": "..."})
        import json as _json_notas, urllib.parse as _uparse
        for _note_item in _NOTE_REQUIRED_ITEMS:
            try:
                _note_rows = _read_supabase(
                    "saldos",
                    select="extra_data",
                    filters={"item": _note_item},
                    extra="&order=timestamp.desc&limit=1"
                )
                if _note_rows and _note_rows[0].get("extra_data"):
                    _ed = _note_rows[0]["extra_data"]
                    if isinstance(_ed, str): _ed = _json_notas.loads(_ed)
                    nota = _ed.get("nota") if isinstance(_ed, dict) else None
                    if nota:
                        _item_notas[_note_item] = nota
            except Exception:
                pass
    except Exception:
        # Fallback: SQLite local
        db_path = _db_path()
        if os.path.exists(db_path):
            conn = init_db()
            try:
                rows_raw = conn.execute(f"""
                    SELECT s.institucion, s.categoria, s.persona, s.item, s.moneda, s.monto, s.timestamp,
                           COALESCE(s.source, 'auto') AS source
                    FROM saldos s
                    INNER JOIN (
                        SELECT institucion, item, persona, MAX(timestamp) AS max_ts
                        FROM saldos WHERE ok = 1 {ts_filter}
                        GROUP BY institucion, item, persona
                    ) latest ON s.institucion = latest.institucion
                             AND s.item = latest.item
                             AND s.persona = latest.persona
                             AND s.timestamp = latest.max_ts
                    WHERE s.ok = 1 {hist_filter}
                """).fetchall()
                for inst, cat_raw, persona_raw, item, moneda, monto_int, ts, source in rows_raw:
                    db_data_ok[(inst, item, persona_raw)] = (cat_raw, persona_raw, moneda, monto_int, ts, source)
            except sqlite3.OperationalError: pass
            conn.close()

    # Obtener catálogo unificado para mostrar TODO lo que debería existir
    all_catalog = _get_unified_catalog_list()
    
    rows = []
    zero_items = []  # items con monto=0 real — se omiten de la tabla y se listan al pie
    # Ordenar por el nombre jerárquico final (A, B, C...)
    all_catalog.sort(key=lambda x: (
        CAT_ORDER.index(cat_to_short(x['cat'], x['inst'])) if cat_to_short(x['cat'], x['inst']) in CAT_ORDER else 99,
        x['inst']
    ))

    for item_meta in all_catalog:
        inst_name = item_meta['inst']
        cat = item_meta['cat']
        item_code = item_meta['item']
        catalog_moneda = item_meta['moneda']

        db_inst = _CATALOG_TO_DB_INST.get(inst_name, inst_name)
        db_key = (db_inst, item_code, cat_to_persona(cat))

        if db_key in db_data_ok:
            cat_raw, persona_raw, moneda, monto_int, ts, source = db_data_ok[db_key]
            cat_disp   = cat_to_short(cat_raw, inst_name, item_code)
            persona    = persona_raw

            # Limpiamos m_val
            try:
                if monto_int is None: m_val = 0
                else: m_val = float(monto_int)
            except: m_val = 0

            # Ítems manuales con nota (CxC, CxP) → siempre ocultar si son cero
            if m_val == 0 and item_code in _NOTE_REQUIRED_ITEMS:
                continue
            # Si el último registro es 0 y estamos en vista completa, omitir y anotar al pie
            if m_val == 0 and hide_zeros:
                zero_items.append(f"{inst_name} / {item_code}")
                continue

            monto_disp = fmt_monto(m_val)
            ts_fmt     = _fmt_ts(ts[:16] if ts else "")
            marker     = _get_source_emoji(item_code, source, inst=inst_name)
        else:
            # Sin registro en DB → si es CxC/CxP, no mostrar
            if item_code in _NOTE_REQUIRED_ITEMS:
                continue
            cat_disp   = cat_to_short(cat, inst_name)
            persona    = cat_to_persona(cat)
            monto_disp = "—"
            ts_fmt     = "—"
            marker     = _get_source_emoji(item_code, item_meta.get('type', 'manual'), inst_name)
            moneda     = catalog_moneda or "CLP"
            m_val      = 0.0
            ts         = None

        # Para ítems con nota, mostrar solo la nota en la columna Item
        item_display = item_code
        if item_code in _NOTE_REQUIRED_ITEMS and item_code in _item_notas:
            item_display = _item_notas[item_code]

        rows.append((
            cat_disp,
            inst_name,
            persona,
            item_display,
            moneda,
            monto_disp,
            m_val,
            ts_fmt,
            marker,
            ts[:10] if ts else None,
            item_meta['cat'], # full_cat (extra[0])
            item_meta.get('bank_key'), # bank_key (extra[1])
            item_code, # item_code real para DB/LAST_TABLE_MAPPING (extra[2])
        ))


    _print_table_rows(rows, title=title, subtitle=rates_subtitle)

    if zero_items and hide_zeros:
        choice = questionary.select(
            "",
            choices=["Incluir registros en cero", "Volver"],
            style=QUESTIONARY_STYLE,
        ).ask()
        if choice == "Incluir registros en cero":
            show_last_saldos(pause=pause, title=title, by_category=by_category,
                             snapshot_before=snapshot_before, hide_zeros=False)
        return
    if pause:
        input("\nPresiona Enter para volver al menú...")


# ══════════════════════════════════════════════════════════════
# SQLITE STORAGE
# ══════════════════════════════════════════════════════════════

def _db_path():
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "saldos.db")

DB_PATH = _db_path()

def init_db():
    conn = sqlite3.connect(DB_PATH, timeout=20)
    # Usar REAL para monto para soportar decimales de UF/USD
    conn.execute("""
        CREATE TABLE IF NOT EXISTS saldos (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp   TEXT    NOT NULL,
            institucion TEXT    NOT NULL,
            categoria   TEXT    NOT NULL,
            persona     TEXT    NOT NULL DEFAULT 'PN',
            item        TEXT    NOT NULL,
            moneda      TEXT    NOT NULL,
            monto       REAL,
            ok          INTEGER NOT NULL DEFAULT 1,
            source      TEXT    NOT NULL DEFAULT 'auto',
            extra_data  TEXT                   -- JSON: {shares: int, price: float}
        )
    """)
    # Migración: agregar columna extra_data si no existe en tabla existente
    try:
        conn.execute("SELECT extra_data FROM saldos LIMIT 1")
    except sqlite3.OperationalError:
        conn.execute("ALTER TABLE saldos ADD COLUMN extra_data TEXT")

    conn.execute("""
        CREATE TABLE IF NOT EXISTS catalog_manual (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            display_name TEXT    NOT NULL,
            bank_key    TEXT    NOT NULL,
            institucion TEXT    NOT NULL,
            categoria   TEXT    NOT NULL,
            item        TEXT    NOT NULL,
            moneda      TEXT    NOT NULL,
            deleted     INTEGER NOT NULL DEFAULT 0
        )
    """)
    # Migración: agregar columna deleted si no existe
    try:
        conn.execute("SELECT deleted FROM catalog_manual LIMIT 1")
    except sqlite3.OperationalError:
        conn.execute("ALTER TABLE catalog_manual ADD COLUMN deleted INTEGER NOT NULL DEFAULT 0")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS rates (
            moneda    TEXT,
            valor     REAL NOT NULL,
            date      TEXT NOT NULL,
            PRIMARY KEY (moneda, date)
        )
    """)
    # Migración: si existe la tabla vieja sin columna 'date', resetearla
    try:
        conn.execute("SELECT date FROM rates LIMIT 1")
    except sqlite3.OperationalError:
        conn.execute("DROP TABLE IF EXISTS rates")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS rates (
                moneda    TEXT,
                valor     REAL NOT NULL,
                date      TEXT NOT NULL,
                PRIMARY KEY (moneda, date)
            )
        """)
    conn.commit()
    # Migración: agregar columna persona si no existe (DB antigua)
    try:
        conn.execute("ALTER TABLE saldos ADD COLUMN persona TEXT NOT NULL DEFAULT 'PN'")
        conn.commit()
    except sqlite3.OperationalError:
        pass   # Ya existe
    # Migración: agregar columna source si no existe
    try:
        conn.execute("ALTER TABLE saldos ADD COLUMN source TEXT NOT NULL DEFAULT 'auto'")
        conn.commit()
    except sqlite3.OperationalError:
        pass   # Ya existe
    # Normalizar categorías antiguas
    try:
        conn.execute("UPDATE saldos SET persona = 'PJ' WHERE categoria LIKE '%PJ%'")
        conn.execute("UPDATE saldos SET categoria = 'CC'  WHERE categoria LIKE '%Corriente%'")
        conn.execute("UPDATE saldos SET categoria = 'TdC' WHERE categoria LIKE '%dito%'")
        conn.commit()
    except Exception:
        pass
    # Tablas TIR — inversiones semi-automáticas
    conn.execute("""
        CREATE TABLE IF NOT EXISTS tir_investments (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            institucion     TEXT    NOT NULL,
            item            TEXT    NOT NULL,
            nominal_usd     REAL    NOT NULL,
            fecha_inversion TEXT    NOT NULL,
            tir_anual       REAL    NOT NULL,
            UNIQUE(institucion, item)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS tir_dividends (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            institucion TEXT    NOT NULL,
            item        TEXT    NOT NULL,
            fecha       TEXT    NOT NULL,
            monto_usd   REAL    NOT NULL
        )
    """)
    # Seed TIR investments (INSERT OR IGNORE = solo si no existe)
    tir_seed = [
        ("HDZ",  "Tucson I",      30000.0, "2023-04-17", 0.1290),
        ("HDZ",  "Kansas I",      40000.0, "2023-10-27", 0.1193),
        ("HDZ",  "Tucson II",     20000.0, "2025-05-03", 0.1150),
        ("HDZ",  "Tucson III",    25000.0, "2026-04-27", 0.1200),
        ("WBuild", "José Ignacio",  30000.0, "2025-09-10", 0.1800),
    ]
    for _ti_inst, _ti_item, _ti_nom, _ti_fecha, _ti_tir in tir_seed:
        conn.execute(
            "INSERT OR IGNORE INTO tir_investments (institucion, item, nominal_usd, fecha_inversion, tir_anual) VALUES (?, ?, ?, ?, ?)",
            (_ti_inst, _ti_item, _ti_nom, _ti_fecha, _ti_tir)
        )
    # Seed dividend: Dorco Tucson II — $600 (2025-12-01)
    conn.execute("""
        INSERT INTO tir_dividends (institucion, item, fecha, monto_usd)
        SELECT 'HDZ', 'Tucson II', '2025-12-01', 600.0
        WHERE NOT EXISTS (
            SELECT 1 FROM tir_dividends WHERE institucion='HDZ' AND item='Tucson II' AND fecha='2025-12-01'
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS script_config (
            key   TEXT PRIMARY KEY,
            value TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS scraper_config (
            bank_key       TEXT PRIMARY KEY,
            name           TEXT NOT NULL,
            active         INTEGER NOT NULL DEFAULT 1,
            in_inversiones INTEGER NOT NULL DEFAULT 0,
            in_bancarios   INTEGER NOT NULL DEFAULT 0,
            updated_at     TEXT
        )
    """)
    # Migraciones: agregar columnas nuevas si no existen
    for _col_sql in [
        "ALTER TABLE scraper_config ADD COLUMN in_inversiones INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE scraper_config ADD COLUMN in_bancarios   INTEGER NOT NULL DEFAULT 0",
    ]:
        try:
            conn.execute(_col_sql)
            conn.commit()
        except Exception:
            pass
    conn.execute("""
        CREATE TABLE IF NOT EXISTS pagos_tdc (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp     TEXT NOT NULL,
            institucion   TEXT NOT NULL,
            card_number   TEXT NOT NULL,
            card_name     TEXT,
            periodo_hasta TEXT,
            pagar_hasta   TEXT,
            facturado_clp REAL,
            pagado_clp    REAL,
            facturado_usd REAL,
            pagado_usd    REAL
        )
    """)
    # Migraciones pagos_tdc
    try:
        conn.execute("ALTER TABLE pagos_tdc DROP COLUMN periodo_desde")
        conn.commit()
    except Exception:
        pass
    try:
        conn.execute("ALTER TABLE pagos_tdc ADD COLUMN no_facturado_clp REAL")
        conn.commit()
    except Exception:
        pass
    # Migración credit_limits: agregar cupo_usd si no existe
    try:
        conn.execute("ALTER TABLE credit_limits ADD COLUMN cupo_usd REAL NOT NULL DEFAULT 0")
        conn.commit()
    except Exception:
        pass
    conn.commit()
    return conn

def _get_config(key, default=None):
    """Obtiene un valor de configuración persistente. Fuente primaria: Supabase."""
    try:
        rows = _read_supabase("script_config", {"key": key}, select="value")
        if rows:
            val = rows[0]["value"]
            try:
                if "." in str(val) or str(val).replace("-","").isdigit():
                    return float(val)
            except: pass
            return val
        return default
    except Exception:
        pass
    # Fallback: SQLite local
    try:
        db_path = _db_path()
        if not os.path.exists(db_path): return default
        conn = sqlite3.connect(db_path, timeout=20)
        row = conn.execute("SELECT value FROM script_config WHERE key = ?", (key,)).fetchone()
        conn.close()
        if row:
            val = row[0]
            try:
                if "." in val or val.replace("-","").isdigit():
                    return float(val)
            except: pass
            return val
        return default
    except Exception as e:
        _console.print(f"[dim red]Error leyendo config {key}: {e}[/dim red]")
        return default

def _set_config(key, value):
    """Guarda un valor de configuración persistente en Supabase y SQLite."""
    # Supabase (primario)
    try:
        _sync_supabase("script_config", [{"key": key, "value": str(value)}])
    except Exception as e:
        if DEBUG: print(f"[Supabase] Config write ERROR: {e}")
    # SQLite (backup)
    try:
        conn = sqlite3.connect(_db_path(), timeout=20)
        conn.execute("CREATE TABLE IF NOT EXISTS script_config (key TEXT PRIMARY KEY, value TEXT)")
        conn.execute("INSERT OR REPLACE INTO script_config (key, value) VALUES (?, ?)", (key, str(value)))
        conn.commit()
        conn.close()
        _console.print(f"[dim green]Configuración '{key}' guardada correctamente.[/dim green]")
    except Exception as e:
        _console.print(f"[bold red]Error guardando config {key}: {e}[/bold red]")

def save_to_db(resultados):
    """Guarda la lista de dicts en la DB."""
    if not resultados: return
    conn = sqlite3.connect(_db_path())
    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    to_sync = []
    for r in resultados:
        # Regla universal: NULL o 0 no sobreescriben un valor previo significativo.
        # Si el scraper no obtuvo nada (None) o leyó 0 cuando antes había un valor real,
        # se descarta el registro y se conserva el último bueno en DB.
        monto_nuevo = r["monto_int"]
        # Items que legítimamente pueden ser 0 (el 0 es información válida, no un bug)
        if monto_nuevo is None:
            persona = r.get("persona", cat_to_persona(r["cat"]))
            # Buscar valor previo en Supabase (fuente primaria)
            prev_val = None
            try:
                prev_rows = _read_supabase(
                    "v_latest_saldos",
                    {"institucion": r["inst"], "item": r["item"], "persona": persona},
                    select="monto",
                    extra="&monto=neq.0"
                )
                prev_val = float(prev_rows[0]["monto"]) if prev_rows else None
            except Exception:
                # Fallback SQLite
                prev = conn.execute(
                    """SELECT monto FROM saldos
                       WHERE institucion=? AND item=? AND persona=?
                         AND monto IS NOT NULL AND monto != 0
                       ORDER BY timestamp DESC LIMIT 1""",
                    (r["inst"], r["item"], persona)
                ).fetchone()
                prev_val = prev[0] if prev else None
            # Si había un valor previo significativo (>100K en absoluto), no guardar el 0/None
            if prev_val is not None and abs(prev_val) > 100_000:
                if DEBUG:
                    print(f"[DB] Skip {r['inst']} {r['item']}: nuevo={monto_nuevo}, prev={prev_val:,.0f}")
                continue
            # Si el previo era pequeño o no había previo, y el nuevo es None → igual skip
            if monto_nuevo is None:
                continue
            # Si el nuevo es 0 y el previo también era pequeño/inexistente → guardar el 0

        # RESPETAR CLASIFICACIÓN ESTABLECIDA:
        row = conn.execute(
            "SELECT categoria FROM catalog_manual WHERE institucion = ? AND item = ?",
            (r["inst"], r["item"])
        ).fetchone()

        final_cat = row[0] if row else r["cat"]

        conn.execute("""
            INSERT INTO saldos (timestamp, institucion, categoria, persona, item, moneda, monto, ok, source, extra_data)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            now_str,
            r["inst"],
            final_cat,
            r.get("persona", cat_to_persona(final_cat)),
            r["item"],
            r["moneda"],
            r["monto_int"],
            1 if r["ok"] else 0,
            "manual" if r.get("manual") else "auto",
            r.get("extra_data")  # Para Shares × Price: {shares: int, price: float}
        ))
        
        # Guardar para sync
        to_sync.append({
            "timestamp": now_str,
            "institucion": r["inst"],
            "categoria": final_cat,
            "persona": r.get("persona", cat_to_persona(final_cat)),
            "item": r["item"],
            "moneda": r["moneda"],
            "monto": r["monto_int"],
            "ok": 1 if r["ok"] else 0,
            "source": "manual" if r.get("manual") else "auto",
            "extra_data": r.get("extra_data")
        })
    conn.commit()
    conn.close()
    
    # Mirror a Supabase (Espejo)
    _sync_supabase("saldos", to_sync)
    
    # Generar reportes automáticamente
    try:
        generate_report_markdown()
    except Exception as e:
        if DEBUG: print(f"[DB] Error generando reportes: {e}")


def generate_report_markdown():
    """Actualiza el archivo 'Reporte Saldos' con la lista completa de ítems."""
    output_path = Path(__file__).parent / "Reporte Saldos"
    # Leer desde Supabase (fuente primaria)
    raw = []
    try:
        raw = _read_supabase("v_latest_saldos",
                             select="institucion,categoria,persona,item,moneda,monto,timestamp,source",
                             extra="&source=neq.historial")
        rows = [(r["institucion"], r["categoria"], r["persona"], r["item"],
                 r["moneda"], r["monto"], r["timestamp"], r.get("source","auto")) for r in raw]
    except Exception:
        rows = []
    if not rows:
        # Fallback: SQLite
        conn = init_db()
        rows = conn.execute("""
            SELECT s.institucion, s.categoria, s.persona, s.item, s.moneda, s.monto, s.timestamp,
                   COALESCE(s.source, 'auto') AS source
            FROM saldos s
            INNER JOIN (
                SELECT institucion, item, persona, MAX(timestamp) AS max_ts
                FROM saldos WHERE ok = 1 GROUP BY institucion, item, persona
            ) latest ON s.institucion = latest.institucion AND s.item = latest.item
                     AND s.persona = latest.persona AND s.timestamp = latest.max_ts
            WHERE s.ok = 1 AND COALESCE(s.source, 'auto') != 'historial'
        """).fetchall()
        conn.close()

    if not rows: return

    data = []
    for inst, cat_raw, persona_raw, item, moneda, monto, ts, source in rows:
        persona = _normalize_persona(cat_raw, persona_raw)
        # Monto preciso
        if moneda == "UF":
            monto_preciso = f"{monto:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
        elif moneda == "USD":
            monto_preciso = f"$ {monto:,.2f}"
        else:
            monto_preciso = f"{int(round(monto or 0)):,}".replace(",", ".")

        emoji = _get_source_emoji(item, source, inst=inst)
        status = f"{emoji} Ok"
        
        data.append({
            "cat_short": cat_to_short(cat_raw)[:5], 
            "inst": inst, "item": item, "persona": persona,
            "moneda": moneda, "monto": monto_preciso, "status": status,
            "cat_full": cat_to_short(cat_raw)
        })

    # Ordenar
    _cats_short = [cat_to_short(c) for c in CAT_ORDER]
    data.sort(key=lambda x: (_cats_short.index(x["cat_full"]) if x["cat_full"] in _cats_short else 99, x["inst"]))

    lines = [
        "#  Reporte Detallado de Saldos (Hoy)",
        "",
        f"Este reporte contiene la lista completa de los **{len(data)} ítems** procesados por el script. Actualizado: {datetime.datetime.now().strftime('%d/%m/%Y %H:%M')}",
        "",
        "| Categoría | Institución | Item | Persona | Moneda | Monto (Preciso) | Estado |",
        "| :--- | :--- | :--- | :--- | :--- | :--- | :--- |"
    ]

    for d in data:
        lines.append(f"| **{d['cat_short']}** | {d['inst']} | {d['item']} | {d['persona']} | {d['moneda']} | **{d['monto']}** | {d['status']} |")

    lines.append("\n> [!NOTE]")
    lines.append("> [AUTO] = Actualizado automáticamente (scraping).")
    lines.append(">  = Semi-automático (precio buscado online, acciones manuales) — CFIETFCD/CFINASDAQ.")
    lines.append(">  = Entrada manual o de sesión anterior.")

    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    if DEBUG: print(f"[REPORTE] Generado en: {output_path}")
    conn.close()


# ══════════════════════════════════════════════════════════════
# SCRAPING POR BANCO
# Cada función: (context, resultados) → True si todo OK, False si algo falló
# ══════════════════════════════════════════════════════════════

_HARVARD_STATE = Path(__file__).parent / "harvard.json"

def scrape_harvard(context, resultados):
    key = "harvard"
    iso_context = None
    page = None
    try:
        print("[HARV] Obteniendo credenciales...")
        # bw list + filtro por URL (el entry puede llamarse "harvardfcu.org" u otro nombre)
        import json as _json_harv
        _bw_result = subprocess.run(["bw", "list", "items", "--search", "harvardfcu"],
                                    capture_output=True, text=True, env=bw_env())
        _bw_items = _json_harv.loads(_bw_result.stdout)
        if not _bw_items:
            raise Exception("[HARV] No se encontró entry de Harvard FCU en Bitwarden")
        _bw_item = _bw_items[0]
        username = _bw_item["login"]["username"]
        pwd      = _bw_item["login"]["password"]
        print(f"[HARV] Credenciales obtenidas: entry='{_bw_item['name']}', user='{username[:3]}...' ({len(username)} chars)")

        ctx_kwargs = {"viewport": {"width": 1280, "height": 800}}
        if _HARVARD_STATE.exists():
            ctx_kwargs["storage_state"] = str(_HARVARD_STATE)
            if DEBUG: print("[HARV] Cargando sesión persistente desde harvard.json...")

        iso_context = context.browser.new_context(**ctx_kwargs)
        iso_context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
        page = iso_context.new_page()

        page.goto("https://my.harvardfcu.org/", timeout=60000)
        page.wait_for_timeout(3000)

        # ── LOGIN ───────────────────────────────────────────────────────
        def _harv_do_login():
            """Llena credenciales y hace click en Log in. Retorna True si llegó al dashboard."""
            print("[HARV] Login requerido. Llenando credenciales — WARNING:   NO hagas click, el script lo hace automáticamente...")

            # Username
            uname_loc = page.locator("#username").first
            uname_loc.wait_for(state="visible", timeout=10000)
            uname_loc.click()
            page.wait_for_timeout(300)
            uname_loc.press_sequentially(username, delay=80)
            page.wait_for_timeout(400)

            # Si el usuario ya clickeó y la página navegó, salir temprano
            if "authentication" not in page.url.lower():
                print("[HARV] Página navegó durante llenado — continuando...")
                return "dashboard" in page.url.lower()

            # Password
            pwd_loc = page.locator("#password").first
            pwd_loc.click()
            page.wait_for_timeout(300)
            pwd_loc.press_sequentially(pwd, delay=80)
            page.wait_for_timeout(500)

            # Verificar si hubo error de credenciales (click prematuro del usuario)
            error_visible = page.evaluate("""() => {
                const err = document.querySelector('.irisv-notification--error, [class*="error"], [class*="alert"]');
                return !!err && err.offsetParent !== null;
            }""")
            if error_visible:
                print("[HARV] WARNING:   Error de login detectado (credenciales incompletas). Recargando...")
                page.reload()
                page.wait_for_timeout(2000)
                return False

            # Esperar Cloudflare Turnstile
            print("[HARV] Esperando Cloudflare Turnstile (hasta 30s)...")
            submit_btn = page.locator("button[type='submit'], #btn_submitCredentials").first
            submit_btn.wait_for(state="visible", timeout=10000)
            for _t in range(60):
                if "authentication" not in page.url.lower():
                    print("[HARV] Página navegó durante Turnstile — continuando...")
                    return "dashboard" in page.url.lower()
                is_disabled = page.evaluate("""() => {
                    const btn = document.querySelector('#btn_submitCredentials, button[type="submit"]');
                    return !btn || btn.disabled || btn.getAttribute('aria-disabled') === 'true';
                }""")
                if not is_disabled:
                    print(f"[HARV] [OK]  Turnstile ok (intento {_t+1}) — haciendo click...")
                    break
                page.wait_for_timeout(500)
            else:
                print("[HARV] WARNING:   Turnstile no resolvió en 30s — puedes hacer click manualmente en el browser")

            if "authentication" in page.url.lower():
                submit_btn.click()
            print("[HARV] Click en 'Log in'. Esperando Dashboard...")
            return None  # continuar esperando

        if "authentication" in page.url.lower():
            # Hasta 2 intentos (por si el usuario clickeó prematuramente)
            for _login_attempt in range(2):
                result = _harv_do_login()
                if result is True:
                    break   # ya en dashboard
                if result is False:
                    continue  # reintentando tras reload
                break       # None = click enviado, esperar dashboard normalmente

            # Esperar redirección al Dashboard (hasta 60s automático)
            reached = False
            for _ in range(30):
                if "dashboard" in page.url.lower():
                    reached = True
                    break
                page.wait_for_timeout(2000)

            # Si Cloudflare/MFA bloquean, dar tiempo para resolución manual
            if not reached:
                print("[HARV] WARNING:   No se llegó al Dashboard automáticamente.")
                print("[HARV]     Si hay Cloudflare/MFA pendiente, resuélvelo manualmente (quedan 60s).")
                for _ in range(30):
                    if "dashboard" in page.url.lower():
                        reached = True
                        break
                    page.wait_for_timeout(2000)

            if not reached:
                raise Exception(f"No se llegó al Dashboard tras login. URL actual: {page.url}")

        # ── SESIÓN PERSISTENTE ──────────────────────────────────────────
        print("[HARV] En Dashboard. Guardando sesión persistente...")
        page.wait_for_timeout(3000)
        iso_context.storage_state(path=str(_HARVARD_STATE))
        if DEBUG and _HARVARD_STATE.exists():
            print(f"[HARV] Sesión guardada ({_HARVARD_STATE.stat().st_size} bytes).")

        # ── EXTRACCIÓN ──────────────────────────────────────────────────
        page.wait_for_selector("#module_accounts li[id^='account_']", timeout=15000)
        page.wait_for_timeout(1000)

        balances = page.evaluate("""
            () => {
                function getBalance(suffix) {
                    const lis = document.querySelectorAll('#module_accounts li[id^="account_"]');
                    for (const li of lis) {
                        const spans = li.querySelectorAll('span');
                        const match = [...spans].find(s => s.textContent.trim().includes(suffix));
                        if (match) {
                            const balSpans = li.querySelectorAll('.balance-double span');
                            if (balSpans.length > 0) {
                                return balSpans[0].textContent.trim();
                            }
                        }
                    }
                    return null;
                }
                return {
                    checking: getBalance('5440'),
                    savings:  getBalance('5400')
                };
            }
        """)

        # Parsear formato US: $5,001.50 → coma=miles, punto=decimal → float
        def parse_usd_us(s):
            if not s: return 0.0
            return float(s.replace('$', '').replace(',', '').strip())

        val_chk = parse_usd_us(balances.get('checking'))
        val_sav = parse_usd_us(balances.get('savings'))
        print(f"[HARV] Checking 5440: ${val_chk:.2f} | Savings 5400: ${val_sav:.2f}")

        add_result_usd(resultados, key, "Harvard FCU", "Cash", "Checking 5440", str(val_chk))
        print_preliminary("Harvard FCU", "Cash", "Checking 5440", str(val_chk), moneda="USD")

        add_result_usd(resultados, key, "Harvard FCU", "Cash", "Savings 5400", str(val_sav))
        print_preliminary("Harvard FCU", "Cash", "Savings 5400", str(val_sav), moneda="USD")

        page.close()
        iso_context.close()
        return True

    except Exception as e:
        import traceback
        _slog('HARV', 'error', f"{type(e).__name__}: {e}")
        traceback.print_exc()
        add_result_usd(resultados, key, "Harvard FCU", "Cash", "Checking 5440", "error", ok=False)
        add_result_usd(resultados, key, "Harvard FCU", "Cash", "Savings 5400", "error", ok=False)
        if page:
            try: page.close()
            except: pass
        if iso_context:
            try: iso_context.close()
            except: pass
        return False

def scrape_lider_bci(context, resultados):
    key = "lider_bci"
    page = None
    try:
        print("[LID] Obteniendo credenciales...")
        rut = bw_get("username", "liderbciserviciosfinancieros.cl")
        pwd = bw_get("password", "liderbciserviciosfinancieros.cl")
        _slog('LID', 'creds', f"rut={rut}")

        print("[LID] Login...")
        page = context.new_page()
        page.goto("https://www.liderbciserviciosfinancieros.cl/login")
        page.wait_for_timeout(2000)
        page.get_by_placeholder("Rut").wait_for(timeout=10000)
        page.get_by_placeholder("Rut").fill(rut)
        page.get_by_placeholder("Rut").evaluate("e => e.dispatchEvent(new Event('input', { bubbles: true }))")
        page.wait_for_timeout(500)
        page.get_by_placeholder("Clave de internet").fill(pwd)
        page.get_by_placeholder("Clave de internet").evaluate("e => e.dispatchEvent(new Event('input', { bubbles: true }))")
        page.wait_for_timeout(500)
        
        # Esperar que el botón se habilite
        btn = page.get_by_role("button", name="Ingresar")
        try:
            page.wait_for_selector("button:not([disabled]):has-text('Ingresar')", timeout=10000)
        except:
            if DEBUG: print("[LID] El botón parece seguir deshabilitado, forzando click...")
        
        btn.click()
        page.wait_for_timeout(2000)
        print("  WARNING:   CAPTCHA posible — resuélvelo en el browser si aparece (90s)...", flush=True)
        page.wait_for_url("**/dashboard**", timeout=90000)
        _slog('LID', 'login', 'ok')
        page.wait_for_timeout(2000)
        try:
            page.locator("dialog button").first.wait_for(state="visible", timeout=5000)
            page.locator("dialog button").first.click()
            page.wait_for_timeout(500)
        except:
            pass
        page.locator("table.balance").first.wait_for(state="visible", timeout=15000)
        deuda_raw = page.locator("table.balance td").first.text_content().strip()
        print(f"[LID] Monto: '{deuda_raw}'")
        deuda = deuda_raw.replace("$", "").replace(".", "").strip()
        monto = f"-{deuda}" if deuda not in ("0", "") else "0"
        add_result(resultados, key, "Líder BCI", "TdC", "TdC 5037", monto)
        print_preliminary("Líder BCI", "TdC", "TdC 5037", monto)

        # --- PAGOS TdC Líder BCI ---
        try:
            global _PAGOS_ERRORS

            # 1. Saldos tab → "Detalle de tu última facturación"
            page.goto("https://www.liderbciserviciosfinancieros.cl/private-home/my-card/balances")
            page.wait_for_selector("text=Cargos del mes", timeout=20000)
            page.wait_for_timeout(2000)

            # Obtener el texto del container más pequeño que incluye todos los campos
            section_text = page.evaluate("""() => {
                const keywords = ['Cargos del mes', 'Fecha de facturación', 'Pagar hasta el'];
                const all = Array.from(document.querySelectorAll('*'));
                const candidates = all.filter(el => {
                    const t = el.innerText || '';
                    return keywords.every(k => t.includes(k)) && t.length < 600;
                });
                if (!candidates.length) return document.body.innerText.substring(0, 2000);
                candidates.sort((a, b) => (a.innerText||'').length - (b.innerText||'').length);
                return candidates[0].innerText;
            }""")

            import re as _re
            # Parsear montos CLP: ej. "$3.577.637" — el primero es "Cargos del mes"
            amounts = _re.findall(r'\$[\d.]+', section_text or '')
            def _parse_clp(s):
                if not s: return None
                try: return int(s.replace("$","").replace(".","").replace(",","").strip())
                except: return None
            facturado_clp = _parse_clp(amounts[0]) if amounts else None

            # Parsear fechas DD/MM/YYYY
            dates = _re.findall(r'\d{2}/\d{2}/\d{4}', section_text or '')
            def _parse_lider_date(s):
                if not s: return None
                try: return datetime.datetime.strptime(s.strip(), "%d/%m/%Y").strftime("%d/%m/%Y")
                except: return s
            periodo_hasta = _parse_lider_date(dates[0]) if len(dates) > 0 else None
            pagar_hasta   = _parse_lider_date(dates[1]) if len(dates) > 1 else None
            _slog("LID-PAG", "info", f"fac={facturado_clp} | periodo={periodo_hasta} | pagar={pagar_hasta}")

            # 2. Movimientos → "Por facturar" → Abonos → sumar por tab
            page.goto("https://www.liderbciserviciosfinancieros.cl/private-home/my-card/movements")
            page.wait_for_selector("text=Por facturar", timeout=15000)
            page.wait_for_timeout(2000)

            # Seleccionar explícitamente "Por facturar" (por si quedó otro período activo)
            try:
                labels = page.locator("label").filter(has_text="Por facturar")
                if labels.count() > 0:
                    labels.first.locator("input[type='radio']").click()
                    page.wait_for_timeout(800)
            except Exception:
                pass

            def _select_abonos():
                page.locator("select[name='selectD']").select_option("2")
                page.wait_for_timeout(1500)

            def _sum_rows():
                """Suma montos de las filas desktop (4 celdas) — tabla sin tbody, sin paginación."""
                return page.evaluate("""() => {
                    let t = 0;
                    const rows = document.querySelectorAll(
                        'table.ssff-lastMovements-grid tr.ssff-lastMovements-grid-row'
                    );
                    for (const row of rows) {
                        const cells = row.querySelectorAll('td');
                        if (cells.length !== 4) continue;  // saltar filas mobile (2 celdas)
                        const txt = cells[3].textContent.trim();
                        const clean = txt.replace(/[$\\s.]/g, '').replace(',', '.');
                        const val = parseFloat(clean);
                        if (!isNaN(val)) t += val;
                    }
                    return Math.round(t);
                }""")

            # Nacionales (tab activo por defecto) → pagado_clp
            _select_abonos()
            pagado_clp = _sum_rows()
            _slog("LID-PAG", "pagos", f"CLP = {abs(pagado_clp)}")

            # Internacionales → pagado_usd
            page.locator(".tab").filter(has_text="Internacionales").click()
            page.wait_for_timeout(1500)
            _select_abonos()
            pagado_usd_raw = _sum_rows()
            pagado_usd = pagado_usd_raw if pagado_usd_raw != 0 else None
            _slog("LID-PAG", "pagos", f"USD = {pagado_usd_raw}")

            # 3. Guardar en DB + Supabase
            ts_pag = datetime.datetime.now().isoformat()
            _save_pago_tdc({"timestamp": ts_pag, "institucion": "Líder BCI",
                "card_number": "5037", "card_name": "Visa",
                "periodo_hasta": periodo_hasta, "pagar_hasta": pagar_hasta,
                "facturado_clp": facturado_clp, "pagado_clp": abs(pagado_clp or 0) or None,
                "facturado_usd": None, "pagado_usd": abs(pagado_usd or 0) or None,
                "no_facturado_clp": None})

            _console.print(_fmt_pagos_log(
                "Líder BCI", "5037",
                fac_clp=facturado_clp, pag_clp=pagado_clp,
                fac_usd=None, pag_usd=pagado_usd if pagado_usd else None,
            ), highlight=False)

        except Exception as e_pag:
            print(f"[LID] Error pagos TdC: {e_pag}")
            _PAGOS_ERRORS.append({"inst": "Líder BCI", "card": "TdC 5037", "error": str(e_pag)})

        page.close()
        return True
    except Exception as e:
        import traceback
        _slog('LID', 'error', f"{type(e).__name__}: {e}")
        traceback.print_exc()
        add_result(resultados, key, "Líder BCI", "TdC", "TdC 5037", "error", ok=False)
        print_preliminary("Líder BCI", "TdC", "TdC 5037", str(e), ok=False)
        try:
            if page: page.close()
        except: pass
        return False


def scrape_banco_chile(context, resultados):
    """
    Extrae saldos desde Banco de Chile.

    URLs procesadas:
    - Login:  https://sitiospublicos.bancochile.cl/personas → "Banco en Línea"
    - CC:     https://portalpersonas.bancochile.cl/mibancochile-web/front/persona/...
    - TdC:    #/tarjeta-credito/consultar/saldos
    - LdC:    #/movimientos/linea/saldos-movimientos/

    Selectores clave:
    - CC: .monto-cuenta.nth(1)
    - TdC: p.lead-title:has-text("Utilizado") → span.number
    - LdC: fenix-movimientos-cuenta p.list-item:has-text("Monto utilizado")

    Retorna: True si éxito, False si falló algún item
    """
    key = "banco_chile"
    added = set()
    page = None
    try:
        print("[BCH-PN] Obteniendo credenciales...")
        rut = bw_get("username", "login.portales.bancochile.cl")
        pwd = bw_get("password", "login.portales.bancochile.cl")
        _slog('BCH-PN', 'creds', f"rut={rut}")

        print("[BCH-PN] Login...")
        page = context.new_page()
        # Login — nueva estructura feb 2026: input[name="userRut"] / input[name="userPassword"]
        page.goto("https://sitiospublicos.bancochile.cl/personas")
        page.wait_for_timeout(2000)
        page.get_by_role("link", name="Banco en Línea").click()
        page.locator("input[name='userRut']").wait_for(state="visible", timeout=15000)
        page.locator("input[name='userRut']").click()
        page.locator("input[name='userRut']").press_sequentially(rut, delay=60)
        page.locator("input[name='userPassword']").click()
        page.locator("input[name='userPassword']").press_sequentially(pwd, delay=60)
        # Esperar que el botón se habilite (validación Angular/React)
        btn = page.locator("button#ppriv_per-login-click-ingresar-login")
        for _ in range(20):
            if btn.is_enabled():
                break
            page.wait_for_timeout(500)
        btn.click()
        print("[BCH-PN] Esperando Dashboard o Promociones...")
        dashboard_sel = ".monto-cuenta, #main-dashboard"
        promo_sel     = "a:has-text('No ver Más'), button[aria-label='Cerrar']"
        
        for _ in range(10):
            if page.locator(dashboard_sel).first.is_visible():
                break
            if page.locator(promo_sel).first.is_visible():
                if DEBUG: print("[BCH-PN] Promo detectada, cerrando...")
                page.locator(promo_sel).first.click()
                page.wait_for_timeout(2000)
            page.wait_for_timeout(3000)

        page.locator(".monto-cuenta").nth(1).wait_for(timeout=20000)
        saldo_cc = page.locator(".monto-cuenta").nth(1).text_content().strip().replace("$", "").strip()
        _slog('BCH-PN', 'saldo', f'CC = {saldo_cc}')
        add_result(resultados, key, "Banco de Chile", "CC PN", "CC 5809", saldo_cc)
        print_preliminary("Banco de Chile", "CC PN", "CC 5809", saldo_cc)
        added.add("CC 5809")
        # TdC
        page.goto("https://portalpersonas.bancochile.cl/mibancochile-web/front/persona/index.html#/tarjeta-credito/consultar/saldos")
        page.wait_for_timeout(3000)
        utilizado_label = page.locator("p.lead-title", has_text="Utilizado").first
        utilizado_label.wait_for(state="visible", timeout=15000)
        deuda_raw = utilizado_label.locator("xpath=..").locator("span.number").text_content()
        deuda = deuda_raw.strip().replace("$ ", "").replace("$", "").strip()
        _slog('BCH-PN', 'saldo', f'TdC = {deuda}')
        monto = f"-{deuda}" if deuda != "0" else "0"
        add_result(resultados, key, "Banco de Chile", "TdC", "TdC 7164", monto)
        print_preliminary("Banco de Chile", "TdC", "TdC 7164", monto)
        added.add("TdC 7164")

        # ── LdC ──────────────────────────────────────────────────
        # Estructura real: p.list-item "Monto utilizado" está en col izquierda de un div.row;
        # el valor span.number está en la col derecha del mismo div.row
        page.goto("https://portalpersonas.bancochile.cl/mibancochile-web/front/persona/index.html#/movimientos/linea/saldos-movimientos/")
        page.wait_for_load_state("load")
        page.wait_for_timeout(5000)
        ldc_label = page.locator("p.list-item", has_text="Monto utilizado").first
        ldc_label.wait_for(state="visible", timeout=20000)
        # Subir al div.row MÁS CERCANO [1] — sin [1] agarra la fila de "Saldo disponible"
        ldc_raw = ldc_label.locator("xpath=ancestor::div[contains(@class,'row')][1]//span[contains(@class,'number')]").first.text_content(timeout=8000).strip()
        ldc = ldc_raw.replace("$", "").replace(".", "").replace(" ", "").strip()
        _slog('BCH-PN', 'saldo', f'LdC = {ldc}')
        monto_ldc = f"-{ldc}" if ldc not in ("0", "") else "0"
        add_result(resultados, key, "Banco de Chile", "LdC", "LdC", monto_ldc)
        print_preliminary("Banco de Chile", "LdC", "LdC", monto_ldc)
        added.add("LdC")

        # ── CH (Crédito Hipotecario) — UF ────────────────────────
        try:
            ch_item_name = "CH New"
            page.goto("https://portalpersonas.bancochile.cl/mibancochile-web/front/persona/index.html#/credito-hipotecario/main/consulta/informe")
            page.wait_for_load_state("load", timeout=15000)
            page.locator("p", has_text="Costo Total del Prepago (UF)").first.wait_for(state="attached", timeout=20000)
            ch_raw = page.evaluate("""() => {
                const p = Array.from(document.querySelectorAll('p'))
                              .find(e => e.textContent.trim() === 'Costo Total del Prepago (UF)');
                if (!p) return null;
                let node = p.parentElement;
                while (node) {
                    if (node.className && node.className.includes('col-4'))
                        return node.nextElementSibling?.textContent?.trim() || null;
                    node = node.parentElement;
                }
                return null;
            }""")
            if not ch_raw:
                raise Exception("Costo Total del Prepago (UF) no encontrado en DOM")
            _slog('BCH-PN', 'saldo', f'CH = {ch_raw}')
            add_result_uf(resultados, key, "Banco de Chile", "CH", ch_item_name, ch_raw)
            print_preliminary("Banco de Chile", "CH", ch_item_name, "-" + ch_raw.lstrip("-"), moneda="UF")
            added.add(ch_item_name)
        except Exception as e_ch:
            print(f"[BCH-PN] Error CH: {e_ch}")
            add_result(resultados, key, "Banco de Chile", "CH", "CH New", "error", ok=False)
            print_preliminary("Banco de Chile", "CH", "CH New", str(e_ch)[:60], ok=False)
            added.add("CH New")

        # ── Pagos TdC ─────────────────────────────────────────────
        try:
            print("[BCH-PN] Extrayendo pagos TdC...")
            ts_now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            BASE_TC = ("https://portalpersonas.bancochile.cl/mibancochile-web"
                       "/front/persona/index.html#/tarjeta-credito/consultar")

            # ── Facturados ────────────────────────────────────────
            page.goto(f"{BASE_TC}/facturados")
            page.wait_for_timeout(4000)
            page.locator("p.lead-title").first.wait_for(state="visible", timeout=15000)

            data_fac = page.evaluate("""() => {
                const lines = document.body.innerText
                    .split('\\n').map(l => l.trim()).filter(l => l.length > 0);
                const after = (lbl) => {
                    const i = lines.indexOf(lbl);
                    return i !== -1 ? lines[i + 1] : null;
                };
                // Monto Facturado: primer p.lead-title → span.number
                const leadTitles = document.querySelectorAll('p.lead-title');
                const fac_clp_raw = leadTitles[0]?.nextElementSibling?.innerText?.trim() || null;
                const fac_usd_raw = leadTitles[1]?.nextElementSibling?.innerText?.trim() || null;
                // Fecha de facturación y pagar hasta desde lista
                const cols = Array.from(document.querySelectorAll(
                    'div.col-12.col-sm-auto.col-print-auto, div.col-sm-auto'
                ));
                const colAfter = (lbl) => {
                    const idx = cols.findIndex(c => c.innerText?.trim() === lbl);
                    return idx !== -1 ? cols[idx + 1]?.innerText?.trim() : null;
                };
                return {
                    facturado_clp: fac_clp_raw,
                    facturado_usd: fac_usd_raw,
                    fecha_facturacion: colAfter('Fecha de facturación'),
                    pagar_hasta: colAfter('Pagar hasta'),
                };
            }""")

            def _bch_parse_clp(s):
                if not s: return None
                return float(s.replace("$", "").replace(".", "").replace(",", "").strip() or "0")

            def _bch_parse_usd(s):
                if not s: return None
                s = s.upper().replace("USD", "").strip()
                s = s.replace(".", "").replace(",", ".")
                try: return round(float(s), 2)
                except: return None

            fac_clp = _bch_parse_clp(data_fac.get("facturado_clp"))
            fac_usd = _bch_parse_usd(data_fac.get("facturado_usd"))
            periodo_hasta = data_fac.get("fecha_facturacion")
            pagar_hasta   = data_fac.get("pagar_hasta")

            # ── No-facturados: buscar pagos (montos negativos) ────
            page.goto(f"{BASE_TC}/saldos")
            # Esperar señal de que la página terminó de cargar:
            # el SPA siempre muestra "Utilizado" o "El X de mes..." antes que la tabla.
            # Usamos wait_for_selector sobre un texto que aparece solo post-carga.
            page.locator("text=Utilizado").first.wait_for(state="visible", timeout=20000)
            # Dar tiempo adicional al SPA para renderizar la tabla de movimientos
            page.wait_for_timeout(2000)

            pago_data = page.evaluate("""() => {
                let pagado_clp = 0, pagado_usd = 0;
                // Buscar tablas de movimientos no facturados
                document.querySelectorAll('table').forEach(tbl => {
                    const headers = Array.from(tbl.querySelectorAll('th'))
                        .map(th => th.innerText.trim());
                    // Buscar columnas "Pago ($)" y "Pago (USD)" por header
                    const pagoClpIdx = headers.findIndex(h => h === 'Pago ($)');
                    const pagoUsdIdx = headers.findIndex(h => h === 'Pago (USD)');
                    // Fallback: buscar columnas cargo/pago por índice si no hay headers claros
                    // Recorrer filas de datos
                    Array.from(tbl.querySelectorAll('tbody tr')).forEach(r => {
                        const cells = Array.from(r.querySelectorAll('td'))
                            .map(c => c.innerText.trim());
                        if (pagoClpIdx >= 0 && cells[pagoClpIdx]) {
                            const v = cells[pagoClpIdx].replace(/[$\\.\\s]/g, '').replace(',', '');
                            const n = parseInt(v, 10);
                            if (!isNaN(n) && n > 0) pagado_clp += n;
                        }
                        if (pagoUsdIdx >= 0 && cells[pagoUsdIdx]) {
                            const v = cells[pagoUsdIdx]
                                .replace(/USD/gi, '').replace(',', '.').replace(/[$\\.\\s]/g, '');
                            const n = parseFloat(v);
                            if (!isNaN(n) && n > 0) pagado_usd += n;
                        }
                    });
                });
                return { pagado_clp, pagado_usd };
            }""")
            pagado_clp = float(pago_data.get("pagado_clp") or 0)
            pagado_usd = round(float(pago_data.get("pagado_usd") or 0), 2)
            _slog("BCH-PAG", "pagos", f"CLP={pagado_clp} | USD={pagado_usd}")

            # ── Guardar en DB + Supabase ───────────────────────────
            _save_pago_tdc({"timestamp": ts_now, "institucion": "Banco de Chile",
                "card_number": "7164", "card_name": "Visa Signature",
                "periodo_hasta": periodo_hasta, "pagar_hasta": pagar_hasta,
                "facturado_clp": fac_clp, "pagado_clp": pagado_clp,
                "facturado_usd": fac_usd, "pagado_usd": pagado_usd,
                "no_facturado_clp": None})

            _console.print(_fmt_pagos_log(
                "BdChile", "7164",
                fac_clp=fac_clp, pag_clp=pagado_clp,
                fac_usd=fac_usd, pag_usd=pagado_usd,
            ), highlight=False)
        except Exception as ep:
            print(f"[BCH-PN] Pagos TdC ERROR: {ep}")
            _PAGOS_ERRORS.append({"inst": "Banco de Chile", "card": "TdC 7164", "error": str(ep)[:60]})

        page.close()
        return True
    except Exception as e:
        import traceback
        _slog('BCH-PN', 'error', f"{type(e).__name__}: {e}")
        traceback.print_exc()
        for item, cat in [("CC 5809","CC PN"), ("TdC 7164","TdC"), ("LdC","LdC"), ("CH New","CH")]:
            if item not in added:
                add_result(resultados, key, "Banco de Chile", cat, item, "error", ok=False)
                print_preliminary("Banco de Chile", cat, item, str(e), ok=False)
        try:
            if page: page.close()
        except: pass
        return False


def scrape_scotiabank_pn(context, resultados):
    """
    Extrae saldos desde Scotiabank Personas (nuevo portal feb 2026).

    URLs procesadas:
    - Login:  https://www.scotiabank.cl/login/personas/?nocache=true
    - CC:     mfe-accounts-balancesmovements-web/?tab=saldos&type=CTACTE
    - TdC:    mfe-simple-account-statement-web-cl/?tab=saldo&card={card}
    - LdC:    mfe-accounts-balancesmovements-web/?tab=saldos&type=LICRED

    Selectores clave (todos dentro iframe#iframe-stage):
    - CC: p.TextCaption__text--bold:has-text("Saldo disponible")
    - TdC: div.saldo:has-text("Cupo utilizado") → h1.saldo__text
    - LdC: p.TextCaption__text:has-text("Saldo utilizado")

    NOTA: RUT sin formato, click+Tab para navegación, xpath para hermanos/padres
    """
    key = "scotiabank_pn"
    added = set()
    page = None
    try:
        print("[SCO-PN] Obteniendo credenciales...")
        rut = bw_get("username", "Scotiabank PN")
        pwd = bw_get("password", "Scotiabank PN")
        _slog('SCO-PN', 'creds', f"rut={rut}")
        if not rut or not pwd:
            raise Exception(f"Credenciales vacías Scotiabank PN (RUT:{'OK' if rut else 'VACÍO'}, PWD:{'OK' if pwd else 'VACÍO'})")

        # Tipear el RUT sin puntos ni guión — el formulario tiene su propio
        # auto-formatter y actualiza el estado React al tipear caracter a caracter
        rut_clean = rut.replace(".", "").replace("-", "").strip()

        print("[SCO-PN] Login...")
        page = context.new_page()
        page.goto("https://www.scotiabank.cl/login/personas/?nocache=true")

        # Esperar a que el campo RUT esté listo
        rut_input = page.get_by_test_id("inputDni")
        rut_input.wait_for(state="visible", timeout=20000)
        page.wait_for_timeout(1500)

        # Click → tipear el RUT raw (sin formatear)
        rut_input.click()
        page.wait_for_timeout(300)
        rut_input.press_sequentially(rut_clean, delay=80)
        page.wait_for_timeout(600)

        # Tab al campo de contraseña
        page.keyboard.press("Tab")
        page.wait_for_timeout(400)

        # Tipear contraseña en el campo que quedó con foco
        pwd_input = page.get_by_test_id("inputPassword")
        pwd_input.press_sequentially(pwd, delay=80)
        page.wait_for_timeout(500)

        # Tab fuera del campo (dispara blur y validación final)
        page.keyboard.press("Tab")
        page.wait_for_timeout(1000)

        # Poll hasta que el botón esté habilitado (máx 10s)
        btn = page.get_by_role("button", name="Ingresar")
        for _ in range(20):
            if btn.is_enabled():
                break
            page.wait_for_timeout(500)
        else:
            raise Exception("Botón 'Ingresar' sigue disabled tras 10s — RUT o clave incorrectos")

        btn.click(timeout=5000)
        _slog('SCO-PN', 'login', 'ok')
        # "networkidle" nunca llega en el nuevo portal (polling constante)
        page.wait_for_load_state("load", timeout=30000)
        page.wait_for_timeout(4000)

        # Cerrar popup si aparece post-login
        try:
            close_btn = page.locator("button[aria-label='Close'], button[aria-label='Cerrar'], button.modal-close, button.close").first
            close_btn.wait_for(state="visible", timeout=4000)
            close_btn.click()
            page.wait_for_timeout(500)
        except:
            pass

        # ── CC saldo ─────────────────────────────────────────────
        page.goto("https://www.scotiabank.cl/mfe/sweb/mfe-shell-web-cl/mfe/mfe/ltmnsw/mfe-accounts-balancesmovements-web/?tab=saldos&type=CTACTE")
        page.wait_for_timeout(4000)
        frame = page.frame_locator("iframe#iframe-stage")
        saldo_label = frame.locator("p.TextCaption__text--bold", has_text="Saldo disponible")
        saldo_label.wait_for(state="visible", timeout=20000)
        saldo_raw = saldo_label.locator(
            "xpath=ancestor::div[contains(@class,'Column__container')]/following-sibling::div[1]/p"
        ).text_content().strip()
        saldo = saldo_raw.replace("$", "").strip()
        _slog('SCO-PN', 'saldo', f'CC = {saldo}')
        add_result(resultados, key, "Scotiabank", "CC PN", "CC 7002", saldo)
        print_preliminary("Scotiabank", "CC PN", "CC 7002", saldo)
        added.add("CC 7002")

        # ── Renta Diaria ─────────────────────────────────────────
        page.goto("https://www.scotiabank.cl/mfe/sweb/mfe-shell-web-cl/mfe/mfe/ltmnsw/mfe-accounts-balancesmovements-web/?tab=saldos&type=CTAHYS")
        page.wait_for_timeout(4000)
        frame_rd = page.frame_locator("iframe#iframe-stage")
        rd_label = frame_rd.locator("p.TextCaption__text--bold", has_text="Saldo disponible")
        rd_label.wait_for(state="visible", timeout=20000)
        rd_raw = rd_label.locator(
            "xpath=ancestor::div[contains(@class,'Column__container')]/following-sibling::div[1]/p"
        ).first.text_content().strip()
        rd_saldo = rd_raw.replace("$", "").replace(".", "").strip()
        _slog('SCO-PN', 'saldo', f'Renta Diaria = {rd_saldo}')
        add_result(resultados, key, "Scotiabank", "Cash", "Renta Diaria", rd_saldo)
        print_preliminary("Scotiabank", "Cash", "Renta Diaria", rd_saldo)
        added.add("Renta Diaria")

        # ── TdC ──────────────────────────────────────────────────
        def get_cupo(card_number):
            url = (f"https://www.scotiabank.cl/mfe/sweb/mfe-shell-web-cl/mfe/"
                   f"mfe-simple-account-statement-web-cl/?tab=saldo&card={card_number}")
            page.goto(url)
            page.wait_for_timeout(4000)
            f = page.frame_locator("iframe#iframe-stage")
            cupo = f.locator("div.saldo", has_text="Cupo utilizado").first
            cupo.wait_for(state="visible", timeout=15000)
            val = cupo.locator("h1.saldo__text").text_content().strip()
            # Si devuelve "0", probablemente el iframe no terminó de cargar — retry con más espera
            if val in ("0", "$0", ""):
                page.wait_for_timeout(4000)
                val = cupo.locator("h1.saldo__text").text_content().strip()
                if val in ("0", "$0", ""):
                    raise ValueError(f"Scotiabank TdC {card_number}: valor '0' tras retry — posible carga incompleta")
            return val

        for card, item in [("3134", "TdC 3134"), ("2730", "TdC 2730")]:
            deuda = get_cupo(card).replace("$", "").strip()
            _slog('SCO-PN', 'saldo', f'{item} = {deuda}')
            monto = f"-{deuda}"
            add_result(resultados, key, "Scotiabank", "TdC", item, monto)
            print_preliminary("Scotiabank", "TdC", item, monto)
            added.add(item)

        # ── LdC ──────────────────────────────────────────────────
        page.goto("https://www.scotiabank.cl/mfe/sweb/mfe-shell-web-cl/mfe/mfe/ltmnsw/mfe-accounts-balancesmovements-web/?tab=saldos&type=LICRED")
        page.wait_for_timeout(4000)
        frame_ldc = page.frame_locator("iframe#iframe-stage")
        ldc_label = frame_ldc.locator("p.TextCaption__text", has_text="Saldo utilizado")
        ldc_label.wait_for(state="visible", timeout=20000)
        ldc_raw = ldc_label.locator(
            "xpath=ancestor::div[contains(@class,'Column__container')]/following-sibling::div[1]/p"
        ).text_content().strip()
        ldc = ldc_raw.replace("$", "").replace(".", "").replace(" ", "").strip()
        _slog('SCO-PN', 'saldo', f'LdC = {ldc}')
        monto_ldc = f"-{ldc}" if ldc not in ("0", "") else "0"
        add_result(resultados, key, "Scotiabank", "LdC", "LdC", monto_ldc)
        print_preliminary("Scotiabank", "LdC", "LdC", monto_ldc)
        added.add("LdC")

        # ── Pagos TdC ─────────────────────────────────────────────
        try:
            SCO_CARDS = [("3134", "Visa Infinite"), ("2730", "Visa Singular")]
            print("[SCO-PN] Extrayendo pagos TdC...")
            ts_now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            conn_pagos = init_db()
            for card_num, card_name in SCO_CARDS:
                try:
                    _slog("SCO-PN", "pagos", f"TdC {card_num}...")
                    data = _scrape_sco_pagos_card(page, card_num)
                    def _raw_to_float(s):
                        if not s: return None
                        s = s.upper().replace("USD","").replace("$","").replace(".","").replace(",",".").strip()
                        try: return float(s)
                        except: return None
                    fac_clp = _raw_to_float(data.get("facturado_clp"))
                    pag_clp = float(data.get("pagado_clp", 0))
                    fac_usd = _raw_to_float(data.get("facturado_usd"))
                    pag_usd = float(data.get("pagado_usd", 0))
                    _save_pago_tdc({"timestamp": ts_now, "institucion": "Scotiabank",
                        "card_number": card_num, "card_name": card_name,
                        "periodo_hasta": data.get("periodo_hasta"), "pagar_hasta": data.get("pagar_hasta"),
                        "facturado_clp": fac_clp, "pagado_clp": pag_clp,
                        "facturado_usd": fac_usd, "pagado_usd": pag_usd,
                        "no_facturado_clp": None})
                    _console.print(_fmt_pagos_log(
                        "Scotiabank", card_num,
                        fac_clp=fac_clp, pag_clp=pag_clp,
                        fac_usd=fac_usd, pag_usd=pag_usd,
                    ), highlight=False)
                except Exception as ep:
                    _console.print(f"  [bold red]ERROR[/bold red] [dim]Pagos Scotiabank • TdC {card_num}[/dim]  [dim]({ep})[/dim]")
                    _PAGOS_ERRORS.append({"inst": "Scotiabank", "card": f"TdC {card_num}", "error": str(ep)[:60]})
        except Exception as ep_outer:
            _slog("SCO-PN", "error", f"pagos TdC: {ep_outer}")
            _PAGOS_ERRORS.append({"inst": "Scotiabank", "card": "TdC 3134/2730", "error": str(ep_outer)[:60]})

        page.close()
        return True
    except Exception as e:
        import traceback
        _slog('SCO-PN', 'error', f"{type(e).__name__}: {e}")
        traceback.print_exc()
        for item, cat in [("CC 7002","CC PN"), ("Renta Diaria","Cash"), ("TdC 3134","TdC"), ("TdC 2730","TdC"), ("LdC","LdC")]:
            if item not in added:
                add_result(resultados, key, "Scotiabank", cat, item, "error", ok=False)
                print_preliminary("Scotiabank", cat, item, str(e), ok=False)
        try:
            if page: page.close()
        except: pass
        return False


def scrape_banco_ripley(context, resultados):
    key = "banco_ripley"
    added = set()
    page = None
    try:
        print("[RIP] Obteniendo credenciales...")
        rut = bw_get("username", "web.bancoripley.cl")
        pwd = bw_get("password", "web.bancoripley.cl")
        _slog('RIP', 'creds', f"rut={rut}")

        print("[RIP] Login...")
        page = context.new_page()
        page.goto("https://web.bancoripley.cl/login")
        page.wait_for_timeout(2000)
        page.get_by_role("textbox").wait_for(timeout=10000)
        page.get_by_role("textbox").click()
        page.get_by_role("textbox").press_sequentially(rut, delay=50)
        page.get_by_role("textbox").press("Tab")
        page.wait_for_timeout(1000)
        page.get_by_role("button", name="Continuar").click(force=True)
        page.wait_for_timeout(1500)
        page.get_by_role("textbox").wait_for(timeout=8000)
        page.get_by_role("textbox").fill(pwd)
        page.get_by_role("button", name="Continuar").click()
        page.wait_for_url("**/home**", timeout=30000)
        _slog('RIP', 'login', 'ok')
        page.wait_for_timeout(7000)
        page.locator("h5.h5-xs").first.wait_for(timeout=30000)
        saldo = page.locator("h5.h5-xs").first.text_content().strip().replace("$", "").strip()
        _slog('RIP', 'saldo', f'CC = {saldo}')
        add_result(resultados, key, "Banco Ripley", "CC PN", "CC 2239", saldo)
        print_preliminary("Banco Ripley", "CC PN", "CC 2239", saldo)
        added.add("CC 2239")
        # TdC
        tdc_card = page.locator("div", has_text="Titular ****9647").first
        utilizado_row = tdc_card.locator("div.min-w-\\[76px\\]", has_text="Utilizado")
        utilizado_row.wait_for(state="visible", timeout=15000)
        deuda_rip = utilizado_row.locator("xpath=..").locator("span.label-md").text_content().strip().replace("$ ", "").replace("$", "").strip()
        _slog('RIP', 'saldo', f'TdC = {deuda_rip}')
        monto = f"-{deuda_rip}" if deuda_rip != "0" else "0"
        add_result(resultados, key, "Banco Ripley", "TdC", "TdC 9647", monto)
        print_preliminary("Banco Ripley", "TdC", "TdC 9647", monto)
        added.add("TdC 9647")

        # ── Pagos TdC 9647 ────────────────────────────────────────
        try:
            print("[RIP] Extrayendo pagos TdC 9647...")
            # "Estado de cuenta" está directo en el home — no hay que navegar a movimientos
            # El primer link es el de la TdC 9647 (Titular), el segundo es la Adicional 1202

            def _rip_clp(s):
                if not s: return None
                try:
                    v = int(s.replace("$","").replace(".","").replace(",","").strip())
                    return v if v != 0 else None
                except: return None

            # --- Cerrar popup snrs si está visible (bloquea clicks) ---
            try:
                page.evaluate("""() => {
                    const m = document.querySelector('.snrs-modal-wrapper.snrs-modal-show');
                    if (m) m.remove();
                    // también intentar botón de cierre dentro del modal
                    const btn = document.querySelector('.snrs-modal-close, .snrs-close, [class*="snrs"][class*="close"]');
                    if (btn) btn.click();
                }""")
                page.wait_for_timeout(500)
            except: pass

            # --- Estado de cuenta → modal con fechas + remaining ---
            page.locator("lib-typography.cursor-pointer", has_text="Estado de cuenta").first.click()
            page.wait_for_selector("app-account-statement", timeout=12000)
            # Esperar que las fechas estén cargadas (no vacías) — máx 10s
            for _ in range(20):
                page.wait_for_timeout(500)
                _check = page.evaluate("""() => {
                    const el = document.querySelector('app-account-statement');
                    if (!el) return '';
                    const paras = [...el.querySelectorAll('p.paragraph-sm')];
                    return paras[3]?.textContent?.trim() || '';
                }""")
                if _check and "/" in _check:
                    break

            modal_data = page.evaluate("""() => {
                const el = document.querySelector('app-account-statement');
                if (!el) return {};
                const paras = [...el.querySelectorAll('p.paragraph-sm')];
                // idx: 0=descripción, 1=monto_remaining, 2=monto_min, 3=fecha_fac, 4=fecha_venc
                return {
                    monto_remaining: paras[1]?.textContent?.trim() || '',
                    fecha_facturacion: paras[3]?.textContent?.trim() || '',
                    fecha_vencimiento: paras[4]?.textContent?.trim() || ''
                };
            }""")

            remaining_clp   = _rip_clp(modal_data.get("monto_remaining", "")) or 0
            periodo_hasta   = (modal_data.get("fecha_facturacion") or "").strip() or None
            pagar_hasta     = (modal_data.get("fecha_vencimiento") or "").strip() or None
            _slog("RIP-PAG", "info", f"remaining={remaining_clp} | periodo={periodo_hasta} | pagar={pagar_hasta}")

            # --- PDF: Ver estado de cuenta → screenshot → OCR (Gemini → Haiku) ---
            facturado_clp = None
            try:
                import pathlib as _pl, base64 as _b64
                from dotenv import dotenv_values as _dv
                _gs_env = _dv(str(_pl.Path(__file__).parent / 'GastoSmart' / 'backend' / '.env'))
                _gem_keys_raw = _gs_env.get('GEMINI_API_KEYS', _gs_env.get('GEMINI_API_KEY', ''))
                _gem_keys = [k.strip() for k in _gem_keys_raw.split(',') if k.strip()]

                with context.expect_page(timeout=20000) as _pdf_info:
                    page.locator("button:has-text('Ver estado de cuenta')").click()
                pdf_pg = _pdf_info.value
                pdf_pg.wait_for_load_state("load", timeout=20000)
                pdf_pg.wait_for_timeout(3000)  # Que renderice el PDF en Chrome
                screenshot = pdf_pg.screenshot()
                pdf_pg.close()

                _PROMPT_OCR = "En este estado de cuenta bancario, ¿cuánto es el 'MONTO TOTAL FACTURADO A PAGAR'? Responde SOLO el número sin puntos ni signos de moneda. Ejemplo: 1148948"
                raw = None

                # Intento 1: Gemini (múltiples modelos como fallback)
                _GEMINI_MODELS = ['gemini-2.5-flash-lite', 'gemini-2.0-flash', 'gemini-1.5-flash', 'gemini-2.5-flash']
                if _gem_keys:
                    from google import genai as _genai
                    import tempfile, os as _os
                    for _gkey in _gem_keys:
                        if raw: break
                        for _gmodel in _GEMINI_MODELS:
                            try:
                                _gclient = _genai.Client(api_key=_gkey)
                                with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as _tf:
                                    _tf.write(screenshot); _tpath = _tf.name
                                _gfile = _gclient.files.upload(file=_tpath)
                                _os.unlink(_tpath)
                                _gresp = _gclient.models.generate_content(
                                    model=_gmodel,
                                    contents=[_gfile, _PROMPT_OCR]
                                )
                                raw = _gresp.text.strip()
                                print(f"[RIP-PAG] OCR Gemini ({_gmodel}): {raw}")
                                break
                            except Exception as _eg:
                                print(f"[RIP-PAG] Gemini {_gmodel} falló: {_eg}")
                                continue

                if raw:
                    digits = "".join(filter(str.isdigit, raw.replace(".", "").replace(",", "")))
                    facturado_clp = int(digits) if digits else None
                _slog("RIP-PAG", "info", f"fac (OCR) = {facturado_clp}")

            except Exception as e_pdf:
                _slog("RIP-PAG", "error", f"PDF/OCR: {e_pdf}")

            # pagado = facturado − remaining (lo que ya se pagó del último estado)
            if facturado_clp is not None:
                pagado_clp = max(0, facturado_clp - remaining_clp) or None
            else:
                pagado_clp = None

            _slog("RIP-PAG", "info", f"fac={facturado_clp} | pag={pagado_clp} | remaining={remaining_clp}")

            # Cerrar modal Estado de cuenta
            try:
                page.locator("app-account-statement").locator("ion-icon").first.click()
                page.wait_for_timeout(500)
            except: pass

            # Guardar en DB + Supabase
            ts_pag = datetime.datetime.now().isoformat()
            _save_pago_tdc({"timestamp": ts_pag, "institucion": "Banco Ripley",
                "card_number": "9647", "card_name": "Mastercard Black",
                "periodo_hasta": periodo_hasta, "pagar_hasta": pagar_hasta,
                "facturado_clp": facturado_clp, "pagado_clp": abs(pagado_clp) if pagado_clp else None,
                "facturado_usd": None, "pagado_usd": None, "no_facturado_clp": None})

            _console.print(_fmt_pagos_log(
                "Banco Ripley", "9647",
                fac_clp=facturado_clp, pag_clp=None, no_fac_clp=pagado_clp,
            ), highlight=False)

        except Exception as e_pag:
            print(f"[RIP] Error pagos TdC: {e_pag}")
            _PAGOS_ERRORS.append({"inst": "Banco Ripley", "card": "TdC 9647", "error": str(e_pag)})

        page.close()
        return True
    except Exception as e:
        import traceback
        _slog('RIP', 'error', f"{type(e).__name__}: {e}")
        traceback.print_exc()
        for item, cat in [("CC 2239","CC PN"), ("TdC 9647","TdC")]:
            if item not in added:
                add_result(resultados, key, "Banco Ripley", cat, item, "error", ok=False)
                print_preliminary("Banco Ripley", cat, item, str(e), ok=False)
        try:
            if page: page.close()
        except: pass
        return False


_SANTANDER_STATE    = Path(__file__).parent / "santander_session.json"
_SANTANDER_DL_STATE = Path(__file__).parent / "santander_dl_session.json"

def scrape_santander(context, resultados):
    """
    Extrae saldos desde Santander.
    Persistencia vía santander_session.json.
    """
    key = "santander"
    added = set()
    page = None
    iso_context = None
    try:
        print("[SAN] Obteniendo credenciales...")
        rut = bw_get("username", "banco.santander.cl")
        pwd = bw_get("password", "banco.santander.cl")
        if not rut or not pwd:
            print("[SAN] Sin credenciales.")
            return False
            
        rut_clean = rut.replace(".", "").replace("-", "").strip()

        # ── Contexto aislado para persistencia ──
        ctx_kwargs = {"viewport": {"width": 1280, "height": 800}}
        if _SANTANDER_STATE.exists():
            ctx_kwargs["storage_state"] = str(_SANTANDER_STATE)
            if DEBUG: print(f"[SAN] Cargando sesión guardada: {_SANTANDER_STATE}")

        iso_context = context.browser.new_context(**ctx_kwargs)
        iso_context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
        page = iso_context.new_page()

        def close_popups():
            try:
                # Evitar selectores genéricos que puedan clickear "Cerrar sesión"
                selectors = ["button.close", ".modal-close", "button:text-is('Cerrar')", "button:has-text('Saltar')", ".pop-over-close", "button:has-text('Entendido')", ".close-modal"]
                for sel in selectors:
                    loc = page.locator(sel)
                    if loc.count() > 0 and loc.first.is_visible():
                        # Verificación extra: que no sea el botón de Logout
                        text = loc.first.text_content().lower()
                        if "sesi" not in text and "salir" not in text:
                            loc.first.click()
                            page.wait_for_timeout(1000)
            except: pass

        # ── Iniciar en la página pública ──
        page.goto("https://banco.santander.cl/personas", timeout=60000)
        page.wait_for_timeout(3000)
        close_popups()

        # Verificar si ya estamos logueados
        already_in = False
        if "/private/" in page.url and "/public/login" not in page.url:
            already_in = True
        else:
            # Si vemos el botón de ingresar, intentamos ver si nos lleva directo vía cookies
            try:
                page.wait_for_selector("#btnIngresar", state="visible", timeout=8000)
                page.locator("#btnIngresar").click()
                page.wait_for_timeout(3000)
                if "/private/" in page.url and "/public/login" not in page.url:
                    already_in = True
            except:
                pass

        def perform_fill():
            """Intento de auto-llenado de credenciales."""
            try:
                # 1. Determinar si es login por Iframe o Directo
                login_frame = page.frame_locator("#login-frame")
                is_iframe   = page.locator("#login-frame").is_visible()
                login_target = login_frame if is_iframe else page
                
                # Check if RUT input is visible
                rut_sel = "input#rut, input[placeholder*='RUT']"
                if login_target.locator(rut_sel).first.is_visible():
                    if DEBUG: print("[SAN] Formulario de login detectado. Llenando...")
                    rut_field = login_target.locator(rut_sel).first
                    rut_field.click()
                    page.wait_for_timeout(300)
                    rut_field.press_sequentially(rut_clean, delay=80)
                    page.wait_for_timeout(500)
                    pass_sel = "input#pass, input#password, input[type='password']"
                    pass_field = login_target.locator(pass_sel).first
                    pass_field.click()
                    page.wait_for_timeout(300)
                    pass_field.press_sequentially(pwd, delay=60)
                    page.wait_for_timeout(500)
                    btn_sel = "button:has-text('INGRESAR'), button:has-text('Ingresar'), button#login-btn"
                    login_target.locator(btn_sel).first.click()
                    return True
            except: pass
            return False

        def smart_dashboard_wait(pg, dashboard_sel, login_sel, timeout_ms=120000):
            """Espera inteligente: si ve el dashboard, éxito. Si ve el login, re-intenta fill."""
            start_time = datetime.datetime.now()
            while (datetime.datetime.now() - start_time).total_seconds() < (timeout_ms / 1000):
                if pg.locator(dashboard_sel).first.is_visible():
                    return "dashboard"
                if pg.locator(login_sel).first.is_visible():
                    return "login"
                pg.wait_for_timeout(3000)
            return "timeout"

        # Si llegamos a la página de login (la del screenshot del usuario)
        if "/public/login" in page.url or "/login" in page.url:
            already_in = False

        # Si no estamos adentro, hacer login completo
        if not already_in:
            if DEBUG: print("[SAN] Procediendo con el login...")
            perform_fill()

            # ESPERA REAL DE LOGIN/MFA (Visual)
            print("[SAN] Esperando a que el Dashboard cargue completamente...")
            
            dashboard_sel = ".box-product, #main-dashboard"
            login_sel = "input#rut, #login-frame"
            
            # Bucle de re-intento interno (Self-Healing)
            for attempt in range(3):
                status = smart_dashboard_wait(page, dashboard_sel, login_sel, timeout_ms=45000)
                if status == "dashboard":
                    break
                if status == "login":
                    if DEBUG: print(f"[SAN] Rebote al login detectado (intento {attempt+1}). Re-llenando...")
                    perform_fill()
                else: # timeout
                    if DEBUG: print(f"[SAN] Timeout en espera de dashboard (intento {attempt+1})")
            
            try:
                page.wait_for_selector(dashboard_sel, timeout=10000)
                # GUARDAR SESIÓN: Solo si estamos adentro de verdad
                if "/private/" in page.url:
                    if DEBUG: print(f"[SAN] Dashboard detectado. Guardando sesión...")
                    iso_context.storage_state(path=str(_SANTANDER_STATE))
            except Exception as e_wait:
                if DEBUG: print(f"[SAN] No detectamos el ingreso automático: {e_wait}")
                # En lugar de input(), simplemente intentamos una navegación forzada si falló la detección
                # pero puede que estemos adentro.
                pass

        # ── EXTRACCIÓN CON ANTI-REBOTE ──
        # Si el sitio nos sacó a la zona pública al entrar, intentamos re-ingresar una vez
        if "/private/" not in page.url or "personas" in page.url[-8:]:
            if DEBUG: print("[SAN] Fuera del sitio privado (rebote detectado). Re-intentando entrar...")
            # Intentar navegar directo al frame de cuentas
            page.goto("https://mibanco.santander.cl/UI.Web.HB/Private_new/frame/#/private/main", timeout=60000)
            page.wait_for_timeout(8000)
            close_popups()
        
        # Si seguimos fuera, último intento de click en ingresar
        if "/private/" not in page.url:
            if page.locator("#btnIngresar").is_visible():
                page.locator("#btnIngresar").click()
                page.wait_for_timeout(5000)

        close_popups()
        page.wait_for_timeout(3000)
        if DEBUG: print(f"[SAN] URL final de extracción: {page.url}")

        # ── CC saldo ──
        try:
            cc_box = page.locator(".box-product", has_text="Cuenta Corriente").filter(has_not_text="Dólar").first
            cc_box.wait_for(state="visible", timeout=20000)
            # Selector más flexible para el saldo de la CC
            saldo_raw = cc_box.locator("p[class*='amount'], .amount-pipe-4").first.text_content().strip()
            # En Chile el punto es separador de miles. Lo eliminamos para tener el número entero.
            saldo = saldo_raw.replace("$", "").replace(".", "").replace(" ", "").replace("\xa0", "").strip()
            # No forzamos signo, el banco dirá si es negativo (sobregiro)
            add_result(resultados, key, "Santander", "CC PN", "CC 2241", saldo)
            print_preliminary("Santander", "CC PN", "CC 2241", saldo)
            added.add("CC 2241")
        except Exception as e_cc:
            if DEBUG: print(f"[SAN] Error en CC: {e_cc}")

        # ── TdC (Tarjetas) ──
        try:
            page.goto("https://mibanco.santander.cl/UI.Web.HB/Private_new/frame/#/private/Saldos_TC/main/detail")
            page.wait_for_timeout(5000)
            close_popups()

            def get_utilizado_tc():
                used = page.locator(".used-amount p.amount-pipe-3, .used-amount p").first
                used.wait_for(state="visible", timeout=15000)
                raw = used.text_content().strip().replace("$", "").replace(".", "").replace(" ", "").replace("\xa0", "").strip()
                return f"-{raw}" if raw not in ("0", "0,00", "") else "0"

            page.locator(".swiper-slide-active").wait_for(state="visible", timeout=15000)
            m_4765 = get_utilizado_tc()
            add_result(resultados, key, "Santander", "TdC", "TdC 4765", m_4765)
            print_preliminary("Santander", "TdC", "TdC 4765", m_4765)
            added.add("TdC 4765")

            if page.locator(".swiper-button-next").is_visible():
                page.locator(".swiper-button-next").click()
                page.wait_for_timeout(2000)
                m_8098 = get_utilizado_tc()
                add_result(resultados, key, "Santander", "TdC", "TdC 8098", m_8098)
                print_preliminary("Santander", "TdC", "TdC 8098", m_8098)
                added.add("TdC 8098")
        except Exception as e_tc:
            if DEBUG: print(f"[SAN] Error en TdC: {e_tc}")

        # ── LdC (Línea de Crédito) ──
        try:
            # Ir SIEMPRE a la página de detalle
            if DEBUG: print("[SAN] Navegando a detalle de LdC para búsqueda de 'Utilizado'...")
            page.goto("https://mibanco.santander.cl/UI.Web.HB/Private_new/frame/#/private/saldos/main/mi-cuenta", timeout=60000)
            page.wait_for_timeout(6000)
            close_popups()
            
            # Buscamos específicamente el texto "Utilizado"
            # Intentamos encontrar el elemento numérico que suele estar cerca de la palabra "utilizado"
            monto_ldc = "0"
            try:
                # Buscamos el contenedor que tenga "Utilizado"
                box = page.locator("div.monto-box, .container-amounts", has_text=re.compile(r"Utilizado", re.IGNORECASE)).first
                if box.count() > 0:
                    # Buscamos el párrafo de monto dentro de esa caja
                    elem = box.locator("p.monto-contable, p.amount, p[class*='monto']").first
                    raw = elem.text_content().strip()
                    val = raw.replace("$", "").replace(".", "").replace(" ","").replace("\xa0", "").strip()
                    
                    # VALIDACIÓN CRÍTICA: Si el número de la LdC es IGUAL al de la CC (que ya extrajimos)
                    # es casi seguro que el script leyó el dato equivocado. Ponemos 0.
                    if "CC 2241" in resultados.get(key, {}) and val == resultados[key]["CC 2241"]["valor"].replace(".", ""):
                        if DEBUG: print(f"[SAN] LdC leyó mismo valor que CC ({val}), asumiendo 0 utilizado.")
                        monto_ldc = "0"
                    else:
                        monto_ldc = f"-{val}" if val not in ("0", "") else "0"
            except:
                if DEBUG: print("[SAN] No se pudo localizar label 'Utilizado', intentando fallback...")

            add_result(resultados, key, "Santander", "LdC", "LdC", monto_ldc)
            print_preliminary("Santander", "LdC", "LdC", monto_ldc)
            added.add("LdC")
        except Exception as e_ldc:
            if DEBUG: print(f"[SAN] Error en LdC: {e_ldc}")

        # ── Pagos TdC 4765 y 8098 ──────────────────────────────────
        try:
            print("[SAN] Extrayendo pagos TdC 4765 y 8098...")
            base_url = "https://mibanco.santander.cl/UI.Web.HB/Private_new/frame/#/private/Saldos_TC/main"

            def _san_clp(txt):
                """Parsea monto CLP Santander: '$15.001.630' o '+$151' o '$15.001.630,00' → int positivo."""
                if not txt: return 0
                try:
                    # Eliminar signos, $, puntos de miles, coma decimal y espacios
                    cleaned = txt.replace("+","").replace("-","").replace("$","").replace(".","").replace(",","").replace(" ","").replace("\xa0","").strip()
                    return int(cleaned) if cleaned.isdigit() else 0
                except: return 0

            def _san_pagos_for_card(card_num, card_name):
                """Extrae pagos_tdc para la tarjeta actualmente activa."""
                # 1. Movimientos Facturados → facturado, periodo_hasta, pagar_hasta
                page.goto(f"{base_url}/billed", timeout=30000)
                page.wait_for_load_state("load", timeout=20000)
                page.wait_for_timeout(4000)
                close_popups()

                # /billed → solo periodo_hasta y pagar_hasta (el facturado viene de /bill)
                try:
                    page.locator(".mat-select-min-line, span.margin-0").first.wait_for(state="visible", timeout=15000)
                except: pass

                billed = page.evaluate("""() => {
                    // Periodo hasta (.mat-select-min-line)
                    let periodo = null;
                    for (const sel of ['.mat-select-min-line', '.mat-mdc-select-min-line', 'mat-select-trigger span', 'span.mat-select-value-text span']) {
                        const el = document.querySelector(sel);
                        if (el) { const t = el.textContent.trim(); if (t) { periodo = t; break; } }
                    }
                    // Pagar hasta (span siguiente al label)
                    let pagarHasta = null;
                    const spans0 = [...document.querySelectorAll('span.margin-0')];
                    const idx = spans0.findIndex(s => s.textContent.trim().toLowerCase().includes('pagar hasta'));
                    if (idx >= 0 && idx + 1 < spans0.length) pagarHasta = spans0[idx + 1].textContent.trim();
                    if (!pagarHasta) {
                        const ds = spans0.find(s => /\\d{2}\\/\\d{2}\\/\\d{4}/.test(s.textContent.trim()));
                        if (ds) pagarHasta = ds.textContent.trim();
                    }
                    return {periodo, pagarHasta};
                }""")

                periodo_hasta = billed.get('periodo') or None
                pagar_hasta   = billed.get('pagarHasta') or None

                # /bill → facturado (SALDO INICIAL = cargo negativo) + no_facturado (abonos positivos)
                facturado_clp    = None
                no_facturado_clp = 0
                try:
                    page.goto(f"{base_url}/bill", timeout=30000)
                    page.wait_for_load_state("load", timeout=20000)
                    page.wait_for_timeout(2000)
                    close_popups()

                    bill = page.evaluate("""() => {
                        const rows = [...document.querySelectorAll('tr.mat-row, tr.cdk-row')];
                        let facturado = 0, pagos = 0;
                        rows.forEach(row => {
                            const chargeCell   = row.querySelector('td.cdk-column-amountCharge');
                            const paymentCell  = row.querySelector('td.cdk-column-paymentAmount');
                            // SALDO INICIAL: fila cuyo texto incluye "SALDO INICIAL" con cargo negativo
                            if (chargeCell && row.textContent.toUpperCase().includes('SALDO INICIAL')) {
                                const num = parseInt(chargeCell.textContent.replace(/[^0-9]/g, ''));
                                if (!isNaN(num) && num > 0) facturado = num;
                            }
                            // Abonos: paymentAmount con prefijo "+"
                            if (paymentCell) {
                                const txt = paymentCell.textContent.trim();
                                if (txt.startsWith('+')) {
                                    const num = parseInt(txt.replace(/[^0-9]/g, ''));
                                    if (!isNaN(num)) pagos += num;
                                }
                            }
                        });
                        return {facturado, pagos};
                    }""")

                    facturado_clp    = bill.get('facturado') or None
                    no_facturado_clp = bill.get('pagos') or 0
                    print(f"[SAN-PAG] {card_num}: fac={facturado_clp} | no_facturado={no_facturado_clp}")
                except Exception as e_nf:
                    _slog("SAN-PAG", "error", f"bill {card_num}: {e_nf}")

                print(f"[SAN-PAG] {card_num}: periodo={periodo_hasta} | pagar={pagar_hasta}")

                # 3. INSERT + Supabase
                ts_pag = datetime.datetime.now().isoformat()
                _save_pago_tdc({"timestamp": ts_pag, "institucion": "Santander",
                    "card_number": card_num, "card_name": card_name,
                    "periodo_hasta": periodo_hasta, "pagar_hasta": pagar_hasta,
                    "facturado_clp": facturado_clp, "pagado_clp": None,
                    "facturado_usd": None, "pagado_usd": None,
                    "no_facturado_clp": no_facturado_clp})
                _console.print(_fmt_pagos_log("Santander", card_num, fac_clp=facturado_clp, pag_clp=None, no_fac_clp=no_facturado_clp), highlight=False)

            # TdC 4765 — carga por defecto al abrir /detail
            page.goto(f"{base_url}/detail", timeout=30000)
            page.wait_for_timeout(3000)
            page.locator(".swiper-slide-active").wait_for(state="visible", timeout=10000)
            _san_pagos_for_card("4765", "Worldmember Visa")

            # TdC 8098 — click swiper-next para activar la segunda tarjeta
            page.goto(f"{base_url}/detail", timeout=30000)
            page.wait_for_timeout(3000)
            if page.locator(".swiper-button-next").is_visible():
                page.locator(".swiper-button-next").click()
                page.wait_for_timeout(2000)
            _san_pagos_for_card("8098", "Worldmember Amex")

        except Exception as e_pag:
            print(f"[SAN] Error pagos TdC: {e_pag}")

        return True
    except Exception as e:
        import traceback
        _slog('SAN', 'error', f"{type(e).__name__}: {e}")
        if DEBUG: traceback.print_exc()
        for item, cat in [("CC 2241","CC PN"), ("TdC 4765","TdC"), ("TdC 8098","TdC"), ("LdC","LdC")]:
            if item not in added:
                add_result(resultados, key, "Santander", cat, item, "error", ok=False)
                print_preliminary("Santander", cat, item, str(e)[:60], ok=False)
        return False
    finally:
        if page:
            try: page.close()
            except: pass
        if iso_context:
            try: iso_context.close()
            except: pass


def scrape_santander_dl(context, resultados):
    """
    Extrae CH Like (50%) desde cuenta Santander DL (Dafna).
    Navega a Mis Créditos → Crédito Hipotecario → Monto pendiente.
    Guarda el 50% del saldo UF como negativo (deuda compartida).
    Bitwarden: "Santander DL"
    """
    key = "santander_dl"
    page = None
    iso_context = None
    try:
        print("[SAN-DL] Obteniendo credenciales...")
        items_raw = subprocess.run(
            ["bw", "list", "items", "--search", "santander dl"],
            capture_output=True, text=True, env=bw_env()
        )
        bw_items = json.loads(items_raw.stdout) if items_raw.stdout.strip() else []
        dl_item = next((i for i in bw_items if "santander dl" in i.get("name", "").lower()), None)
        if not dl_item:
            print("[SAN-DL] No se encontró 'Santander DL' en Bitwarden.")
            return False
        rut = dl_item["login"]["username"]
        pwd = dl_item["login"]["password"]
        rut_clean = rut.replace(".", "").replace("-", "").strip()

        ctx_kwargs = {"viewport": {"width": 1280, "height": 800}}
        if _SANTANDER_DL_STATE.exists():
            ctx_kwargs["storage_state"] = str(_SANTANDER_DL_STATE)

        iso_context = context.browser.new_context(**ctx_kwargs)
        iso_context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
        page = iso_context.new_page()

        def close_popups_dl():
            try:
                for sel in ["button.close", ".modal-close", "button:text-is('Cerrar')", "button:has-text('Saltar')", "button:has-text('Entendido')"]:
                    loc = page.locator(sel)
                    if loc.count() > 0 and loc.first.is_visible():
                        text = loc.first.text_content().lower()
                        if "sesi" not in text and "salir" not in text:
                            loc.first.click()
                            page.wait_for_timeout(800)
            except: pass

        page.goto("https://banco.santander.cl/personas", timeout=60000)
        page.wait_for_timeout(3000)
        close_popups_dl()

        # Verificar sesión activa
        already_in = "/private/" in page.url and "/public/login" not in page.url
        if not already_in:
            try:
                page.wait_for_selector("#btnIngresar", state="visible", timeout=8000)
                page.locator("#btnIngresar").click()
                page.wait_for_timeout(3000)
                already_in = "/private/" in page.url and "/public/login" not in page.url
            except: pass

        if not already_in or "/public/login" in page.url:
            # Login
            try:
                login_frame   = page.frame_locator("#login-frame")
                is_iframe     = page.locator("#login-frame").is_visible()
                login_target  = login_frame if is_iframe else page
                rut_field = login_target.locator("input#rut, input[placeholder*='RUT']").first
                rut_field.click()
                page.wait_for_timeout(300)
                rut_field.press_sequentially(rut_clean, delay=80)
                page.wait_for_timeout(500)
                pass_field = login_target.locator("input#pass, input[type='password']").first
                pass_field.click()
                page.wait_for_timeout(300)
                pass_field.press_sequentially(pwd, delay=60)
                page.wait_for_timeout(500)
                login_target.locator("button:has-text('INGRESAR'), button:has-text('Ingresar')").first.click()
            except Exception as e_login:
                print(f"[SAN-DL] Error en login: {e_login}")
                return False

            print("[SAN-DL] Login enviado, esperando dashboard...")
            try:
                page.wait_for_url("**/private/**", timeout=45000)
                iso_context.storage_state(path=str(_SANTANDER_DL_STATE))
            except Exception as e_wait:
                print(f"[SAN-DL] Timeout esperando dashboard: {e_wait}")
                # Intento forzado
                page.goto("https://mibanco.santander.cl/UI.Web.HB/Private_new/frame/#/private/main", timeout=60000)
                page.wait_for_timeout(6000)

        close_popups_dl()

        # ── Navegar a Mis Créditos ──
        CREDITOS_URL = "https://mibanco.santander.cl/UI.Web.HB/Private_new/frame/#/private/creditos/main/list-creditos/creditos"
        page.goto(CREDITOS_URL, timeout=60000)
        page.wait_for_timeout(4000)
        close_popups_dl()

        # ── Extraer Monto pendiente ──
        try:
            page.wait_for_selector("text=Monto pendiente", timeout=20000)
            page.wait_for_timeout(500)
            monto_raw = page.evaluate("""() => {
                // Buscar <p> con texto exacto 'Monto pendiente'
                const all = Array.from(document.querySelectorAll('*'));
                const lbl = all.find(el =>
                    el.children.length === 0 && el.textContent.trim() === 'Monto pendiente'
                );
                if (!lbl) return null;
                // El valor está en el nextElementSibling (ej: "UF 2.390,00")
                const sib = lbl.nextElementSibling;
                if (sib) return sib.textContent.trim();
                // Fallback: children del padre
                const parent = lbl.parentElement;
                if (parent) {
                    for (const child of parent.children) {
                        if (child !== lbl) return child.textContent.trim();
                    }
                }
                return null;
            }""")

            if not monto_raw:
                raise Exception("No se encontró valor junto a 'Monto pendiente'")

            # Parse: "UF 2.390,00" → strip "UF", punto=miles, coma=decimal (formato CL)
            clean = monto_raw.replace("UF", "").replace(" ", "").strip()
            valor_uf = float(clean.replace(".", "").replace(",", "."))
            valor_50pct = round(valor_uf * 0.5, 2)
            valor_str   = str(valor_50pct)
            # Guardar como negativo (deuda) en UF — add_result_uf niega automáticamente
            add_result_uf(resultados, key, "Santander", "CH", "CH Like (50%)", valor_str)
            print_preliminary("Santander", "CH", "CH Like (50%)", "-" + valor_str.lstrip("-"), moneda="UF")
            return True

        except Exception as e_ext:
            print(f"[SAN-DL] Error extrayendo Monto pendiente: {e_ext}")
            add_result(resultados, key, "Santander", "CH", "CH Like (50%)", None, ok=False)
            return False

    except Exception as e:
        print(f"[SAN-DL] Error general: {e}")
        return False
    finally:
        if page:
            try: page.close()
            except: pass
        if iso_context:
            try: iso_context.close()
            except: pass


def scrape_itau(context, resultados):
    key = "itau"
    added = set()
    fresh_ctx_pn = None
    page = None
    try:
        print("[ITA-PN] Obteniendo credenciales...")
        # Contexto fresco (incógnito) — sin cookies de otras sesiones
        fresh_ctx_pn = context.browser.new_context(viewport={"width": 1280, "height": 800})
        fresh_ctx_pn.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
        page = fresh_ctx_pn.new_page()

        # Credenciales por ID para evitar ambigüedad con entry "Itau Empresas"
        import json as _json
        _env = bw_env()
        _list = subprocess.run(["bw", "list", "items", "--search", "banco.itau.cl"],
                               capture_output=True, text=True, env=_env)
        _items = _json.loads(_list.stdout)
        _pn = [i for i in _items if "empresa" not in i["name"].lower()]
        _id = _pn[0]["id"]
        rut = subprocess.run(["bw", "get", "username", _id], capture_output=True, text=True, env=_env).stdout.strip()
        pwd = subprocess.run(["bw", "get", "password", _id], capture_output=True, text=True, env=_env).stdout.strip()
        _slog('ITA-PN', 'creds', f"rut={rut}")
        rut_clean = rut.replace(".", "").replace("-", "").strip()

        print("[ITA-PN] Login...")
        # ── Navegar desde itau.cl → Acceso clientes → Personas (limpia cookies viejas) ──
        page.goto("https://www.itau.cl")
        page.wait_for_load_state("load", timeout=20000)
        page.wait_for_timeout(2000)

        # Click en "Acceso clientes" (usar ID específico para evitar strict mode)
        page.locator("#dropdown_acceso-clientes").click()
        page.wait_for_timeout(1500)

        # Click en "Personas" (buscar link que apunte a newolb login)
        # CRÍTICO headless: el link está dentro del dropdown cerrado → hidden.
        # Usar JS click para bypass de visibility check.
        page.evaluate("""() => {
            const link = Array.from(document.querySelectorAll('a')).find(
                a => a.href && a.href.includes('newolb') && a.href.includes('login')
            );
            if (link) link.click();
            else throw new Error('Link newolb/login no encontrado');
        }""")
        page.wait_for_load_state("load", timeout=20000)
        page.wait_for_timeout(2000)

        rut_input = page.locator("#loginNameID")
        rut_input.wait_for(state="visible", timeout=15000)
        rut_input.click(click_count=3)
        page.wait_for_timeout(200)
        rut_input.press_sequentially(rut_clean, delay=60)
        page.wait_for_timeout(500)

        pwd_input = page.locator("#pswdId")
        pwd_input.click()
        page.wait_for_timeout(300)
        pwd_input.press_sequentially(pwd, delay=60)
        page.wait_for_timeout(500)

        # Esperar que el botón se habilite (Dojo ValidationTextBox valida antes)
        btn = page.locator("#btnLoginPortal")
        for _ in range(30):
            if btn.is_enabled():
                break
            page.wait_for_timeout(500)
        btn.click()

        try:
            page.wait_for_url("**/myportal/**", timeout=30000)
        except Exception:
            pass
        _slog('ITA-PN', 'login', 'ok')
        page.wait_for_timeout(4000)

        # CC saldo
        page.goto("https://banco.itau.cl/wps/myportal/newolb/web/cuentas/cuenta-corriente/saldos/")
        page.wait_for_load_state("load", timeout=15000)
        page.wait_for_timeout(4000)
        label = page.locator("small.itau-card-text", has_text="Saldo disponible para uso")
        label.wait_for(state="visible", timeout=20000)
        saldo_raw = label.locator("xpath=..").locator("h6.itau-card-title").text_content().strip()
        saldo = saldo_raw.replace("$", "").replace(".", "").replace(" ", "").strip()
        _slog('ITA-PN', 'saldo', f'CC = {saldo}')
        add_result(resultados, key, "Itaú", "CC PN", "CC 8792", saldo)
        print_preliminary("Itaú", "CC PN", "CC 8792", saldo)
        added.add("CC 8792")

        # TdC — nth(0)=disponible, nth(1)=utilizado
        page.goto("https://banco.itau.cl/wps/myportal/newolb/web/tarjeta-credito/resumen/deuda/")
        page.wait_for_load_state("load", timeout=15000)
        page.wait_for_timeout(4000)
        page.locator("p.monto-saldo").nth(1).wait_for(state="visible", timeout=15000)
        deuda_raw = page.locator("p.monto-saldo").nth(1).text_content().strip()
        deuda = deuda_raw.replace("$", "").replace(".", "").replace(" ", "").strip()
        _slog('ITA-PN', 'saldo', f'TdC = {deuda}')
        monto = f"-{deuda}" if deuda not in ("0", "") else "0"
        add_result(resultados, key, "Itaú", "TdC", "TdC 6132", monto)
        print_preliminary("Itaú", "TdC", "TdC 6132", monto)
        added.add("TdC 6132")

        # ── LdC ──────────────────────────────────────────────────
        page.goto("https://banco.itau.cl/wps/myportal/newolb/web/cuentas/linea-credito/saldos/")
        page.wait_for_load_state("load", timeout=15000)
        page.wait_for_timeout(4000)
        page.locator('span[name="LCMontoUtilizado"]').wait_for(state="visible", timeout=20000)
        ldc_raw = page.locator('span[name="LCMontoUtilizado"]').text_content().strip()
        ldc = ldc_raw.replace("$", "").replace(".", "").replace(" ", "").strip()
        _slog('ITA-PN', 'saldo', f'LdC = {ldc}')
        monto_ldc = f"-{ldc}" if ldc not in ("0", "") else "0"
        add_result(resultados, key, "Itaú", "LdC", "LdC", monto_ldc)
        print_preliminary("Itaú", "LdC", "LdC", monto_ldc)
        added.add("LdC")

        # ── CH (Crédito Hipotecario) — UF ────────────────────────
        ch_item_name = "CH Cívico"
        page.goto("https://banco.itau.cl/wps/myportal/newolb/web/creditos/credito-hipotecario/consultar-creditos")
        page.wait_for_load_state("load", timeout=15000)
        page.wait_for_timeout(4000)
        ch_label = page.locator("td.bold", has_text="Saldo actual").first
        ch_label.wait_for(state="visible", timeout=15000)
        ch_raw = ch_label.locator("xpath=following-sibling::td[1]").text_content().strip()  # "UF 2.706,70"
        _slog('ITA-PN', 'saldo', f'CH = {ch_raw}')
        add_result_uf(resultados, key, "Itaú", "CH", ch_item_name, ch_raw)
        print_preliminary("Itaú", "CH", ch_item_name, "-" + ch_raw.lstrip("-"), moneda="UF")
        added.add(ch_item_name)

        # ── Pagos TdC 6132 (Mastercard Legend) ───────────────────
        try:
            print("[ITA-PN] Extrayendo pagos TdC 6132...")

            def _ita_clp(s):
                if not s: return None
                try:
                    v = int(s.replace("$","").replace(".","").replace(",","").replace("-","").strip())
                    return v if v != 0 else None
                except: return None

            # 1. Estado de cuenta → facturado, fechas, pagado período anterior
            page.goto("https://banco.itau.cl/wps/myportal/newolb/web/tarjeta-credito/resumen/cuenta-nacional")
            page.wait_for_load_state("load", timeout=20000)
            page.locator('span[name="infoMontoFacturado_iso8601"]').wait_for(state="visible", timeout=20000)

            facturado_raw   = page.locator('span[name="infoMontoFacturado_iso8601"]').text_content().strip()
            pagar_hasta_raw = page.locator('span[name="PagarHasta_iso8601"]').text_content().strip()
            periodo_raw     = page.locator('span[name="pfFechaFacturacion_iso8601"]').text_content().strip()
            pagado_raw      = page.locator('span[name="fld_156_iso8601"]').text_content().strip()

            facturado_clp     = _ita_clp(facturado_raw)
            pagar_hasta_ita   = pagar_hasta_raw.strip() or None
            periodo_hasta_ita = periodo_raw.strip() or None
            pagado_clp_ita    = _ita_clp(pagado_raw)   # abs de "Monto pagado período anterior"

            _slog("ITA-PAG", "info", f"fac={facturado_clp} | periodo={periodo_hasta_ita} | pagar={pagar_hasta_ita}")

            # 2. Últimas compras pesos → pagos no facturados (suma negativos, todas las páginas)
            no_facturado_clp = None
            try:
                page.goto("https://banco.itau.cl/wps/myportal/newolb/web/tarjeta-credito/resumen/compras-pesos")
                page.wait_for_load_state("load", timeout=20000)
                page.wait_for_timeout(2000)

                def _ita_sum_pagos_page():
                    """Suma todos los montos negativos en la página actual."""
                    return page.evaluate("""() => {
                        const rows = [...document.querySelectorAll('table tr')];
                        let total = 0;
                        rows.forEach(row => {
                            const tds = [...row.querySelectorAll('td')];
                            if (tds.length < 5) return;
                            const fechaTxt = tds[0]?.textContent?.trim();
                            if (!/\\d{2}\\/\\d{2}\\/\\d{4}/.test(fechaTxt)) return;
                            const montoTxt = tds[4]?.textContent?.trim() || '';
                            const monto = parseInt(montoTxt.replace(/[$\\. ]/g,'').replace('\\u2212','-').replace('\\u2013','-').replace('−','-').replace('–','-'));
                            if (!isNaN(monto) && monto < 0) total += monto;
                        });
                        return total;
                    }""")

                def _ita_next_enabled():
                    """True si el botón 'nextbtn' está habilitado (imagen _on.png)."""
                    try:
                        src = page.evaluate("""() => {
                            const btn = document.querySelector('a[name="nextbtn"]');
                            return btn?.querySelector('img')?.src || '';
                        }""")
                        return '_on.png' in (src or '')
                    except:
                        return False

                total_pagos = 0
                page_num = 1
                while True:
                    pagos_pag = _ita_sum_pagos_page()
                    total_pagos += pagos_pag
                    _slog("ITA-PAG", "pagos", f"p.{page_num} = {pagos_pag} | total = {total_pagos}")
                    if not _ita_next_enabled():
                        break
                    page.locator('a[name="nextbtn"]').click()
                    page.wait_for_timeout(2500)
                    page_num += 1

                no_facturado_clp = abs(int(total_pagos)) if total_pagos else 0
                _slog("ITA-PAG", "pagos", f"total ({page_num} pág.) = {no_facturado_clp}")
            except Exception as e_nf:
                _slog("ITA-PAG", "error", f"no_facturado: {e_nf}")

            ts_pag = datetime.datetime.now().isoformat()
            _save_pago_tdc({"timestamp": ts_pag, "institucion": "Itaú",
                "card_number": "6132", "card_name": "Mastercard Legend",
                "periodo_hasta": periodo_hasta_ita, "pagar_hasta": pagar_hasta_ita,
                "facturado_clp": facturado_clp, "pagado_clp": no_facturado_clp,
                "facturado_usd": None, "pagado_usd": None,
                "no_facturado_clp": pagado_clp_ita})  # pagado_clp=pagos ciclo actual; no_facturado_clp=pago período anterior
            _console.print(_fmt_pagos_log("Itaú", "6132", fac_clp=facturado_clp, pag_clp=no_facturado_clp), highlight=False)

        except Exception as e_pag:
            print(f"[ITA-PN] Error pagos TdC: {e_pag}")
            _PAGOS_ERRORS.append({"inst": "Itaú", "card": "TdC 6132", "error": str(e_pag)})

        page.close()
        fresh_ctx_pn.close()
        return True
    except Exception as e:
        import traceback
        _slog('ITA-PN', 'error', f"{type(e).__name__}: {e}")
        traceback.print_exc()
        for item, cat in [("CC 8792", "CC PN"), ("TdC 6132", "TdC"), ("LdC", "LdC"), ("CH Cívico", "CH")]:
            if item not in added:
                add_result(resultados, key, "Itaú", cat, item, "error", ok=False)
                print_preliminary("Itaú", cat, item, str(e), ok=False)
        try:
            if page: page.close()
        except: pass
        try:
            if fresh_ctx_pn: fresh_ctx_pn.close()
        except: pass
        return False


def scrape_itau_pj(context, resultados):
    """
    Itaú Empresas (PJ) — One Western Spa 77.788.417-4, CC 0230845735 (CC 5735)
    Login via portal EMPRESAS (newiol):
      www.itau.cl → "Acceso clientes" → "Empresas" (href newiol/web/login)
      Toggle ON ("Quiero acceder con RUT empresa") → 3 campos visibles:
        #rut_empresaID  = RUT empresa 777884174 (campo auto-formatea a 77.788.417-4)
        #rut_usuarioID  = RUT personal 156417076 (sin formato — NO auto-formatea)
        #claveId        = clave internet personal
      Toggle: hacer click en el <label> del checkbox para disparar los eventos JS correctamente
    Bitwarden: banco.itau.cl — credenciales personales (RUT personal + clave internet)
    RUT empresa: hardcoded 777884174 (One Western Spa)
    Post-login: newiol/web/h/home/ — saldo CC en tabla de la home (último td de la fila)
    Contexto aislado para no heredar cookies de Itaú PN.
    """
    key = "itau_pj"
    fresh_ctx = None
    page = None
    try:
        print("[ITA-PJ] Obteniendo credenciales...")
        # Bitwarden: bw list --search itau → filtrar "empresa" in name → entry "Itau Empresas"
        import json as _json
        bw_unlock()  # garantiza BW_SESSION activo antes del bw list
        _env = bw_env()
        _list = subprocess.run(["bw", "list", "items", "--search", "itau"],
                               capture_output=True, text=True, env=_env)
        _items = _json.loads(_list.stdout)
        _pj_entries = [i for i in _items if "empresa" in i["name"].lower()]
        if not _pj_entries:
            raise Exception("No se encontró entry 'Itau Empresas' en Bitwarden")
        _id = _pj_entries[0]["id"]
        rut_empresa  = subprocess.run(["bw", "get", "username", _id],
                                      capture_output=True, text=True, env=_env).stdout.strip()
        pwd          = subprocess.run(["bw", "get", "password", _id],
                                      capture_output=True, text=True, env=_env).stdout.strip()
        rut_empresa  = rut_empresa.replace(".", "").replace("-", "").strip() or "777884174"
        rut_personal = "156417076"  # RUT personal Matias — hardcoded, no cambia
        print(f"[ITA-PJ] rut_personal='{rut_personal}' rut_empresa='{rut_empresa}' pwd={'***' if pwd else '(vacío)'}")

        # ── Contexto aislado (no hereda cookies de Itaú PN) ──
        fresh_ctx = context.browser.new_context(viewport={"width": 1280, "height": 800})
        fresh_ctx.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
        page = fresh_ctx.new_page()

        # ── 1. Navegar itau.cl → Acceso clientes → Empresas ──
        page.goto("https://www.itau.cl")
        page.wait_for_load_state("load", timeout=20000)
        page.wait_for_timeout(2000)

        page.locator("#dropdown_acceso-clientes").click()
        page.wait_for_timeout(1500)

        # CRÍTICO headless: el link está dentro del dropdown cerrado → hidden.
        # Usar JS click para bypass de visibility check.
        page.evaluate("""() => {
            const link = Array.from(document.querySelectorAll('a')).find(
                a => a.href && a.href.includes('newiol/web/login')
            );
            if (link) link.click();
            else throw new Error('Link newiol/web/login no encontrado');
        }""")
        # Esperar networkidle (no solo load) — el SPA hace varias redirecciones intermedias
        # y si el contexto JS se destruye durante una redirección, el evaluate del toggle falla
        try:
            page.wait_for_load_state("networkidle", timeout=25000)
        except Exception:
            page.wait_for_load_state("load", timeout=10000)
        page.wait_for_timeout(2000)

        # ── 2. Activar toggle "Quiero acceder con RUT empresa" ──
        # CRÍTICO: el span #sliderEmpresa intercepta pointer events — no se puede clickear con Playwright
        # Solución: polling JS que espera Y clickea en el mismo evaluate (evita race condition SPA)
        page.evaluate("""() => new Promise((resolve, reject) => {
            const start = Date.now();
            const attempt = () => {
                const el = document.getElementById('new-switch-login');
                if (el) { el.click(); resolve(); return; }
                if (Date.now() - start > 15000) { reject(new Error('Timeout: #new-switch-login no apareció')); return; }
                setTimeout(attempt, 300);
            };
            attempt();
        })""")
        page.wait_for_timeout(800)
        print("[ITA-PJ] Toggle activado")

        # ── 3. Llenar 3 campos ──
        # Campo 1: RUT empresa (auto-formatea 777884174 → 77.788.417-4)
        rut_emp_field = page.locator("#rut_empresaID")
        rut_emp_field.wait_for(state="visible", timeout=10000)
        rut_emp_field.click()
        page.wait_for_timeout(300)
        rut_emp_field.press_sequentially(rut_empresa, delay=60)
        page.wait_for_timeout(400)

        # Campo 2: RUT personal (NO auto-formatea — tipear limpio)
        rut_field = page.locator("#rut_usuarioID")
        rut_field.click()
        page.wait_for_timeout(300)
        rut_field.press_sequentially(rut_personal, delay=60)
        page.wait_for_timeout(400)

        # Campo 3: clave internet
        pwd_field = page.locator("#claveId")
        pwd_field.click()
        page.wait_for_timeout(300)
        pwd_field.press_sequentially(pwd, delay=60)
        page.wait_for_timeout(500)

        # ── 4. Submit ──
        page.get_by_role("button", name="Ingresar").first.click()
        print("[ITA-PJ] Login enviado, esperando home...")

        # ── 4. Post-login ──
        try:
            page.wait_for_url(lambda url: "newiol/web/h" in url, timeout=45000)
        except Exception:
            pass
        _slog('ITA-PJ', 'login', 'ok')
        page.wait_for_timeout(3000)

        # ── 5. Extraer saldo disponible CC desde tabla home ──
        # Esperar a que la tabla de cuentas cargue via AJAX (networkidle + link con número de cuenta)
        try:
            page.wait_for_load_state("networkidle", timeout=20000)
        except Exception:
            pass
        page.wait_for_timeout(3000)
        # Esperar link con número de cuenta (no el menú nav) — más específico que 'Cuenta Corriente'
        try:
            page.wait_for_selector("a:has-text('0230845735')", timeout=15000)
        except Exception:
            pass  # continuar igual y dejar que el evaluate devuelva null si no está

        raw_val = page.evaluate("""() => {
            const link = Array.from(document.querySelectorAll('a'))
                .find(a => a.textContent.includes('0230845735'));
            if (!link) return null;
            const row = link.closest('tr');
            if (!row) return null;
            const cells = Array.from(row.querySelectorAll('td'));
            return cells[cells.length - 1]?.textContent?.trim() || null;
        }""")

        if not raw_val:
            # Debug: mostrar todos los links de la página para entender estructura
            debug_links = page.evaluate("""() => {
                return Array.from(document.querySelectorAll('a'))
                    .map(a => a.textContent.trim().replace(/\\s+/g,' '))
                    .filter(t => t.length > 3 && t.length < 60)
                    .slice(0, 30);
            }""")
            print(f"[ITA-PJ] DEBUG links en página: {debug_links}")
            raise Exception("No se encontró saldo CC 5735 en tabla home newiol")

        saldo = raw_val.replace("$", "").replace(".", "").replace(" ", "").strip()
        _slog('ITA-PJ', 'saldo', f'CC = {saldo}')
        add_result(resultados, key, "Itaú PJ", "CC PJ", "CC 5735", saldo)
        print_preliminary("Itaú PJ", "CC PJ", "CC 5735", saldo)

        page.close()
        fresh_ctx.close()
        return True

    except Exception as e:
        import traceback
        _slog('ITA-PJ', 'error', f"{type(e).__name__}: {e}")
        traceback.print_exc()
        add_result(resultados, key, "Itaú PJ", "CC PJ", "CC 5735", "error", ok=False)
        print_preliminary("Itaú PJ", "CC PJ", "CC 5735", str(e), ok=False)
        try:
            if page: page.close()
        except: pass
        try:
            if fresh_ctx: fresh_ctx.close()
        except: pass
        return False


def scrape_consorcio(context, resultados):
    key = "consorcio"
    added = set()
    page = None
    try:
        print("[CON] Obteniendo credenciales...")
        rut = bw_get("username", "login.consorcio.cl")
        pwd = bw_get("password", "login.consorcio.cl")
        _slog('CON', 'creds', f"rut={rut}")

        print("[CON] Login...")
        page = context.new_page()
        page.goto("https://login.consorcio.cl/onboarding-consorcio/admin")
        page.wait_for_load_state("load", timeout=15000)
        page.wait_for_timeout(1500)
        page.locator("input#input-rut").wait_for(state="visible", timeout=10000)
        page.locator("input#input-rut").press_sequentially(rut, delay=50)
        page.wait_for_timeout(500)
        page.locator("input#input-new-pass").press_sequentially(pwd, delay=50)
        page.wait_for_timeout(500)
        page.get_by_role("button", name="Ingresar").click()
        page.wait_for_load_state("load", timeout=15000)
        _slog('CON', 'login', 'ok')
        page.wait_for_timeout(3000)
        try:
            page.locator("button.btn-cerrar-modal").wait_for(state="visible", timeout=5000)
            page.locator("button.btn-cerrar-modal").click()
            page.wait_for_timeout(500)
        except:
            pass
        cc_card = page.locator("div.elastic-card", has_text="Cuenta Corriente")
        cc_card.wait_for(state="visible", timeout=15000)
        saldo = cc_card.locator("p.elastic-card--product-info").text_content().strip().replace("$", "").strip()
        _slog('CON', 'saldo', f'CC = {saldo}')
        add_result(resultados, key, "Consorcio", "CC PN", "CC 6758", saldo)
        print_preliminary("Consorcio", "CC PN", "CC 6758", saldo)
        added.add("CC 6758")

        # ── LdC ──────────────────────────────────────────────────
        page.goto("https://personas.consorcio.cl/spi/hall-banco/ultimos-movimientos#/?acc=4320116774")
        page.wait_for_timeout(3000)
        ldc_label = page.locator("span.cns-body-sm", has_text="Cupo Utilizado")
        ldc_label.wait_for(state="visible", timeout=15000)
        ldc_raw = ldc_label.locator("xpath=following-sibling::span[1]").text_content().strip()
        ldc = ldc_raw.replace("$", "").replace(".", "").replace(" ", "").strip()
        _slog('CON', 'saldo', f'LdC = {ldc}')
        monto_ldc = f"-{ldc}" if ldc not in ("0", "") else "0"
        add_result(resultados, key, "Consorcio", "LdC", "LdC", monto_ldc)
        print_preliminary("Consorcio", "LdC", "LdC", monto_ldc)
        added.add("LdC")

        # ── CH (Crédito Hipotecario) — UF ────────────────────────
        # servicios.bancoconsorcio.cl es dominio distinto — hay que navegar via menú
        # para que el SSO transfiera la sesión correctamente
        page.goto("https://personas.consorcio.cl/spi")
        page.wait_for_load_state("networkidle", timeout=20000)
        page.wait_for_timeout(2000)
        # Esperar explícitamente a que el menú esté presente
        page.wait_for_selector("#itemHeader1", state="visible", timeout=20000)
        page.locator("#itemHeader1").hover()   # hover abre dropdown "Créditos"
        page.wait_for_timeout(800)
        page.locator("div.card-header-spi-text-header", has_text="Mis Créditos Hipotecarios").click()
        page.wait_for_timeout(8000)            # espera navegación + render Angular
        ch_cell = page.locator("td.tac.ng-binding").first
        ch_cell.wait_for(state="visible", timeout=20000)
        ch_raw = ch_cell.text_content().strip()   # "UF 9.590,97"
        _slog('CON', 'saldo', f'CH = {ch_raw}')
        add_result_uf(resultados, key, "Consorcio", "CH", "CH Taihuén", ch_raw)
        print_preliminary("Consorcio", "CH", "CH Taihuén", "-" + ch_raw.lstrip("-"), moneda="UF")
        added.add("CH Taihuén")

        page.close()
        return True
    except Exception as e:
        import traceback
        _slog('CON', 'error', f"{type(e).__name__}: {e}")
        traceback.print_exc()
        for item, cat in [("CC 6758","CC PN"), ("LdC","LdC"), ("CH Taihuén","CH")]:
            if item not in added:
                add_result(resultados, key, "Consorcio", cat, item, "error", ok=False)
                print_preliminary("Consorcio", cat, item, str(e), ok=False)
        try:
            if page: page.close()
        except: pass
        return False


def scrape_scotiabank_pj(context, resultados):
    key = "scotiabank_pj"
    page = None
    try:
        print("[SCO-PJ] Obteniendo credenciales...")
        user = bw_get("username", "Scotiabank Empresas")
        pwd = bw_get("password", "Scotiabank Empresas")
        print(f"[SCO-PJ] user='{user}' pwd={'***' if pwd else '(vacío)'}")

        print("[SCO-PJ] Login...")
        page = context.new_page()
        page.goto("https://appservtrx.scotiabank.cl/portalempresas/login")
        page.wait_for_timeout(2000)
        page.get_by_placeholder("RUT Empresa").fill("77.788.417-4")
        page.get_by_placeholder("RUT Usuario").fill(user)
        page.locator("#INP_COMMON_PASSWORD_PASS").fill(pwd)
        page.wait_for_timeout(500)
        page.get_by_role("button", name="Ingresar").click()
        page.wait_for_url("**/portalempresas/home**", timeout=15000)
        _slog('SCO-PJ', 'login', 'ok')
        page.wait_for_timeout(1000)
        try:
            page.get_by_role("button", name="Aceptar").wait_for(state="visible", timeout=3000)
            page.get_by_role("button", name="Aceptar").click()
            page.wait_for_timeout(500)
        except:
            pass
        page.goto("https://appservtrx.scotiabank.cl/portalempresas/home/products")
        page.locator("#DISPONIBLE_CTA_DK").wait_for(state="visible", timeout=15000)
        saldo = page.locator("#DISPONIBLE_CTA_DK").inner_text().strip().replace("$", "").strip()
        _slog('SCO-PJ', 'saldo', f'CC = {saldo}')
        add_result(resultados, key, "Scotiabank", "CC PJ", "CC 7381", saldo)
        print_preliminary("Scotiabank", "CC PJ", "CC 7381", saldo)
        page.close()
        return True
    except Exception as e:
        import traceback
        _slog('SCO-PJ', 'error', f"{type(e).__name__}: {e}")
        traceback.print_exc()
        add_result(resultados, key, "Scotiabank", "CC PJ", "CC 7381", "error", ok=False)
        print_preliminary("Scotiabank", "CC PJ", "CC 7381", str(e), ok=False)
        try:
            if page: page.close()
        except: pass
        return False


def scrape_afc(context, resultados):
    """Scrape AFC - Fondo de Cesantía (Previsional) — CAPTCHA requerido, igual que Líder BCI"""
    key = "afc"
    page = None
    try:
        print("[AFC] Obteniendo credenciales Bitwarden...")
        rut_raw = bw_get("username", "webafiliados.afc.cl")
        pwd = bw_get("password", "webafiliados.afc.cl")
        print(f"[AFC] rut_raw='{rut_raw}' pwd={'***' if pwd else '(vacío)'}")

        if not rut_raw or not pwd:
            add_result(resultados, key, "AFC", "Previsional", "Fondo de Cesantía", "error", ok=False)
            print_preliminary("AFC", "Previsional", "Fondo de Cesantía", "No se encontraron credenciales en Bitwarden", ok=False)
            return False

        print("[AFC] Abriendo browser...")
        page = context.new_page()

        # Navegación: directo a Default.aspx (misma URL que www.afc.cl → Sucursal virtual → Afiliados)
        print("[AFC] Navegando a portal afiliados AFC...")
        page.goto("https://webafiliados.afc.cl/WUI.AAP.OVIRTUAL/Default.aspx")
        page.wait_for_load_state("load")
        page.wait_for_timeout(2000)
        print(f"[AFC] URL: {page.url} — Title: {page.title()}")

        # Ahora estamos en Default.aspx con botones "ClaveÚnica" y "Clave AFC" + CAPTCHA
        # El botón "Clave AFC" es: input[type="submit"]#btnIngresarafc
        print("[AFC] WARNING:   Marca 'No soy un robot' en el browser — el script continúa automáticamente")

        # Loop: intentar clic en #btnIngresarafc hasta que CAPTCHA esté resuelto (máx 90s)
        clicked = False
        for intento in range(45):  # 45 × 2s = ~90s
            try:
                btn = page.locator("#btnIngresarafc")
                btn.wait_for(state="visible", timeout=5000)
                btn.click()
                page.wait_for_timeout(2000)
                # Verificar si el modal de captcha desapareció o si el form de RUT se ve al fondo
                if page.locator("#txtRutTrabajador").is_visible() or page.locator("text=RUT TRABAJADOR").is_visible():
                    print("[AFC] Transition detectada!")
                    clicked = True
                    break
                if page.locator("text=Verificar casilla de seguridad").is_visible():
                    print(f"[AFC] ({intento+1}) CAPTCHA aún no resuelto...")
                    continue
                # Sin error → clic exitoso, dar tiempo al form de cargar (SPA transition)
                page.wait_for_timeout(3000)
                print(f"[AFC] 'Clave AFC' clickeado ok — URL: {page.url}")
                clicked = True
                break
            except Exception as ex:
                import traceback
                print(f"[AFC] ({intento+1}) Excepción: {type(ex).__name__}: {ex}")
                traceback.print_exc()
                page.wait_for_timeout(2000)

        if not clicked:
            raise Exception("No se pudo hacer clic en 'Clave AFC' después de 90s")

        # Esperar que aparezca el campo RUT — IDs: #txtRutTrabajador, #txtPwdTrabajador
        print(f"[AFC] Esperando form de login... URL actual: {page.url}")
        page.wait_for_selector("#txtRutTrabajador", state="visible", timeout=30000)
        page.wait_for_timeout(800)

        # Rellenar RUT y clave usando IDs (los labels no tienen atributo for)
        print("[AFC] Form visible. Ingresando credenciales...")
        page.locator("#txtRutTrabajador").fill(rut_raw)
        page.wait_for_timeout(300)
        page.locator("#txtPwdTrabajador").fill(pwd)
        page.wait_for_timeout(300)
        page.locator("#btnIngresar").click()
        print("[AFC] Clic Ingresar. Esperando dashboard...")

        # Esperar dashboard
        page.wait_for_url("**/Portada.aspx**", timeout=20000)
        page.wait_for_load_state("load")
        page.wait_for_timeout(5000)  # esperar más para que carguen los popups lazy
        print(f"[AFC] Dashboard ok → {page.url[:70]}")

        # Cerrar modal de seguridad AFC ("En AFC Chile nos preocupamos de su seguridad")
        # × del modal: button.modal-close (clase única, confirmado con inspección)
        print("[AFC] Cerrando modal de seguridad...")
        try:
            modal_close = page.locator("button.modal-close")
            modal_close.wait_for(state="visible", timeout=8000)
            modal_close.click()
            print("[AFC] Modal de seguridad cerrado")
            page.wait_for_timeout(1000)
        except Exception as e:
            print(f"[AFC] Modal no apareció o ya cerrado: {e}")

        # Extraer saldo: heading "Saldo total: $12.692.099"
        if DEBUG:
            pass

        saldo_loc = page.get_by_role("heading", name=re.compile(r"Saldo total"))
        saldo_loc.wait_for(state="visible", timeout=10000)
        saldo_text = " ".join(saldo_loc.text_content().split())  # colapsar newlines/espacios

        # Parsear: "Saldo total: $12.692.099" → 12692099
        match = re.search(r'\$([\d\.]+)', saldo_text)
        if not match:
            add_result(resultados, key, "AFC", "Previsional", "Fondo de Cesantía", "error", ok=False)
            print_preliminary("AFC", "Previsional", "Fondo de Cesantía", "Monto no encontrado", ok=False)
            page.close()
            return False

        monto = int(match.group(1).replace(".", ""))
        _slog("AFC", "saldo", f"Fondo de Cesantía = {monto:,}".replace(",", "."))
        add_result(resultados, key, "AFC", "Previsional", "Fondo de Cesantía", str(monto))
        print_preliminary("AFC", "Previsional", "Fondo de Cesantía", fmt_monto(monto))

        page.close()
        return True

    except Exception as e:
        import traceback
        _slog('AFC', 'error', f"{type(e).__name__}: {e}")
        traceback.print_exc()
        add_result(resultados, key, "AFC", "Previsional", "Fondo de Cesantía", "error", ok=False)
        print_preliminary("AFC", "Previsional", "Fondo de Cesantía", str(e), ok=False)
        try:
            if page: page.close()
        except: pass
        return False


def format_rut_cl(rut_raw):
    """Convierte RUT sin formato ('156417076') a formato portal AFP ('15.641.707-6')"""
    rut = rut_raw.strip().upper().replace(".", "").replace("-", "")
    if len(rut) < 2:
        return rut_raw
    verif = rut[-1]
    numero = rut[:-1]
    formatted = ""
    for i, ch in enumerate(reversed(numero)):
        if i > 0 and i % 3 == 0:
            formatted = "." + formatted
        formatted = ch + formatted
    return f"{formatted}-{verif}"


def scrape_afp_modelo(context, resultados):
    """Scrape AFP Modelo - Cuenta Obligatoria (Previsional)"""
    key = "afp_modelo"
    page = None
    try:
        # Obtener credenciales ANTES de abrir el navegador
        # Bitwarden entry: "nueva.afpmodelo.cl" (usuario: 156417076, clave: 6 dígitos)
        print("[AFP] Obteniendo credenciales Bitwarden...")
        rut_raw = bw_get("username", "nueva.afpmodelo.cl")
        pwd = bw_get("password", "nueva.afpmodelo.cl")
        print(f"[AFP] rut_raw='{rut_raw}' pwd={'***' if pwd else '(vacío)'}")

        # El portal Vue valida RUT con formato "15.641.707-6" (con puntos y guión)
        rut = format_rut_cl(rut_raw) if rut_raw else ""
        print(f"[AFP] rut formateado='{rut}'")

        if not rut or not pwd:
            add_result(resultados, key, "AFP Modelo", "Previsional", "Cuenta Obligatoria", "error", ok=False)
            print_preliminary("AFP Modelo", "Previsional", "Cuenta Obligatoria", "No se encontraron credenciales en Bitwarden", ok=False)
            return False

        print("[AFP] Abriendo browser...")
        page = context.new_page()
        page.goto("https://nueva.afpmodelo.cl/portalprivado/user/login#/")
        page.wait_for_load_state("load")
        page.wait_for_timeout(1500)

        # Esperar que el form cargue completamente
        print("[AFP] Esperando form #rut...")
        page.wait_for_selector('#rut', state="visible", timeout=10000)
        print("[AFP] Form cargado. Tipeando RUT...")

        # RUT: campo id="rut", portal Vue.js
        page.locator('#rut').click()
        page.keyboard.press('Control+a')  # Seleccionar todo para limpiar
        page.locator('#rut').press_sequentially(rut, delay=80)
        page.wait_for_timeout(500)

        # Leer valor que quedó en el campo
        rut_en_campo = page.locator('#rut').input_value()
        print(f"[AFP] RUT en campo: '{rut_en_campo}'")

        # Password: campo id="password", solo 6 dígitos numéricos, maxlength=6
        print("[AFP] Tipeando password...")
        page.locator('#password').click()
        page.keyboard.press('Control+a')
        page.keyboard.press('Delete')
        page.locator('#password').press_sequentially(pwd, delay=80)
        page.wait_for_timeout(500)

        pwd_en_campo = page.locator('#password').input_value()
        print(f"[AFP] Password en campo: {len(pwd_en_campo)} chars")

        # Verificar estado del botón antes de esperar
        btn_disabled = page.locator('button[type="submit"]').get_attribute('disabled')
        print(f"[AFP] Botón disabled={btn_disabled}. Esperando que se habilite...")

        # Esperar que el botón se habilite (Vue valida el form antes de habilitar)
        page.wait_for_selector('button[type="submit"]:not([disabled])', timeout=8000)
        print("[AFP] Botón habilitado. Haciendo click...")
        page.click('button[type="submit"]')

        # Esperar dashboard
        print("[AFP] Esperando dashboard...")
        page.wait_for_url("**/portalprivado/inicio**", timeout=20000)
        page.wait_for_load_state("load")
        page.wait_for_timeout(2000)
        print("[AFP] Dashboard cargado. Extrayendo monto...")

        # Extraer monto: h2.card-balance (único elemento, contiene "$65.630.736")
        monto_locator = page.locator('h2.card-balance').first
        monto_locator.wait_for(state="visible", timeout=8000)
        monto_text = monto_locator.text_content().strip()
        print(f"[AFP] monto_text='{monto_text}'")

        if not monto_text or "$" not in monto_text:
            add_result(resultados, key, "AFP Modelo", "Previsional", "Cuenta Obligatoria", "error", ok=False)
            print_preliminary("AFP Modelo", "Previsional", "Cuenta Obligatoria", "Monto no encontrado", ok=False)
            page.close()
            return False

        # Parsear: "$65.630.736" → 65630736
        monto_clean = monto_text.replace("$", "").replace(".", "").replace(",", ".").strip()
        monto = float(monto_clean)

        add_result(resultados, key, "AFP Modelo", "Previsional", "Cuenta Obligatoria", str(int(monto)))
        print_preliminary("AFP Modelo", "Previsional", "Cuenta Obligatoria", fmt_monto(int(monto)))

        page.close()
        return True

    except Exception as e:
        import traceback
        _slog('AFP', 'error', f"{type(e).__name__}: {e}")
        traceback.print_exc()
        add_result(resultados, key, "AFP Modelo", "Previsional", "Cuenta Obligatoria", "error", ok=False)
        print_preliminary("AFP Modelo", "Previsional", "Cuenta Obligatoria", str(e), ok=False)
        try:
            if page: page.close()
        except: pass
        return False


_BTG_STATE = Path(__file__).parent / "btg_session.json"

def scrape_btg(context, resultados):
    """Scrape BTG Pactual - Fondos de inversión PN (CFISP500, CFINASDAQ, CFIETFGE)"""
    key         = "btg"
    iso_context = None
    page        = None
    try:
        if DEBUG: print("[BTG] Obteniendo credenciales Bitwarden...")
        rut_raw = bw_get("username", "app.btgpactual.cl - Persona Natural")
        pwd     = bw_get("password", "app.btgpactual.cl - Persona Natural")
        if DEBUG: print(f"[BTG] rut_raw='{rut_raw}' pwd={'***' if pwd else '(vacío)'}")

        rut = format_rut_cl(rut_raw) if rut_raw else ""
        if DEBUG: print(f"[BTG] rut formateado='{rut}'")

        if not rut or not pwd:
            for item in ["CFISP500", "CFINASDAQ", "CFIETFGE"]:
                add_result(resultados, key, "BTG Pactual", "Inversiones Líquidas", item, "error", ok=False)
                print_preliminary("BTG Pactual", "Inversiones Líquidas", item, "Sin credenciales", ok=False)
            return False

        # ── Contexto aislado para persistencia ──
        ctx_kwargs = {"viewport": {"width": 1280, "height": 800}}
        if _BTG_STATE.exists():
            ctx_kwargs["storage_state"] = str(_BTG_STATE)
            if DEBUG: print(f"[BTG] Cargando sesión guardada: {_BTG_STATE}")

        iso_context = context.browser.new_context(**ctx_kwargs)
        iso_context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
        page = iso_context.new_page()

        def perform_login_btg():
            try:
                if "/login" in page.url or page.locator("#rut").first.is_visible():
                    if DEBUG: print("[BTG] Llenando formulario de login...")
                    page.wait_for_selector("#rut", state="visible", timeout=20000)
                    page.locator("#rut").first.fill(rut)
                    page.wait_for_timeout(500)
                    page.locator("#password").first.fill(pwd)
                    page.wait_for_timeout(500)
                    page.get_by_role("button", name="Iniciar sesión").click()
                    return True
            except: pass
            return False

        # Intentar Portfolio; si redirige a login → hacer login
        page.goto("https://app.btgpactual.cl/portfolio", timeout=60000)
        page.wait_for_timeout(10000)
        
        # Anti-Rebote: si vemos logout en la URL o rebote al login
        if "logout" in page.url or "/login" in page.url:
            if DEBUG: print("[BTG] Redirección detectada, intentando estabilizar...")
            if "/login" in page.url:
                perform_login_btg()
            else:
                page.goto("https://app.btgpactual.cl/portfolio")
                page.wait_for_timeout(5000)
                if "/login" in page.url: perform_login_btg()

        # Espera de dashboard con re-intento si aparece el login
        dashboard_sel = "div.dropdown__header"
        for _ in range(3):
            try:
                page.wait_for_selector(dashboard_sel, timeout=30000)
                if "/portfolio" in page.url:
                    iso_context.storage_state(path=str(_BTG_STATE))
                    break
            except:
                if page.locator("#rut").first.is_visible():
                    if DEBUG: print("[BTG] Re-apareció login durante espera. Re-intentando...")
                    perform_login_btg()
                else: 
                    page.goto("https://app.btgpactual.cl/portfolio")
                    page.wait_for_timeout(5000)

        if DEBUG: print(f"[BTG] Post-login URL: {page.url}")

        page.wait_for_selector("div.dropdown__header", state="visible", timeout=30000)
        page.wait_for_timeout(500)

        header = page.locator("div.dropdown__header").first
        header_class = header.get_attribute("class") or ""
        if "--open" not in header_class:
            if DEBUG: print("[BTG] Expandiendo Fondos de inversión...")
            header.click()
            page.wait_for_timeout(2000)

        # Esperar que aparezcan las filas de fondos
        page.wait_for_selector("div.table__row.text-emphasis-base-high", timeout=30000)
        page.wait_for_timeout(500)

        # Extraer valorización de cada fondo
        fondos = {
            "CFISP500":  "S&P 500",
            "CFINASDAQ": "Nasdaq",
            "CFIETFGE":  "Global Equities",
        }

        datos = page.evaluate("""() => {
            const result = {};
            const nombres = {"S&P 500": "CFISP500", "Nasdaq": "CFINASDAQ", "Global Equities": "CFIETFGE"};
            document.querySelectorAll('div.table__row.text-emphasis-base-high').forEach(row => {
                const celdas = row.querySelectorAll('div.table__row-cell--has-details');
                if (!celdas.length) return;
                const nombre = celdas[0].innerText || '';
                const valorCelda = celdas[celdas.length - 1];
                const valor = (valorCelda?.innerText || '').trim();
                Object.entries(nombres).forEach(([k, item]) => {
                    if (nombre.includes(k)) result[item] = valor;
                });
            });
            return result;
        }""")

        if DEBUG:
            print(f"[BTG] datos extraídos: {datos}")

        for item_key, nombre_fondo in fondos.items():
            val_str = datos.get(item_key, "")
            if val_str and val_str.startswith("$"):
                monto = int(val_str.replace("$", "").replace(".", "").replace(",", "").strip())
                add_result(resultados, key, "BTG Pactual", "Inversiones Líquidas", item_key, str(monto))
                print_preliminary("BTG Pactual", "Inversiones Líquidas", item_key, str(monto))
            else:
                add_result(resultados, key, "BTG Pactual", "Inversiones Líquidas", item_key, "error", ok=False)
                print_preliminary("BTG Pactual", "Inversiones Líquidas", item_key, "No obtenido", ok=False)

        page.close()
        iso_context.close()
        return True

    except Exception as e:
        print(f"[BTG] [ERROR]  Error: {e}")
        for item in ["CFISP500", "CFINASDAQ", "CFIETFGE"]:
            if item not in [r["item"] for r in resultados if r["bank_key"] == key]:
                add_result(resultados, key, "BTG Pactual", "Inversiones Líquidas", item, "error", ok=False)
                print_preliminary("BTG Pactual", "Inversiones Líquidas", item, str(e)[:60], ok=False)
        return False
    finally:
        if page:
            try: page.close()
            except: pass
        if iso_context:
            try: iso_context.close()
            except: pass


_BTG_PJ_STATE = Path(__file__).parent / "btg_pj_session.json"

def scrape_btg_pj(context, resultados):
    """Scrape BTG Pactual PJ - Fondos de inversión PJ (mismos selectores que PN)"""
    key         = "btg_pj"
    page        = None
    iso_context = None
    try:
        if DEBUG: print("[BTG PJ] Obteniendo credenciales Bitwarden...")
        import json as _json
        _bw_env = bw_env()
        _list = subprocess.run(["bw", "list", "items", "--search", "btgpactual"],
                               capture_output=True, text=True, env=_bw_env)
        _items = _json.loads(_list.stdout) if _list.stdout.strip() else []
        _pj = next((i for i in _items if "777884174" in str(i.get("login", {}).get("username", ""))), None)
        rut_raw = _pj["login"]["username"] if _pj else ""
        pwd     = _pj["login"]["password"] if _pj else ""

        rut = format_rut_cl(rut_raw) if rut_raw else ""
        if DEBUG: print(f"[BTG PJ] rut='{rut}'")

        if not rut or not pwd:
            for item in ["CFISP500", "CFINASDAQ", "CFIETFGE"]:
                add_result(resultados, key, "BTG Pactual", "Inversiones Líquidas PJ", item, "error", ok=False)
                print_preliminary("BTG Pactual", "Inversiones Líquidas PJ", item, "Sin credenciales", ok=False)
            return False

        # Contexto aislado — evita interferencia de cookies de BTG PN
        ctx_kwargs = {"viewport": {"width": 1280, "height": 800}}
        if _BTG_PJ_STATE.exists():
            ctx_kwargs["storage_state"] = str(_BTG_PJ_STATE)
            if DEBUG: print(f"[BTG PJ] Cargando sesión guardada: {_BTG_PJ_STATE}")

        iso_context = context.browser.new_context(**ctx_kwargs)
        iso_context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
        page = iso_context.new_page()

        # BTG Pactual PJ: mayor espera inicial para estabilizar redirecciones
        page.goto("https://app.btgpactual.cl/portfolio", timeout=60000)
        page.wait_for_timeout(10000)
        
        # Anti-Rebote: si vemos logout en la URL, reintentamos portfolio una vez
        if "logout" in page.url:
            if DEBUG: print("[BTG PJ] Detectado redirección a logout, re-intentando portfolio...")
            page.goto("https://app.btgpactual.cl/portfolio")
            page.wait_for_timeout(5000)

        if "/login" in page.url:
            if DEBUG: print("[BTG PJ] Sesión inválida, procediendo a login...")
            page.wait_for_selector("#rut", state="visible", timeout=20000)
            page.locator("#rut").first.fill(rut)
            page.wait_for_timeout(500)
            page.locator("#password").first.fill(pwd)
            page.wait_for_timeout(500)
            page.get_by_role("button", name="Iniciar sesión").click()
            
            page.wait_for_url("**/portfolio", timeout=90000)
            page.wait_for_timeout(5000)
            # Guardar sesión tras login exitoso
            iso_context.storage_state(path=str(_BTG_PJ_STATE))
        if DEBUG: print(f"[BTG PJ] Post-login URL: {page.url}")

        page.wait_for_selector("div.dropdown__header", state="visible", timeout=30000)
        page.wait_for_timeout(500)

        header = page.locator("div.dropdown__header").first
        header_class = header.get_attribute("class") or ""
        if "--open" not in header_class:
            if DEBUG: print("[BTG PJ] Expandiendo Fondos de inversión...")
            header.click()
            page.wait_for_timeout(2000)

        page.wait_for_selector("div.table__row.text-emphasis-base-high", timeout=30000)
        page.wait_for_timeout(500)

        datos = page.evaluate("""() => {
            const result = {};
            const nombres = {"S&P 500": "CFISP500", "Nasdaq": "CFINASDAQ", "Global Equities": "CFIETFGE"};
            document.querySelectorAll('div.table__row.text-emphasis-base-high').forEach(row => {
                const celdas = row.querySelectorAll('div.table__row-cell--has-details');
                if (!celdas.length) return;
                const nombre = celdas[0].innerText || '';
                const valorCelda = celdas[celdas.length - 1];
                const valor = (valorCelda?.innerText || '').trim();
                Object.entries(nombres).forEach(([k, item]) => {
                    if (nombre.includes(k)) result[item] = valor;
                });
            });
            return result;
        }""")

        if DEBUG: print(f"[BTG PJ] datos: {datos}")

        fondos = ["CFISP500", "CFINASDAQ", "CFIETFGE"]
        for item_key in fondos:
            val_str = datos.get(item_key, "")
            if val_str and val_str.startswith("$"):
                monto = int(val_str.replace("$", "").replace(".", "").replace(",", "").strip())
                add_result(resultados, key, "BTG Pactual", "Inversiones Líquidas PJ", item_key, str(monto))
                print_preliminary("BTG Pactual", "Inversiones Líquidas PJ", item_key, str(monto))
            else:
                add_result(resultados, key, "BTG Pactual", "Inversiones Líquidas PJ", item_key, "error", ok=False)
                print_preliminary("BTG Pactual", "Inversiones Líquidas PJ", item_key, "No obtenido", ok=False)

        page.close()
        iso_context.close()
        return True

    except Exception as e:
        print(f"[BTG PJ] [ERROR]  Error: {e}")
        for item in ["CFISP500", "CFINASDAQ", "CFIETFGE"]:
            add_result(resultados, key, "BTG Pactual", "Inversiones Líquidas PJ", item, "error", ok=False)
            print_preliminary("BTG Pactual", "Inversiones Líquidas PJ", item, str(e)[:60], ok=False)
        try:
            if page: page.close()
        except: pass
        try:
            if iso_context: iso_context.close()
        except: pass
        return False


_SCHWAB_STATE = Path(__file__).parent / "schwab_session.json"

def scrape_schwab(context, resultados):
    """Charles Schwabb — Inversiones Líquidas PN (USD): BRK/B, QQQ.
    Login via iframe #lmsIframe. Post-login: Account Summary.
    Extrae Market Value de tr.positions-parent-row por símbolo."""
    key         = "schwab"
    SYMBOLS     = ["BRK/B", "QQQ"]
    iso_context = None
    page        = None
    try:
        username = bw_get("username", "client.schwab.com")
        password = bw_get("password", "client.schwab.com")
        if not username or not password:
            for s in SYMBOLS:
                add_result_usd(resultados, key, "Charles Schwabb", "Inversiones Líquidas", s, "error", ok=False)
                print_preliminary("Charles Schwabb", "Inversiones Líquidas", s, "Sin credenciales", ok=False, moneda="USD")
            return False

        # ── Contexto aislado para persistencia ──
        ctx_kwargs = {"viewport": {"width": 1280, "height": 800}}
        if _SCHWAB_STATE.exists():
            ctx_kwargs["storage_state"] = str(_SCHWAB_STATE)
            if DEBUG: print(f"[SCHWABB] Cargando sesión guardada: {_SCHWAB_STATE}")

        iso_context = context.browser.new_context(**ctx_kwargs)
        iso_context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
        page = iso_context.new_page()

        # Intentar ir a Summary; si redirige a login → hacer login
        page.goto("https://client.schwab.com/app/accounts/summary/", timeout=60000)
        page.wait_for_timeout(3000)

        if "Login" in page.url or "Access" in page.url:
            if DEBUG: print("[SCHWABB] Sesión inválida, procediendo a login...")
            try:
                lms = page.frame_locator("#lmsIframe")
                lms.locator("#loginIdInput").wait_for(state="visible", timeout=12000)
                lms.locator("#loginIdInput").fill(username)
                page.wait_for_timeout(400)
                lms.locator("#passwordInput").fill(password)
                page.wait_for_timeout(400)
                lms.locator("#btnLogin").click()
            except Exception as e_login:
                if DEBUG: print(f"[SCHWABB] Auto-login falló: {e_login}")
                print("WARNING:   [SCHWABB] Inicia sesión/MFA manualmente (120s max)...")

            page.wait_for_url("**/app/accounts/**", timeout=120000)
            page.wait_for_timeout(3000)
            iso_context.storage_state(path=str(_SCHWAB_STATE))

        # Si no estamos en el dashboard tras login/sesión, esperar
        if "app/accounts" not in page.url:
            page.wait_for_url("**/app/accounts/**", timeout=120000)
            page.wait_for_timeout(3000)

        # Ir a Account Summary si no estamos ahí
        if "summary" not in page.url:
            page.goto("https://client.schwab.com/app/accounts/summary/", timeout=30000)
            page.wait_for_load_state("load")
            page.wait_for_timeout(4000)

        # Esperar que carguen las posiciones
        page.wait_for_selector("tr.positions-parent-row", timeout=30000)
        page.wait_for_timeout(2000)

        # Extraer Market Value por símbolo via JS
        all_ok = True
        for symbol in SYMBOLS:
            try:
                raw_val = page.evaluate("""(symbol) => {
                    const span = Array.from(document.querySelectorAll('tr.positions-parent-row span'))
                        .find(el => el.textContent.trim() === symbol);
                    if (!span) return null;
                    const row = span.closest('tr.positions-parent-row');
                    const mvCell = Array.from(row.querySelectorAll('td'))
                        .find(td => td.innerText.trim().startsWith('Market Value'));
                    if (!mvCell) return null;
                    // Tomar el span sin clase sr-only (es el valor limpio, sin ‡)
                    const valSpan = Array.from(mvCell.querySelectorAll('span'))
                        .find(s => !s.className.includes('sr-only'));
                    return valSpan ? valSpan.textContent.trim() : null;
                }""", symbol)

                if raw_val:
                    add_result_usd(resultados, key, "Charles Schwabb", "Inversiones Líquidas", symbol, raw_val)
                    print_preliminary("Charles Schwabb", "Inversiones Líquidas", symbol, raw_val, moneda="USD")
                else:
                    if DEBUG: print(f"[SCHWABB] {symbol}: no encontrado en página")
                    add_result_usd(resultados, key, "Charles Schwabb", "Inversiones Líquidas", symbol, "error", ok=False)
                    print_preliminary("Charles Schwabb", "Inversiones Líquidas", symbol, "No obtenido", ok=False, moneda="USD")
                    all_ok = False
            except Exception as e:
                if DEBUG: print(f"[SCHWABB] Error extrayendo {symbol}: {e}")
                add_result_usd(resultados, key, "Charles Schwabb", "Inversiones Líquidas", symbol, "error", ok=False)
                print_preliminary("Charles Schwabb", "Inversiones Líquidas", symbol, str(e)[:60], ok=False, moneda="USD")
                all_ok = False

        return all_ok

    except Exception as e:
        if DEBUG: print(f"[SCHWABB] Error general: {e}")
        for s in SYMBOLS:
            add_result_usd(resultados, key, "Charles Schwabb", "Inversiones Líquidas", s, "error", ok=False)
            print_preliminary("Charles Schwabb", "Inversiones Líquidas", s, str(e)[:60], ok=False, moneda="USD")
        return False
    finally:
        if page:
            try: page.close()
            except: pass


_RACIONAL_STATE = Path(__file__).parent / "racional_session.json"


def _racional_get_en_progreso_extra(page):
    """
    Suma montos de retiros cuyas acciones ya se vendieron pero la transferencia aún no llega.
    Condición exacta (verificada con Claude Chrome):
      - El detalle del movimiento tiene URL con status=toSendToWithdrawalsApi, O
      - El texto de la página contiene "Transferenciarealizada" sin fecha inmediatamente después.
    Excluye el retiro de $52.511.911 (bug conocido de Racional).
    """
    import re as _re
    BUG_AMT   = 52_511_911
    BUG_TOL   = 200_000
    extra     = 0

    try:
        # ── 1. Ir al home ──────────────────────────────────────────────────────
        if "/tabs/home" not in page.url:
            page.goto("https://app.racional.cl/tabs/home", timeout=30000)
            page.wait_for_load_state("load", timeout=20000)
            page.wait_for_timeout(2000)

        # ── 2. Esperar que el panel derecho renderice y parsear montos ────────────
        # El panel "Movimientos en progreso" carga async — esperarlo explícitamente
        try:
            page.wait_for_selector(
                "text=Movimientos en progreso", timeout=12000
            )
            page.wait_for_timeout(800)
        except Exception:
            print("[RACIONAL-PROG] Timeout esperando 'Movimientos en progreso'.", flush=True)
            return 0

        # El innerText del home tiene formato (sin saltos de línea):
        #   "...Movimientos en progresoRetiro$5.000.00004/05/2026...Movimientos recientes..."
        # Regex con formato chileno estricto (grupos de exactamente 3 dígitos) para no
        # capturar la fecha que sigue pegada: "$5.000.00004" → solo "$5.000.000"
        home_text = page.inner_text("body")
        # Normalizar: aplanar y colapsar espacios extra
        flat_home = _re.sub(r'\s+', ' ', home_text)
        m_sec = _re.search(
            r'Movimientos en progreso(.*?)Movimientos recientes',
            flat_home, _re.DOTALL
        )
        if not m_sec:
            # Debug: mostrar fragmento del texto para diagnosticar
            snippet = flat_home[max(0, flat_home.find('Movimientos')-20):flat_home.find('Movimientos')+120] if 'Movimientos' in flat_home else flat_home[:200]
            print(f"[RACIONAL-PROG] No se encontró sección. Texto: '{snippet}'", flush=True)
            return 0

        sec_text = m_sec.group(1)
        # Pattern: $ + 1-3 dígitos + (punto + exactamente 3 dígitos)*
        amounts = []
        for m in _re.finditer(r'\$(\d{1,3}(?:\.\d{3})*)', sec_text):
            amt = int(m.group(1).replace('.', ''))
            if amt > 0 and amt not in amounts:
                amounts.append(amt)

        if not amounts:
            print("[RACIONAL-PROG] No se encontraron montos en progreso.", flush=True)
            return 0

        print(f"[RACIONAL-PROG] Montos en progreso: {['$'+str(a) for a in amounts]}", flush=True)

        # ── 3. Revisar cada movimiento ─────────────────────────────────────────
        for amt in amounts:
            if abs(amt - BUG_AMT) < BUG_TOL:
                print(f"[RACIONAL-PROG] Saltando bug ${amt:,}", flush=True)
                continue

            print(f"[RACIONAL-PROG] Revisando ${amt:,}...", flush=True)

            try:
                # Asegurar home antes de cada click + esperar que el panel renderice
                if "/tabs/home" not in page.url:
                    page.goto("https://app.racional.cl/tabs/home", timeout=30000)
                    page.wait_for_load_state("load", timeout=20000)
                    page.wait_for_timeout(2000)
                # Re-esperar el panel (puede estar desmontado tras go_back)
                try:
                    page.wait_for_selector("text=Movimientos en progreso", timeout=8000)
                    page.wait_for_timeout(600)
                except Exception:
                    pass

                # Obtener coordenadas del item via JS, luego click con mouse real de Playwright
                # (page.mouse.click genera isTrusted=true, necesario para el router Angular/React)
                coords = page.evaluate("""(targetAmt) => {
                    const h = Array.from(document.querySelectorAll('h1,h2,h3,h4,h5,h6'))
                        .find(el => el.innerText.trim() === 'Movimientos en progreso');
                    if (!h) return {err: 'no-heading'};

                    // Estrategia 1: DOM traversal (hasta 7 niveles)
                    let container = h.parentElement;
                    for (let lvl = 0; lvl < 7; lvl++) {
                        if (!container) break;
                        for (const child of Array.from(container.children)) {
                            if (child.contains(h) || child === h) continue;
                            const text = child.innerText || '';
                            if (text.includes('Movimientos recientes')) break;
                            const nums = text.match(/\\d[\\d.]*/g) || [];
                            for (const n of nums) {
                                if (parseInt(n.replace(/\\./g, ''), 10) === targetAmt) {
                                    const r = child.getBoundingClientRect();
                                    return {x: r.left + r.width/2, y: r.top + r.height/2, method: 'dom'+lvl};
                                }
                            }
                        }
                        container = container.parentElement;
                    }

                    // Estrategia 2: posición visual entre los dos headings
                    const allH = Array.from(document.querySelectorAll('h1,h2,h3,h4,h5,h6'));
                    const hRec = allH.find(el => el.innerText.trim() === 'Movimientos recientes');
                    const topY = h.getBoundingClientRect().bottom;
                    const botY = hRec ? hRec.getBoundingClientRect().top : topY + 600;
                    let best = null, bestArea = Infinity;
                    for (const el of document.querySelectorAll('div,li,a')) {
                        const r = el.getBoundingClientRect();
                        if (r.top < topY - 5 || r.bottom > botY + 30) continue;
                        if (r.width < 80 || r.height < 20) continue;
                        const nums = (el.innerText || '').match(/\\d[\\d.]*/g) || [];
                        let found = false;
                        for (const n of nums) {
                            if (parseInt(n.replace(/\\./g, ''), 10) === targetAmt) { found = true; break; }
                        }
                        if (!found) continue;
                        const area = r.width * r.height;
                        if (area < bestArea) {
                            bestArea = area;
                            best = {x: r.left + r.width/2, y: r.top + r.height/2, method: 'visual'};
                        }
                    }
                    if (best) return best;
                    return {err: 'not-found'};
                }""", amt)

                print(f"[RACIONAL-PROG] Coords: {coords}", flush=True)
                if coords.get('err'):
                    continue

                # Click real (isTrusted) para disparar el router SPA
                page.mouse.click(coords['x'], coords['y'])

                # Esperar navegación SPA: polling cada 400ms hasta 12s
                _nav_ok = False
                for _p in range(30):
                    page.wait_for_timeout(400)
                    if "/movements/" in page.url:
                        _nav_ok = True
                        break
                print(f"[RACIONAL-PROG] URL tras click: {page.url[30:120]}", flush=True)
                if not _nav_ok:
                    print(f"[RACIONAL-PROG] Sin navegación a /movements/, saltando.", flush=True)
                    continue
                page.wait_for_timeout(800)   # pequeño extra para que renderice el contenido

                # ── Verificar condición ────────────────────────────────────────
                current_url  = page.url
                detail_text  = page.inner_text("body")
                condition    = False

                # Método 1 (primario): URL contiene el status exacto
                if "status=toSendToWithdrawalsApi" in current_url:
                    condition = True
                    print(f"[RACIONAL-PROG] ✓ URL status=toSendToWithdrawalsApi", flush=True)

                # Método 2 (fallback): paso3 "Acciones vendidas" con fecha + paso4 "Transferencia realizada" sin fecha
                if not condition:
                    flat = detail_text.replace('\n', ' ')
                    m_step3 = _re.search(r'Acciones\s*vendidas(.{0,30})',      flat, _re.IGNORECASE)
                    m_step4 = _re.search(r'Transferencia\s*realizada(.{0,30})', flat, _re.IGNORECASE)
                    if m_step3 and m_step4:
                        after3 = m_step3.group(1).strip()
                        after4 = m_step4.group(1).strip()
                        step3_done = bool(_re.match(r'\d{2}/\d{2}/\d{2}', after3))
                        step4_done = bool(_re.match(r'\d{2}/\d{2}/\d{2}', after4))
                        condition = step3_done and not step4_done
                        print(f"[RACIONAL-PROG] paso3='{after3}' done={step3_done} | paso4='{after4}' done={step4_done} → cond={condition}", flush=True)
                    else:
                        print(f"[RACIONAL-PROG] No se encontraron pasos 3/4 en texto.", flush=True)

                if condition:
                    extra += amt
                    print(f"[RACIONAL-PROG] ✓ +${amt:,} sumado.", flush=True)
                else:
                    print(f"[RACIONAL-PROG] ${amt:,} ya transferido o condición no cumplida.", flush=True)

                # Volver al home
                try:
                    page.go_back()
                    page.wait_for_timeout(1500)
                except Exception:
                    page.goto("https://app.racional.cl/tabs/home", timeout=20000)
                    page.wait_for_timeout(2000)

            except Exception as e_item:
                print(f"[RACIONAL-PROG] Error en ${amt:,}: {e_item}", flush=True)
                try:
                    page.goto("https://app.racional.cl/tabs/home", timeout=20000)
                    page.wait_for_timeout(2000)
                except Exception:
                    pass

    except Exception as e:
        print(f"[RACIONAL-PROG] Error general: {e}", flush=True)

    if extra:
        print(f"[RACIONAL-PROG] Total extra: ${extra:,}", flush=True)
    return extra


def scrape_racional(context, resultados):
    """Racional — Inversiones Líquidas PN (CLP): CFIETFCD (= portafolio DtdC).
    Login: email + password en app.racional.cl. Extrae valor de la card DtdC en dashboard.
    Persistencia vía racional_session.json."""
    key         = "racional"
    iso_context = None
    page        = None
    try:
        username = bw_get("username", "racional-prod.firebaseapp.com")
        password = bw_get("password", "racional-prod.firebaseapp.com")
        if not username or not password:
            add_result(resultados, key, "Racional", "Inversiones Líquidas", "CFIETFCD", "error", ok=False)
            print_preliminary("Racional", "Inversiones Líquidas", "CFIETFCD", "Sin credenciales", ok=False)
            return False

        # ── Contexto aislado para persistencia ──
        # Viewport ancho: el panel derecho "Movimientos en progreso" solo renderiza en layouts ≥1500px
        ctx_kwargs = {"viewport": {"width": 1600, "height": 900}}
        if _RACIONAL_STATE.exists():
            ctx_kwargs["storage_state"] = str(_RACIONAL_STATE)
            if DEBUG: print(f"[RACIONAL] Cargando sesión guardada: {_RACIONAL_STATE}")

        iso_context = context.browser.new_context(**ctx_kwargs)
        iso_context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
        page = iso_context.new_page()

        # Navegar a home; si redirige a login → hacer login
        page.goto("https://app.racional.cl", timeout=60000)
        page.wait_for_timeout(3000)

        if "login" in page.url:
            if DEBUG: print("[RACIONAL] Sesión inválida, procediendo a login...")
            try:
                page.locator("input[type='email']").wait_for(state="visible", timeout=12000)
                page.locator("input[type='email']").fill(username)
                page.wait_for_timeout(400)
                page.locator("input[type='password']").fill(password)
                page.wait_for_timeout(400)

                # Marcar "Mantener sesión"
                try:
                    cb = page.locator("input[type='checkbox']").first
                    if not cb.is_checked():
                        cb.check(force=True)
                except:
                    try: page.locator("text=Mantener sesi").first.click()
                    except: pass

                page.get_by_role("button", name="Iniciar sesión").click()
                print("[RACIONAL] Login enviado. ESPERANDO MFA... Complétalo en la ventana si es necesario.")
            except Exception as e_login:
                if DEBUG: print(f"[RACIONAL] Auto-login falló: {e_login}")
                print("WARNING:   [RACIONAL] Inicia sesión/MFA manualmente (120s max)...")

            # Esperar a que la URL ya no sea de login ni de mfa
            try:
                page.wait_for_url(lambda url: all(x not in url.lower() for x in ["login", "mfa", "verify"]), timeout=120000)
                page.wait_for_timeout(5000)
                # Guardar sesión
                iso_context.storage_state(path=str(_RACIONAL_STATE))
            except:
                print("WARNING:  [RACIONAL] Tiempo de espera MFA agotado o URL inesperada.")
        
        # Navegar al home para extraer Total Inversiones
        if "/tabs/home" not in page.url:
            page.goto("https://app.racional.cl/tabs/home", timeout=30000)
        page.wait_for_load_state("load", timeout=20000)
        page.wait_for_timeout(3000)
        # Selector actualizado: clase "smaller-total" fue removida del elemento
        page.wait_for_selector(".investment-amount", timeout=30000)
        page.wait_for_timeout(1000)
        raw_val = page.locator(".investment-amount").first.text_content()
        raw_val = (raw_val or "").strip()

        if raw_val:
            raw_val_clean = raw_val.lstrip("$").strip()
            # Parsear saldo base
            base_int = int(float(clean_monto(raw_val_clean, "CLP")))
            # Sumar retiros en progreso (acciones vendidas, transferencia aún pendiente)
            extra = _racional_get_en_progreso_extra(page)
            total_int = base_int + extra
            if extra:
                print(f"[RACIONAL] Saldo base {base_int:,} + en progreso {extra:,} = {total_int:,}", flush=True)
            add_result(resultados, key, "Racional", "Inversiones Líquidas", "CFIETFCD", total_int)
            print_preliminary("Racional", "Inversiones Líquidas", "CFIETFCD", fmt_monto(total_int))
        else:
            if DEBUG: print("[RACIONAL] No se encontró .investment-amount en la página")
            add_result(resultados, key, "Racional", "Inversiones Líquidas", "CFIETFCD", "error", ok=False)
            print_preliminary("Racional", "Inversiones Líquidas", "CFIETFCD", "No obtenido", ok=False)
            return False

        return True

    except Exception as e:
        if DEBUG: print(f"[RACIONAL] Error general: {e}")
        add_result(resultados, key, "Racional", "Inversiones Líquidas", "CFIETFCD", "error", ok=False)
        print_preliminary("Racional", "Inversiones Líquidas", "CFIETFCD", str(e)[:60], ok=False)
        return False
    finally:
        if page:
            try: page.close()
            except: pass
        if iso_context:
            try: iso_context.close()
            except: pass


_WEALTHFRONT_STATE = Path(__file__).parent / "wealthfront_session.json"

def scrape_wealthfront(context, resultados):
    """Wealthfront — Inversiones Líquidas PN (USD): ONEQ (= US stocks en Cuenta Base).
    Login: email + password. Account URL directo post-login."""
    key         = "wealthfront"
    ACCOUNT_URL = "https://www.wealthfront.com/accounts/289918"
    iso_context = None
    page        = None
    try:
        username = bw_get("username", "wealthfront.com")
        password = bw_get("password", "wealthfront.com")
        if not username or not password:
            add_result_usd(resultados, key, "Wealthfront", "Inversiones Líquidas", "ONEQ", "error", ok=False)
            print_preliminary("Wealthfront", "Inversiones Líquidas", "ONEQ", "Sin credenciales", ok=False, moneda="USD")
            return False

        # ── Contexto aislado para persistencia ──
        ctx_kwargs = {"viewport": {"width": 1280, "height": 800}}
        if _WEALTHFRONT_STATE.exists():
            ctx_kwargs["storage_state"] = str(_WEALTHFRONT_STATE)
            if DEBUG: print(f"[WEALTHFRONT] Cargando sesión guardada: {_WEALTHFRONT_STATE}")

        iso_context = context.browser.new_context(**ctx_kwargs)
        iso_context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
        page = iso_context.new_page()

        # Intentar Account URL; si redirige a login → hacer login
        page.goto(ACCOUNT_URL, timeout=60000)
        page.wait_for_timeout(3000)

        if "/login" in page.url or "/sign-in" in page.url:
            if DEBUG: print("[WEALTHFRONT] Sesión inválida, procediendo a login...")
            # Intentar auto-login (email + password estándar)
            try:
                page.locator("input[type='email']").wait_for(state="visible", timeout=12000)
                page.locator("input[type='email']").fill(username)
                page.wait_for_timeout(400)
                page.locator("input[type='password']").fill(password)
                page.wait_for_timeout(400)
                page.locator("button[type='submit']").click()
                print("[WEALTHFRONT] Esperando post-login... Si hay MFA, complétalo en la ventana (120s max)")
            except Exception as e_login:
                if DEBUG: print(f"[WEALTHFRONT] Auto-login falló: {e_login}")
                print("WARNING:   [WEALTHFRONT] Inicia sesión/MFA manualmente (120s max)...")

            # LOG DE INSPECCIÓN: Ver a dónde nos manda el banco
            if DEBUG: print(f"[WEALTHFRONT] URL actual tras login/MFA: {page.url}")
            
            # ESPERA REAL DE LOGIN/MFA
            print("[WEALTHFRONT] Esperando a que llegues al Dashboard (compléta el MFA en la ventana)...")
            try:
                # Esperar a que la URL contenga dashboard o cuentas, que es el éxito real
                page.wait_for_url(lambda url: "/dashboard" in url or "/accounts/" in url, timeout=120000)
                page.wait_for_timeout(5000) # Darle un respiro para que carguen las cookies
                
                # Guardar sesión ahora que estamos ADENTRO de verdad
                print(f"[WEALTHFRONT] ¡Dashboard detectado! Guardando sesión en: {_WEALTHFRONT_STATE.name}...")
                iso_context.storage_state(path=str(_WEALTHFRONT_STATE))
                if _WEALTHFRONT_STATE.exists():
                    print(f"[OK]  [WEALTHFRONT] Sesión guardada con éxito ({_WEALTHFRONT_STATE.stat().st_size} bytes)")
            except Exception as e_wait:
                if DEBUG: print(f"[WEALTHFRONT] No llegamos al dashboard automáticamente: {e_wait}")
                if not AUTOMATED:
                    print("   Si ya ves tu saldo, presiona ENTER aquí para forzar el guardado.")
                    input("   (Esperando ENTER en la terminal...) ")
                    iso_context.storage_state(path=str(_WEALTHFRONT_STATE))
            
            # Navegar a la cuenta específica para extraer el dato
            print(f"[WEALTHFRONT] Navegando a cuenta específica: {ACCOUNT_URL}")
            page.goto(ACCOUNT_URL, timeout=60000)
            page.wait_for_timeout(5000)

        # Extraer valor "US stocks" (= ONEQ) via JS mas robusto
        page.wait_for_selector("body", timeout=10000)
        page.wait_for_timeout(5000)
        
        if DEBUG: print(f"[WEALTHFRONT] Extrayendo datos de {page.url}...")
        
        raw_val = page.evaluate("""() => {
            const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
            let node;
            const terms = ['US stocks', 'U.S. stocks', 'U.S. Stocks', 'Stock Portfolio'];
            while ((node = walker.nextNode())) {
                const txt = node.textContent.trim();
                if (terms.some(t => txt.includes(t))) {
                    let el = node.parentElement;
                    for (let i = 0; i < 12; i++) {
                        if (!el) break;
                        const content = el.innerText || el.textContent || '';
                        const matches = content.match(/\\$[\\d,]+\\.?\\d+/g);
                        const valid = matches && matches.find(m => m !== '$0.00' && m !== '$0' && m.length > 3);
                        if (valid && content.length < 500) return valid;
                        el = el.parentElement;
                    }
                }
            }
            // Último recurso: buscar cualquier valor $ grande en la página que no sea $0
            const bodyText = document.body.innerText;
            const allMatches = bodyText.match(/\\$[\\d,]{4,}\\.\\d{2}/g);
            return (allMatches && allMatches.length > 0) ? allMatches[0] : null;
        }""")

        if raw_val:
            add_result_usd(resultados, key, "Wealthfront", "Inversiones Líquidas", "ONEQ", raw_val)
            print_preliminary("Wealthfront", "Inversiones Líquidas", "ONEQ", raw_val, moneda="USD")
            # Guardar sesión por si acaso
            iso_context.storage_state(path=str(_WEALTHFRONT_STATE))
        else:
            if DEBUG: print("[WEALTHFRONT] No se encontró valor 'US stocks' en la página")
            add_result_usd(resultados, key, "Wealthfront", "Inversiones Líquidas", "ONEQ", "error", ok=False)
            print_preliminary("Wealthfront", "Inversiones Líquidas", "ONEQ", "No obtenido", ok=False, moneda="USD")
            return False

        return True

    except Exception as e:
        if DEBUG: print(f"[WEALTHFRONT] Error general: {e}")
        add_result_usd(resultados, key, "Wealthfront", "Inversiones Líquidas", "ONEQ", "error", ok=False)
        print_preliminary("Wealthfront", "Inversiones Líquidas", "ONEQ", str(e)[:60], ok=False, moneda="USD")
        return False
    finally:
        if page:
            try: page.close()
            except: pass
        if iso_context:
            try: iso_context.close()
            except: pass


def _parse_fraccional_monto(raw_val):
    """Parsea el monto de Fraccional de forma robusta.
    Soporta: '$5.601.536,88', '$ 5.601.536,88', '5601536', variantes con espacios/newlines.
    Retorna int (parte entera) o lanza ValueError si no hay dígitos.
    """
    # Normalizar: quitar espacios/newlines, símbolo $
    s = raw_val.strip().replace("\n", " ").replace("\r", "")
    # Tomar solo la parte antes de la coma (separador decimal chileno)
    s = s.split(",")[0]
    # Dejar solo dígitos
    digits = re.sub(r'[^\d]', '', s)
    if not digits:
        raise ValueError(f"No se encontraron dígitos en '{raw_val}'")
    return int(digits)


def scrape_fraccional(context, resultados):
    """Fraccional - Total patrimonio (Fondos Inmobiliarios, PN, CLP).
    Siempre cierra sesión al inicio (evita sesión de OW) y al final.
    Login: email → Continuar → password (dos pasos).
    Portfolio URL: /app (redirige a /<locale>/app).
    Selector: h2 con 'Total patrimonio'/'Total Equity' → span[class*='mt-0.5'].
    Parse: strip $, split en coma, quitar puntos → int.
    """
    key  = "fraccional"
    page = None

    def _logout(pg):
        """Logout via URL dedicada: /app/auth/logout → click botón rojo 'Cerrar sesión'."""
        try:
            pg.goto("https://www.fraccional.cl/app/auth/logout", wait_until="domcontentloaded", timeout=15000)
            pg.wait_for_timeout(2000)
            # Si ya nos redirigió a /auth, es que ya estamos fuera
            if "/auth" in pg.url and "logout" not in pg.url:
                if DEBUG: print("[FRACCIONAL] Ya deslogueado (redirigido a auth)")
                return
            # Página de confirmación tiene botón rojo "Cerrar sesión"
            btn = pg.locator("button", has_text=re.compile(r"Cerrar sesión", re.IGNORECASE))
            if btn.count() > 0:
                btn.first.click()
                pg.wait_for_timeout(2000)
                if DEBUG: print("[FRACCIONAL] Logout completado via botón")
            else:
                if DEBUG: print("[FRACCIONAL] No se encontró botón de logout (posiblemente ya fuera)")
        except Exception as e_out:
            if DEBUG: print(f"[FRACCIONAL] logout error: {e_out}")

    try:
        username = bw_get("username", "fraccional.cl - Persona Natural")
        password = bw_get("password", "fraccional.cl - Persona Natural")
        if not username or not password:
            add_result(resultados, key, "Fraccional", "Fondos Inmobiliarios", "Fraccional", "error", ok=False)
            print_preliminary("Fraccional", "Fondos Inmobiliarios", "Fraccional", "Sin credenciales", ok=False)
            return

        page = context.new_page()

        # ── Logout siempre al inicio (sesión puede ser OW u otra cuenta) ──
        if DEBUG: print("[FRACCIONAL] Haciendo logout inicial...")
        _logout(page)

        # ── Login: navegar a /app/auth ──
        if DEBUG: print("[FRACCIONAL] Navegando al login...")
        page.goto("https://www.fraccional.cl/app/auth", wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(1500)
        if DEBUG: print(f"[FRACCIONAL] URL login: {page.url}")

        # Paso 1: email
        page.wait_for_selector('input[type="email"]', state="visible", timeout=15000)
        if DEBUG: print("[FRACCIONAL] Campo email visible — llenando...")
        page.locator('input[type="email"]').first.fill(username)
        page.wait_for_timeout(400)
        page.locator('button[type="submit"]').first.click()
        if DEBUG: print("[FRACCIONAL] Click 'Continuar' — esperando paso 2...")

        # Paso 2: password
        try:
            page.wait_for_selector('input[type="password"]', state="visible", timeout=15000)
            if DEBUG: print("[FRACCIONAL] Campo password visible — llenando...")
            page.locator('input[type="password"]').first.fill(password)
            page.wait_for_timeout(400)
            if DEBUG:
                btns2 = page.evaluate("""() =>
                    Array.from(document.querySelectorAll('button')).map(b => b.textContent.trim()).filter(t => t)
                """)
                print(f"[FRACCIONAL] Botones paso 2: {btns2}")
            page.locator('button[type="submit"]').first.click()
            if DEBUG: print("[FRACCIONAL] Click submit — esperando portfolio...")
        except Exception as e_pwd:
            if DEBUG: print(f"[FRACCIONAL] No apareció campo password: {e_pwd}")
            print("WARNING:   Fraccional: completa el login manualmente (60s)...")

        page.wait_for_url(lambda url: "/app" in url and "/auth" not in url, timeout=60000)
        if DEBUG: print(f"[FRACCIONAL] URL portfolio: {page.url}")
        page.wait_for_timeout(2000)
        # Esperar a que el dato de patrimonio esté en el DOM antes de extraer
        try:
            page.wait_for_function("""() => {
                const LABELS = ['Total patrimonio', 'Total Equity', 'Total equity'];
                const h2 = Array.from(document.querySelectorAll('h2')).find(el =>
                    LABELS.some(label => el.textContent.includes(label))
                );
                if (!h2) return false;
                const span = h2.querySelector('span[class*="mt-0.5"]')
                          || h2.querySelector('span[class*="text-4xl"]');
                return span && span.textContent.trim().length > 0;
            }""", timeout=45000)
            if DEBUG: print("[FRACCIONAL] Elemento 'Total patrimonio' cargado ✓")
        except Exception as e_wait:
            if DEBUG: print(f"[FRACCIONAL] Timeout esperando elemento: {e_wait} — intentando igual")

        # ── Extracción — soporta español e inglés ──
        if DEBUG:
            # Volcar h2s para diagnóstico
            h2s = page.evaluate("""() =>
                Array.from(document.querySelectorAll('h2')).map(h => h.textContent.trim().substring(0, 80))
            """)
            print(f"[FRACCIONAL] h2s en página: {h2s}")

        raw_val = page.evaluate("""() => {
            const LABELS = ['Total patrimonio', 'Total Equity', 'Total equity'];
            const h2 = Array.from(document.querySelectorAll('h2')).find(el =>
                LABELS.some(label => el.textContent.includes(label))
            );
            if (!h2) return null;
            const span = h2.querySelector('span[class*="mt-0.5"]')
                      || h2.querySelector('span[class*="text-4xl"]');
            if (span) return span.textContent.trim();
            return null;
        }""")

        if DEBUG: print(f"[FRACCIONAL] raw_val='{raw_val}'")

        if not raw_val:
            add_result(resultados, key, "Fraccional", "Fondos Inmobiliarios", "Fraccional", "error", ok=False)
            print_preliminary("Fraccional", "Fondos Inmobiliarios", "Fraccional", "Monto no encontrado", ok=False)
            return False

        monto = _parse_fraccional_monto(raw_val)
        if DEBUG: print(f"[FRACCIONAL] monto parseado={monto}")

        add_result(resultados, key, "Fraccional", "Fondos Inmobiliarios", "Fraccional", str(monto))
        print_preliminary("Fraccional", "Fondos Inmobiliarios", "Fraccional", fmt_monto(monto))
        return True

    except Exception as e:
        if DEBUG: print(f"[FRACCIONAL] Error general: {e}")
        add_result(resultados, key, "Fraccional", "Fondos Inmobiliarios", "Fraccional", "error", ok=False)
        print_preliminary("Fraccional", "Fondos Inmobiliarios", "Fraccional", str(e)[:60], ok=False)
        return False
    finally:
        # Siempre logout al terminar
        if page:
            try: _logout(page)
            except: pass
            try: page.close()
            except: pass


def scrape_fraccional_pj(context, resultados):
    """Fraccional PJ - Total patrimonio (Fondos Inmobiliarios PJ, PJ, CLP).
    Contexto aislado para evitar interferencia con sesión PN.
    Siempre cierra sesión al inicio y al final.
    Bitwarden: bw list items --search fraccional + filtrar username "owa605" (evita encoding "Jurídica").
    Login: email → Continuar → password (dos pasos).
    Selector: h2 con 'Total patrimonio' → span[class*='mt-0.5'].
    Parse: strip $, split en coma, quitar puntos → int.
    """
    key         = "fraccional_pj"
    page        = None
    iso_context = None

    def _logout(pg):
        """Logout via URL dedicada: /app/auth/logout → click botón rojo 'Cerrar sesión'."""
        try:
            pg.goto("https://www.fraccional.cl/app/auth/logout", wait_until="domcontentloaded", timeout=15000)
            pg.wait_for_timeout(1500)
            btn = pg.locator("button", has_text=re.compile(r"Cerrar sesión", re.IGNORECASE))
            if btn.count() > 0:
                btn.first.click()
                pg.wait_for_timeout(1500)
                if DEBUG: print("[FRACCIONAL PJ] Logout completado")
            else:
                if DEBUG: print("[FRACCIONAL PJ] Ya deslogueado (no apareció confirmación)")
        except Exception as e_out:
            if DEBUG: print(f"[FRACCIONAL PJ] logout error: {e_out}")

    try:
        # Bitwarden: buscar por lista+filtro para evitar problemas de encoding con "Jurídica"
        # Filtramos por username = owa605 entre todos los items de fraccional
        import json as _json
        _bw_env = bw_env()
        _list = subprocess.run(["bw", "list", "items", "--search", "fraccional"],
                               capture_output=True, text=True, env=_bw_env)
        if "Session key is invalid" in _list.stderr or not _list.stdout.strip():
            bw_unlock()
            _list = subprocess.run(["bw", "list", "items", "--search", "fraccional"],
                                   capture_output=True, text=True, env=_bw_env)
        _items = _json.loads(_list.stdout) if _list.stdout.strip() else []
        # Filtrar el item PJ: username contiene "owa605" (email PJ)
        _pj = next((i for i in _items if "owa605" in str(i.get("login", {}).get("username", ""))), None)
        username = _pj["login"]["username"] if _pj else ""
        password = _pj["login"]["password"] if _pj else ""
        if DEBUG: print(f"[FRACCIONAL PJ] username='{username}'")

        if not username or not password:
            add_result(resultados, key, "Fraccional", "Fondos Inmobiliarios PJ", "Fraccional", "error", ok=False)
            print_preliminary("Fraccional", "Fondos Inmobiliarios PJ", "Fraccional", "Sin credenciales", ok=False)
            return False

        # Contexto aislado — evita interferencia de cookies de Fraccional PN
        iso_context = context.browser.new_context()
        iso_context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
        page = iso_context.new_page()

        # ── Logout siempre al inicio ──
        if DEBUG: print("[FRACCIONAL PJ] Haciendo logout inicial...")
        _logout(page)

        # ── Login: navegar a /app/auth ──
        if DEBUG: print("[FRACCIONAL PJ] Navegando al login...")
        page.goto("https://www.fraccional.cl/app/auth", wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(1500)
        if DEBUG: print(f"[FRACCIONAL PJ] URL login: {page.url}")

        # Paso 1: email
        page.wait_for_selector('input[type="email"]', state="visible", timeout=15000)
        if DEBUG: print("[FRACCIONAL PJ] Campo email visible — llenando...")
        page.locator('input[type="email"]').first.fill(username)
        page.wait_for_timeout(400)
        page.locator('button[type="submit"]').first.click()
        if DEBUG: print("[FRACCIONAL PJ] Click 'Continuar' — esperando paso 2...")

        # Paso 2: password
        try:
            page.wait_for_selector('input[type="password"]', state="visible", timeout=15000)
            if DEBUG: print("[FRACCIONAL PJ] Campo password visible — llenando...")
            page.locator('input[type="password"]').first.fill(password)
            page.wait_for_timeout(400)
            if DEBUG:
                btns2 = page.evaluate("""() =>
                    Array.from(document.querySelectorAll('button')).map(b => b.textContent.trim()).filter(t => t)
                """)
                print(f"[FRACCIONAL PJ] Botones paso 2: {btns2}")
            page.locator('button[type="submit"]').first.click()
            if DEBUG: print("[FRACCIONAL PJ] Click submit — esperando portfolio...")
        except Exception as e_pwd:
            if DEBUG: print(f"[FRACCIONAL PJ] No apareció campo password: {e_pwd}")
            print("WARNING:   Fraccional PJ: completa el login manualmente (60s)...")

        page.wait_for_url(lambda url: "/app" in url and "/auth" not in url, timeout=60000)
        if DEBUG: print(f"[FRACCIONAL PJ] URL portfolio: {page.url}")
        page.wait_for_timeout(2000)
        # Esperar a que el dato de patrimonio esté en el DOM antes de extraer
        try:
            page.wait_for_function("""() => {
                const LABELS = ['Total patrimonio', 'Total Equity', 'Total equity'];
                const h2 = Array.from(document.querySelectorAll('h2')).find(el =>
                    LABELS.some(label => el.textContent.includes(label))
                );
                if (!h2) return false;
                const span = h2.querySelector('span[class*="mt-0.5"]')
                          || h2.querySelector('span[class*="text-4xl"]');
                return span && span.textContent.trim().length > 0;
            }""", timeout=45000)
            if DEBUG: print("[FRACCIONAL PJ] Elemento 'Total patrimonio' cargado ✓")
        except Exception as e_wait:
            if DEBUG: print(f"[FRACCIONAL PJ] Timeout esperando elemento: {e_wait} — intentando igual")

        # ── Extracción ──
        if DEBUG:
            h2s = page.evaluate("""() =>
                Array.from(document.querySelectorAll('h2')).map(h => h.textContent.trim().substring(0, 80))
            """)
            print(f"[FRACCIONAL PJ] h2s en página: {h2s}")

        raw_val = page.evaluate("""() => {
            const LABELS = ['Total patrimonio', 'Total Equity', 'Total equity'];
            const h2 = Array.from(document.querySelectorAll('h2')).find(el =>
                LABELS.some(label => el.textContent.includes(label))
            );
            if (!h2) return null;
            const span = h2.querySelector('span[class*="mt-0.5"]')
                      || h2.querySelector('span[class*="text-4xl"]');
            if (span) return span.textContent.trim();
            return null;
        }""")

        if DEBUG: print(f"[FRACCIONAL PJ] raw_val='{raw_val}'")

        if not raw_val:
            add_result(resultados, key, "Fraccional", "Fondos Inmobiliarios PJ", "Fraccional", "error", ok=False)
            print_preliminary("Fraccional", "Fondos Inmobiliarios PJ", "Fraccional", "Monto no encontrado", ok=False)
            return False

        monto = _parse_fraccional_monto(raw_val)
        if DEBUG: print(f"[FRACCIONAL PJ] monto parseado={monto}")

        add_result(resultados, key, "Fraccional", "Fondos Inmobiliarios PJ", "Fraccional", str(monto))
        print_preliminary("Fraccional", "Fondos Inmobiliarios PJ", "Fraccional", fmt_monto(monto))

        _logout(page)
        page.close()
        page = None
        iso_context.close()
        iso_context = None
        return True

    except Exception as e:
        if DEBUG: print(f"[FRACCIONAL PJ] Error general: {e}")
        add_result(resultados, key, "Fraccional", "Fondos Inmobiliarios PJ", "Fraccional", "error", ok=False)
        print_preliminary("Fraccional", "Fondos Inmobiliarios PJ", "Fraccional", str(e)[:60], ok=False)
        return False
    finally:
        if page:
            try: page.close()
            except: pass
        if iso_context:
            try: iso_context.close()
            except: pass


_FINTUAL_STATE = Path(__file__).parent / "fintual_session.json"

def scrape_fintual(context, resultados):
    """Fintual PN — Inversiones Líquidas (CLP + USD) y Previsional (APV CLP).
    Login URL: https://fintual.cl/f/sign-in/ (1 paso: input[name=email] + input[name=password])
    Sesión persistente via fintual_session.json (storage state) → MFA solo al expirar.
    CLP via .goal-item.goal-item--no-shadow:
      - Risky Norris = sum(Depositado + Ganancias) donde detail='Largo plazo'
      - Risky Norris APV = sum(APV + APV Regimen A)
    USD via .asset-row: VOO, IVV, BRK.B
    Parse CLP: '$ 295.001.270' → strip '$ ' → remove '.' → int
    Parse USD: 'US $13.493,36' → strip 'US $' → remove '.' → replace ',' → float
    Bitwarden: 'fintual.cl - Persona Natural' (matigd@gmail.com)
    """
    key         = "fintual"
    iso_context = None
    page        = None

    def parse_clp(s):
        return int(s.replace("$", "").replace(".", "").strip())

    def parse_usd(s):
        clean = s.replace("US", "").replace("$", "").strip()
        return float(clean.replace(".", "").replace(",", "."))

    try:
        username = bw_get("username", "fintual.cl - Persona Natural")
        password = bw_get("password", "fintual.cl - Persona Natural")
        if not username or not password:
            for item, cat in [("Risky Norris", "Inversiones Líquidas"), ("Risky Norris APV", "Previsional"),
                               ("VOO", "Inversiones Líquidas"), ("IVV", "Inversiones Líquidas"), ("BRK.B", "Inversiones Líquidas")]:
                add_result(resultados, key, "Fintual", cat, item, "error", ok=False)
                moneda = "USD" if item in ("VOO", "IVV", "BRK.B") else "CLP"
                print_preliminary("Fintual", cat, item, "Sin credenciales", ok=False, moneda=moneda)
            return False

        # ── Contexto aislado con sesión guardada si existe ─────────────────
        ctx_kwargs = {}
        if _FINTUAL_STATE.exists():
            ctx_kwargs["storage_state"] = str(_FINTUAL_STATE)
            if DEBUG: print(f"[FINTUAL] Cargando sesión guardada: {_FINTUAL_STATE}")
        iso_context = context.browser.new_context(**ctx_kwargs)
        iso_context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
        page = iso_context.new_page()

        # Intentar ir directo a goals; si redirige a sign-in → hacer login
        page.goto("https://fintual.cl/app/goals", wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(2000)

        # ── Login si redirigió a sign-in ───────────────────────────────────
        if "/f/sign-in" in page.url:
            if DEBUG: print(f"[FINTUAL] Sesión inválida — en login: {page.url}")
            try:
                page.wait_for_selector('input[name="email"]', state="visible", timeout=15000)
                if DEBUG: print("[FINTUAL] Llenando credenciales...")
                page.locator('input[name="email"]').fill(username)
                page.wait_for_timeout(300)
                page.locator('input[name="password"]').fill(password)
                page.wait_for_timeout(300)
                page.locator('button[type="submit"]').first.click()
            except Exception as e_login:
                if DEBUG: print(f"[FINTUAL] Error en login: {e_login}")
                print("WARNING:   Fintual: completa el login/MFA manualmente (120s)...")

            page.wait_for_url(lambda url: "/app/" in url, timeout=120000)
            page.wait_for_timeout(1000)
            if "/app/goals" not in page.url:
                page.goto("https://fintual.cl/app/goals", wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(2000)

        if DEBUG: print(f"[FINTUAL] URL activa: {page.url}")

        # ── Esperar goal-items ─────────────────────────────────────────────
        page.wait_for_selector(".goal-item--no-shadow", state="visible", timeout=20000)
        page.wait_for_timeout(2000)

        # ── Extraer CLP ───────────────────────────────────────────────────
        clp_items = page.evaluate("""() => {
            return Array.from(document.querySelectorAll('.goal-item.goal-item--no-shadow')).map(el => ({
                name:    (el.querySelector('.goal-item__info-name')?.textContent || '').trim(),
                detail:  (el.querySelector('.goal-item__info-detail')?.textContent || '').trim(),
                balance: (el.querySelector('.goal-item__balance')?.textContent || '').trim(),
            })).filter(it => it.balance);
        }""")
        if DEBUG: print(f"[FINTUAL] CLP items: {clp_items}")

        largo_plazo  = [it for it in clp_items if it["detail"] == "Largo plazo"]
        risky_norris = sum(parse_clp(it["balance"]) for it in largo_plazo) if largo_plazo else 0
        if DEBUG: print(f"[FINTUAL] Risky Norris: {risky_norris}")
        if risky_norris:
            add_result(resultados, key, "Fintual", "Inversiones Líquidas", "Risky Norris", str(risky_norris))
            print_preliminary("Fintual", "Inversiones Líquidas", "Risky Norris", fmt_monto(risky_norris))
        else:
            add_result(resultados, key, "Fintual", "Inversiones Líquidas", "Risky Norris", "error", ok=False)
            print_preliminary("Fintual", "Inversiones Líquidas", "Risky Norris", "No encontrado", ok=False)

        apv_items = [it for it in clp_items if "APV" in it["name"]]
        apv_total  = sum(parse_clp(it["balance"]) for it in apv_items) if apv_items else 0
        if DEBUG: print(f"[FINTUAL] APV: {apv_total}")
        if apv_total:
            add_result(resultados, key, "Fintual", "Previsional", "Risky Norris APV", str(apv_total))
            print_preliminary("Fintual", "Previsional", "Risky Norris APV", fmt_monto(apv_total))
        else:
            add_result(resultados, key, "Fintual", "Previsional", "Risky Norris APV", "error", ok=False)
            print_preliminary("Fintual", "Previsional", "Risky Norris APV", "No encontrado", ok=False)

        # ── Extraer USD ───────────────────────────────────────────────────
        usd_items = page.evaluate("""() => {
            return Array.from(document.querySelectorAll('.asset-row')).map(el => ({
                symbol:  (el.querySelector('.asset-row__info-symbol')?.textContent || '').trim(),
                balance: (el.querySelector('.asset-row__balance')?.textContent || '').trim(),
            })).filter(it => it.symbol && it.balance);
        }""")
        if DEBUG: print(f"[FINTUAL] USD items: {usd_items}")

        usd_map = {it["symbol"]: it["balance"] for it in usd_items}
        for sym in ["VOO", "IVV", "BRK.B"]:
            bal = usd_map.get(sym, "")
            if bal:
                val = parse_usd(bal)
                add_result_usd(resultados, key, "Fintual", "Inversiones Líquidas", sym, str(val))
                print_preliminary("Fintual", "Inversiones Líquidas", sym, str(round(val)), moneda="USD")
            else:
                add_result_usd(resultados, key, "Fintual", "Inversiones Líquidas", sym, "0", ok=False)
                print_preliminary("Fintual", "Inversiones Líquidas", sym, "No encontrado", ok=False, moneda="USD")

        # ── Guardar sesión para próxima ejecución ─────────────────────────
        iso_context.storage_state(path=str(_FINTUAL_STATE))
        if DEBUG: print(f"[FINTUAL] Sesión guardada en: {_FINTUAL_STATE}")

        page.close();        page        = None
        iso_context.close(); iso_context = None
        return True

    except Exception as e:
        if DEBUG: print(f"[FINTUAL] Error general: {e}")
        for item, cat in [("Risky Norris", "Inversiones Líquidas"), ("Risky Norris APV", "Previsional"),
                           ("VOO", "Inversiones Líquidas"), ("IVV", "Inversiones Líquidas"), ("BRK.B", "Inversiones Líquidas")]:
            add_result(resultados, key, "Fintual", cat, item, "error", ok=False)
            moneda = "USD" if item in ("VOO", "IVV", "BRK.B") else "CLP"
            print_preliminary("Fintual", cat, item, str(e)[:60], ok=False, moneda=moneda)
        return False
    finally:
        if page:
            try: page.close()
            except: pass
        if iso_context:
            try: iso_context.close()
            except: pass


_FINTUAL_PJ_STATE = Path(__file__).parent / "fintual_pj_session.json"

def scrape_fintual_pj(context, resultados):
    """Fintual PJ (One Western) — Inversiones Líquidas PJ, CLP.
    Fondos en sitio:
      - 'Patrimonial' (Largo plazo)  → registrado como 'Risky Norris'
      - 'Cash Owa'    (Corto plazo)  → registrado como 'Cash Owa'
    Bitwarden: bw list --search fintual + filtrar 'owa605' en username (NO bw get '...Jurídica' — falla por acento).
    Sesión persistente via fintual_pj_session.json.
    Login: input[name='email'] (type=text) + input[name='password'] + button[type='submit'].first
    """
    key         = "fintual_pj"
    iso_context = None
    page        = None

    def parse_clp(s):
        return int(s.replace("$", "").replace(".", "").strip())

    try:
        # ── Bitwarden PJ: bw list + filtrar por "owa605" ───────────────────
        import json as _json
        env = bw_env()
        _raw = subprocess.run(["bw", "list", "items", "--search", "fintual"],
                              capture_output=True, text=True, env=env)
        _items = _json.loads(_raw.stdout) if _raw.stdout.strip() else []
        _pj = next((i for i in _items if "owa605" in str(i.get("login", {}).get("username", ""))), None)
        if not _pj:
            add_result(resultados, key, "Fintual", "Inversiones Líquidas PJ", "Risky Norris", "error", ok=False)
            print_preliminary("Fintual", "Inversiones Líquidas PJ", "Risky Norris", "Sin credenciales PJ", ok=False)
            return False
        username = _pj["login"]["username"]
        password = _pj["login"]["password"]

        # ── Contexto aislado con sesión guardada si existe ─────────────────
        ctx_kwargs = {}
        if _FINTUAL_PJ_STATE.exists():
            ctx_kwargs["storage_state"] = str(_FINTUAL_PJ_STATE)
            if DEBUG: print(f"[FINTUAL_PJ] Cargando sesión guardada: {_FINTUAL_PJ_STATE}")
        iso_context = context.browser.new_context(**ctx_kwargs)
        iso_context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
        page = iso_context.new_page()

        # Intentar ir directo a goals; si redirige a sign-in → hacer login
        page.goto("https://fintual.cl/app/goals", wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(2000)

        # ── Login si redirigió a sign-in ───────────────────────────────────
        if "/f/sign-in" in page.url:
            if DEBUG: print(f"[FINTUAL_PJ] Sesión inválida — en login: {page.url}")
            try:
                page.wait_for_selector('input[name="email"]', state="visible", timeout=15000)
                if DEBUG: print("[FINTUAL_PJ] Llenando credenciales PJ...")
                page.locator('input[name="email"]').fill(username)
                page.wait_for_timeout(300)
                page.locator('input[name="password"]').fill(password)
                page.wait_for_timeout(300)
                page.locator('button[type="submit"]').first.click()
            except Exception as e_login:
                if DEBUG: print(f"[FINTUAL_PJ] Error en login: {e_login}")
                print("WARNING:   Fintual PJ: completa el login/MFA manualmente (120s)...")

            page.wait_for_url(lambda url: "/app/" in url, timeout=120000)
            page.wait_for_timeout(1000)
            if "/app/goals" not in page.url:
                page.goto("https://fintual.cl/app/goals", wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(2000)

        if DEBUG: print(f"[FINTUAL_PJ] URL activa: {page.url}")

        # ── Esperar goal-items ─────────────────────────────────────────────
        page.wait_for_selector(".goal-item--no-shadow", state="visible", timeout=20000)
        page.wait_for_timeout(2000)

        # ── Extraer fondo Patrimonial ──────────────────────────────────────
        clp_items = page.evaluate("""() => {
            return Array.from(document.querySelectorAll('.goal-item.goal-item--no-shadow')).map(el => ({
                name:    (el.querySelector('.goal-item__info-name')?.textContent || '').trim(),
                detail:  (el.querySelector('.goal-item__info-detail')?.textContent || '').trim(),
                balance: (el.querySelector('.goal-item__balance')?.textContent || '').trim(),
            })).filter(it => it.balance);
        }""")
        if DEBUG: print(f"[FINTUAL_PJ] items: {clp_items}")

        # ── Fondo Patrimonial (Largo plazo) → "Risky Norris" ──────────────
        patrimonial = [it for it in clp_items if "Patrimonial" in it["name"]]
        total_patrimonial = sum(parse_clp(it["balance"]) for it in patrimonial) if patrimonial else 0
        if DEBUG: print(f"[FINTUAL_PJ] Patrimonial total: {total_patrimonial}")

        if total_patrimonial:
            add_result(resultados, key, "Fintual", "Inversiones Líquidas PJ", "Risky Norris", str(total_patrimonial))
            print_preliminary("Fintual", "Inversiones Líquidas PJ", "Risky Norris", fmt_monto(total_patrimonial))
        else:
            add_result(resultados, key, "Fintual", "Inversiones Líquidas PJ", "Risky Norris", "error", ok=False)
            print_preliminary("Fintual", "Inversiones Líquidas PJ", "Risky Norris", "No encontrado", ok=False)

        # ── Fondo Cash Owa (Corto plazo) → "Cash Owa" ─────────────────────
        cash_owa = [it for it in clp_items if "Cash Owa" in it["name"]]
        total_cash = sum(parse_clp(it["balance"]) for it in cash_owa) if cash_owa else 0
        if DEBUG: print(f"[FINTUAL_PJ] Cash Owa total: {total_cash}")

        if total_cash:
            add_result(resultados, key, "Fintual", "Inversiones Líquidas PJ", "Cash Owa", str(total_cash))
            print_preliminary("Fintual", "Inversiones Líquidas PJ", "Cash Owa", fmt_monto(total_cash))
        else:
            add_result(resultados, key, "Fintual", "Inversiones Líquidas PJ", "Cash Owa", "error", ok=False)
            print_preliminary("Fintual", "Inversiones Líquidas PJ", "Cash Owa", "No encontrado", ok=False)

        # ── Retiro Pendiente (puede no existir) ───────────────────────────
        retiro_pendiente = page.evaluate("""() => {
            const nameEl = Array.from(document.querySelectorAll('.goal-item__info-name'))
                .find(el => /retiro pendiente/i.test(el.textContent));
            if (!nameEl) return null;
            const detail = nameEl.parentElement.querySelector('.goal-item__info-detail');
            if (!detail) return null;
            const match = detail.textContent.trim().match(/\$\s*([\d.]+)/);
            return match ? match[1] : null;
        }""")
        if retiro_pendiente:
            monto_retiro = int(retiro_pendiente.replace(".", "").replace(",", ""))
            add_result(resultados, key, "Fintual", "Inversiones Líquidas PJ", "Retiro Pendiente", str(monto_retiro))
            print_preliminary("Fintual", "Inversiones Líquidas PJ", "Retiro Pendiente", fmt_monto(monto_retiro))
            if DEBUG: print(f"[FINTUAL_PJ] Retiro Pendiente: {monto_retiro}")
        else:
            add_result(resultados, key, "Fintual", "Inversiones Líquidas PJ", "Retiro Pendiente", "0")
            print_preliminary("Fintual", "Inversiones Líquidas PJ", "Retiro Pendiente", "0")
            if DEBUG: print("[FINTUAL_PJ] Sin retiro pendiente — guardando 0")

        # ── Guardar sesión ─────────────────────────────────────────────────
        iso_context.storage_state(path=str(_FINTUAL_PJ_STATE))
        if DEBUG: print(f"[FINTUAL_PJ] Sesión guardada en: {_FINTUAL_PJ_STATE}")

        page.close();        page        = None
        iso_context.close(); iso_context = None
        return True if (total_patrimonial or total_cash) else False

    except Exception as e:
        if DEBUG: print(f"[FINTUAL_PJ] Error general: {e}")
        add_result(resultados, key, "Fintual", "Inversiones Líquidas PJ", "Risky Norris", "error", ok=False)
        print_preliminary("Fintual", "Inversiones Líquidas PJ", "Risky Norris", str(e)[:60], ok=False)
        return False
    finally:
        if page:
            try: page.close()
            except: pass
        if iso_context:
            try: iso_context.close()
            except: pass


# ══════════════════════════════════════════════════════════════
# NEAT
# ══════════════════════════════════════════════════════════════

_NEAT_STATE = Path(__file__).parent / "neat_session.json"

# Combos (Tipo de cuenta, Detalle del pago) que se suman cuando Estado == "En progreso"
_NEAT_VALID_COMBOS = {
    ("Servicios Profesionales", "Accountant"),
    ("Club Deportivo",          "Nrjo Club"),
}

def _g66_select_email_channel(page):
    """Intenta hacer clic en la opción 'correo electrónico' del selector de canal MFA de G66.
    Si no encuentra el selector (canal ya configurado), continúa sin error.
    """
    try:
        # Esperar brevemente a que cargue la pantalla de selección
        page.wait_for_timeout(1500)
        # Selectores posibles para el botón/opción de correo
        email_selectors = [
            'button:has-text("correo")',
            'button:has-text("Correo")',
            'button:has-text("email")',
            'button:has-text("Email")',
            'li:has-text("correo")',
            'li:has-text("Correo")',
            '[class*="option"]:has-text("correo")',
            '[class*="option"]:has-text("Correo")',
            'label:has-text("correo")',
            'label:has-text("Correo")',
            'span:has-text("Correo electrónico")',
        ]
        clicked = False
        for sel in email_selectors:
            loc = page.locator(sel)
            if loc.count() > 0:
                loc.first.click()
                print(f"[G66] Canal correo seleccionado ({sel})", flush=True)
                page.wait_for_timeout(800)
                clicked = True
                break

        if clicked:
            # Confirmar/enviar si hay botón de "Enviar"/"Continuar"/"Siguiente"
            for btn_sel in [
                'button:has-text("Enviar")',
                'button:has-text("Continuar")',
                'button:has-text("Siguiente")',
                'button:has-text("Aceptar")',
            ]:
                btn = page.locator(btn_sel)
                if btn.count() > 0 and btn.first.is_enabled():
                    btn.first.click()
                    print(f"[G66] Confirmación de canal enviada ({btn_sel})", flush=True)
                    page.wait_for_timeout(1500)
                    break
        else:
            print("[G66] No se encontró selector de canal MFA — asumiendo canal ya configurado", flush=True)

    except Exception as e:
        print(f"[G66] Advertencia al seleccionar canal: {e}", flush=True)


def _get_g66_otp_from_gmail(after_dt, timeout_s=90):
    """Espera y retorna el OTP de Global66 leyendo Gmail via IMAP.

    after_dt  : datetime.datetime — ignora correos anteriores a este momento.
    timeout_s : segundos máximos de espera.
    Retorna   : str con el código de 6 dígitos.
    """
    import time

    IMAP_HOST  = "imap.gmail.com"
    IMAP_PORT  = 993
    GMAIL_USER = "owa605.g66@gmail.com"
    GMAIL_PWD  = "zkyh ubsz uwre geov".replace(" ", "")

    import time, calendar

    # Timestamp UTC de referencia (usando mktime = local→UTC correcto)
    after_ts = time.mktime(after_dt.timetuple())

    print("[G66-IMAP] Esperando 5s para que el email llegue...", flush=True)
    time.sleep(5)

    print("[G66-IMAP] Buscando OTP en Gmail...", flush=True)
    deadline = time.time() + timeout_s

    while time.time() < deadline:
        try:
            with imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT) as imap:
                imap.login(GMAIL_USER, GMAIL_PWD)
                imap.select('"[Gmail]/All Mail"')

                # Buscar emails de global66 de hoy
                date_str = after_dt.strftime("%d-%b-%Y")
                _, data  = imap.search(None, f'(FROM "global66" SINCE "{date_str}")')
                msg_ids  = data[0].split()
                print(f"[G66-IMAP] Emails G66 hoy: {len(msg_ids)}", flush=True)

                # Revisar del más reciente al más antiguo (IDs crecientes en Gmail)
                best_code = None
                best_ts   = 0
                for mid in reversed(msg_ids):
                    _, raw_data = imap.fetch(mid, "(RFC822)")
                    raw = raw_data[0][1]
                    msg = _email_lib.message_from_bytes(raw)

                    # Timestamp del email
                    try:
                        date_tuple = _email_utils.parsedate_tz(msg.get("Date", ""))
                        msg_ts = _email_utils.mktime_tz(date_tuple) if date_tuple else 0
                    except Exception:
                        msg_ts = 0

                    # Solo emails que llegaron DESPUÉS del inicio del scraper
                    if msg_ts < after_ts - 30:
                        print(f"[G66-IMAP] Email muy antiguo, saltando (ts={msg_ts:.0f} < {after_ts:.0f})", flush=True)
                        continue

                    print(f"[G66-IMAP] Revisando: {msg.get('From')} | {msg.get('Subject')}", flush=True)

                    # Extraer body y strip HTML (evita falsos positivos con colores CSS)
                    from html.parser import HTMLParser
                    class _StripHTML(HTMLParser):
                        def __init__(self): super().__init__(); self._p = []
                        def handle_data(self, d): self._p.append(d)
                        def text(self): return ' '.join(self._p)

                    raw_body = ""
                    if msg.is_multipart():
                        for part in msg.walk():
                            ct = part.get_content_type()
                            payload = part.get_payload(decode=True)
                            if payload and ct in ("text/plain", "text/html"):
                                raw_body += payload.decode("utf-8", errors="ignore")
                    else:
                        payload = msg.get_payload(decode=True)
                        if payload:
                            raw_body = payload.decode("utf-8", errors="ignore")

                    # Strip HTML → texto plano → regex (evita colores CSS como #203478)
                    try:
                        stripper = _StripHTML()
                        stripper.feed(raw_body)
                        plain_body = stripper.text()
                    except Exception:
                        plain_body = re.sub(r'<[^>]+>', ' ', raw_body)

                    match = re.search(r'\b(\d{6})\b', plain_body)
                    if match and msg_ts > best_ts:
                        best_code = match.group(1)
                        best_ts   = msg_ts
                        print(f"[G66-IMAP] Código candidato: {best_code}", flush=True)

                if best_code:
                    print(f"[G66-IMAP] ✅ OTP: {best_code}", flush=True)
                    return best_code

        except Exception as e:
            print(f"[G66-IMAP] Error IMAP: {e}", flush=True)

        remaining = max(0, int(deadline - time.time()))
        print(f"[G66-IMAP] Reintentando... ({remaining}s)", flush=True)
        time.sleep(3)

    raise Exception(f"Timeout ({timeout_s}s) esperando OTP de Global66 en Gmail")


def scrape_global66_pj(context, resultados):
    """Scraper Global66 Empresas (One Western SpA) — CLP y USD.

    URL       : https://empresas.global66.com/auth/log-in
    MFA       : 6 dígitos automático — se solicita por correo y se lee desde Gmail via IMAP
    Bitwarden : empresas.global66.com  (user: owa605.g66@gmail.com)
    Gmail     : owa605@gmail.com  (app password en código)
    Cuentas   : CLP 5441 (No. 10155441) + USD 6038 (No. 8338136038)
    """
    key         = "global66_pj"
    iso_context = None
    page        = None

    try:
        print("[G66] Obteniendo credenciales de Bitwarden...", flush=True)
        username = bw_get("username", "empresas.global66.com")
        password = bw_get("password", "empresas.global66.com")
        print(f"[G66] Credenciales OK: {username}", flush=True)

        # Contexto aislado — sin cookies de otras sesiones
        iso_context = context.browser.new_context(viewport={"width": 1280, "height": 800})
        iso_context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
        page = iso_context.new_page()

        # ── LOGIN ─────────────────────────────────────────────────────────
        print("[G66] Navegando a login...", flush=True)
        page.goto("https://empresas.global66.com/auth/log-in", timeout=30000)
        page.wait_for_timeout(1500)

        # Email — input[type="email"]
        email_loc = page.locator('input[type="email"]')
        email_loc.wait_for(state="visible", timeout=10000)
        email_loc.click()
        page.wait_for_timeout(300)
        email_loc.press_sequentially(username, delay=60)
        page.wait_for_timeout(300)

        # Password — input[type="password"]
        pwd_loc = page.locator('input[type="password"]')
        pwd_loc.click()
        page.wait_for_timeout(300)
        pwd_loc.press_sequentially(password, delay=60)
        page.wait_for_timeout(500)

        # Submit
        submit_btn = page.locator('button:has-text("Iniciar sesión")')
        submit_btn.wait_for(state="visible", timeout=5000)
        submit_btn.click()
        print("[G66] Login enviado. Esperando pantalla MFA...", flush=True)

        # ── MFA AUTOMÁTICO VÍA CORREO + GMAIL IMAP ────────────────────────
        # Marcar tiempo antes de solicitar el código (para ignorar OTPs viejos)
        otp_request_time = datetime.datetime.now()

        # Intentar seleccionar "correo electrónico" como canal de envío
        _g66_select_email_channel(page)

        # Esperar campos OTP (6 inputs tel)
        page.wait_for_selector('input[type="tel"].gui-input', timeout=20000)
        page.wait_for_timeout(500)

        # Obtener OTP desde Gmail automáticamente
        otp_code = _get_g66_otp_from_gmail(after_dt=otp_request_time, timeout_s=90)

        # Llenar los 6 campos del OTP
        otp_inputs = page.locator('input[type="tel"].gui-input')
        for i, digit in enumerate(otp_code[:6]):
            otp_inputs.nth(i).click()
            page.wait_for_timeout(80)
            page.keyboard.type(digit)
            page.wait_for_timeout(80)
        print("[G66] OTP ingresado automáticamente. Esperando dashboard...", flush=True)
        page.wait_for_url("**/home**", timeout=30000)

        # Esperar a que los saldos reales carguen (la app hace fetch async post-render)
        # Polling hasta que al menos un p.text-3xl tenga contenido != "$ 0" y != ""
        print("[G66] Esperando carga de saldos...", flush=True)
        for _attempt in range(40):          # hasta 20s (40 × 500ms)
            balances_raw = page.evaluate("""() => {
                const cards = Array.from(document.querySelectorAll('p.text-3xl'));
                return cards.map(el => el.textContent.trim());
            }""")
            ZERO_VALS = {"$ 0", "$0", "0", "$ 0.00", "$0.00", "0.00", "$ 0,00"}
            non_zero = [b for b in balances_raw if b and b not in ZERO_VALS]
            # Esperar a que AMBAS cuentas (CLP + USD) tengan valor cargado
            if len(non_zero) >= 2:
                break
            page.wait_for_timeout(500)
        else:
            loaded = len([b for b in balances_raw if b and b not in ("$ 0", "$0", "0")])
            ZERO_VALS = {"$ 0", "$0", "0", "$ 0.00", "$0.00", "0.00", "$ 0,00"}
            loaded = len([b for b in balances_raw if b and b not in ZERO_VALS])
            print(f"[G66] WARNING:  Solo {loaded}/2 saldos cargados después del timeout — usando lo que hay", flush=True)

        # ── EXTRACCIÓN DE SALDOS ──────────────────────────────────────────
        # Detección de moneda: priorizar número de cuenta (5441/6038) sobre texto "USD"
        # porque el contexto puede mencionar "USD" en botones de conversión.
        balances = page.evaluate("""() => {
            const cards = Array.from(document.querySelectorAll('p.text-3xl'));
            return cards.map(el => {
                // Subir hasta 7 niveles buscando un contenedor con número de cuenta o moneda
                let moneda = null;
                let node = el;
                for (let i = 0; i < 7; i++) {
                    node = node.parentElement;
                    if (!node) break;
                    const txt = node.innerText || '';
                    // Número de cuenta es el indicador más fiable
                    if (txt.includes('5441')) { moneda = 'CLP'; break; }
                    if (txt.includes('6038')) { moneda = 'USD'; break; }
                    // Nombre de moneda también es fiable
                    if (/Peso(s)?/i.test(txt)) { moneda = 'CLP'; break; }
                    if (/D[oó]lar/i.test(txt)) { moneda = 'USD'; break; }
                }
                // Fallback: primer card sin moneda asignada = CLP (orden fijo en G66)
                return { balance: el.textContent.trim(), moneda: moneda };
            });
        }""")

        print(f"[G66] Saldos encontrados: {balances}", flush=True)

        # Fallback de orden: si la detección por contexto falló, usar posición (0=CLP, 1=USD)
        ORDER_FALLBACK = ["CLP", "USD"]
        for idx, item in enumerate(balances):
            moneda = item['moneda'] or (ORDER_FALLBACK[idx] if idx < len(ORDER_FALLBACK) else "CLP")
            raw    = item['balance'].replace('$', '').replace(' ', '').strip()
            item_name = "CLP 5441" if moneda == "CLP" else "USD 6038"

            try:
                if moneda == "USD":
                    # Formato US: comas = miles, punto = decimal → quitar comas
                    raw_usd = raw.replace(',', '')
                    add_result_usd(resultados, bank_key=key, inst="Global 66",
                                   cat="Cash PJ", item=item_name, usd_str=raw_usd)
                    print_preliminary("Global 66", "Cash PJ", item_name, raw_usd, moneda="USD")
                else:
                    # Formato CL: coma = decimal, puntos = miles
                    # Separar parte entera (antes de la coma), luego quitar puntos de miles
                    raw_clp = raw.split(',')[0].replace('.', '')
                    add_result(resultados, bank_key=key, inst="Global 66",
                               cat="Cash PJ", item=item_name, monto_str=raw_clp)
                    print_preliminary("Global 66", "Cash PJ", item_name, raw_clp)
            except Exception as e_item:
                print(f"[G66] ERROR parseando {item_name} (raw='{item['balance']}'): {e_item}", flush=True)

        return True

    except Exception as e:
        print(f"[G66] [ERROR]  Error: {e}", flush=True)
        for item_name in ("CLP 5441", "USD 6038"):
            add_result(resultados, bank_key=key, inst="Global 66",
                       cat="Cash PJ", item=item_name, monto_str="0", ok=False)
        return False

    finally:
        if page:        page.close()
        if iso_context: iso_context.close()


def scrape_neat(context, resultados):
    """Scraper para Neat — suma depósitos 'En progreso' de combos válidos.

    URL historial : https://app.neatpagos.com/dashboard/historial
    Condición     : Estado == 'En progreso'  AND  (tipo, detalle) in _NEAT_VALID_COMBOS
    Bitwarden     : app.neatpagos.com
    Sesión        : neat_session.json (persistente, igual que Fintual/Harvard)
    """
    key         = "neat"
    iso_context = None
    page        = None

    try:
        print("[NEAT] Obteniendo credenciales de Bitwarden...")
        username = bw_get("username", "app.neatpagos.com")
        password = bw_get("password", "app.neatpagos.com")
        print(f"[NEAT] Credenciales OK: user='{username[:4]}...' ({len(username)} chars)")

        # Contexto "incógnito" — siempre limpio, sin IndexedDB ni cookies previas.
        # Motivo: Neat usa Firebase Auth (IndexedDB). Si hay sesión expirada guardada,
        # muestra swal2 modal INCLUSO al ir directo a /inicia-sesion. Contexto limpio
        # = sin sesión previa = sin swal2 = login directo y limpio siempre.
        # Neat no tiene MFA → login automático en cada ejecución, sin fricción.
        iso_context = context.browser.new_context(viewport={"width": 1280, "height": 800})
        iso_context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
        page = iso_context.new_page()

        # ── LOGIN (siempre, contexto limpio) ───────────────────────────────
        # Selectores confirmados en inspección live (25 Mar 2026):
        #   Formulario cambió de Angular formControlName → HTML plano (name/id)
        #   Email     : input[name="email"] (id="email", type="text") — sin formcontrolname
        #   Password  : input[name="password"] (id="password", type="password") — sin formcontrolname
        #   Submit    : button[type="submit"] texto "Iniciar sesión"
        #   Filas     : mat-expansion-panel.entity-box
        #   Estado OK : classList.contains('InProgress')
        print("[NEAT] Navegando a login (contexto incógnito)...")
        page.goto("https://app.neatpagos.com/inicia-sesion", timeout=60000)
        page.wait_for_timeout(1500)

        page.wait_for_timeout(2000)

        # Email
        email_loc = page.locator('input[name="email"]')
        email_loc.wait_for(state="visible", timeout=10000)
        email_loc.click()
        page.wait_for_timeout(200)
        email_loc.fill(username)
        page.wait_for_timeout(300)

        # Password
        pwd_loc = page.locator('input[name="password"]')
        pwd_loc.wait_for(state="visible", timeout=8000)
        pwd_loc.click()
        page.wait_for_timeout(200)
        pwd_loc.fill(password)
        page.wait_for_timeout(400)

        # Submit — intentar login automático
        submit_btn = page.locator('button[type="submit"]')
        submit_btn.wait_for(state="visible", timeout=5000)
        submit_btn.click()
        print("[NEAT] Login enviado. Verificando resultado...", flush=True)
        page.wait_for_timeout(3000)

        # Detectar si hubo error de credenciales → caer a login manual
        login_error = page.evaluate("""() =>
            Array.from(document.querySelectorAll('*')).some(el =>
                /error con el mail|contraseña|incorrect/i.test(el.textContent) && el.children.length === 0
            )
        """)
        if login_error or "/inicia-sesion" in page.url:
            # Cerrar modal de error si está abierto
            try:
                aceptar = page.locator('button:has-text("Aceptar"), button:has-text("×")')
                if aceptar.first.is_visible():
                    aceptar.first.click()
                    page.wait_for_timeout(500)
            except: pass
            print("WARNING:   Neat: login automático falló. Completa el login en la ventana del browser (Google/Apple/manual). Tienes 120s.", flush=True)

        page.wait_for_url("**/dashboard/**", timeout=120000)

        # Ir al historial
        page.goto("https://app.neatpagos.com/dashboard/historial", timeout=60000)

        # ── EXTRACCIÓN ─────────────────────────────────────────────
        # Selector confirmado en live: mat-expansion-panel.entity-box
        # Estado via clase CSS: InProgress / Done / Error / UnexpectedError
        print("[NEAT] Esperando filas del historial...")
        page.wait_for_selector("mat-expansion-panel.entity-box", timeout=20000)
        page.wait_for_timeout(2000)

        # Scroll progresivo para forzar carga de TODAS las filas (lazy loading Angular).
        # Neat es una SPA Angular con contenedor interno scrollable (mat-sidenav-content),
        # NO scrollea el window — hay que scrollear el contenedor correcto.
        print("[NEAT] Scrolleando para cargar todas las filas...", flush=True)
        prev_count = 0
        for _ in range(40):  # máximo 40 scrolls → soporta listas muy largas
            page.evaluate("""() => {
                // Buscar el contenedor scrollable que contiene las filas.
                // Angular Material usa mat-sidenav-content; fallbacks para otros layouts.
                const candidates = [
                    document.querySelector('mat-sidenav-content'),
                    document.querySelector('.mat-sidenav-content'),
                    document.querySelector('mat-drawer-content'),
                    document.querySelector('main'),
                    document.scrollingElement,
                    document.body,
                ];
                for (const el of candidates) {
                    if (el && el.scrollHeight > el.clientHeight) {
                        el.scrollTop = el.scrollHeight;
                        break;
                    }
                }
            }""")
            page.wait_for_timeout(600)
            cur_count = page.locator("mat-expansion-panel.entity-box").count()
            if cur_count == prev_count:
                break  # sin filas nuevas → llegamos al final real
            prev_count = cur_count
        page.wait_for_timeout(500)
        total_rows = page.locator("mat-expansion-panel.entity-box").count()
        print(f"[NEAT] {total_rows} filas totales en DOM.", flush=True)

        # Extraer TODAS las filas (InProgress y no-InProgress) para debug completo.
        # NUNCA saltar filas — si el selector primario falla, usar fallback.
        # Motivo: filas con acciones (ej: "Anular pago") pueden usar clases d-md-block
        # en vez de d-md-flex, rompiendo el selector primario.
        data = page.evaluate("""() => {
            const rows = document.querySelectorAll('mat-expansion-panel.entity-box');
            const result = [];
            for (const row of rows) {
                // Detectar "En progreso" por clase CSS O por texto del badge de estado.
                // Motivo: filas con "Anular pago" pueden tener clase distinta (ej: Cancellable)
                // aunque visualmente muestren "En progreso".
                const hasInProgressClass = row.classList.contains('InProgress');
                const statusText = row.querySelector('[class*="status"], [class*="estado"], .badge, span')?.textContent ?? '';
                const inProgress = hasInProgressClass || statusText.toLowerCase().includes('en progreso');

                // Guardar clases reales para diagnóstico
                const rowClasses = Array.from(row.classList).join(' ');

                // Selector primario: d-none + d-md-flex (layout normal)
                let cols = Array.from(row.querySelectorAll('[class*="d-none"][class*="d-md-flex"]'));

                // Fallback: d-none + cualquier clase d-md-* (cubre d-md-block, d-md-inline-flex, etc.)
                if (cols.length < 3) {
                    cols = Array.from(row.querySelectorAll('[class*="d-none"]')).filter(el =>
                        Array.from(el.classList).some(c => c.startsWith('d-md-'))
                    );
                }

                const tipo    = cols[0]?.textContent.trim() ?? '';
                const detalle = cols[1]?.textContent.trim() ?? '';
                const monto   = cols[2]?.textContent.trim() ?? '';
                result.push({ tipo, detalle, monto, inProgress, colCount: cols.length, rowClasses });
            }
            return result;
        }""")

        # Log todas las filas InProgress para diagnóstico
        in_progress_rows = [r for r in data if r['inProgress']]
        not_detected = [r for r in data if not r['inProgress'] and r['colCount'] >= 3]
        print(f"[NEAT] {len(in_progress_rows)} filas InProgress de {len(data)} totales:", flush=True)
        for r in in_progress_rows:
            marker  = "✓" if (r['tipo'], r['detalle']) in _NEAT_VALID_COMBOS else "✗"
            fb_warn = " WARNING:  fallback" if r['colCount'] < 3 else ""
            print(f"  [{marker}] tipo='{r['tipo']}' | detalle='{r['detalle']}' | monto='{r['monto']}'{fb_warn}", flush=True)
        # Mostrar clases de filas NO detectadas como InProgress (para diagnóstico de clases alternativas)
        if not_detected:
            unique_classes = set(r['rowClasses'] for r in not_detected[:5])
            print(f"[NEAT] INFO:   Clases en filas no-InProgress (muestra): {list(unique_classes)[:3]}", flush=True)

        total = 0
        matched = 0
        for r in in_progress_rows:
            if (r['tipo'], r['detalle']) in _NEAT_VALID_COMBOS:
                monto_str = r['monto'].replace('$', '').replace('.', '').replace(',', '').strip()
                try:
                    total   += int(monto_str)
                    matched += 1
                except ValueError:
                    print(f"[NEAT] WARNING:   No pudo parsear monto: '{r['monto']}'", flush=True)

        print(f"[NEAT] {matched} depósitos válidos → Total: ${total:,}".replace(',', '.'), flush=True)
        add_result(resultados, bank_key=key, inst="Neat", cat="Cash", item="Neat", monto_str=str(total))
        print_preliminary("Neat", "Cash", "Neat", str(total))
        return True

    except Exception as e:
        print(f"[NEAT] [ERROR]  Error: {e}", flush=True)
        if DEBUG:
            import traceback; traceback.print_exc()
        add_result(resultados, bank_key=key, inst="Neat", cat="Cash", item="Neat",
                   monto_str="0", ok=False)
        return False

    finally:
        if page:
            try: page.close()
            except: pass
        if iso_context:
            try: iso_context.close()
            except: pass


# ══════════════════════════════════════════════════════════════
# TIR: Inversiones semi-automáticas (Dorco / WBuild)
# ══════════════════════════════════════════════════════════════

def _is_tir_item(inst, item):
    """Retorna True si (inst, item) es una inversión TIR semi-automática."""
    try:
        rows = _read_supabase("tir_investments", {"institucion": inst, "item": item}, select="id")
        return len(rows) > 0
    except Exception:
        return False


def calc_tir_value(inst, item):
    """
    Calcula el valor actual de una inversión TIR.
    Fórmula: valor_bruto = nominal × (1 + daily_rate)^días
             valor_neto  = valor_bruto - Σdividendos
    Retorna: (net_value_usd, info_dict) o (None, None) si hay error.
    Lee desde Supabase como fuente primaria.
    """
    try:
        rows = _read_supabase("tir_investments", {"institucion": inst, "item": item},
                              select="nominal_usd,fecha_inversion,tir_anual")
        if not rows:
            return None, None
        nominal    = float(rows[0]["nominal_usd"])
        fecha_str  = rows[0]["fecha_inversion"]
        tir_anual  = float(rows[0]["tir_anual"])
        fecha_inv  = datetime.date.fromisoformat(fecha_str)
        today      = datetime.date.today()
        days       = (today - fecha_inv).days
        daily_rate = (1 + tir_anual) ** (1 / 365) - 1
        valor_bruto = nominal * (1 + daily_rate) ** days
        div_rows   = _read_supabase("tir_dividends", {"institucion": inst, "item": item},
                                    select="monto_usd")
        total_divs = sum(float(r["monto_usd"]) for r in div_rows)
        valor_neto = valor_bruto - total_divs
        info = {
            "nominal": nominal,
            "fecha_inv": fecha_str,
            "tir_anual": tir_anual,
            "days": days,
            "valor_bruto": valor_bruto,
            "total_divs": total_divs,
            "valor_neto": valor_neto,
        }
        return valor_neto, info
    except Exception:
        return None, None


def _update_tir_item(inst, cat, item, moneda, bank_key, interactive=True):
    """
    Actualiza una inversión TIR.
    - interactive=True: muestra panel informativo y pregunta por dividendos nuevos.
    - interactive=False: solo calcula y guarda (modo batch/Actualizar Todo).
    """
    net_value, info = calc_tir_value(inst, item)
    if net_value is None:
        _console.print(f"[red]ERROR: No se encontraron datos TIR para {inst} - {item}[/red]")
        return False

    if interactive:
        tir_pct = info['tir_anual'] * 100
        _console.print(Panel(
            f"[bold]{inst}[/bold] — [italic]{item}[/italic]\n\n"
            f"  Nominal:      [cyan]USD {info['nominal']:,.0f}[/cyan]\n"
            f"  Fecha inv:    [cyan]{info['fecha_inv']}[/cyan]\n"
            f"  TIR anual:    [cyan]{tir_pct:.2f}%[/cyan]\n"
            f"  Días:         [cyan]{info['days']}[/cyan]\n"
            f"  Dividendos:   [cyan]USD {info['total_divs']:,.2f}[/cyan]\n"
            f"  Valor actual: [bold green]USD {net_value:,.2f}[/bold green]",
            title=" TIR Semi-automático",
            border_style="cyan"
        ))

        ans = questionary.confirm(
            "¿Hubo algún dividendo nuevo?", default=False, style=QUESTIONARY_STYLE
        ).ask()
        if ans is None:
            return False

        if ans:
            monto_str = questionary.text("    Monto del dividendo (USD):").ask()
            if not monto_str or not monto_str.strip():
                return False
            fecha_div = questionary.text("    Fecha del dividendo (YYYY-MM-DD):").ask()
            if not fecha_div or not fecha_div.strip():
                return False
            try:
                monto_div = float(monto_str.strip().replace(",", "."))
                fecha_div_clean = fecha_div.strip()
                # Guardar en SQLite local
                conn = init_db()
                conn.execute(
                    "INSERT OR IGNORE INTO tir_dividends (institucion, item, fecha, monto_usd) VALUES (?, ?, ?, ?)",
                    (inst, item, fecha_div_clean, monto_div)
                )
                conn.commit()
                conn.close()
                # Sincronizar a Supabase
                _sync_supabase("tir_dividends", [{
                    "institucion": inst, "item": item,
                    "fecha": fecha_div_clean, "monto_usd": monto_div
                }])
                _console.print(f"[green]  ✓ Dividendo registrado: USD {monto_div:,.2f} ({fecha_div_clean})[/green]")
                net_value, info = calc_tir_value(inst, item)
                _console.print(f"[bold green]  Valor recalculado: USD {net_value:,.2f}[/bold green]")
            except Exception as e:
                _console.print(f"[red]ERROR al registrar dividendo: {e}[/red]")

    resultados = []
    add_result_usd(resultados, bank_key, inst, cat, item, str(net_value))
    if resultados:
        save_to_db(resultados)
        if interactive:
            _console.print(f"[bold green]OK: {inst} {item} guardado como USD {net_value:,.2f}[/bold green]")
    return True


def _scrape_tir_institution(inst, resultados, interactive=True):
    """Actualiza todos los items TIR de una institución. Lee desde Supabase."""
    try:
        sup_rows = _read_supabase("tir_investments", {"institucion": inst}, select="item")
        rows = [(r["item"],) for r in sup_rows]
    except Exception:
        return False

    if not rows:
        return False

    bank_key = inst.lower().replace(" ", "_")
    default_cat = "Fondos Inmobiliarios"
    items_cat_map = {}  # item_name → category (per-item, not shared)
    for name, bk, _, items_list in INSTITUTION_ITEMS:
        if name == inst:
            bank_key = bk
            if items_list:
                default_cat = items_list[0][1]
                items_cat_map = {item_code: item_cat for item_code, item_cat in items_list}
            break

    ok_all = True
    for (item,) in rows:
        cat = items_cat_map.get(item, default_cat)  # per-item category (e.g. Tucson III → PJ)
        print(f"\nActualizando Fondos Inmobiliarios — {inst} ({item}) (TIR semi-automático)...", flush=True)
        ok = _update_tir_item(inst, cat, item, "USD", bank_key, interactive=interactive)
        if not ok:
            ok_all = False
    return ok_all


def scrape_dorco(context, resultados):
    """Wrapper TIR para Dorco — no necesita browser."""
    return _scrape_tir_institution("HDZ", resultados, interactive=False)


def scrape_wbuild(context, resultados):
    """Wrapper TIR para WBuild — no necesita browser."""
    return _scrape_tir_institution("WBuild", resultados, interactive=False)


# ══════════════════════════════════════════════════════════════
# CATÁLOGO DE INSTITUCIONES
# (name, key, scrape_func, [(item, cat), ...])  — orden alfabético
# ══════════════════════════════════════════════════════════════

INSTITUTION_ITEMS = [
    # ── Primero: requieren interacción manual (CAPTCHA/MFA) ──
    ("AFC",                 "afc",            scrape_afc,           [("Fondo de Cesantía", "Previsional")]),
    ("Global 66",           "global66_pj",    scrape_global66_pj,   [("CLP 5441", "Cash PJ"), ("USD 6038", "Cash PJ")]),
    ("Líder BCI",           "lider_bci",      scrape_lider_bci,     [("TdC 5037", "TdC")]),
    # ── Persona Natural (PN) — orden alfabético ──────────────
    ("AFP Modelo",          "afp_modelo",     scrape_afp_modelo,    [("Cuenta Obligatoria", "Previsional")]),
    ("Banco de Chile",      "banco_chile",    scrape_banco_chile,   [("CC 5809", "CC PN"), ("TdC 7164", "TdC"), ("LdC", "LdC"), ("CH New", "CH")]),
    ("Banco Ripley",        "banco_ripley",   scrape_banco_ripley,  [("CC 2239", "CC PN"), ("TdC 9647", "TdC")]),
    ("BTG Pactual",         "btg",            scrape_btg,           [("CFISP500", "Inversiones Líquidas"), ("CFINASDAQ", "Inversiones Líquidas"), ("CFIETFGE", "Inversiones Líquidas")]),
    ("Charles Schwabb",      "schwab",         scrape_schwab,        [("BRK/B", "Inversiones Líquidas"), ("QQQ", "Inversiones Líquidas")]),
    ("Fintual",              "fintual",        scrape_fintual,       [("Risky Norris", "Inversiones Líquidas"), ("Risky Norris APV", "Previsional"), ("VOO", "Inversiones Líquidas"), ("IVV", "Inversiones Líquidas"), ("BRK.B", "Inversiones Líquidas")]),
    ("Fraccional",           "fraccional",     scrape_fraccional,    [("Fraccional", "Fondos Inmobiliarios")]),
    ("Harvard FCU",          "harvard",        scrape_harvard,       [("Checking 5440", "Cash"), ("Savings 5400", "Cash")]),
    ("Neat",                 "neat",           scrape_neat,          [("Neat", "Cash")]),
    ("Racional",             "racional",       scrape_racional,      [("CFIETFCD", "Inversiones Líquidas")]),
    ("Consorcio",           "consorcio",      scrape_consorcio,     [("CC 6758", "CC PN"), ("LdC", "LdC"), ("CH Taihuén", "CH")]),
    ("Itaú",                "itau",           scrape_itau,          [("CC 8792", "CC PN"), ("TdC 6132", "TdC"), ("LdC", "LdC"), ("CH Cívico", "CH")]),
    ("Santander",           "santander",      scrape_santander,     [("CC 2241", "CC PN"), ("TdC 4765", "TdC"), ("TdC 8098", "TdC"), ("LdC", "LdC")]),
    ("Santander",           "santander_dl",   scrape_santander_dl,  [("CH Like (50%)", "CH")]),
    ("Scotiabank",          "scotiabank_pn",  scrape_scotiabank_pn, [("CC 7002", "CC PN"), ("Renta Diaria", "Cash"), ("TdC 3134", "TdC"), ("TdC 2730", "TdC"), ("LdC", "LdC")]),
    ("Wealthfront",         "wealthfront",    scrape_wealthfront,   [("ONEQ", "Inversiones Líquidas")]),
    # ── Persona Jurídica (PJ) — orden alfabético ─────────────
    ("BTG Pactual",         "btg_pj",         scrape_btg_pj,        [("CFISP500", "Inversiones Líquidas PJ"), ("CFINASDAQ", "Inversiones Líquidas PJ"), ("CFIETFGE", "Inversiones Líquidas PJ")]),
    ("Fintual",              "fintual_pj",     scrape_fintual_pj,    [("Risky Norris", "Inversiones Líquidas PJ"), ("Cash Owa", "Inversiones Líquidas PJ"), ("Retiro Pendiente", "Inversiones Líquidas PJ")]),
    ("Fraccional",          "fraccional_pj",  scrape_fraccional_pj, [("Fraccional", "Fondos Inmobiliarios PJ")]),
    ("Itaú PJ",               "itau_pj",        scrape_itau_pj,       [("CC 5735", "CC PJ")]),
    ("Scotiabank",          "scotiabank_pj",  scrape_scotiabank_pj, [("CC 7381", "CC PJ")]),
    # ── TIR Semi-automáticos (sin browser) ──────────────────────────────
    ("HDZ",               "hdz",          scrape_dorco,         [("Tucson I", "Fondos Inmobiliarios"), ("Kansas I", "Fondos Inmobiliarios"), ("Tucson II", "Fondos Inmobiliarios"), ("Tucson III", "Fondos Inmobiliarios PJ")]),
    ("WBuild",              "wbuild",         scrape_wbuild,        [("José Ignacio", "Fondos Inmobiliarios")]),
]

CAPTCHA_KEYS   = {"lider_bci", "afc", "harvard"}
TIR_KEYS       = {"hdz", "wbuild"}
INACTIVE_KEYS  = {"schwab", "wealthfront"}   # Scraper desactivado — usa último valor conocido
# (Se recarga dinámicamente desde scraper_config al iniciar run_scraping)

def _seed_scraper_config():
    """Puebla scraper_config desde Supabase (fuente principal). Fallback a SQLite."""
    _DEFAULT_INACTIVE    = {"schwab", "wealthfront"}
    _DEFAULT_INVERSIONES = {
        "racional", "fraccional", "fraccional_pj",
        "btg", "btg_pj", "fintual", "fintual_pj", "afp_modelo",
    }
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # 1. Intentar leer desde Supabase
    sup_rows = {}
    try:
        rows_sup = _read_supabase("scraper_config")
        for r in rows_sup:
            sup_rows[r["bank_key"]] = r
    except Exception:
        pass

    conn = init_db()

    # 2. Para cada scraper conocido, insertar/actualizar en SQLite (usando Supabase como fuente si existe)
    to_upsert_sup = []
    for name, bk, _, _ in INSTITUTION_ITEMS:
        if bk in sup_rows:
            r = sup_rows[bk]
            conn.execute(
                "INSERT OR REPLACE INTO scraper_config "
                "(bank_key, name, active, in_inversiones, in_bancarios, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (bk, r.get("name", name), r.get("active", 1),
                 r.get("in_inversiones", 0), r.get("in_bancarios", 0), r.get("updated_at", now))
            )
        else:
            existing = conn.execute(
                "SELECT bank_key, active, in_inversiones, in_bancarios FROM scraper_config WHERE bank_key = ?", (bk,)
            ).fetchone()
            if existing:
                act, inv, ban = int(existing[1]), int(existing[2]), int(existing[3] or 0)
                if bk in _DEFAULT_INVERSIONES and not inv:
                    conn.execute("UPDATE scraper_config SET in_inversiones=1 WHERE bank_key=?", (bk,))
                    inv = 1
            else:
                act = 0 if bk in _DEFAULT_INACTIVE else 1
                inv = 1 if bk in _DEFAULT_INVERSIONES else 0
                ban = 0
                conn.execute(
                    "INSERT INTO scraper_config "
                    "(bank_key, name, active, in_inversiones, in_bancarios, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (bk, name, act, inv, ban, now)
                )
            to_upsert_sup.append({
                "bank_key": bk, "name": name, "active": act,
                "in_inversiones": inv, "in_bancarios": ban, "updated_at": now
            })

    conn.commit()

    # 3. Push los nuevos a Supabase
    if to_upsert_sup:
        try:
            _sync_supabase("scraper_config", to_upsert_sup)
        except Exception:
            pass

    conn.close()


def _reload_inactive_keys():
    """Recarga INACTIVE_KEYS desde Supabase (fuente principal). Fallback a SQLite."""
    global INACTIVE_KEYS
    # Intentar Supabase primero
    try:
        rows = _read_supabase("scraper_config", filters={"active": "0"})
        INACTIVE_KEYS = {r["bank_key"] for r in rows}
        return
    except Exception:
        pass
    # Fallback SQLite
    try:
        conn = init_db()
        rows = conn.execute("SELECT bank_key FROM scraper_config WHERE active = 0").fetchall()
        conn.close()
        INACTIVE_KEYS = {r[0] for r in rows}
    except Exception as e:
        if DEBUG: print(f"[CFG] _reload_inactive_keys error: {e}")

# ── Items sin scraper — se registran a mano ───────────────────────────────
# (display_name, bank_key, institution, category, item, moneda)
MANUAL_ITEMS = [
    ("Itaú PJ",   "itau_pj", "Itaú PJ",    "Inversiones Líquidas PJ", "CFINASDAQ", "CLP"),
    ("Itaú PJ",   "itau_pj", "Itaú PJ",    "Inversiones Líquidas PJ", "CFIETFCD",  "CLP"),
    ("Itaú PJ",   "itau_pj", "Itaú PJ",    "Inversiones Líquidas PJ", "Caja corredora", "CLP"),
    ("Woperty",    "woperty",      "Woperty",     "Inversión Startup",      "SAFE Woperty",  "USD"),
    # Ítems genéricos manuales — piden nota de referencia al ingresar
    ("Cuentas por cobrar", "cxc", "Cuentas por cobrar", "Inversiones Líquidas", "Cuentas por cobrar", "CLP"),
    ("Cuentas por pagar",  "cxp", "Cuentas por pagar",  "Inversiones Líquidas", "Cuentas por pagar",  "CLP"),
]

# Items que requieren nota de referencia al ingresar un valor
_NOTE_REQUIRED_ITEMS = {"Cuentas por cobrar", "Cuentas por pagar"}
# Items que son deuda → se almacenan como negativo (usuario ingresa valor positivo)
_DEBT_MANUAL_ITEMS   = {"Cuentas por pagar"}

def _get_manual_items():
    """Retorna la lista completa de items manuales (Hardcoded + DB), excluyendo items marcados como deleted."""
    conn = init_db()
    cur = conn.cursor()
    # Cargar deleted items para filtrar los hardcoded también
    deleted_set = set()
    try:
        cur.execute("SELECT institucion, item FROM catalog_manual WHERE deleted = 1")
        for d_inst, d_item in cur.fetchall():
            deleted_set.add((d_inst, d_item))
    except: pass
    # Hardcoded: excluir los marcados como deleted
    items = [row for row in MANUAL_ITEMS if (row[2], row[4]) not in deleted_set]
    try:
        cur.execute("SELECT display_name, bank_key, institucion, categoria, item, moneda FROM catalog_manual WHERE deleted = 0")
        for row in cur.fetchall():
            items.append(row)
    except Exception as e:
        if DEBUG: print(f"[DB] Error al cargar catálogo manual: {e}")
    conn.close()
    return items

def _get_unified_catalog_list():
    """Genera una lista de diccionarios con la metadata de TODO el catálogo (Auto + Manual Hard + DB)."""
    seen = set() # (inst, item, persona)
    unified = []

    # Cargar lista de items eliminados (deleted=1) en catalog_manual
    deleted_items = set()
    try:
        conn_temp = init_db()
        cur_temp = conn_temp.cursor()
        cur_temp.execute("SELECT institucion, item FROM catalog_manual WHERE deleted = 1")
        for d_inst, d_item in cur_temp.fetchall():
            deleted_items.add((d_inst, d_item))
        conn_temp.close()
    except:
        pass  # Si hay error, simplemente no filtra deleted

    # 1. Automáticos (excluir si están marcados como deleted)
    for name, b_key, _, items in INSTITUTION_ITEMS:
        for item_code, cat in items:
            key = (name, item_code, cat_to_persona(cat))
            # SKIP si el item automático fue eliminado manualmente
            if (name, item_code) in deleted_items:
                continue
            if key not in seen:
                unified.append({
                    'inst': name,
                    'item': item_code,
                    'cat': cat,
                    'moneda': 'UF' if cat == 'CH' else ('USD' if b_key in TIR_KEYS else 'CLP'),
                    'type': 'auto',
                    'bank_key': b_key
                })
                seen.add(key)

    # 2. Manuales Hardcoded (excluir si están marcados como deleted)
    for _, m_key, m_inst, m_cat, m_item, m_moneda in MANUAL_ITEMS:
        if (m_inst, m_item) in deleted_items:
            continue
        key = (m_inst, m_item, cat_to_persona(m_cat))
        if key not in seen:
            unified.append({
                'inst': m_inst,
                'item': m_item,
                'cat': m_cat,
                'moneda': m_moneda,
                'type': 'manual_hard',
                'bank_key': m_key
            })
            seen.add(key)

    # 3. DB Dinámicos (excluyendo items marcados como deleted)
    # Leer catalog_manual desde Supabase (fuente primaria)
    catalog_rows = []
    try:
        catalog_rows = _read_supabase("catalog_manual",
                                      select="institucion,item,categoria,moneda,bank_key",
                                      extra="&deleted=eq.0")
    except Exception:
        pass
    if not catalog_rows:
        # Fallback: SQLite local
        try:
            conn = init_db()
            cur = conn.cursor()
            cur.execute("SELECT institucion, item, categoria, moneda, bank_key FROM catalog_manual WHERE deleted = 0")
            for row in cur.fetchall():
                catalog_rows.append({"institucion": row[0], "item": row[1], "categoria": row[2],
                                      "moneda": row[3], "bank_key": row[4]})
            conn.close()
        except: pass

    for r in catalog_rows:
        m_inst, m_item, m_cat, m_moneda, m_key = (
            r["institucion"], r["item"], r["categoria"], r["moneda"], r["bank_key"]
        )
        key = (m_inst, m_item, cat_to_persona(m_cat))
        existing = next((x for x in unified if (x['inst'], x['item'], cat_to_persona(x['cat'])) == key), None)
        if existing:
            existing['cat'] = m_cat
            existing['moneda'] = m_moneda
            if existing.get('type') != 'auto':
                existing['type'] = 'manual_db'
            existing['bank_key'] = m_key
        else:
            unified.append({
                'inst': m_inst, 'item': m_item, 'cat': m_cat,
                'moneda': m_moneda, 'type': 'manual_db', 'bank_key': m_key
            })
            seen.add(key)
    return unified

def add_new_manual_type():
    """Pregunta Persona, Categoría, Institución, Moneda y Monto inicial para crear un nuevo tipo de registro manual."""
    _console.print("\n[bold cyan]NUEVO TIPO DE REGISTRO MANUAL[/bold cyan]\n")

    # 1. Persona (PN vs PJ)
    _clear_terminal_buffer()
    persona = questionary.select(
        "Seleccione tipo de Persona:",
        choices=[
            questionary.Choice("Persona Natural (PN)", value="PN"),
            questionary.Choice("Persona Jurídica (PJ)", value="PJ"),
            questionary.Separator(),
            questionary.Choice("« Volver", value="back"),
            questionary.Choice("Salir", value="exit"),
        ],
        style=QUESTIONARY_STYLE,
        pointer="»",
        qmark=""
    ).ask(patch_stdout=True)
    if persona == "exit": sys.exit(0)
    if not persona or persona == "back": return

    # 2. Categoría (Filtrada por Persona)
    all_catalog = _get_unified_catalog_list()
    
    # Obtener categorías únicas que correspondan a esa persona
    cat_choices = []
    seen_labels = set()
    
    for c in CAT_ORDER:
        if (persona == "PJ" and "PJ" in c) or (persona == "PN" and "PJ" not in c):
            label = cat_to_short(c)
            if label not in seen_labels:
                cat_choices.append(questionary.Choice(label, value=c))
                seen_labels.add(label)
    
    # Agregar categorías extra de DB manual si corresponden
    for item_meta in all_catalog:
        m_cat = item_meta['cat']
        m_pers = cat_to_persona(m_cat)
        if m_pers == persona:
            label = cat_to_short(m_cat)
            if label not in seen_labels:
                cat_choices.append(questionary.Choice(label, value=m_cat))
                seen_labels.add(label)

    cat_choices += [
        questionary.Separator(),
        questionary.Choice("Otro", value="Otro"),
        questionary.Choice("« Volver", value="back"),
        questionary.Choice("Salir", value="exit"),
    ]

    cat = questionary.select(
        f"Selecciona la Categoría ({persona}):",
        choices=cat_choices,
        style=QUESTIONARY_STYLE,
        pointer="»",
        qmark=""
    ).ask(patch_stdout=True)
    if cat == "exit": sys.exit(0)
    if not cat or cat == "back": return
    if cat == "Otro":
        cat_text = questionary.text("Escribe la nueva categoría:").ask(patch_stdout=True)
        if not cat_text: return
        # Si es PJ y no lo dice, lo agregamos internamente para consistencia
        cat = f"{cat_text} PJ" if persona == "PJ" and "PJ" not in cat_text.upper() else cat_text

    # 3. Institución
    existing_insts = sorted(list(set([itm[0] for itm in INSTITUTION_ITEMS] + [itm['inst'] for itm in all_catalog])))
    inst_choices = [questionary.Choice(i, value=i) for i in existing_insts]
    inst_choices += [
        questionary.Separator(),
        questionary.Choice("Otro (especificar)", value="Otro"),
        questionary.Choice("« Volver", value="back"),
        questionary.Choice("Salir", value="exit"),
    ]

    inst = questionary.select(
        "Selecciona la Institución:",
        choices=inst_choices,
        style=QUESTIONARY_STYLE,
        pointer="»",
        qmark=""
    ).ask(patch_stdout=True)
    if inst == "exit": sys.exit(0)
    if not inst or inst == "back": return
    if inst == "Otro":
        inst = questionary.text("Escribe la nueva institución:").ask(patch_stdout=True)
    if not inst: return
    inst = inst.strip()

    # 4. Item
    item_name = questionary.text("Nombre del Item (ej: Cuenta Ahorro, Inversión X):").ask(patch_stdout=True)
    if not item_name: return
    item_name = item_name.strip()

    # 5. Moneda
    moneda_choices = [
        questionary.Choice("CLP", value="CLP"),
        questionary.Choice("USD", value="USD"),
        questionary.Choice("UF", value="UF"),
        questionary.Separator(),
        questionary.Choice("Otro", value="Otro"),
        questionary.Choice("« Volver", value="back"),
        questionary.Choice("Salir", value="exit"),
    ]
    moneda = questionary.select(
        "Selecciona la Moneda:",
        choices=moneda_choices,
        style=QUESTIONARY_STYLE,
        pointer="»",
        qmark=""
    ).ask(patch_stdout=True)
    if moneda == "exit": sys.exit(0)
    if not moneda or moneda == "back": return
    if moneda == "Otro":
        moneda = questionary.text("Escribe la nueva moneda:").ask(patch_stdout=True)
    if not moneda: return
    moneda = moneda.strip().upper()

    # 6. Monto inicial
    monto_v = questionary.text(f"Monto inicial ({moneda}):").ask(patch_stdout=True)
    if monto_v is None: return # Cancelado
    try:
        monto_raw = monto_v.replace(".", "").replace(",", ".").strip()
        monto_f = float(monto_raw) if monto_raw else 0.0
    except:
        monto_f = 0.0

    # Guardar en catálogo
    conn = init_db()
    bank_key = inst.lower().replace(" ", "_").strip()
    conn.execute("""
        INSERT INTO catalog_manual (display_name, bank_key, institucion, categoria, item, moneda)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (inst, bank_key, inst, cat, item_name, moneda))
    conn.commit()
    # Mirroring to Supabase
    _sync_supabase("catalog_manual", [{
        "display_name": inst, "bank_key": bank_key, "institucion": inst,
        "categoria": cat, "item": item_name, "moneda": moneda, "deleted": 0
    }])
    conn.close()

    # Guardar monto inicial en la tabla 'saldos' vía add_result
    monto_str = str(int(monto_f)) if moneda == "CLP" else str(monto_f)
    resultados = []
    if moneda == "USD":
        add_result_usd(resultados, bank_key, inst, cat, item_name, monto_str, manual=True)
    elif moneda == "UF":
        add_result_uf(resultados, bank_key, inst, cat, item_name, monto_str, manual=True)
    else:
        add_result(resultados, bank_key, inst, cat, item_name, monto_str, manual=True)
    
    save_to_db(resultados)
    
    _console.print(f"\n[bold green]OK: Registro '{item_name}' en '{inst}' ({persona}) creado con éxito.[/bold green]\n")

def _actualizar_algunos_automaticos():
    """Submenu: correr scrapers de un grupo o institución específica."""
    while True:
        _clear_terminal_buffer()
        ans = questionary.select(
            "¿Qué quieres actualizar?",
            choices=[
                questionary.Choice("Actualizar solo inversiones",   value="inversiones"),
                questionary.Choice("Actualizar solo bancarios",     value="bancarios"),
                questionary.Choice("Correr scraper de institución", value="scraper"),
                questionary.Separator(),
                questionary.Choice("« Volver",                      value="back"),
            ],
            style=QUESTIONARY_STYLE,
            pointer="»",
            qmark=""
        ).ask(patch_stdout=True)

        if not ans or ans == "back":
            return

        if ans in ("inversiones", "bancarios"):
            _seed_scraper_config()
            db_field = "in_inversiones" if ans == "inversiones" else "in_bancarios"
            label    = "inversiones" if ans == "inversiones" else "bancarios"
            # Leer desde Supabase primero
            group_keys = set()
            try:
                sup_cfg = _read_supabase("scraper_config", filters={db_field: "1"})
                group_keys = {r["bank_key"] for r in sup_cfg}
            except Exception:
                pass
            if not group_keys:
                conn_g = init_db()
                rows_g = conn_g.execute(
                    f"SELECT bank_key FROM scraper_config WHERE {db_field}=1"
                ).fetchall()
                conn_g.close()
                group_keys = {r[0] for r in rows_g}
            insts_group = [t for t in INSTITUTION_ITEMS if t[1] in group_keys and t[1] not in TIR_KEYS]
            if not insts_group:
                _console.print(f"[yellow]No hay scrapers configurados para '{label}'. Configúralos en Gestión de scrapers.[/yellow]")
                input("Enter para continuar...")
                continue
            _console.print(f"[bold sky_blue3]Actualizando {label}:[/bold sky_blue3] {', '.join(t[0] for t in insts_group)}")
            try:
                bw_unlock()
                run_scraping(insts_group)
            except Exception as e:
                _console.print(f"\n[bold red][ERROR] Scraping falló: {e}[/bold red]")
            finally:
                _reset_terminal()
                _clear_terminal_buffer()
            continue

        if ans == "scraper":
            # Separar PN vs PJ — PJ: bank_key termina en _pj o items tienen cat con "PJ"
            pn_insts = []
            pj_insts = []
            for t in INSTITUTION_ITEMS:
                name, bk, func, items_list = t
                if bk in TIR_KEYS:
                    continue
                if bk.endswith("_pj") or any("PJ" in cat for _, cat in items_list):
                    pj_insts.append(t)
                else:
                    pn_insts.append(t)

            # Instituciones que aparecen en ambos grupos (ej. BTG, Fintual) → agregar sufijo
            pn_names = {t[0] for t in pn_insts}
            pj_names = {t[0] for t in pj_insts}
            shared   = pn_names & pj_names

            # Nombres especiales por bank_key
            _LABEL_OVERRIDES = {
                "santander":    "Santander MG",
                "santander_dl": "Santander DL",
                "global66_pj":  "Global 66",
            }

            def _inst_label(t):
                return _LABEL_OVERRIDES.get(t[1], t[0])

            # Ordenar alfabéticamente dentro de cada grupo
            pn_insts.sort(key=lambda t: _inst_label(t))
            pj_insts.sort(key=lambda t: _inst_label(t))

            choices = []
            choices.append(questionary.Separator("—— Persona Natural ——"))
            for t in pn_insts:
                choices.append(questionary.Choice(_inst_label(t), value=t))

            choices.append(questionary.Separator("—— Persona Jurídica ——"))
            for t in pj_insts:
                choices.append(questionary.Choice(_inst_label(t), value=t))

            choices.append(questionary.Separator())
            choices.append(questionary.Choice("« Volver", value="back"))

            selected = questionary.checkbox(
                "Seleccioná las instituciones (Espacio para marcar, Enter para confirmar):",
                choices=choices,
                style=QUESTIONARY_STYLE,
                pointer="»"
            ).ask(patch_stdout=True)

            if not selected or "back" in selected:
                continue

            insts = [s for s in selected if isinstance(s, tuple)]
            if not insts:
                continue

            try:
                bw_unlock()
                run_scraping(insts)
            except Exception as e:
                _console.print(f"\n[bold red][ERROR] Scraping falló: {e}[/bold red]")
            finally:
                _reset_terminal()
                _clear_terminal_buffer()


def prompt_manual_items(all_sequential=False):
    """
    Muestra lista de items que NO tienen scraper automático.
    all_sequential: si es True, actualiza todos sin preguntar selección.
    """
    all_catalog = _get_unified_catalog_list()
    if not all_catalog:
        return

    # Pre-cargar divisas de hoy
    rates = get_rates()
    
    # Anchos dinámicos para el selector (ajustados para incluir fecha)
    w_cat = max([len(cat_to_short(i['cat'], i['inst'])) for i in all_catalog] + [3]) + 1
    w_inst = max([len(i['inst']) for i in all_catalog] + [4]) + 1
    w_per = 3
    w_item = max([len(i['item']) for i in all_catalog] + [4]) + 1
    w_monto = 14
    w_mon = 4
    w_date = 14 # '10 Mar 11:24'
    
    # ── Filtrar: solo items genuinamente manuales ──────────────────────────
    has_scraper_inst_item = set()
    for name, bk, _, items_list in INSTITUTION_ITEMS:
        for item_code, _ in items_list:
            has_scraper_inst_item.add((name, item_code))

    filtered = []
    for item_meta in all_catalog:
        inst_f = item_meta['inst']
        item_f = item_meta['item']
        # Excluir si tiene scraper automático (incluyendo TIR)
        if (inst_f, item_f) in has_scraper_inst_item:
            continue
        # EXCLUIR SIEMPRE los de Shares x Price (Semiautomáticos) del menú puramente manual
        if _is_shares_price_item(item_f):
            continue
        filtered.append(item_meta)
    all_catalog = filtered

    if not all_catalog:
        _console.print("[dim]No hay ítems manuales para actualizar.[/dim]")
        return

    # Ordenar por categoría
    all_catalog.sort(key=lambda x: (CAT_ORDER.index(cat_to_short(x['cat'], x['inst'])) if cat_to_short(x['cat'], x['inst']) in CAT_ORDER else 99, x['inst']))

    # Si es actualización total secuencial, solo necesitamos preparar la data UNA vez
    if all_sequential:
        items_data = _prepare_manual_items_data(all_catalog, w_cat, w_inst, w_per, w_item, w_monto, w_mon, w_date)
        _clear_terminal_buffer()
        _console.print(Panel("[bold green]Iniciando actualización DE TODOS los ítems manuales...[/bold green]"))
        for itm in items_data:
            _quick_update_balance(itm["inst"], itm["cat"], itm["item"], itm["moneda"], itm.get("bank_key"))
        return

    # Si es selección manual por tabla - REFRESCAR EN CADA VUELTA
    # ── Selección manual usando la tabla oficial ──────────────────────────
    while True:
        _clear_content()
        # Mostramos la tabla oficial. Ella misma se encarga de poblar _LAST_TABLE_MAPPING
        show_last_saldos(pause=False, title="Actualizar Algunos Específicos")
        
        _console.print(f"\n  [dim]{'-' * 40}[/dim]")
        _console.print("  [bold cyan]v[/bold cyan]   Volver atrás\n")

        _clear_terminal_buffer()
        # 2. Pedir número de fila
        ans = questionary.text("Ingresa el número a editar (o 'v' para volver):", qmark="").ask(patch_stdout=True)

        if ans is None or ans.lower() == 'v':
            break
        
        # Validar selección contra el mapping global de la tabla recién mostrada
        try:
            import time
            val = int(ans)
            if 1 <= val <= len(_LAST_TABLE_MAPPING):
                selected = _LAST_TABLE_MAPPING[val-1]
                # Actualizar
                _quick_update_balance(
                    selected["inst"], 
                    selected["cat"], 
                    selected["item"], 
                    selected["moneda"], 
                    selected.get("bank_key") or selected["inst"].lower().replace(" ", "_").strip()
                )
            else:
                rprint("[red]Número no válido.[/red]")
                time.sleep(1)
        except ValueError:
            import time
            rprint("[red]Ingresa un número válido.[/red]")
            time.sleep(1)

def _prepare_manual_items_data(all_catalog, w_cat, w_inst, w_per, w_item, w_monto, w_mon, w_date):
    """Auxiliar para generar la lista de items con sus saldos actuales de la DB."""
    conn = init_db()
    cur  = conn.cursor()
    items_data = []
    
    for m_idx, item_meta in enumerate(all_catalog):
        inst = item_meta['inst']
        cat = item_meta['cat']
        item_code = item_meta['item']
        moneda = item_meta['moneda']
        persona = cat_to_persona(cat)
        bank_key = item_meta.get('bank_key')
        
        db_inst = _CATALOG_TO_DB_INST.get(inst, inst)
        cur.execute("""SELECT monto, timestamp FROM saldos
                       WHERE institucion=? AND item=? AND persona=?
                       ORDER BY timestamp DESC LIMIT 1""", (db_inst, item_code, persona))
        row = cur.fetchone()
        val = row[0] if (row and row[0] is not None) else None
        ts  = row[1] if (row and row[1]) else None
        
        # Formatear fecha
        fecha_fmt = "—"
        if ts:
            try:
                dt = datetime.datetime.strptime(ts[:16], "%Y-%m-%d %H:%M")
                fecha_fmt = f"{dt.day:02d} {_MES[dt.month-1]} {dt.strftime('%H:%M')}"
            except:
                pass

        if val is None:
            ultimo_disp = "—"
        else:
            m_num = float(val)
            if m_num == 0:
                ultimo_disp = "—"
            elif moneda == "USD":
                ultimo_disp = f"{m_num:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
            elif moneda == "UF":
                ultimo_disp = f"{m_num:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
            else: # CLP
                ultimo_disp = fmt_monto(int(round(m_num)))

        # Aplicar el estilo: Alineación simple sin tags de Rich
        row_str = (
            f"{cat_to_short(cat, inst):<{w_cat}} "
            f"{inst:<{w_inst}} "
            f"{persona:<{w_per}} "
            f"{item_code:<{w_item}} "
            f"{ultimo_disp:>{w_monto}} "
            f"{moneda:<{w_mon}} "
            f"{fecha_fmt:<{w_date}}"
        )
        
        items_data.append({
            "idx": m_idx,
            "idx_row": m_idx + 1,
            "inst": inst,
            "cat": cat,
            "item": item_code,
            "moneda": moneda,
            "ultimo": ultimo_disp,
            "row_str": row_str,
            "bank_key": bank_key
        })
    conn.close()
    return items_data

def prompt_failed_items(still_errors, resultados):
    """
    Después del scraping, muestra items fallidos como lista numerada.
    El usuario tipea números para actualizar o Enter para continuar.
    Auto-continúa en 5 minutos sin respuesta.
    """
    if not still_errors:
        return

    conn = init_db()
    cur = conn.cursor()

    items_data = []
    for idx, r in enumerate(still_errors):
        inst    = r["inst"]
        item    = r["item"]
        cat     = r["cat"]
        moneda  = r.get("moneda", "CLP")

        db_inst = _CATALOG_TO_DB_INST.get(inst, inst)
        persona = cat_to_persona(cat)
        cur.execute("""SELECT monto, timestamp FROM saldos
                       WHERE institucion=? AND item=? AND persona=?
                       ORDER BY timestamp DESC LIMIT 1""", (db_inst, item, persona))
        row = cur.fetchone()
        ultimo_disp = "—"
        fecha_disp = "—"
        if row and row[0] is not None:
            m_num = float(row[0])
            timestamp = row[1]
            if m_num == 0:
                ultimo_disp = "—"
            elif moneda == "USD":
                ultimo_disp = f"{m_num:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
            elif moneda == "UF":
                ultimo_disp = f"{m_num:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
            else:
                ultimo_disp = fmt_monto(int(round(m_num)))
            # Parsear timestamp: "2026-03-08 18:49:15.123456" → "08-Mar 18:49"
            if timestamp:
                try:
                    fecha_disp = timestamp[5:7] + "-" + timestamp[5:10].split("-")[1][:3].upper() + " " + timestamp[11:16]
                except:
                    fecha_disp = timestamp[:16] if len(timestamp) >= 16 else timestamp

        items_data.append({
            "inst": inst, "cat": cat, "item": item, "persona": persona,
            "moneda": moneda, "ultimo": ultimo_disp, "fecha": fecha_disp,
        })
    conn.close()

    # Detectar si hay ambigüedad PN/PJ (misma inst con ambos tipos)
    from collections import Counter
    inst_personas = Counter((d["inst"], d["persona"]) for d in items_data)
    ambiguous_insts = {inst for (inst, _), _ in inst_personas.items()
                       if any(inst == i2 and p != p2
                              for (i2, p2), _ in inst_personas.items())}
    show_persona = bool(ambiguous_insts)

    # ── Tabla Rich limpia ─────────────────────────────────────────────────
    from rich.table import Table
    tbl = Table(box=None, show_header=True, header_style="bold red",
                title="[bold red]WARNING:   Items Fallidos[/bold red]", title_justify="left")
    tbl.add_column("#",       style="dim", width=3)
    tbl.add_column("Cat.",    style="dim", min_width=8)
    tbl.add_column("Inst.",   style="white", min_width=14)
    if show_persona:
        tbl.add_column("P.", style="dim", width=4)
    tbl.add_column("Item",    style="white", min_width=16)
    tbl.add_column("Último",  style="cyan",  justify="right", min_width=14)
    tbl.add_column("Mon.",    style="dim",   width=5)
    tbl.add_column("Última act.", style="dim", min_width=13)

    for i, itm in enumerate(items_data, 1):
        row = [str(i), cat_to_short(itm["cat"], itm["inst"]), itm["inst"]]
        if show_persona:
            row.append(itm["persona"])
        row += [itm["item"], itm["ultimo"], itm["moneda"], itm["fecha"]]
        tbl.add_row(*row)

    # Filas de pagos TdC que fallaron (no interactivas — requieren re-scraping)
    if _PAGOS_ERRORS:
        for pe in _PAGOS_ERRORS:
            tbl.add_row("[dim]—[/dim]", "[dim]Pagos TdC[/dim]", f"[dim]{pe['inst']}[/dim]",
                        f"[dim]{pe['card']}[/dim]", "[dim]—[/dim]", "[dim]—[/dim]",
                        f"[dim red]Re-scrapear[/dim red]")

    _console.print()
    _console.print(tbl)

    _TIMEOUT_SEG = 300
    def _handler_timeout(signum, frame):
        raise TimeoutError()

    _console.print(f"\n[dim]Ingresá número(s) para actualizar (ej: [bold]1[/bold] o [bold]1,2[/bold]) o Enter para continuar.[/dim]")
    _console.print(f"[dim][WAIT]   Auto-continúa en {_TIMEOUT_SEG // 60} min si no hay respuesta.[/dim]\n")

    _clear_terminal_buffer()
    _old_handler = signal.signal(signal.SIGALRM, _handler_timeout)
    signal.alarm(_TIMEOUT_SEG)
    try:
        raw_input = input("  » ").strip()
    except (TimeoutError, EOFError):
        _console.print("[dim][WAIT]   Tiempo agotado — continuando.[/dim]")
        raw_input = ""
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, _old_handler)

    if not raw_input:
        return

    # Parsear números seleccionados
    selected_idxs = set()
    for part in raw_input.replace(" ", "").split(","):
        try:
            n = int(part)
            if 1 <= n <= len(items_data):
                selected_idxs.add(n - 1)
        except ValueError:
            pass

    if not selected_idxs:
        return

    # ── Actualizar cada item seleccionado ─────────────────────────────────
    for i in sorted(selected_idxs):
        itm     = items_data[i]
        inst    = itm["inst"]
        cat     = itm["cat"]
        item    = itm["item"]
        moneda  = itm["moneda"]
        bank_key = inst.lower().replace(" ", "_")

        if _is_shares_price_item(item, inst=inst):
            _update_shares_price_item(inst, cat, item, moneda, bank_key)
            continue

        lbl = "deuda" if cat in ("TdC", "LdC", "CH") else "saldo"
        try:
            raw = input(f"  {inst} / {item} — {lbl} ({moneda}): ").strip()
        except EOFError:
            continue
        if not raw:
            continue

        try:
            if moneda == "CLP":
                add_result(resultados, bank_key, inst, cat, item, raw, manual=True)
                print_preliminary(inst, cat, item, raw)
            elif moneda == "UF":
                add_result_uf(resultados, bank_key, inst, cat, item, raw, manual=True)
                print_preliminary(inst, cat, item, raw, moneda="UF")
            elif moneda == "USD":
                add_result_usd(resultados, bank_key, inst, cat, item, raw, manual=True)
                print_preliminary(inst, cat, item, raw, moneda="USD")
            resultados[:] = [x for x in resultados if not (x["item"] == item and x["inst"] == inst and not x["ok"])]
        except Exception as e:
            _console.print(f"[dim]WARNING:   Error al procesar {item}: {e}[/dim]")

def _is_shares_price_item(item, inst=None):
    """Verifica si un item requiere lógica de Shares × Price.

    IMPORTANTE: Solo aplica para Itaú CdB — NO para Racional u otras instituciones
    que también usan el ticker CFIETFCD (en Racional es un scraper automático normal).
    Si se pasa `inst`, se verifica que sea Itaú. Sin `inst` se asume que aplica
    (compatibilidad con contextos donde ya se filtró por institución).
    """
    if item not in ("CFIETFCD", "CFINASDAQ", "CFISP500", "CFIETFGE"):
        return False
    if inst is not None:
        # Solo aplica para instituciones bancarias/CdB. 
        # En Racional, estos tickers son scrapers automáticos.
        # Solo Itaú tiene items manuales Shares×Price (BTG, Racional, Scotiabank = scraping automático)
        return any(x in inst.lower() for x in ("itau", "itaú"))
    return True  # sin inst: asumir que aplica (backward compat)

def get_fund_price(ticker):
    """
    Obtiene el precio actual de un fondo desde Yahoo Finance.
    Intenta múltiples campos y períodos históricos como fallback.

    Args:
        ticker (str): Símbolo del fondo con exchange (ej: "CFIETFCD.SN", "CFINASDAQ.SN")

    Returns:
        float: Precio actual, o None si falla
    """
    try:
        import yfinance as yf
        fund = yf.Ticker(ticker)

        if DEBUG: print(f"[YAHOO] Buscando precio para {ticker}...")

        # ESTRATEGIA 1: Buscar en múltiples campos de info
        info = fund.info
        price_fields = [
            'regularMarketPrice',      # Campo estándar
            'currentPrice',            # Alternativa común
            'navPrice',                # Para fondos (NAV = Net Asset Value)
            'bidPrice',                # Precio de compra
            'askPrice',                # Precio de venta
            'previousClose',           # Cierre anterior
            'fiftyTwoWeekHigh',        # Alto 52 semanas
        ]

        for field in price_fields:
            price = info.get(field)
            if price and isinstance(price, (int, float)) and price > 0:
                if DEBUG: print(f"[YAHOO]   ✓ Encontrado en campo '{field}': {price}")
                return float(price)

        if DEBUG: print(f"[YAHOO]   ✗ No encontrado en info fields, intentando histórico...")

        # ESTRATEGIA 2: Fallback histórico (intentar múltiples períodos)
        for period in ['1d', '5d', '1mo', '3mo']:
            try:
                history = fund.history(period=period)
                if not history.empty:
                    # Tomar el último Close válido
                    close_price = None
                    for idx in range(len(history) - 1, -1, -1):
                        val = history['Close'].iloc[idx]
                        if val > 0 and not (isinstance(val, float) and val != val):  # No NaN
                            close_price = float(val)
                            break

                    if close_price and close_price > 0:
                        if DEBUG: print(f"[YAHOO]   ✓ Encontrado en histórico (período={period}): {close_price}")
                        return close_price
            except Exception as e:
                if DEBUG: print(f"[YAHOO]   ✗ Histórico período={period} falló: {e}")
                continue

        if DEBUG: print(f"[YAHOO]   ✗ No se pudo obtener precio para {ticker}")
        return None

    except Exception as e:
        if DEBUG: print(f"[YAHOO] Error general fetching {ticker}: {e}")
        return None


# ── Tickers para workaround de precio BTG Pactual ──────────────────────────
# Tickers en Bolsa de Comercio de Santiago (CLP directo, sin conversión FX)
_BTG_ITEM_TICKERS = {
    "CFISP500":  "CFISP500.SN",
    "CFINASDAQ": "CFINASDAQ.SN",
    "CFIETFGE":  "CFIETFGE.SN",
}


def _get_historical_price(ticker, dt):
    """
    Precio de cierre de un ticker en o cerca de una fecha dada.
    Ventana ±5 días para cubrir fines de semana y feriados.
    """
    try:
        import yfinance as yf
        import datetime as _dt
        date  = dt.date() if hasattr(dt, 'date') else dt
        start = date - _dt.timedelta(days=1)
        end   = date + _dt.timedelta(days=5)
        hist  = yf.Ticker(ticker).history(start=str(start), end=str(end))
        if hist.empty:
            return None
        for idx in range(len(hist)):
            val = float(hist['Close'].iloc[idx])
            if val > 0:
                if DEBUG: print(f"[BTG-WA] Precio hist {ticker} @ {hist.index[idx].date()}: {val:.4f}")
                return val
        return None
    except Exception as e:
        if DEBUG: print(f"[BTG-WA] _get_historical_price error: {e}")
        return None


def _apply_btg_price_workaround(resultados):
    """
    Workaround para ítems BTG con ok=False:
    corrected = valor_anterior × (precio_actual / precio_en_fecha_anterior)
    Aplica a ambos BTG PN y PJ para CFISP500, CFINASDAQ, CFIETFGE.
    """
    import datetime as _dt

    btg_failed = [
        r for r in resultados
        if not r["ok"]
        and r.get("bank_key") in ("btg", "btg_pj")
        and r.get("item") in _BTG_ITEM_TICKERS
    ]
    if not btg_failed:
        return

    print(f"\n{'─'*46}")
    print(f"⟳  BTG workaround de precio para {len(btg_failed)} ítem(s)...")

    current_prices = {}  # cache ticker → precio actual

    for r in btg_failed:
        item     = r["item"]
        inst     = r["inst"]
        cat      = r.get("cat", "Inversiones Líquidas")
        # persona derivada del cat (no viene en el dict de resultado)
        persona  = "PJ" if "PJ" in cat else "PN"
        bank_key = r["bank_key"]
        ticker   = _BTG_ITEM_TICKERS[item]

        try:
            # 1. Última lectura con valor en DB local
            with sqlite3.connect(DB_PATH) as conn:
                row = conn.execute("""
                    SELECT monto, timestamp FROM saldos
                    WHERE institucion=? AND item=? AND persona=? AND monto>0
                    ORDER BY timestamp DESC LIMIT 1
                """, (inst, item, persona)).fetchone()
                # Fallback: sin filtro de persona
                if not row:
                    row = conn.execute("""
                        SELECT monto, timestamp FROM saldos
                        WHERE institucion=? AND item=? AND monto>0
                        ORDER BY timestamp DESC LIMIT 1
                    """, (inst, item)).fetchone()

            if not row:
                print(f"[BTG-WA] {inst}/{item} ({persona}): sin lectura previa en DB, saltando.", flush=True)
                continue

            old_value = float(row[0])
            old_dt    = _dt.datetime.fromisoformat(row[1])
            print(f"[BTG-WA] {inst}/{item}: lectura previa ${old_value:,.0f} @ {old_dt}", flush=True)

            # 2. Precio histórico en CLP (Bolsa de Comercio) en fecha de esa lectura
            price_old = _get_historical_price(ticker, old_dt)
            if not price_old:
                print(f"[BTG-WA] {inst}/{item}: no hay precio histórico para {ticker}, saltando.", flush=True)
                continue

            # 3. Precio actual en CLP (cacheado)
            if ticker not in current_prices:
                current_prices[ticker] = get_fund_price(ticker)
            price_new = current_prices[ticker]
            if not price_new:
                print(f"[BTG-WA] {inst}/{item}: no hay precio actual para {ticker}, saltando.", flush=True)
                continue

            # 4. Valor corregido: ratio CLP directo (sin ajuste FX)
            ratio     = price_new / price_old
            corrected = int(round(old_value * ratio))
            print(f"[BTG-WA] {inst}/{item} ({ticker}): "
                  f"${old_value:,.0f} × ({price_new:.2f}/{price_old:.2f} = {ratio:.5f}) "
                  f"= ${corrected:,.0f}", flush=True)

            # 5. Actualizar resultados (monto = string formateado, monto_int = entero)
            for res in resultados:
                if res.get("bank_key") == bank_key and res.get("item") == item:
                    res["monto"]     = fmt_monto(corrected)
                    res["monto_int"] = corrected
                    res["ok"]        = True
                    res["source"]    = "workaround"
                    break

            print_preliminary(inst, cat, item, fmt_monto(corrected) + " [WA]")

        except Exception as e_wa:
            print(f"[BTG-WA] Error en {inst}/{item}: {e_wa}", flush=True)


def _update_shares_price_item(inst, cat, item, moneda, bank_key):
    """
    Actualiza un ítem especial con lógica Shares × Price.
    Flujo: (1) Cuántas acciones, (2) Scrapper o Manual para precio, (3) Calcula valor.
    """
    # Obtener número de acciones anterior desde la DB
    prev_shares = None
    persona = cat_to_persona(cat)
    try:
        conn = init_db()
        row = conn.execute("""
            SELECT extra_data FROM saldos
            WHERE institucion = ? AND item = ? AND persona = ? AND ok = 1
            ORDER BY timestamp DESC LIMIT 1
        """, (inst, item, persona)).fetchone()
        if row and row[0]:
            import json
            try:
                extra = json.loads(row[0])
                prev_shares = extra.get("shares")
            except:
                pass
        conn.close()
    except:
        pass

    # 1. PREGUNTA: Cuántas acciones
    shares = None
    # Sin decimales para acciones
    shares_fmt = f"{int(prev_shares):,}".replace(",", ".") if prev_shares is not None else "—"
    
    choices = []
    if prev_shares is not None:
        choices.append(questionary.Choice(f"Mantener cantidad: {shares_fmt} acciones", value="keep"))
    choices.append(questionary.Choice("Ingresar nueva cantidad", value="new"))
    choices.append(questionary.Separator())
    choices.append(questionary.Choice("« Volver", value="back"))
    choices.append(questionary.Choice("Salir", value="exit"))

    _clear_terminal_buffer()
    action = questionary.select(
        f"¿Cuántas acciones tienes de {item}?",
        choices=choices,
        style=QUESTIONARY_STYLE,
        pointer="»",
        use_indicator=True,
        qmark=""
    ).ask(patch_stdout=True)

    if action == "exit": sys.exit(0)
    if not action or action == "back": return False

    if action == "keep":
        shares = prev_shares
    else:
        while shares is None:
            shares_input = questionary.text(f"  Cantidad de acciones para {item} (ej: 20000):").ask()
            if shares_input is None: return False
            if not shares_input.strip():
                _console.print("[yellow]Debes ingresar una cantidad.[/yellow]")
                continue
            try:
                # Limpiar por si pone puntos/comas
                clean_s = shares_input.strip().replace(".", "").replace(",", ".")
                shares = float(clean_s)
                if shares <= 0:
                    _console.print("[red]Cantidad inválida (debe ser > 0)[/red]")
                    shares = None
            except ValueError:
                _console.print("[red]Error: Ingresa un número válido[/red]")
                shares = None

    shares_disp = f"{int(shares):,}".replace(",", ".")
    _console.print(f" [green]OK: {shares_disp} acciones[/green]")

    # 2. PREGUNTA: Scrapper o Manual para precio
    price_method = questionary.select(
        f"¿Cómo obtener el precio de {item}?",
        choices=[
            questionary.Choice("Scrapper (Yahoo Finance) — automático", value="scrapper"),
            questionary.Choice("Ingresar precio manualmente", value="manual"),
            questionary.Separator(),
            questionary.Choice("« Volver", value="back"),
            questionary.Choice("Salir", value="exit"),
        ],
        style=QUESTIONARY_STYLE,
        pointer="»",
        use_indicator=True,
        qmark=""
    ).ask(patch_stdout=True)

    if price_method == "exit":
        sys.exit(0)
    if not price_method or price_method == "back":
        return False

    price = None

    # Si elige scrapper, intenta buscar en Yahoo Finance
    if price_method == "scrapper":
        ticker_map = {
            "CFIETFCD": "CFIETFCD.SN",
            "CFINASDAQ": "CFINASDAQ.SN",
            "CFISP500": "CFISP500.SN",
            "CFIETFGE": "CFIETFGE.SN"
        }
        ticker = ticker_map.get(item)

        if ticker:
            _console.print(f"[cyan]Buscando precio de {item} en Yahoo Finance...[/cyan]")
            price = get_fund_price(ticker)

            if price:
                _console.print(f"[green]OK: Precio encontrado: ${price:,.2f}[/green]")
            else:
                _console.print("[yellow]No se encontró precio. Pasando a manual.[/yellow]")
                price_method = "manual"
        else:
            price_method = "manual"

    # 3. FALLBACK: Precio manual si scrapper falló o usuario lo eligió
    if price_method == "manual" or price is None:
        price_input = questionary.text(
            f"Ingresa el precio actual de {item} (ej: 1234.56):"
        ).ask()

        if not price_input:
            return False

        try:
            price = float(price_input.strip().replace(",", "."))
            if price <= 0:
                _console.print("[red]Precio inválido (debe ser > 0)[/red]")
                return False
        except ValueError:
            _console.print("[red]Error: Ingresa un número válido[/red]")
            return False

    # 4. CALCULAR: Valor Total = Acciones × Precio
    total_value = shares * price

    _console.print(f"\n[bold cyan]Cálculo:[/bold cyan]")
    shares_fmt_calc = f"{int(shares):,}".replace(",", ".")
    total_value_fmt = f"{int(round(total_value)):,}".replace(",", ".")
    _console.print(f"  {shares_fmt_calc} acciones × ${price:,.2f} = ${total_value_fmt}\n")

    # 5. GUARDAR en DB (con extra_data para Shares × Price)
    resultados = []
    try:
        import json
        # Siempre CLP para estos items
        add_result(resultados, bank_key, inst, cat, item, str(int(total_value)), manual=True)
        print_preliminary(inst, cat, item, str(int(total_value)), manual=True)

        if resultados:
            # Agregar shares y price a extra_data
            resultados[0]["extra_data"] = json.dumps({"shares": shares, "price": price})
            save_to_db(resultados)
            _console.print(f"[bold green][OK]  '{item}' actualizado: ${total_value_fmt}[/bold green]")
            return True
    except Exception as e:
        _console.print(f"[bold red]ERROR: {e}[/bold red]")
    return False

def _quick_update_balance(inst, cat, item, moneda, bank_key):
    """Helper para actualizar el saldo de un item específico de forma rápida."""
    # 1. LÓGICA ESPECIAL: TIR semi-automático (Dorco, WBuild)
    if _is_tir_item(inst, item):
        return _update_tir_item(inst, cat, item, moneda, bank_key, interactive=True)

    # 2. LÓGICA ESPECIAL: Tickers Semiautomáticos (CFIETFCD, CFINASDAQ, CFISP500, CFIETFGE)
    if _is_shares_price_item(item, inst=inst):
        return _update_shares_price_item(inst, cat, item, moneda, bank_key)

    # 3. AUTOMÁTICO: Buscar si este ítem tiene scraper automático asociado
    # Pasada 1: match exacto por bank_key + item
    scraper_tuple = None
    for t in INSTITUTION_ITEMS:
        s_inst, s_key, s_func, s_items = t
        if s_key in TIR_KEYS: continue
        if bank_key and s_key == bank_key:
            if any(it_code == item for it_code, _ in s_items):
                scraper_tuple = t
                break
    # Pasada 2 (fallback): match por inst + item — cubre bank_key incorrecto/stale en catalog_manual
    if not scraper_tuple:
        for t in INSTITUTION_ITEMS:
            s_inst, s_key, s_func, s_items = t
            if s_key in TIR_KEYS: continue
            if s_inst == inst:
                if any(it_code == item for it_code, _ in s_items):
                    scraper_tuple = t
                    break
    # Pasada 3 (fallback tolerante): normaliza inst (sin espacios, lowercase) — cubre "Global 66" vs "Global66"
    if not scraper_tuple:
        inst_norm = inst.replace(" ", "").lower()
        for t in INSTITUTION_ITEMS:
            s_inst, s_key, s_func, s_items = t
            if s_key in TIR_KEYS: continue
            if s_inst.replace(" ", "").lower() == inst_norm:
                if any(it_code == item for it_code, _ in s_items):
                    scraper_tuple = t
                    break
    
    if scraper_tuple:
        ans = questionary.select(
            f"[{inst} - {item}] tiene sincronización automática. ¿Cómo actualizar?",
            choices=[
                questionary.Choice("Correr Scraper (Automático)", value="auto"),
                questionary.Choice("Ingresar Valor (Manual)", value="manual"),
                questionary.Separator(),
                questionary.Choice("« Volver", value="back"),
            ],
            style=QUESTIONARY_STYLE,
            pointer="»",
            qmark=""
        ).ask(patch_stdout=True)
        if not ans or ans == "back": return False
        if ans == "auto":
            try:
                bw_unlock()
                run_scraping([scraper_tuple])
            except Exception as e_scrape:
                _console.print(f"\n[bold red][ERROR] Scraping falló: {e_scrape}[/bold red]")
            finally:
                _reset_terminal()
                _clear_terminal_buffer()
            return True
        # else: continue with fallback manual flow below (e.g. choice was manual)

    # 4. FLUJO MANUAL: Pedir monto directo
    lbl = "deuda" if (cat in ("TdC", "LdC", "CH") or item in _DEBT_MANUAL_ITEMS) else "saldo"

    # Obtener último valor conocido de la DB para mostrarlo como referencia
    try:
        conn = sqlite3.connect(DB_PATH)
        row = conn.execute(
            "SELECT monto, timestamp, persona FROM saldos WHERE institucion=? AND item=? "
            "ORDER BY timestamp DESC LIMIT 1",
            (inst, item)
        ).fetchone()
        conn.close()
        prev_val = row[0] if (row and row[0] is not None) else None
        prev_ts  = row[1] if (row and row[1]) else None
        persona  = row[2] if (row and row[2]) else cat_to_persona(cat)
    except Exception:
        prev_val = None
        prev_ts  = None
        persona  = cat_to_persona(cat)

    if prev_val is not None:
        prev_fmt = f"{int(round(prev_val)):,}".replace(",", ".") if moneda != "USD" else f"{prev_val:,.2f}"
        ts_f = _fmt_ts(prev_ts) if prev_ts else "—"
        # Texto sutil: todo en gris, sin negrita, sin corchetes ni paréntesis
        prev_str = f" [dim]anterior: {prev_fmt} {moneda} {ts_f}[/dim]"
    else:
        prev_str = " [dim]sin registro previo[/dim]"

    cat_short = cat_to_short(cat, inst)
    # Header minimalista: Todo en dim (gris), Item en cyan (sin bold) para enfoque sutil
    _clear_terminal_buffer()
    _console.print(f"\n[dim]{cat_short} - {inst} - {persona} -[/dim] [cyan]{item}[/cyan]  {prev_str}", highlight=False)
    raw = questionary.text(f"  {lbl.capitalize()} en {moneda} (Enter p/ mantener, 'v' p/ volver):").ask(patch_stdout=True)
    if raw is None or (isinstance(raw, str) and raw.strip().lower() == 'v'): 
        _console.print(" [dim]Volviendo...[/dim]")
        return False 
    if not str(raw).strip():            # Enter vacío → mantener valor anterior
        if prev_val is not None:
            if moneda in ("USD", "UF"):
                prev_show = f"{prev_val}"
            else:
                prev_show = f"{int(round(prev_val))}"
            _console.print(f"[dim]  → Manteniendo valor anterior: {prev_show}[/dim]")
        return False

    resultados = []
    try:
        if moneda == "UF":
            add_result_uf(resultados, bank_key, inst, cat, item, raw, manual=True)
            print_preliminary(inst, cat, item, raw, moneda="UF", prev_monto=prev_val, last_date=prev_ts, manual=True)
        elif moneda == "USD":
            add_result_usd(resultados, bank_key, inst, cat, item, raw, manual=True)
            print_preliminary(inst, cat, item, raw, moneda="USD", prev_monto=prev_val, last_date=prev_ts, manual=True)
        else:
            add_result(resultados, bank_key, inst, cat, item, raw, manual=True)
            print_preliminary(inst, cat, item, raw, prev_monto=prev_val, last_date=prev_ts, manual=True)

        # ── Items que requieren nota de referencia ───────────────────────────
        if item in _NOTE_REQUIRED_ITEMS and resultados:
            nota_raw = questionary.text(
                "  Nota de referencia (ej: 'cuota Juan', 'arriendo May'):  "
            ).ask(patch_stdout=True)
            nota = (nota_raw or "").strip() or None
            # Forzar signo de deuda para Cuentas por pagar
            if item in _DEBT_MANUAL_ITEMS and resultados[0]["monto_int"] > 0:
                resultados[0]["monto_int"] = -resultados[0]["monto_int"]
            # Guardar nota en extra_data (JSON)
            import json as _json
            existing_extra = resultados[0].get("extra_data")
            try:
                extra = _json.loads(existing_extra) if existing_extra else {}
            except Exception:
                extra = {}
            if nota:
                extra["nota"] = nota
            resultados[0]["extra_data"] = _json.dumps(extra, ensure_ascii=False) if extra else None

        if resultados:
            save_to_db(resultados)
            _console.print(f"[bold green]OK: Saldo de '{item}' actualizado correctamente.[/bold green]")
            return True
    except Exception as e:
        _console.print(f"[bold red]ERROR al procesar el monto: {e}[/bold red]")
    return False

def parse_range_input(text, max_val):
    """Parsea inputs tipo '1, 3, 5-8' y devuelve lista de ints."""
    nums = set()
    parts = [p.strip() for p in text.replace(",", " ").split()]
    for p in parts:
        if "-" in p:
            try:
                start, end = map(int, p.split("-"))
                for i in range(start, end + 1):
                    if 1 <= i <= max_val: nums.add(i)
            except: pass
        else:
            try:
                i = int(p)
                if 1 <= i <= max_val: nums.add(i)
            except: pass
    return sorted(list(nums))

def manage_scrapers():
    """UI para configurar scrapers automáticos — activos y grupo inversiones."""
    from rich.box import SIMPLE_HEAD

    _PJ_KEYS  = {"global66_pj", "btg_pj", "fintual_pj", "fraccional_pj", "itau_pj", "scotiabank_pj"}
    _SKIP_KEYS = TIR_KEYS

    def _group(bk):
        return 1 if bk in _PJ_KEYS else 0

    def _load_cfg_rows():
        """Lee scraper_config desde Supabase; fallback SQLite."""
        try:
            rows = _read_supabase("scraper_config")
            if rows:
                bk_set = {bk for _, bk, _, _ in INSTITUTION_ITEMS}
                return sorted(
                    [r for r in rows if r["bank_key"] not in _SKIP_KEYS and r["bank_key"] in bk_set],
                    key=lambda r: (_group(r["bank_key"]), r.get("name", ""))
                )
        except Exception:
            pass
        # Fallback SQLite
        conn = init_db()
        rows = conn.execute(
            "SELECT bank_key, name, active, in_inversiones, COALESCE(in_bancarios,0) FROM scraper_config"
        ).fetchall()
        conn.close()
        return sorted(
            [{"bank_key": r[0], "name": r[1], "active": r[2],
              "in_inversiones": r[3], "in_bancarios": r[4]}
             for r in rows if r[0] not in _SKIP_KEYS],
            key=lambda r: (_group(r["bank_key"]), r["name"])
        )

    def _render_table(cfg_rows, mode):
        """Muestra tabla con estado para el modo seleccionado."""
        group_labels = {0: "Persona Natural", 1: "Persona Jurídica"}
        col_headers  = {"active": "Estado", "inversiones": "Inversiones", "bancarios": "Bancarios"}
        t = Table(box=SIMPLE_HEAD, show_lines=False, padding=(0, 1))
        t.add_column("Scraper", style="white", min_width=22)
        t.add_column(col_headers[mode], width=12, justify="center")
        current_group = -1
        for r in cfg_rows:
            bk, name = r["bank_key"], r["name"]
            g = _group(bk)
            if g != current_group:
                current_group = g
                t.add_row(f"[dim]── {group_labels[g]} ──[/dim]", "")
            if mode == "active":
                col = "[green]🟢 ON[/green]" if r["active"] else "[red]🔴 OFF[/red]"
            elif mode == "inversiones":
                col = "[cyan]★ SÍ[/cyan]" if r.get("in_inversiones") else "[dim]—[/dim]"
            else:  # bancarios
                col = "[yellow]★ SÍ[/yellow]" if r.get("in_bancarios") else "[dim]—[/dim]"
            t.add_row(name, col)
        _console.print(t)

    def _save_changes(cfg_rows, changed_map, field):
        """Escribe cambios en SQLite y sincroniza Supabase."""
        now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        conn = init_db()
        for bk, new_val in changed_map.items():
            conn.execute(f"UPDATE scraper_config SET {field}=?, updated_at=? WHERE bank_key=?",
                         (new_val, now, bk))
        conn.commit()
        try:
            all_rows = conn.execute(
                "SELECT bank_key, name, active, in_inversiones, COALESCE(in_bancarios,0) FROM scraper_config"
            ).fetchall()
            _sync_supabase("scraper_config", [
                {"bank_key": bk, "name": nm, "active": act,
                 "in_inversiones": inv, "in_bancarios": ban, "updated_at": now}
                for bk, nm, act, inv, ban in all_rows
            ])
        except Exception:
            pass
        conn.close()

    _seed_scraper_config()

    while True:
        mode = questionary.select(
            "Gestión de scrapers:",
            choices=[
                questionary.Choice("Configurar todos los automáticos", value="active"),
                questionary.Choice("Configurar solo de inversiones",    value="inversiones"),
                questionary.Choice("Configurar solo bancarios",         value="bancarios"),
                questionary.Separator(),
                questionary.Choice("« Volver",                          value="back"),
            ],
            style=QUESTIONARY_STYLE,
            pointer="»",
            qmark="",
        ).ask(patch_stdout=True)

        if not mode or mode == "back":
            _reload_inactive_keys()
            return

        cfg_rows = _load_cfg_rows()
        if not cfg_rows:
            _console.print("[yellow]No hay scrapers configurados.[/yellow]")
            input("Presiona Enter para continuar...")
            continue

        group_labels = {0: "Persona Natural", 1: "Persona Jurídica"}
        _field_map       = {"active": "active",         "inversiones": "in_inversiones", "bancarios": "in_bancarios"}
        _prompt_map      = {
            "active":      "Scrapers activos (Espacio = toggle, Enter = confirmar):",
            "inversiones": "Scrapers en 'Solo inversiones' (Espacio = toggle, Enter = confirmar):",
            "bancarios":   "Scrapers en 'Solo bancarios' (Espacio = toggle, Enter = confirmar):",
        }
        field       = _field_map[mode]
        checked_key = field
        prompt_txt  = _prompt_map[mode]

        _clear_content()
        _render_table(cfg_rows, mode)

        check_choices = []
        current_group = -1
        for r in cfg_rows:
            bk, name = r["bank_key"], r["name"]
            g = _group(bk)
            if g != current_group:
                current_group = g
                check_choices.append(questionary.Separator(f"── {group_labels[g]} ──"))
            check_choices.append(questionary.Choice(name, value=bk, checked=bool(r[checked_key])))

        selected = questionary.checkbox(
            prompt_txt, choices=check_choices, style=QUESTIONARY_STYLE, pointer="»",
        ).ask(patch_stdout=True)
        if selected is None:
            continue

        action = questionary.select(
            "", choices=[
                questionary.Choice("Guardar cambios", value="save"),
                questionary.Separator(),
                questionary.Choice("« Volver", value="back"),
            ], style=QUESTIONARY_STYLE, pointer="»", qmark="",
        ).ask(patch_stdout=True)
        if not action or action == "back":
            continue

        selected_set = set(selected)
        changed_map  = {}
        change_lines = []
        for r in cfg_rows:
            bk, name = r["bank_key"], r["name"]
            new_val = 1 if bk in selected_set else 0
            old_val = int(r.get(checked_key, 0))
            if new_val != old_val:
                changed_map[bk] = new_val
                if mode == "active":
                    change_lines.append(f"  {name}  →  {'🟢 ON' if new_val else '⏸ OFF'}")
                else:
                    change_lines.append(f"  {name}  →  {'★ SÍ' if new_val else '— NO'}")

        if changed_map:
            _save_changes(cfg_rows, changed_map, field)
            if mode == "active":
                _reload_inactive_keys()
            _console.print("\n[bold green]✓ Cambios guardados:[/bold green]")
            for c in change_lines:
                _console.print(c)
            _console.print()

        input("Presiona Enter para continuar...")


def manage_manual_records(initial_action=None):
    """Permite editar, eliminar o actualizar ítems. Muestra SIEMPRE el catálogo completo (con ceros)."""
    if initial_action == "update": action_verb = "actualizar"
    elif initial_action == "edit": action_verb = "editar"
    elif initial_action == "delete": action_verb = "eliminar"
    else: action_verb = "gestionar"

    _clear_content()

    # Renderizamos la tabla original idénticamente
    show_last_saldos(pause=False, title=f"GESTIÓN TOTAL ({action_verb.upper()})")

    if not _LAST_TABLE_MAPPING:
        _console.print("[yellow]No hay ítems para gestionar.[/yellow]")
        input("\nPresiona Enter para volver...")
        return
        
    full_catalog = _get_unified_catalog_list()
    
    def find_catalog_item(m):
        m_persona = cat_to_persona(m.get('cat', ''))
        # Strict: inst + item + cat_short + persona (distingue PN vs PJ aunque cat_short sea igual)
        for fc in full_catalog:
            if (fc['inst'] == m['inst'] and fc['item'] == m['item']
                    and cat_to_short(fc['cat']) == cat_to_short(m['cat'])
                    and cat_to_persona(fc['cat']) == m_persona):
                return fc
        # Fallback: inst + item + persona
        for fc in full_catalog:
            if fc['inst'] == m['inst'] and fc['item'] == m['item'] and cat_to_persona(fc['cat']) == m_persona:
                return fc
        # Último fallback: solo inst + item
        for fc in full_catalog:
            if fc['inst'] == m['inst'] and fc['item'] == m['item']:
                return fc
        return None

    mapped_catalog = []
    for m in _LAST_TABLE_MAPPING:
        fc = find_catalog_item(m)
        if fc:
            fc_copy = dict(fc)
            fc_copy['moneda'] = m['moneda'] # Usar la moneda de la tabla, no la por defecto db
            mapped_catalog.append(fc_copy)
        else:
            mapped_catalog.append({
                'inst': m['inst'],
                'item': m['item'],
                'cat': m['cat'],
                'moneda': m['moneda'],
                'type': 'auto',
                'bank_key': None
            })
    
    prompt_msg = f"Números (ej: 1, 3), nombre de institución, 'v' para Volver, o Enter para ver categorías:"
    num_input = questionary.text(prompt_msg, style=QUESTIONARY_STYLE).ask()

    if num_input is None or num_input.strip().lower() == 'v':
        return

    stripped = num_input.strip()

    if stripped and (stripped.isdigit() or ',' in stripped or '-' in stripped):
        # Input numérico / rango
        indices = parse_range_input(stripped, len(mapped_catalog))
        if indices:
            if initial_action == "update":
                _run_update_batch([mapped_catalog[idx-1] for idx in indices])
            else:
                for idx in indices:
                    _handle_item_action(mapped_catalog[idx-1], initial_action)
            return manage_manual_records(initial_action)

    elif stripped:
        # Búsqueda por nombre de institución (parcial, case-insensitive)
        # Soporta múltiples separadas por coma: "Itaú, Santander"
        tokens = [t.strip().lower() for t in stripped.split(",") if t.strip()]
        matched_indices = [
            i+1 for i, m in enumerate(mapped_catalog)
            if any(tok in m['inst'].lower() for tok in tokens)
        ]
        if matched_indices:
            names_found = ", ".join(sorted(set(
                mapped_catalog[i-1]['inst'] for i in matched_indices
            )))
            _console.print(f"[bold sky_blue3]→ {len(matched_indices)} ítems de:[/bold sky_blue3] {names_found}")
            if initial_action == "update":
                _run_update_batch([mapped_catalog[idx-1] for idx in matched_indices])
            else:
                for idx in matched_indices:
                    _handle_item_action(mapped_catalog[idx-1], initial_action)
            return manage_manual_records(initial_action)
        else:
            _console.print(f"[yellow]No se encontró institución '{stripped}'.[/yellow]")
            return manage_manual_records(initial_action)

    # Fallback a navegación jerárquica
    if num_input == "":
        full_catalog = _get_unified_catalog_list()
        all_cats = sorted(list(set(cat_to_short(x['cat']) for x in full_catalog if x['cat'])))
        cat_choices = [questionary.Choice(c, value=c) for c in all_cats]
        cat_choices += [questionary.Separator(), questionary.Choice("« Volver", value="back")]

        selected_cat_disp = questionary.select(
            "Selecciona la Categoría:",
            choices=cat_choices,
            style=QUESTIONARY_STYLE,
            pointer="»",
            qmark=""
        ).ask(patch_stdout=True)
        
        if not selected_cat_disp or selected_cat_disp == "back": return
        
        subset_cat = [x for x in full_catalog if cat_to_short(x['cat']) == selected_cat_disp]
        personas = sorted(list(set(cat_to_persona(x['cat']) for x in subset_cat)))
        p_choices = []
        if "PN" in personas: p_choices.append(questionary.Choice("Persona Natural (PN)", value="PN"))
        if "PJ" in personas: p_choices.append(questionary.Choice("Persona Jurídica (PJ)", value="PJ"))
        p_choices += [questionary.Separator(), questionary.Choice("« Volver", value="back")]

        persona = questionary.select(f"Persona ({selected_cat_disp}):", choices=p_choices, style=QUESTIONARY_STYLE, pointer="»", qmark="").ask(patch_stdout=True)
        if not persona or persona == "back": return manage_manual_records(initial_action)
        
        subset_pers = [x for x in subset_cat if cat_to_persona(x['cat']) == persona]
        insts = sorted(list(set(x['inst'] for x in subset_pers)))
        inst_choices = [questionary.Choice(i, value=i) for i in insts]
        inst_choices.append(questionary.Separator())
        inst_choices.append(questionary.Choice("« Volver", value="back"))

        inst = questionary.select(f"Institución:", choices=inst_choices, style=QUESTIONARY_STYLE, pointer="»", qmark="").ask(patch_stdout=True)
        if not inst or inst == "back": return manage_manual_records(initial_action)
        
        items_subset = [x for x in subset_pers if x['inst'] == inst]
        item_choices = [questionary.Choice(f"{itm['item']} [{itm['moneda']}]", value=itm) for itm in items_subset]
        item_choices.append(questionary.Separator())
        item_choices.append(questionary.Choice("« Volver", value="back"))

        selection = questionary.select(f"Selecciona el registro:", choices=item_choices, style=QUESTIONARY_STYLE, pointer="»", qmark="").ask(patch_stdout=True)
        if not selection or selection == "back": return manage_manual_records(initial_action)
        
        if initial_action == "update":
            _run_update_batch([selection])
        else:
            _handle_item_action(selection, initial_action)
        return manage_manual_records(initial_action)

    return

def _run_update_batch(items):
    """Procesa ítems seleccionados para actualizar, preguntando si correr scraper cuando aplica."""
    auto_insts_to_run = []
    manual_items = []

    for itm in items:
        # LÓGICA ESPECIAL: TIR semi-automático
        if _is_tir_item(itm['inst'], itm['item']):
            _update_tir_item(
                itm['inst'], itm['cat'], itm['item'], itm['moneda'],
                itm.get('bank_key') or itm['inst'].lower().replace(" ", "_"),
                interactive=True
            )
            continue

        scraper_tuple = None
        if itm.get('type') == "auto" or itm.get('bank_key'):
            # Buscar en INSTITUTION_ITEMS si el item específico tiene un scraper
            # CRÍTICO: usar bank_key para match cuando está disponible (evita ambigüedad PN/PJ, ej. Fintual)
            inst_norm = itm['inst'].replace(" ", "").lower()
            for t in INSTITUTION_ITEMS:
                t_inst, t_key, t_func, t_items = t
                bk_match  = bool(itm.get('bank_key')) and t_key == itm['bank_key']
                inst_match = (not itm.get('bank_key') and t_inst == itm['inst'])
                # Tolerante: normaliza espacios y case ("Global 66" == "Global66")
                inst_match_norm = t_inst.replace(" ", "").lower() == inst_norm
                if bk_match or inst_match or inst_match_norm:
                    for item_code, cat in t_items:
                        if item_code == itm['item']:
                            scraper_tuple = t
                            break
                if scraper_tuple:
                    break

        if scraper_tuple:
            ans = questionary.select(
                f"[{itm['inst']} - {itm['item']}] tiene sincronización automática. ¿Actualizar?",
                choices=[
                    questionary.Choice("Correr Scraper (Automático)", value="auto"),
                    questionary.Choice("Ingresar Valor (Manual)", value="manual"),
                    questionary.Separator(),
                    questionary.Choice("« Volver", value="back"),
                    questionary.Choice("Salir", value="exit"),
                ],
                style=QUESTIONARY_STYLE,
                pointer="»",
                qmark=""
            ).ask(patch_stdout=True)
            if ans == "exit": sys.exit(0)
            if ans == "back": continue
            if ans == "auto":
                if scraper_tuple not in auto_insts_to_run:
                    auto_insts_to_run.append(scraper_tuple)
            elif ans == "manual":
                manual_items.append(itm)
        else:
            manual_items.append(itm)

    if auto_insts_to_run:
        _console.print("\n[bold cyan]Iniciando scrapers seleccionados...[/bold cyan]")
        try:
            bw_unlock()
            run_scraping(auto_insts_to_run)
        except Exception as e_scrape:
            _console.print(f"\n[bold red][ERROR] Scraping falló: {e_scrape}[/bold red]")
        finally:
            _reset_terminal()
            _clear_terminal_buffer()

    if manual_items:
        _console.print("\n[bold cyan]Actualizando ítems manualmente...[/bold cyan]")
        for m in manual_items:
            _quick_update_balance(m['inst'], m['cat'], m['item'], m['moneda'], m.get('bank_key') or m['inst'].lower().replace(" ", "_"))

def _remove_manual_item_from_source(inst, item):
    """Si (inst, item) está en MANUAL_ITEMS hardcodeado, elimina esa línea del archivo fuente.
    Garantiza consistencia entre el código Python y el estado real del catálogo."""
    try:
        script_path = os.path.abspath(__file__)
        with open(script_path, 'r', encoding='utf-8') as f:
            lines = f.readlines()
        # Buscar y eliminar la línea dentro del bloque MANUAL_ITEMS que contenga inst e item como strings literales
        new_lines = []
        in_block = False
        removed = False
        for line in lines:
            if 'MANUAL_ITEMS = [' in line:
                in_block = True
            if in_block and not removed:
                # Verificar que la línea contenga exactamente este inst e item entre comillas
                if f'"{inst}"' in line and f'"{item}"' in line:
                    removed = True
                    continue  # Omitir esta línea → queda eliminada del código
            if in_block and line.strip() == ']':
                in_block = False
            new_lines.append(line)
        if removed:
            with open(script_path, 'w', encoding='utf-8') as f:
                f.writelines(new_lines)
    except Exception:
        pass  # Fallo silencioso: la DB tombstone igual protege


def _handle_item_action(selection, action, silent_confirm=False):
    """Lógica interna para editar/eliminar un item seleccionado."""
    item_meta = selection
    current_inst = item_meta['inst']
    current_item = item_meta['item']
    current_cat = item_meta['cat']
    current_moneda = item_meta['moneda']

    if action == "edit":
        # ... (código de edición existente)
        _console.print(f"\n[bold cyan]Editando {current_inst} - {current_item}[/bold cyan]")
        _console.print("[dim](Enter para mantener el valor actual)[/dim]\n")
        
        current_persona = cat_to_persona(current_cat)
        new_persona = questionary.select(
            f"Persona (actual: {current_persona}):",
            choices=[
                questionary.Choice("Persona Natural (PN)", value="PN"),
                questionary.Choice("Persona Jurídica (PJ)", value="PJ"),
                questionary.Choice("Mantener actual", value="same"),
                questionary.Separator(),
                questionary.Choice("« Volver", value="back"),
                questionary.Choice("Salir", value="exit"),
            ],
            style=QUESTIONARY_STYLE,
            pointer="»",
            qmark=""
        ).ask(patch_stdout=True)
        if new_persona == "exit": sys.exit(0)
        if not new_persona or new_persona == "back": return
        if new_persona == "same": new_persona = current_persona

        new_inst = (questionary.text(f"Institución [{current_inst}]:").ask() or current_inst).strip()
        
        current_display_cat = cat_to_short(current_cat, current_inst)
        new_cat = (questionary.text(f"Categoría [{current_display_cat}]:").ask() or current_display_cat).strip()
        
        # Re-formatear categoría si cambió persona
        if new_persona == "PJ" and "PJ" not in new_cat.upper():
            new_cat = f"{new_cat} PJ"
        elif new_persona == "PN" and "PJ" in new_cat.upper():
            new_cat = new_cat.replace(" PJ", "").replace(" pj", "").strip()

        new_item = (questionary.text(f"Item [{current_item}]:").ask() or current_item).strip()
        new_moneda = (questionary.text(f"Moneda [{current_moneda}]:").ask() or current_moneda).strip().upper()
        
        # bank_key dinámico
        new_key = new_inst.lower().replace(" ", "_").strip()
        
        conn = init_db()
        # Verificar si ya existe en catalog_manual para UPDATE o INSERT
        exists = conn.execute("SELECT id FROM catalog_manual WHERE institucion=? AND item=?", (current_inst, current_item)).fetchone()
        if exists:
            conn.execute("""
                UPDATE catalog_manual 
                SET institucion = ?, categoria = ?, item = ?, moneda = ?, display_name = ?, bank_key = ?
                WHERE id = ?
            """, (new_inst, new_cat, new_item, new_moneda, new_inst, new_key, exists[0]))
        else:
            conn.execute("""
                INSERT INTO catalog_manual (display_name, bank_key, institucion, categoria, item, moneda)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (new_inst, new_key, new_inst, new_cat, new_item, new_moneda))
            
        # Replicar retroactivamente los cambios de nombre / categoría en todos los registros históricos en `saldos`
        conn.execute("""
            UPDATE saldos
            SET institucion = ?, categoria = ?, item = ?, moneda = ?
            WHERE institucion = ? AND item = ?
        """, (new_inst, new_cat, new_item, new_moneda, current_inst, current_item))
            
        conn.commit()
        # Mirroring to Supabase — catálogo
        _sync_supabase("catalog_manual", [{
            "display_name": new_inst, "bank_key": new_key, "institucion": new_inst,
            "categoria": new_cat, "item": new_item, "moneda": new_moneda, "deleted": 0
        }])
        # Mirroring to Supabase — retroactive rename in saldos
        try:
            import ssl as _ssl, json as _json, urllib.parse as _up
            _url = f"{SUP_URL}/rest/v1/saldos?institucion=eq.{_up.quote(current_inst)}&item=eq.{_up.quote(current_item)}"
            _body = _json.dumps({"institucion": new_inst, "categoria": new_cat, "item": new_item, "moneda": new_moneda}).encode()
            _hdrs = {"apikey": SUP_KEY, "Authorization": f"Bearer {SUP_KEY}",
                     "Content-Type": "application/json", "Prefer": "return=minimal"}
            _req = urllib.request.Request(_url, data=_body, headers=_hdrs, method="PATCH")
            with urllib.request.urlopen(_req, context=_ssl._create_unverified_context()): pass
        except Exception as _e:
            if DEBUG: print(f"[Supabase] saldos rename PATCH error: {_e}")
        conn.close()
        _console.print(f"[bold green][OK]  Registro actualizado correctamente en el catálogo.[/bold green]")
        
        # Preguntar si se quiere actualizar el valor ahora
        if questionary.confirm("¿Deseas actualizar el saldo de este registro ahora?").ask():
            _quick_update_balance(new_inst, new_cat, new_item, new_moneda, new_key)
        
    elif action == "delete":
        if not silent_confirm:
            confirm = questionary.confirm(f"¿Estas SEGURO de eliminar '{current_inst} - {current_item}' del catálogo?").ask()
        else:
            confirm = True

        if confirm:
            conn = init_db()
            # Soft delete en DB: UPDATE si ya existe en catalog_manual, INSERT tombstone si no
            existing = conn.execute(
                "SELECT id FROM catalog_manual WHERE institucion = ? AND item = ?",
                (current_inst, current_item)
            ).fetchone()
            if existing:
                conn.execute("UPDATE catalog_manual SET deleted = 1 WHERE id = ?", (existing[0],))
            else:
                bank_key = current_inst.lower().replace(" ", "_")
                conn.execute(
                    "INSERT INTO catalog_manual (display_name, bank_key, institucion, categoria, item, moneda, deleted) VALUES (?, ?, ?, ?, ?, ?, 1)",
                    (current_inst, bank_key, current_inst, current_cat, current_item, current_moneda)
                )
            conn.commit()
            # Mirror deletion to Supabase
            _sync_supabase("catalog_manual", [{
                "institucion": current_inst, "item": current_item, "deleted": 1
            }])
            conn.close()
            # Si el ítem está hardcodeado en MANUAL_ITEMS, quitarlo del código fuente también (consistencia código ↔ tabla)
            _remove_manual_item_from_source(current_inst, current_item)
            if not silent_confirm:
                _console.print(f"[bold red]Eliminado: {current_inst} - {current_item}[/bold red]")
            _console.print("[dim]✓ El item ha sido eliminado permanentemente del catálogo.[/dim]")


# ══════════════════════════════════════════════════════════════
# MENÚ Y SELECCIÓN
# ══════════════════════════════════════════════════════════════

# Función eliminada: print_main_menu (migrada a _print_table_menu en main)

def _get_combined_institutions():
    """Genera lista unificada de instituciones (auto + manuales), fusionando por nombre y persona."""
    combined = []
    # 1. Automáticas como base
    for name, key, func, items in INSTITUTION_ITEMS:
        is_pj = any("PJ" in cat for _, cat in items)
        combined.append({
            "name": name, 
            "key": key, 
            "is_pj": is_pj, 
            "type": "auto", 
            "func": func, 
            "items": items,
            "manual_keys": []
        })
    
    # 2. Manuales puras (Hardcoded + dinámicas de DB)
    all_manual = _get_manual_items()
    for m_disp, m_key, m_inst, m_cat, m_item, m_moneda in all_manual:
        is_pj = "PJ" in m_cat
        # Buscar si ya existe la institución del mismo tipo (PJ/PN)
        existing = next((a for a in combined if a["name"] == m_inst and a["is_pj"] == is_pj), None)
        if existing:
            # Si no es exacto el key, lo agregamos a manual_keys para rastrearlo
            if m_key != existing["key"] and m_key not in existing["manual_keys"]:
                existing["manual_keys"].append(m_key)
        else:
            # Crear nueva entrada puramente manual
            combined.append({
                "name": m_inst, 
                "key": m_key, 
                "is_pj": is_pj, 
                "type": "manual",
                "manual_keys": [m_key]
            })
    
    # Ordenar: PN primero, luego PJ, alfabético dentro de cada uno
    combined.sort(key=lambda x: (x["is_pj"], x["name"]))
    return combined

def select_institutions_for_scraping():
    """Selección de instituciones con búsqueda rápida por nombre o checkbox completo."""
    combined = _get_combined_institutions()

    # Paso 1 — Búsqueda rápida por nombre
    # El usuario puede escribir uno o varios nombres separados por coma, o Enter para ver lista completa
    all_names = sorted(set(item["name"] for item in combined))
    _console.print(
        "[dim]Escribe el nombre de una o varias instituciones separadas por coma, "
        "o presiona Enter para ver la lista completa.[/dim]"
    )
    search_raw = questionary.autocomplete(
        "Institución(es):",
        choices=all_names,
        style=QUESTIONARY_STYLE,
        match_middle=True,
        validate=lambda t: True,   # siempre válido (Enter vacío = lista completa)
    ).ask()

    if search_raw is None:
        return "back"

    search_raw = search_raw.strip()

    # Si el usuario escribió algo, intentar match
    if search_raw:
        # Soporta múltiples nombres separados por coma: "Itaú, Santander"
        tokens = [t.strip().lower() for t in search_raw.split(",") if t.strip()]
        matched = [
            item for item in combined
            if any(tok in item["name"].lower() for tok in tokens)
        ]
        if matched:
            names_found = ", ".join(sorted(set(m["name"] for m in matched)))
            _console.print(f"[bold sky_blue3]→ Seleccionadas:[/bold sky_blue3] {names_found}")
            autos   = [m for m in matched if m["type"] == "auto"]
            manuals = [m for m in matched if m["type"] == "manual"]
            return {"autos": autos, "manuals": manuals}
        else:
            _console.print(f"[yellow]No se encontró '{search_raw}'. Mostrando lista completa...[/yellow]")

    # Paso 2 — Checkbox completo (fallback o Enter vacío)
    choices = []
    pn_header = False
    pj_header = False

    for item in combined:
        if not item["is_pj"] and not pn_header:
            choices.append(questionary.Separator("—— Persona Natural ——"))
            pn_header = True
        elif item["is_pj"] and not pj_header:
            choices.append(questionary.Separator("—— Persona Jurídica ——"))
            pj_header = True
        choices.append(questionary.Choice(item["name"], value=item))

    choices.append(questionary.Separator())
    choices.append(questionary.Choice("« Volver", value="back"))

    selected = questionary.checkbox(
        "Selecciona las instituciones (Espacio para marcar/desmarcar):",
        choices=choices,
        style=QUESTIONARY_STYLE,
        pointer="»"
    ).ask(patch_stdout=True)

    if not selected:
        return []
    if "back" in selected: return "back"

    real_selected = [s for s in selected if isinstance(s, dict)]
    autos   = [s for s in real_selected if s["type"] == "auto"]
    manuals = [s for s in real_selected if s["type"] == "manual"]
    return {"autos": autos, "manuals": manuals}

def select_items_for_update():
    """Muestra lista de checkboxes para actualizar ítems."""
    full_catalog = _get_unified_catalog_list()
    if not full_catalog:
        _console.print("[yellow]No hay ítems registrados.[/yellow]")
        return None

    full_catalog.sort(key=lambda x: (cat_to_short(x['cat']), x['inst'], x['item']))
    
    choices = []
    for itm in full_catalog:
        label = f"{cat_to_short(itm['cat'])} | {itm['inst']} - {itm['item']} [{itm['moneda']}]"
        choices.append(questionary.Choice(label, value=itm))
    choices.append(questionary.Separator())
    choices.append(questionary.Choice("« Volver", value="back"))
    choices.append(questionary.Choice("Salir", value="exit"))

    selected = questionary.checkbox(
        "Selecciona qué ítems actualizar:",
        choices=choices,
        style=QUESTIONARY_STYLE,
        pointer="»"
    ).ask(patch_stdout=True)

    if not selected or "back" in selected:
        return "back"
    if "exit" in selected:
        sys.exit(0)

    return [s for s in selected if isinstance(s, dict)]


# ══════════════════════════════════════════════════════════════
# SISTEMA DE CAJA NEGRA (BLACK BOX) PARA DEBUGGING
# Guarda screenshots, HTML, video y logs cuando hay errores
# ══════════════════════════════════════════════════════════════

def save_error_debug(page, banco_name, error_type="exception"):
    """
    Guarda información de debugging cuando ocurre un error.
    - Screenshot: backups/error_debug/[banco]_[timestamp].png
    - HTML: backups/error_debug/[banco]_[timestamp].html
    - Console logs: se imprimen en log
    """
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    debug_dir = Path("backups/error_debug")
    debug_dir.mkdir(parents=True, exist_ok=True)

    try:
        # Screenshot
        screenshot_path = debug_dir / f"{banco_name}_{ts}.png"
        page.screenshot(path=str(screenshot_path))
        logger.info(f"📸 Screenshot guardado: {screenshot_path}")

        # HTML dump
        html_path = debug_dir / f"{banco_name}_{ts}.html"
        html_content = page.content()
        with open(html_path, "w", encoding="utf-8") as f:
            f.write(html_content)
        logger.info(f"📄 HTML guardado: {html_path}")

    except Exception as e:
        logger.error(f"WARNING:   No se pudo guardar debug info: {e}")

def setup_console_logging(page, banco_name):
    """
    Configura captura de errores de JavaScript de la página.
    Los errores se guardan en una lista y se muestran en consola.
    """
    js_errors = []

    def on_console_msg(msg):
        if msg.type in ("error", "warning"):
            logger.warning(f"[{banco_name} JS] {msg.text}")
            js_errors.append((msg.type, msg.text))

    def on_page_error(err):
        logger.error(f"[{banco_name} PAGE ERROR] {err}")
        js_errors.append(("page_error", str(err)))

    page.on("console", on_console_msg)
    page.on("pageerror", on_page_error)

    return js_errors

# ══════════════════════════════════════════════════════════════
# LOG DE EJECUCIONES
# ══════════════════════════════════════════════════════════════

def save_execution_log(resultados, bank_errors=None):
    """
    Guarda un resumen detallado en backups/ejecucion_YYYY-MM-DD_HH-MM.txt
    Incluye: resultados por banco, errores, entradas manuales, totales por categoría.
    bank_errors: dict {inst_name: error_message} para los bancos que fallaron.
    """
    ts_now = datetime.datetime.now()
    ts_str = ts_now.strftime("%Y-%m-%d %H:%M")
    fname  = ts_now.strftime("ejecucion_%Y-%m-%d_%H-%M.txt")
    base   = os.path.dirname(os.path.abspath(__file__))
    backups_dir = os.path.join(base, "backups")
    os.makedirs(backups_dir, exist_ok=True)
    path = os.path.join(backups_dir, fname)
    if bank_errors is None:
        bank_errors = {}

    ok_items  = [r for r in resultados if r["ok"]]
    err_items = [r for r in resultados if not r["ok"]]

    lines = [
        f"╔══════════════════════════════════════════════════════╗",
        f"║  EJECUCIÓN SALDOS  {ts_str:<34}║",
        f"╚══════════════════════════════════════════════════════╝",
        "",
    ]

    # ── Resultados por banco ──────────────────────────────────
    lines.append("RESULTADOS POR BANCO:")
    lines.append("─" * 60)
    bancos_en_orden = list(dict.fromkeys(r["inst"] for r in resultados))  # orden de ejecución
    for banco in bancos_en_orden:
        items_banco = [r for r in resultados if r["inst"] == banco]
        ok_count  = sum(1 for r in items_banco if r["ok"])
        err_count = len(items_banco) - ok_count
        status = "[OK] " if err_count == 0 else ("WARNING:  " if ok_count > 0 else "[ERROR] ")
        lines.append(f"\n  {status} {banco}")
        for r in items_banco:
            if r["ok"]:
                monto_disp = r["monto"] if r["monto"] else "0"
                manual_tag = " [MANUAL]" if r.get("manual") else ""
                lines.append(f"       [OK]   {r['cat']:<8} {r['item']:<12}  CLP  {monto_disp:>15}{manual_tag}")
            else:
                err_msg = (bank_errors or {}).get(banco, r.get("error_msg", "sin detalle"))
                # Truncar errores muy largos
                if len(str(err_msg)) > 120:
                    err_msg = str(err_msg)[:120] + "..."
                lines.append(f"       [ERROR]   {r['cat']:<8} {r['item']:<12}  No obtenido")
                lines.append(f"           Error: {err_msg}")

    lines.append("")
    lines.append("─" * 60)

    # ── Totales por categoría ────────────────────────────────
    lines.append("\nTOTALES POR CATEGORÍA:")
    UF_CATS = {"CH"}   # categorías en UF — no mezclar con CLP
    for cat in CAT_ORDER:
        items_cat = [r for r in resultados if r["cat"] == cat and r["ok"] and r.get("monto_int") is not None]
        if items_cat:
            total = sum(r["monto_int"] for r in items_cat)
            if cat in UF_CATS:
                total_fmt = f"{round(total):,}".replace(",", ".")
                lines.append(f"  {cat:<8}  UF   {total_fmt:>18}")
            else:
                lines.append(f"  {cat:<8}  CLP  {fmt_monto(int(round(total))):>18}")

    # Gran total solo en CLP (excluye categorías UF)
    all_ok_clp = [r for r in resultados if r["ok"] and r.get("monto_int") is not None and r["cat"] not in UF_CATS]
    if all_ok_clp:
        gran_total = sum(r["monto_int"] for r in all_ok_clp)
        lines.append(f"  {'TOTAL':<8}  CLP  {fmt_monto(int(round(gran_total))):>18}")

    lines.append("")

    # ── Resumen de fallidos ──────────────────────────────────
    if err_items:
        failed_banks = list(dict.fromkeys(r["inst"] for r in err_items))
        lines.append(f"FALLIDOS ({len(err_items)} item(s) en {len(failed_banks)} banco(s)):")
        for bank in failed_banks:
            items_fail = [r["item"] for r in err_items if r["inst"] == bank]
            lines.append(f"  • {bank}: {', '.join(items_fail)}")
    else:
        lines.append("FALLIDOS: ninguno [OK] ")

    lines.append("")
    lines.append(f"Items OK:     {len(ok_items)}/{len(resultados)}")
    lines.append(f"Items error:  {len(err_items)}/{len(resultados)}")
    lines.append("")

    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
    except Exception as e:
        _console.print(f"  [dim]WARNING:   No se pudo guardar log de ejecución: {e}[/dim]")


# ══════════════════════════════════════════════════════════════
# OPERACIONES
# ══════════════════════════════════════════════════════════════

def run_scraping(selected, full_update=False):
    """selected: lista de entradas de INSTITUTION_ITEMS."""
    global _PAGOS_ERRORS
    _PAGOS_ERRORS = []  # limpiar errores de pagos de corridas anteriores

    # Recargar estado de scrapers desde DB antes de filtrar
    _seed_scraper_config()
    _reload_inactive_keys()

    # Filtrar scrapers inactivos
    inactive_skipped = [b for b in selected if b[1] in INACTIVE_KEYS]
    selected         = [b for b in selected if b[1] not in INACTIVE_KEYS]

    # Separar TIR (sin browser) de los que necesitan browser
    tir_selected     = [b for b in selected if b[1] in TIR_KEYS]
    browser_selected = [b for b in selected if b[1] not in TIR_KEYS]

    captcha_first   = [b for b in browser_selected if b[1] in CAPTCHA_KEYS]
    rest            = [b for b in browser_selected if b[1] not in CAPTCHA_KEYS]
    execution_order = captcha_first + rest

    all_names = [n for n,_,_,_ in execution_order] + [n for n,_,_,_ in tir_selected]
    print(f"\n→ Consultando: {', '.join(all_names)}\n")

    with sync_playwright() as p:
        # Determinar modo headless según argumentos
        headless_mode = False  # Por defecto: siempre con ventanas visibles
        if args.headless:
            headless_mode = True
            logger.info(" Modo headless: navegador oculto")

        browser = p.chromium.launch(
            channel="chrome",
            headless=headless_mode,
            args=["--disable-blink-features=AutomationControlled"],
            ignore_default_args=["--enable-automation"],
        )

        # Configurar recording de video solo en modo --debug (y sin --no-video)
        context_options = {"viewport": {"width": 1280, "height": 800}}
        if DEBUG and not args.no_video:
            video_dir = Path("backups/error_debug/videos")
            video_dir.mkdir(parents=True, exist_ok=True)
            context_options["record_video_dir"] = str(video_dir)
            logger.info(f"🎥 Video recording habilitado en: {video_dir}")

        context = browser.new_context(**context_options)
        # Anti-detección global: aplica a TODAS las páginas del contexto
        context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
        
        # --- Lógica No-Focus para macOS ---
        if not headless_mode and sys.platform == "darwin":
            # Devolver el foco a la Terminal después de que el browser se abra
            # E intentar ocultar el navegador para que no moleste
            try:
                subprocess.run(["osascript", "-e", 'tell application "Terminal" to activate'], 
                              capture_output=True, timeout=2)
                # Intentar "esconder" Google Chrome (o el canal usado)
                # Esto lo hace menos molesto pero deja que Playwright trabaje
                subprocess.run(["osascript", "-e", 'tell application "System Events" to set visible of process "Google Chrome" to false'], 
                              capture_output=True, timeout=2)
            except: pass

        resultados = []
        failed = []
        saved_keys = set()   # bank_keys ya guardados en DB — evita re-guardar al final

        def _save_new(bank_key, prev_len):
            """Guarda solo los resultados nuevos del scraper bank_key (slice desde prev_len)."""
            new_items = [r for r in resultados[prev_len:] if r["bank_key"] == bank_key]
            if new_items:
                save_to_db(new_items)
                saved_keys.add(bank_key)

        try:
            for name, key, func, _ in execution_order:
                print(f"\nConsultando {name}...", flush=True)
                prev_len = len(resultados)
                try:
                    ok = func(context, resultados)
                except Exception as e_scraper:
                    print(f"  [ERROR] {name} lanzó excepción: {e_scraper}", flush=True)
                    ok = False
                if ok:
                    _save_new(key, prev_len)   # ← guardar inmediatamente
                else:
                    failed.append((name, key, func, _))

            # Auto-reintento único (si falló en headless, reintenta con ventana visible)
            if failed:
                print(f"\n{'─'*46}")
                print(f"⟳  Reintentando {len(failed)} banco(s) fallido(s):")
                for name, _, _, _ in failed:
                    print(f"  • {name}")

                # Si estábamos en headless, cerramos y reabrimos con ventana
                if headless_mode:
                    print("\n[REINTENTO] Cambiando a modo visible para logs/MFA...")
                    context.close()
                    browser.close()
                    browser = p.chromium.launch(
                        channel="chrome", headless=False,
                        args=["--disable-blink-features=AutomationControlled"],
                        ignore_default_args=["--enable-automation"]
                    )
                    context = browser.new_context(**context_options)
                    context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
                    # No-focus de nuevo
                    if sys.platform == "darwin":
                        try: subprocess.run(["osascript", "-e", 'tell application "Terminal" to activate'], capture_output=True, timeout=2)
                        except: pass

                print()
                still_failed = []
                for name, key, func, items in failed:
                    # Limpiar items del banco para evitar duplicados en retry parcial
                    resultados = [r for r in resultados if r["bank_key"] != key]
                    saved_keys.discard(key)   # ya no está guardado (lo limpiamos)
                    print(f"\nReintentando {name}...", flush=True)
                    prev_len = len(resultados)
                    try:
                        ok = func(context, resultados)
                    except Exception as e_retry:
                        print(f"  [ERROR] {name} falló en reintento: {e_retry}", flush=True)
                        ok = False
                    if ok:
                        _save_new(key, prev_len)   # ← guardar inmediatamente en retry
                    else:
                        still_failed.append((name, key, func, items))
                failed = still_failed

            # ── Retry de componentes fallidos en bancos que retornaron True ──────────
            # (ej: Santander CC ok pero LdC guardó ok=False — el banco no entró al loop failed)
            bank_level_retried_keys = {key for _, key, _, _ in failed}
            comp_error_keys = (
                {r["bank_key"] for r in resultados if not r["ok"]}
                - bank_level_retried_keys
            )
            if comp_error_keys:
                key_to_entry = {k: (n, f, it) for n, k, f, it in execution_order}
                print(f"\n{'─'*46}")
                print(f"⟳  Reintentando componentes fallidos en: {', '.join(comp_error_keys)}")
                for bkey in sorted(comp_error_keys):
                    if bkey not in key_to_entry:
                        continue
                    bname, bfunc, _ = key_to_entry[bkey]
                    resultados = [r for r in resultados if r["bank_key"] != bkey]
                    saved_keys.discard(bkey)
                    print(f"\nReintentando {bname} (componente)...", flush=True)
                    prev_len = len(resultados)
                    try:
                        bfunc(context, resultados)
                        _save_new(bkey, prev_len)   # ← guardar componente reintentado
                    except Exception as e_comp:
                        print(f"  [ERROR] {bname} componente retry: {e_comp}", flush=True)

            # ── BTG workaround de precio (si aún hay ítems BTG fallidos) ───────
            _apply_btg_price_workaround(resultados)
            # Guardar BTG si fue corregido por workaround y no está ya guardado
            for bkey in ("btg", "btg_pj"):
                if bkey not in saved_keys:
                    new_btg = [r for r in resultados if r["bank_key"] == bkey]
                    if new_btg:
                        save_to_db(new_btg)
                        saved_keys.add(bkey)

        finally:
            # SIEMPRE cerrar browser/context, incluso si hubo excepción
            try: context.close()
            except: pass
            try: browser.close()
            except: pass

    # ── Restaurar terminal INMEDIATAMENTE después de cerrar browser ────────
    _reset_terminal()
    _clear_terminal_buffer()

    # ── TIR Semi-automáticos — corren DESPUÉS del browser ──────────────────
    if tir_selected:
        for name, key, func, _ in tir_selected:
            prev_len = len(resultados)
            try:
                func(None, resultados)
                _save_new(key, prev_len)   # ← guardar TIR inmediatamente
            except Exception as e_tir:
                print(f"  [ERROR] TIR {name}: {e_tir}", flush=True)

    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")

    # ── Ofrecer entrada manual para items que fallaron ─────────
    still_errors = [r for r in resultados if not r["ok"]]

    if still_errors:
        if AUTOMATED:
            _console.print(f"\n[yellow]WARNING:   {len(still_errors)} item(s) sin obtener (modo automatizado, saltando manual):[/yellow]")
            for r in still_errors[:5]:  # Mostrar primeros 5
                _console.print(f"  • [bold]{r['inst']}[/bold] / {r['item']}")
            if len(still_errors) > 5:
                _console.print(f"  ... y {len(still_errors)-5} más")
        else:
            _reset_terminal()  # Refuerzo antes de questionary interactivo
            prompt_failed_items(still_errors, resultados)

    # Guardar solo lo que no fue guardado aún (entradas manuales de prompt_failed_items,
    # items de bancos que fallaron ambos intentos, etc.)
    unsaved = [r for r in resultados if r["bank_key"] not in saved_keys]
    if unsaved:
        save_to_db(unsaved)
    save_execution_log(resultados)

    # ACTUALIZAR ITEMS SEMIAUTOMÁTICOS: Mantener acciones, buscar precio automático
    # Corre si: (a) fue un "Actualizar todo", o (b) se seleccionó Itaú CdB específicamente
    selected_keys = {key for _, key, _, _ in selected}
    if full_update or "itau_pj" in selected_keys:
        _update_semiautomatics_auto()

    # ── Resumen final: mostrar items que fallaron tras todos los reintentos ─────
    final_errors = [r for r in resultados if not r["ok"]]
    if final_errors:
        failed_str = ", ".join(r["inst"] + "/" + r["item"] for r in final_errors)
        _console.print(f"\n  [green][OK]  Datos guardados.[/green]  [yellow]Sin datos: {failed_str}[/yellow]")
    else:
        _console.print("\n  [green][OK]  Datos guardados.[/green]")

    # Avisar scrapers inactivos solo en full update
    if full_update:
        all_inactive = [b for b in INSTITUTION_ITEMS if b[1] in INACTIVE_KEYS]
        if all_inactive:
            names = ", ".join(f"[yellow]{n}[/yellow]" for n, _, _, _ in all_inactive)
            _console.print(f"  [dim]⏸  Scrapers inactivos (usan último valor conocido): {names}[/dim]\n")
        else:
            _console.print()
    else:
        _console.print()


def _update_semiautomatics_auto():
    """
    Actualiza automáticamente items Semiautomáticos (CFIETFCD, CFINASDAQ) después del scraping.
    Mantiene: número de acciones (de BD)
    Busca automáticamente: precio en Yahoo Finance
    Guarda: nuevo valor total = acciones × precio
    """
    items_to_update = [
        ("CFIETFCD", "Itaú PJ", "Inversiones Líquidas PJ", "CLP", "itau_pj"),
        ("CFINASDAQ", "Itaú PJ", "Inversiones Líquidas PJ", "CLP", "itau_pj"),
    ]

    updated_count = 0
    for item, inst, cat, moneda, bank_key in items_to_update:
        try:
            # 1. Recuperar shares de BD (última actualización)
            conn = init_db()
            row = conn.execute("""
                SELECT extra_data FROM saldos
                WHERE institucion = ? AND item = ? AND ok = 1
                ORDER BY timestamp DESC LIMIT 1
            """, (inst, item)).fetchone()
            conn.close()

            shares = None
            if row and row[0]:
                try:
                    import json
                    extra = json.loads(row[0])
                    shares = extra.get("shares")
                except:
                    pass

            if not shares:
                continue  # Skip si no hay shares registradas

            # 2. Buscar precio en Yahoo Finance
            ticker_map = {"CFIETFCD": "CFIETFCD.SN", "CFINASDAQ": "CFINASDAQ.SN"}
            ticker = ticker_map.get(item)
            if not ticker:
                continue

            price = get_fund_price(ticker)
            if not price:
                continue  # Skip si no se encuentra precio

            # 3. Calcular valor total y guardar
            total_value = shares * price
            resultados = []
            add_result(resultados, bank_key, inst, cat, item, str(int(total_value)), manual=False)

            if resultados:
                import json
                resultados[0]["extra_data"] = json.dumps({"shares": shares, "price": price})
                save_to_db(resultados)
                _console.print(f"  [cyan][SYNC]  {item}: {shares:,} acciones × ${price:,.2f} = ${total_value:,.2f}[/cyan]")
                updated_count += 1

        except Exception as e:
            if DEBUG:
                _console.print(f"  [dim]WARNING:   Error actualizando {item}: {e}[/dim]")

    if updated_count > 0:
        _console.print(f"\n  [cyan][OK]  {updated_count} item(s) Semiautomático(s) actualizado(s)[/cyan]")


def manual_entry():
    """Registro manual de saldos interactivo."""
    combined = _get_combined_institutions()
    
    choices = []
    pn_header = False
    pj_header = False
    
    for item in combined:
        if not item["is_pj"] and not pn_header:
            choices.append(questionary.Separator("—— Persona Natural ——"))
            pn_header = True
        elif item["is_pj"] and not pj_header:
            choices.append(questionary.Separator("—— Persona Jurídica ——"))
            pj_header = True
        choices.append(questionary.Choice(item["name"], value=item))

    choices.append(questionary.Separator())
    choices.append(questionary.Choice("« Volver", value="back"))
    choices.append(questionary.Choice("Salir", value="exit"))

    selected_targets = questionary.checkbox(
        "Selecciona las instituciones:",
        choices=choices,
        style=QUESTIONARY_STYLE,
        pointer="»"
    ).ask(patch_stdout=True)

    if not selected_targets: return
    if "exit" in selected_targets: sys.exit(0)
    if "back" in selected_targets: return

    # Iterar sobre cada institución seleccionada
    for target in selected_targets:
        if not isinstance(target, dict): continue # Saltamos back/exit si se colaron
        
        name = target["name"]
        key = target["key"]
        all_manual = _get_manual_items()
        
        # Obtener items unificados
        items_to_process = []
        
        # 1. Si tiene parte auto
        if target.get("type") == "auto":
            for itm_name, cat in target["items"]:
                items_to_process.append({
                    "item": itm_name, 
                    "cat": cat, 
                    "moneda": "UF" if cat == "CH" else "CLP",
                    "bank_key": key
                })
        
        # 2. Si tiene items manuales asociados
        m_keys = [key] if target["type"] == "manual" else target.get("manual_keys", [])
        for m_disp, m_key, m_inst, m_cat, m_item, m_moneda in all_manual:
            if m_key in m_keys or (target["type"] == "auto" and m_inst == name and ("PJ" in m_cat) == target["is_pj"]):
                # Evitar duplicados por nombre de item
                if not any(x["item"] == m_item for x in items_to_process):
                    items_to_process.append({
                        "item": m_item, 
                        "cat": m_cat, 
                        "moneda": m_moneda,
                        "bank_key": m_key
                    })

        # Seleccionar items o todos
        _console.print(f"\n[bold sky_blue3]───── {name} ({target['persona']}) ─────[/bold sky_blue3]")
        item_choices = [questionary.Choice(f"Todos ({len(items_to_process)})", value="all")]
        for i, itm in enumerate(items_to_process):
            item_choices.append(questionary.Choice(f"{itm['item']} ({itm['cat']})", value=i))
        
        item_choices.append(questionary.Separator())
        item_choices.append(questionary.Choice("Omitir institución", value="skip"))
        item_choices.append(questionary.Choice("« Volver", value="back"))
        item_choices.append(questionary.Choice("Salir", value="exit"))

        selected_idxs = questionary.checkbox(
            f"Selecciona items de {name}:",
            choices=item_choices,
            style=QUESTIONARY_STYLE,
            pointer="»"
        ).ask(patch_stdout=True)

        if selected_idxs is None or "skip" in selected_idxs: continue
        if "exit" in selected_idxs: sys.exit(0)
        if "back" in selected_idxs: return
        
        if "all" in selected_idxs:
            to_enter = items_to_process
        else:
            to_enter = [items_to_process[i] for i in selected_idxs if i != "all"]
        
        resultados_inst = []
        for itm in to_enter:
            cat = itm["cat"]
            item_code = itm["item"]
            moneda = itm["moneda"]
            b_key = itm["bank_key"]

            # LÓGICA ESPECIAL: Si es CFIETFCD o CFINASDAQ de Itaú CdB, usa shares × price
            if _is_shares_price_item(item_code, inst=name):
                _update_shares_price_item(name, cat, item_code, moneda, b_key)
                continue

            # FLUJO NORMAL: Pedir monto directo
            lbl = "deuda" if cat in ("TdC", "LdC", "CH") else "saldo"
            raw = questionary.text(f"    {item_code} — {lbl} ({moneda}):").ask()
            if not raw: continue

            try:
                if moneda == "UF":
                    add_result_uf(resultados_inst, b_key, name, cat, item_code, raw, manual=True)
                    print_preliminary(name, cat, item_code, raw, moneda="UF")
                elif moneda == "USD":
                    add_result_usd(resultados_inst, b_key, name, cat, item_code, raw, manual=True)
                    print_preliminary(name, cat, item_code, raw, moneda="USD")
                else:
                    add_result(resultados_inst, b_key, name, cat, item_code, raw, manual=True)
                    print_preliminary(name, cat, item_code, raw)
            except:
                print("      WARNING:   Monto inválido.")

        if resultados_inst:
            save_to_db(resultados_inst)
            _console.print(f"  [green][OK]  {name} guardado.[/green]")

    _console.print("\n[bold green]Finalizado el ingreso manual.[/bold green]")




def show_historical_evolution():
    """Muestra la evolución de los balances en múltiples fechas (estilo comparador)."""
    from rich.table import Table
    from rich.box import HEAVY_HEAD
    from rich.panel import Panel
    from rich.text import Text
    from rich.rule import Rule

    dates_list = get_available_snapshot_dates(limit=5000)
    if not dates_list:
        _console.print("[yellow]No hay datos históricos disponibles.[/yellow]")
        return

    dates_sorted = sorted(dates_list)
    
    with _console.status("[bold blue]Consultando historia...[/]"):
        consolidated = {}
        for d in dates_sorted:
            consolidated[d] = {}
            db_data = _build_db_data_ok_internal(d)
            for val in db_data.values():
                raw_c, _, mon, mont, _, _ = val
                cp = cat_to_persona(raw_c)
                if cp not in consolidated[d]:
                    consolidated[d][cp] = {"clp_mm": 0.0, "usd": 0.0, "uf": 0.0}
                c_mm, u, f, _, _ = convert_to_all(float(mont or 0), mon, d)
                consolidated[d][cp]["clp_mm"] += c_mm
                consolidated[d][cp]["usd"] += u
                consolidated[d][cp]["uf"] += f

        cat_order_present = []
        for c in CAT_ORDER:
            cp = cat_to_persona(c)
            if any(abs(consolidated[d].get(cp, {}).get("clp_mm", 0)) > 0.01 for d in dates_sorted):
                cat_order_present.append(c)

    # Helpers de formato
    def fv(v, decimals=0, style=None):
        if abs(v or 0) < 0.001: return Text("-", style="dim")
        if decimals == 0:
            res = f"{int(round(v)):,}".replace(",", ".")
        else:
            res = f"{v:,.{decimals}f}".replace(",", "X").replace(".", ",").replace("X", ".")
        return Text(res, style=style or "white")

    def print_section(unit_key, label, color):
        decimals = 1 if unit_key == "usd" else 0
        table = Table(
            box=HEAVY_HEAD, 
            header_style=f"bold {color}",
            border_style="grey37",
            row_styles=["", "on grey11"],
            expand=True
        )
        
        table.add_column("📆 FECHA", style="cyan", no_wrap=True)
        for cat in cat_order_present:
            sn = cat.replace("Inversiones", "Inv.").replace("Cripto", "Cryp.").replace("Casa", "Casa")
            table.add_column(sn, justify="right", style="grey78")
        
        table.add_column("📊 PATRI.", justify="right", style="bold orange1")
        table.add_column("💰 TOTAL", justify="right", style=f"bold {color}")

        for d in dates_sorted:
            d_fmt = datetime.datetime.strptime(d, "%Y-%m-%d").strftime("%d/%m/%y")
            row = [d_fmt]
            t_tot = 0.0
            t_pat = 0.0
            
            for cat in cat_order_present:
                cp = cat_to_persona(cat)
                val = consolidated[d].get(cp, {}).get(unit_key, 0.0)
                row.append(fv(val, decimals))
                t_tot += val
                if "Casa" not in cat:
                    t_pat += val
            
            row.append(fv(t_pat, decimals, style="bold orange1"))
            row.append(fv(t_tot, decimals, style=f"bold {color}"))
            table.add_row(*row)

        _console.print(Panel(table, title=f"[bold {color}]EVOLUCIÓN PATRIMONIAL ({label})[/]", border_style=color, padding=(0,1)))

    _clear_content()
    _console.print(Rule(style="dim blue"))
    print_section("clp_mm", "CLP MM", "sky_blue3")
    print_section("usd", "USD M", "spring_green3")
    print_section("uf", "UF", "orange3")

    _console.print("\n")
    questionary.press_any_key_to_continue("Presiona cualquier tecla para volver...").ask()



def _build_db_data_ok_internal(target_date):
    """Snapshot histórico por fecha. Fuente primaria: Supabase RPC get_snapshot."""
    result = {}
    ts_filter_str = f"{target_date} 23:59:59"
    try:
        rows_raw = _sup_rpc("get_snapshot", {"ts_filter": ts_filter_str})
        for r in rows_raw:
            result[(r["institucion"], r["item"], r["persona"])] = (
                r["categoria"], r["persona"], r["moneda"], r["monto"], r["ts"], r.get("source", "auto")
            )
        return result
    except Exception:
        pass
    # Fallback: SQLite local
    db_path = _db_path()
    if not os.path.exists(db_path): return result
    conn = init_db()
    try:
        rows_raw = conn.execute(f"""
            SELECT s.institucion, s.categoria, s.persona, s.item,
                   s.moneda, s.monto, s.timestamp,
                   COALESCE(s.source, 'auto') AS source
            FROM saldos s
            INNER JOIN (
                SELECT institucion, item, persona, MAX(timestamp) AS max_ts
                FROM saldos WHERE ok = 1 AND timestamp <= '{ts_filter_str}'
                GROUP BY institucion, item, persona
            ) latest ON s.institucion = latest.institucion
                     AND s.item = latest.item
                     AND s.persona = latest.persona
                     AND s.timestamp = latest.max_ts
            WHERE s.ok = 1
        """).fetchall()
        for inst, cat_raw, persona_raw, item, moneda, monto_int, ts, source in rows_raw:
            result[(inst, item, persona_raw)] = (cat_raw, persona_raw, moneda, monto_int, ts, source)
    except Exception: pass
    finally: conn.close()
    return result


def _show_highlights_vs_prev():
    """Genera y muestra el análisis IA comparando los dos últimos snapshots."""
    from datetime import datetime
    from rich.padding import Padding

    dates = get_available_snapshot_dates(limit=5000)
    if len(dates) < 2:
        return  # Sin datos suficientes, silencioso

    newest_d = dates[0]
    prev_d   = dates[1]

    try:
        lbl_new  = datetime.strptime(newest_d, "%Y-%m-%d").strftime("%d %b %Y")
        lbl_prev = datetime.strptime(prev_d,   "%Y-%m-%d").strftime("%d %b %Y")
    except:
        lbl_new, lbl_prev = newest_d, prev_d

    with _console.status("[bold blue]  Generando highlights...[/]"):
        db_ok_a = _build_db_data_ok_internal(prev_d)
        db_ok_b = _build_db_data_ok_internal(newest_d)
        rates_a = get_rates(prev_d)
        rates_b = get_rates(newest_d)

        usd_a, uf_a = rates_a.get("USD", 0), rates_a.get("UF", 0)
        usd_b, uf_b = rates_b.get("USD", 0), rates_b.get("UF", 0)

        # Construir cat_data (igual que show_comparison)
        all_catalog   = _get_unified_catalog_list()
        catalog_keys  = {(m['inst'], m['item'], cat_to_persona(m['cat'])) for m in all_catalog}
        unique_keys   = (set(db_ok_a.keys()) | set(db_ok_b.keys())) & catalog_keys
        cat_data      = {}

        for key in unique_keys:
            db_inst, db_item, db_pers = key
            va = db_ok_a.get(key)
            vb = db_ok_b.get(key)
            clp_a, usd_ma, uf_ia = (0.0, 0.0, 0.0)
            clp_b, usd_mb, uf_ib = (0.0, 0.0, 0.0)
            cat_a = cat_b = None
            if va:
                cat_a, _, mon_a, monto_a, _, _ = va
                clp_a, usd_ma, uf_ia, _, _ = convert_to_all(monto_a, mon_a)
            if vb:
                cat_b, _, mon_b, monto_b, _, _ = vb
                clp_b, usd_mb, uf_ib, _, _ = convert_to_all(monto_b, mon_b)
            cat_short = cat_to_short(cat_b or cat_a or "Otros", db_inst, db_item)
            if cat_short not in cat_data:
                cat_data[cat_short] = {"clp_a": 0.0, "clp_b": 0.0, "usd_a": 0.0, "usd_b": 0.0, "uf_a": 0.0, "uf_b": 0.0}
            cat_data[cat_short]["clp_a"] += clp_a; cat_data[cat_short]["clp_b"] += clp_b
            cat_data[cat_short]["usd_a"] += usd_ma; cat_data[cat_short]["usd_b"] += usd_mb
            cat_data[cat_short]["uf_a"]  += uf_ia;  cat_data[cat_short]["uf_b"]  += uf_ib

        from datetime import datetime as dt
        days_diff = max(1, (dt.strptime(newest_d, "%Y-%m-%d") - dt.strptime(prev_d, "%Y-%m-%d")).days)

        analysis = _ai_comparison_analysis(
            cat_data, db_ok_a, db_ok_b,
            prev_d, newest_d, lbl_prev, lbl_new, days_diff,
            usd_a, usd_b, uf_a, uf_b
        )

    if analysis:
        from rich.padding import Padding
        _console.print()
        _console.print(Panel(
            Padding(_render_bullets(analysis), (1, 2)),
            title=f"[bold sky_blue3]✦  PRINCIPALES HIGHLIGHTS  ·  {lbl_prev} → {lbl_new}[/bold sky_blue3]",
            border_style="sky_blue3",
            expand=True,
        ))
    else:
        _console.print("[dim]  (No se pudo generar highlights)[/dim]")


def show_historical_snapshot(by_category=False):
    """Elige fecha histórica y muestra snapshot usando _pick_snapshot_date."""
    from datetime import datetime

    selected_date = _pick_snapshot_date("¿A qué fecha quieres ver?")
    if selected_date is None:
        return

    snapshot_before = f"{selected_date} 23:59:59"
    try:
        date_label = datetime.strptime(selected_date, "%Y-%m-%d").strftime("%d %b %Y").title()
    except:
        date_label = selected_date

    cat_title = f"RESUMEN POR CATEGORÍA — {date_label}" if by_category else f"TOTAL AL {date_label.upper()}"
    show_last_saldos(by_category=by_category, snapshot_before=snapshot_before, title=cat_title)


def show_historico_assets():
    """Total Assets entre dos fechas — mismo flujo que show_comparison pero solo fila total, en 3 monedas."""
    from rich.table import Table
    from rich.box import ROUNDED
    from rich.text import Text
    from datetime import datetime

    while True:
        dates = get_available_snapshot_dates(limit=5000)
        if len(dates) < 2:
            _console.print("[yellow]No hay suficientes datos históricos.[/yellow]")
            return

        newest_d, oldest_d = dates[0], dates[-1]
        try:
            lbl_new = datetime.strptime(newest_d, "%Y-%m-%d").strftime("%d %b %Y")
            lbl_old = datetime.strptime(oldest_d, "%Y-%m-%d").strftime("%d %b %Y")
        except Exception:
            lbl_new, lbl_old = newest_d, oldest_d

        mode = questionary.select(
            "MODO DE COMPARACIÓN:",
            choices=[
                questionary.Choice(f"Comparar extremos ({lbl_old} vs {lbl_new})", value="quick"),
                questionary.Choice("Elegir fechas manualmente", value="manual"),
                questionary.Choice("« Volver", value="back"),
            ],
            style=QUESTIONARY_STYLE, pointer="»", qmark=""
        ).ask(patch_stdout=True)

        if mode is None or mode == "back":
            return

        if mode == "quick":
            date_a, date_b = oldest_d, newest_d
        else:
            date_a = _pick_snapshot_date("Fecha base (la más antigua):")
            if date_a is None: continue
            date_b = _pick_snapshot_date("Fecha a comparar:")
            if date_b is None: continue
            if date_a > date_b:
                date_a, date_b = date_b, date_a

        try:
            label_a = datetime.strptime(date_a, "%Y-%m-%d").strftime("%d %b %Y").title()
            label_b = datetime.strptime(date_b, "%Y-%m-%d").strftime("%d %b %Y").title()
        except Exception:
            label_a, label_b = date_a, date_b

        dt_a = datetime.strptime(date_a, "%Y-%m-%d")
        dt_b = datetime.strptime(date_b, "%Y-%m-%d")
        days_diff   = max((dt_b - dt_a).days, 1)
        months_diff = max(days_diff / 30.44, 1 / 30.44)
        if days_diff < 31:
            time_spent = f"{days_diff} días"
        else:
            m_val = round(months_diff, 1) if months_diff < 12 else int(round(months_diff))
            time_spent = f"{m_val} meses ({days_diff} días)"

        with _console.status("[bold blue]Consultando datos y tipos de cambio...[/]"):
            db_ok_a  = _build_db_data_ok_internal(date_a)
            db_ok_b  = _build_db_data_ok_internal(date_b)
            rates_a  = get_rates(date_a)
            rates_b  = get_rates(date_b)

        unified_catalog = _get_unified_catalog_list()

        # ── Acumular totales en las 3 monedas ──
        clp_a = clp_b = usd_a = usd_b = uf_a = uf_b = 0.0
        for item_meta in unified_catalog:
            db_inst = _CATALOG_TO_DB_INST.get(item_meta['inst'], item_meta['inst'])
            pers    = item_meta.get('persona_hist', cat_to_persona(item_meta['cat']))
            db_key  = (db_inst, item_meta['item'], pers)

            for db_ok, rates, side in [(db_ok_a, rates_a, "a"), (db_ok_b, rates_b, "b")]:
                val = db_ok.get(db_key)
                if not val: continue
                _, _, moneda, monto, _, _ = val
                try: m = float(monto)
                except Exception: continue
                usd_r = rates.get("USD", 1)
                uf_r  = rates.get("UF", 1)
                if moneda == "USD":
                    c = m * usd_r; u = m; f = m * usd_r / uf_r
                elif moneda == "UF":
                    c = m * uf_r; u = m * uf_r / usd_r; f = m
                else:
                    c = m; u = m / usd_r; f = m / uf_r
                if side == "a": clp_a += c; usd_a += u; uf_a += f
                else:           clp_b += c; usd_b += u; uf_b += f

        # ── Helpers de formato (idénticos al comparador) ──
        def fv(v, decimals=0, style=None):
            rounded = int(round(v)) if decimals == 0 else v
            if decimals == 0:
                if rounded == 0: return Text("0", style=style or "dim")
                res = f"{rounded:,}".replace(",", ".")
            else:
                if abs(v) < 0.001: return Text("0", style=style or "dim")
                res = f"{v:,.{decimals}f}".replace(",","X").replace(".",",").replace("X",".")
            return Text(res, style=style or "white")

        def fdp(val, is_pct=False, bold=False, style_override=None, decimals=None):
            if val is None: return Text("—", style="dim")
            if abs(val) < 1e-6: return Text("0" + ("%" if is_pct else ""), style="dim")
            sign = "+" if val > 0 else ""
            if is_pct:   fmt = f"{int(round(val)):,}"
            elif decimals is not None: fmt = f"{val:,.{decimals}f}"
            else:        fmt = f"{int(round(val)):,}"
            fmt = f"{sign}{fmt.replace(',','X').replace('.',',').replace('X','.')}{'%' if is_pct else ''}"
            if style_override: style = style_override
            else:
                color = "bright_green" if val > 0 else "bright_red"
                style = f"bold {color}" if bold else color
            return Text(fmt, style=style)

        # ── Tabla ──
        title_str = (
            f"\n[bold sky_blue3]TOTAL ASSETS: {label_a.upper()} vs {label_b.upper()}[/bold sky_blue3]\n"
            f"[dim white]Periodo: {time_spent}[/dim white]"
        )
        table = Table(
            title=title_str, box=ROUNDED,
            header_style="bold sky_blue3", border_style="dim",
            title_justify="center", show_header=True,
            row_styles=["", "on grey15"],
        )
        table.add_column("Moneda",    no_wrap=True, style="bold white")
        table.add_column(label_a,     justify="right", width=14)
        table.add_column(label_b,     justify="right", width=14)
        table.add_column("Δ",         justify="right", width=15)
        table.add_column("Δ%",        justify="right", width=10)
        table.add_column("Δ/día",     justify="right", width=12)
        table.add_column("Δ/mes",     justify="right", width=12)
        table.add_column("Δ% Anual",  justify="right", width=12)

        ts = "bold white"
        for label, va, vb in [("CLP MM", clp_a, clp_b), ("USD M", usd_a, usd_b), ("UF", uf_a, uf_b)]:
            d = vb - va
            table.add_row(
                Text(f"  {label}", style=ts),
                fv(va, style=ts),
                fv(vb, style=ts),
                fdp(d, style_override=ts),
                fdp(d / abs(va) * 100 if va else 0, is_pct=True, style_override=ts),
                fdp(d / days_diff, style_override=ts, decimals=1),
                fdp(d / months_diff, style_override=ts),
                fdp((d / days_diff * 365) / abs(va) * 100 if va else 0, is_pct=True, style_override=ts),
            )

        _clear_content()
        _console.print(table)

        sub_opts = ["  Cambiar fechas", "  « Volver al menú principal"]
        sub_idx = _print_table_menu(f"TOTAL ASSETS: {label_a} vs {label_b}", sub_opts)
        if sub_idx == 1:
            continue
        else:
            return


def _ai_comparison_analysis(cat_data, db_ok_a, db_ok_b, date_a, date_b,
                             label_a, label_b, days_diff,
                             usd_a, usd_b, uf_a, uf_b):
    """Llama a Claude Haiku y retorna un análisis narrativo del período."""
    try:
        import pathlib
        from dotenv import dotenv_values
        from google import genai

        base = pathlib.Path(__file__).parent
        # Leer keys de GastoSmart .env
        gs_env = dotenv_values(str(base / 'GastoSmart' / 'backend' / '.env'))
        raw_keys = gs_env.get('GEMINI_API_KEYS', gs_env.get('GEMINI_API_KEY', ''))
        api_keys = [k.strip() for k in raw_keys.split(',') if k.strip()]
        if not api_keys:
            return None

        GEMINI_MODELS = [
            'gemini-2.5-flash',
            'gemini-2.5-flash-lite',
            'gemini-2.0-flash',
            'gemini-2.5-pro',
        ]

        # ── Construir contexto estructurado ──
        lines = []
        lines.append(f"PERÍODO: {label_a} → {label_b} ({days_diff} días)")
        lines.append(f"TIPOS DE CAMBIO: USD {usd_a:,.0f}→{usd_b:,.0f} CLP ({(usd_b-usd_a)/usd_a*100:+.1f}%) | UF {uf_a:,.0f}→{uf_b:,.0f} CLP ({(uf_b-uf_a)/uf_a*100:+.1f}%)")
        lines.append("")
        lines.append("EVOLUCIÓN POR CATEGORÍA (CLP MM):")

        total_a = total_b = 0.0
        patri_a = patri_b = 0.0
        for cat in CAT_ORDER:
            if cat not in cat_data:
                continue
            d = cat_data[cat]
            ca, cb = d["clp_a"], d["clp_b"]
            delta = cb - ca
            pct   = delta / abs(ca) * 100 if ca else 0
            lines.append(f"  {cat:<30} {int(ca):>8} → {int(cb):>8}  Δ={int(delta):>+8}  ({pct:+.1f}%)")
            total_a += ca; total_b += cb
            if "Casa" not in cat:
                patri_a += ca; patri_b += cb

        d_patri = patri_b - patri_a
        d_total = total_b - total_a
        lines.append(f"  {'TOTAL PATRIMONIO INVERSIONES':<30} {int(patri_a):>8} → {int(patri_b):>8}  Δ={int(d_patri):>+8}  ({d_patri/abs(patri_a)*100:+.1f}%)" if patri_a else "")
        lines.append(f"  {'TOTAL GENERAL':<30} {int(total_a):>8} → {int(total_b):>8}  Δ={int(d_total):>+8}  ({d_total/abs(total_a)*100:+.1f}%)" if total_a else "")

        # Top movers a nivel de ítem
        item_deltas = []
        all_keys = set(db_ok_a.keys()) | set(db_ok_b.keys())
        for key in all_keys:
            va = db_ok_a.get(key)
            vb = db_ok_b.get(key)
            if not va or not vb:
                continue
            _, _, mon_a, monto_a, _, _ = va
            _, _, mon_b, monto_b, _, _ = vb
            clp_a_i, _, _, _, _ = convert_to_all(monto_a, mon_a)
            clp_b_i, _, _, _, _ = convert_to_all(monto_b, mon_b)
            delta_i = clp_b_i - clp_a_i
            inst, item, _ = key
            item_deltas.append((delta_i, inst, item))

        item_deltas.sort(key=lambda x: abs(x[0]), reverse=True)
        lines.append("")
        lines.append("TOP MOVIMIENTOS POR ÍTEM (CLP MM, mayores cambios absolutos):")
        for delta_i, inst, item in item_deltas[:8]:
            lines.append(f"  {inst} / {item:<25} Δ={int(delta_i):>+8}")

        context_str = "\n".join(lines)

        subyacentes = """
COMPOSICIÓN DE ACTIVOS (para interpretar los movimientos):
  Fondos líquidos:
    - CFISP500 (BTG PN/PJ): fondo indexado al S&P 500, en CLP
    - CFINASDAQ (BTG PN/PJ, Itaú PJ): fondo indexado al Nasdaq 100, en CLP
    - CFIETFGE (BTG): fondo de renta variable global diversificada, en CLP
    - CFIETFCD (Racional, Itaú PJ): ETF de renta fija / bonos corporativos, en CLP
    - BRK/B (Schwab): acción Berkshire Hathaway, en USD
    - QQQ (Schwab): ETF Nasdaq 100, en USD
    - ONEQ (Wealthfront): ETF Fidelity Nasdaq Composite, en USD
    - VOO, IVV (Fintual): ETFs S&P 500, en USD
    - Risky Norris (Fintual PN/PJ): fondo renta variable global (mix S&P500 + mercados globales), en CLP
    - Risky Norris APV (Fintual): mismo fondo pero en régimen APV previsional
  Fondos Inmobiliarios:
    - Fraccional PN/PJ: propiedades chilenas fraccionadas, CLP
    - HDZ Tucson I, Kansas I, Tucson II: inversiones inmobiliarias US con TIR fija 11-13%, en USD
    - WBuild José Ignacio: inversión inmobiliaria US con TIR 18%, en USD
  Previsional:
    - AFP Modelo Cuenta Obligatoria: AFP chilena, fondo de pensiones, CLP
    - AFC Fondo de Cesantía: seguro cesantía Chile, CLP
  Casa:
    - Valor Taihuen: valor activo de propiedad casa, UF
    - CH Taihuen (Consorcio): crédito hipotecario casa, UF (negativo = deuda)
    - CH Cívico, CH New (Itaú, BdChile): créditos hipotecarios propiedades inversión, UF
"""

        prompt = f"""Eres asesor patrimonial personal de Matias, un chileno con inversiones diversificadas (fondos mutuos, acciones US, propiedades, previsional, cash, deuda hipotecaria).

{subyacentes}

Aquí están los datos del período:

{context_str}

Responde en exactamente 5 bullet points (usa "•"). Sin títulos, sin secciones, sin formato LaTeX ni símbolos matemáticos. Solo texto plano con números normales (ej: "CLP 4 MM" no "CLP $4$ MM").

ORDEN de los bullets:
1. Resumen: cambio total en CLP MM, en USD (usa K si es menor a 1M, usa M si es mayor), porcentaje, y cantidad de días del período
2. FX: USD subió/bajó X%, efecto sobre activos en dólares. UF subió/bajó X%, efecto sobre deudas/activos en UF
3. Mayor driver positivo con causa raíz (ej: si Risky Norris subió, vincúlalo al mercado global de renta variable en ese período)
4. Mayor driver negativo o movimiento relevante con causa raíz
5. Destacar cualquier movimiento llamativo en categorías individuales (ej: si Cash subió o bajó mucho en términos relativos, o si alguna categoría tuvo un cambio porcentual muy alto o muy bajo vs el resto), explicando la causa probable. Si no hay nada llamativo, una observación de contexto o tendencia

Tono impersonal y directo. Sin nombres propios. En español. Sin ningún símbolo matemático."""

        last_err = None
        for api_key in api_keys:
            for model_name in GEMINI_MODELS:
                try:
                    client = genai.Client(api_key=api_key)
                    response = client.models.generate_content(
                        model=model_name,
                        contents=prompt,
                    )
                    return response.text.strip()
                except Exception as e:
                    last_err = e
                    continue
        return f"Error al generar análisis: {last_err}"

    except Exception as e:
        return f"[dim red]Error al generar análisis: {e}[/dim red]"


def show_comparison():
    """Compara dos snapshots históricos. Tabla con 3 secciones: CLP MM / USD M / UF."""
    from datetime import datetime
    from rich.table import Table
    from rich.box import ROUNDED
    from rich.text import Text

    while True:
        # ── Elegir modo de comparación ──
        dates = get_available_snapshot_dates(limit=5000)
        if len(dates) < 2:
            _console.print("[yellow]No hay suficientes datos históricos para comparar.[/yellow]")
            return

        # dates[0] es la más nueva, dates[-1] la más vieja
        newest_d = dates[0]
        oldest_d = dates[-1]
        prev_d   = dates[1] if len(dates) > 1 else None

        try:
            lbl_new  = datetime.strptime(newest_d, "%Y-%m-%d").strftime("%d %b %Y")
            lbl_old  = datetime.strptime(oldest_d, "%Y-%m-%d").strftime("%d %b %Y")
            lbl_prev = datetime.strptime(prev_d,   "%Y-%m-%d").strftime("%d %b %Y") if prev_d else None
        except:
            lbl_new, lbl_old, lbl_prev = newest_d, oldest_d, prev_d

        choices = []
        if prev_d:
            choices.append(questionary.Choice("Comparar versus medición anterior", value="prev"))
        choices.append(questionary.Choice("Comparar extremos", value="quick"))
        choices.append(questionary.Choice("Elegir fechas específicas", value="manual"))
        choices.append(questionary.Choice("« Volver", value="back"))

        mode = questionary.select(
            "MODO DE COMPARACIÓN:",
            choices=choices,
            style=QUESTIONARY_STYLE,
            pointer="»",
            qmark=""
        ).ask(patch_stdout=True)

        if mode is None or mode == "back":
            return

        if mode == "prev":
            date_a, date_b = prev_d, newest_d
        elif mode == "quick":
            date_a, date_b = oldest_d, newest_d
        else:
            # ── Elegir fechas manualmente ──
            date_a = _pick_snapshot_date("Fecha base (la más antigua):")
            if date_a is None:
                continue

            date_b = _pick_snapshot_date("Fecha a comparar:")
            if date_b is None:
                continue

            if date_a > date_b:
                date_a, date_b = date_b, date_a

        try:
            label_a = datetime.strptime(date_a, "%Y-%m-%d").strftime("%d %b %Y").title()
            label_b = datetime.strptime(date_b, "%Y-%m-%d").strftime("%d %b %Y").title()
        except:
            label_a, label_b = date_a, date_b

        snap_a = f"{date_a} 23:59:59"
        snap_b = f"{date_b} 23:59:59"

        with _console.status("[bold blue]Consultando datos y tipos de cambio...[/]"):
            # ── Usar el MISMO flujo que generate_category_summary():
            # 1) Traer db_data_ok con filtro de timestamp para cada fecha
            # 2) Iterar sobre el catálogo actual (_get_unified_catalog_list)
            # Esto garantiza que solo se usen los ítems del catálogo vigente,
            # sin incluir datos stale, duplicados ni entradas de test.
            db_ok_a = _build_db_data_ok_internal(date_a)
            db_ok_b = _build_db_data_ok_internal(date_b)
            rates_a = get_rates(date_a)
            rates_b = get_rates(date_b)

        usd_a, uf_a = rates_a.get("USD", 0), rates_a.get("UF", 0)
        usd_b, uf_b = rates_b.get("USD", 0), rates_b.get("UF", 0)

        # ── Acumular por categoría usando el catálogo vigente (igual que RESUMEN) ──
        def _norm_k(inst, item, pers):
            it = str(item).strip().lower().replace("á","a").replace("é","e").replace("í","i").replace("ó","o").replace("ú","u")
            ins = str(inst).strip().lower()
            return (ins, it, pers)

        all_catalog = _get_unified_catalog_list()
        cat_keys = set()
        for i_meta in all_catalog:
            db_inst = _CATALOG_TO_DB_INST.get(i_meta['inst'], i_meta['inst'])
            pers = i_meta.get('persona_hist', cat_to_persona(i_meta['cat']))
            cat_keys.add(_norm_k(db_inst, i_meta['item'], pers))


            # 1. Obtener catálogo unificado para filtrar basura/ítems no vigentes
            unified_catalog = _get_unified_catalog_list()
            catalog_keys = set()
            for item_meta in unified_catalog:
                # Key must match (inst, item, persona)
                c_pers = cat_to_persona(item_meta['cat'])
                catalog_keys.add((item_meta['inst'], item_meta['item'], c_pers))

            # 2. Filtrar unique_keys para que SOLO contenga ítems vigentes en el catálogo
            unique_keys = (set(db_ok_a.keys()) | set(db_ok_b.keys())) & catalog_keys
            cat_data = {}

        for it_id in unique_keys:
            db_inst, db_item, db_pers = it_id

            # Datos Fecha A
            val_a = db_ok_a.get(it_id)
            if val_a:
                cat_a, _, moneda_a, monto_a, _, _ = val_a
                # Sincronizar con show_last_saldos: usar target_date=None
                clp_a, usd_ma, uf_ia, _, _ = convert_to_all(monto_a, moneda_a, target_date=None)
            else:
                clp_a, usd_ma, uf_ia = 0.0, 0.0, 0.0
                cat_a, moneda_a = None, None

            # Datos Fecha B
            val_b = db_ok_b.get(it_id)
            if val_b:
                cat_b, _, moneda_b, monto_b, _, _ = val_b
                clp_b, usd_mb, uf_ib, _, _ = convert_to_all(monto_b, moneda_b, target_date=None)
            else:
                clp_b, usd_mb, uf_ib = 0.0, 0.0, 0.0
                cat_b, moneda_b = cat_a, moneda_a

            # Determinar categoría corta (Igual que en reporte detallado)
            final_cat = cat_b or cat_a or "Otros"
            cat_short = cat_to_short(final_cat, db_inst, db_item)
            
            if cat_short not in cat_data:
                cat_data[cat_short] = {"clp_a": 0.0, "clp_b": 0.0, "usd_a": 0.0, "usd_b": 0.0, "uf_a": 0.0, "uf_b": 0.0}
            
            cat_data[cat_short]["clp_a"] += clp_a; cat_data[cat_short]["clp_b"] += clp_b
            cat_data[cat_short]["usd_a"] += usd_ma; cat_data[cat_short]["usd_b"] += usd_mb
            cat_data[cat_short]["uf_a"]  += uf_ia; cat_data[cat_short]["uf_b"]  += uf_ib


        # ── Helpers de formato internos para evitar NameError ──
        def fv(v, decimals=0, style=None):
            if decimals == 0:
                rounded = int(round(v))
                if rounded == 0: return Text("0", style=style or "dim")
                res = f"{rounded:,}".replace(",", ".")
            else:
                if abs(v) < 0.001: return Text("0", style=style or "dim")
                res = f"{v:,.{decimals}f}".replace(",", "X").replace(".", ",").replace("X", ".")
            return Text(res, style=style or ("white" if style else None))

        def fmt_delta_premium(val, is_pct=False, bold=False, style_override=None, decimals=None):
            if val is None: return Text("—", style="dim")
            abs_val = abs(val)
            if abs_val < 1e-6:
                return Text("0" + ("%" if is_pct else ""), style="dim")
            
            sign = "+" if val > 0 else ""
            if is_pct:
                fmt = f"{val:,.1f}"
            elif decimals is not None:
                fmt = f"{val:,.{decimals}f}"
            else:
                fmt = f"{val:,.1f}"
            
            fmt = f"{sign}{fmt.replace(',', 'X').replace('.', ',').replace('X', '.')}{'%' if is_pct else ''}"
            
            if style_override:
                style = style_override
            else:
                color = "bright_green" if val > 0 else "bright_red"
                style = f"bold {color}" if bold else color
            return Text(fmt, style=style)

        # Calcular diferencia de tiempo
        from datetime import datetime
        dt_a = datetime.strptime(date_a, "%Y-%m-%d")
        dt_b = datetime.strptime(date_b, "%Y-%m-%d")
        days_diff = (dt_b - dt_a).days
        if days_diff <= 0: days_diff = 1
        months_diff = days_diff / 30.44
        if months_diff <= 0: months_diff = 1/30.44

        # Construir texto de duración
        if days_diff < 31:
            time_spent = f"{days_diff} días"
        else:
            m_val = round(months_diff, 1) if months_diff < 12 else int(round(months_diff))
            time_spent = f"{m_val} meses ({days_diff} días)"

        # ── Construir tabla premium ──
        title_str = f"\n[bold sky_blue3]COMPARATIVA: {label_a.upper()} vs {label_b.upper()}[/bold sky_blue3]\n[dim white]Periodo: {time_spent}[/dim white]"
        table = Table(
            title=title_str, box=ROUNDED,
            header_style="bold sky_blue3", border_style="dim",
            show_footer=False, title_justify="center", show_header=True,
            row_styles=["", "on grey15"],
        )
        table.add_column("Categoría", no_wrap=True, style="bold white")
        table.add_column(label_a,      justify="right", width=14)
        table.add_column(label_b,      justify="right", width=14)
        table.add_column("Δ",          justify="right", width=15)
        table.add_column("Δ%",         justify="right", width=12)
        table.add_column("Δ/día",      justify="right", width=12)
        table.add_column("Δ/mes",      justify="right", width=12)
        table.add_column("Δ% Anual",   justify="right", width=12)

        def _add_section_rows_premium(unit_key_a, unit_key_b, section_label, decimals=0):
            table.add_row(Text(f" {section_label} ", style="bold black on sky_blue3"), "", "", "", "", "", "", "")
            total_a_s = total_b_s = patri_a_s = patri_b_s = 0.0
            patri_shown_s = False
            cat_rows = []

            for cat in CAT_ORDER:
                if cat not in cat_data: continue
                d = cat_data[cat]
                ca, cb = d[unit_key_a], d[unit_key_b]
                cat_rows.append((cat, ca, cb))
                total_a_s += ca; total_b_s += cb
                if "Casa" not in cat:
                    patri_a_s += ca; patri_b_s += cb

            for cat, ca, cb in cat_rows:
                if "Casa" in cat and not patri_shown_s:
                    patri_shown_s = True
                    p_style = "bold orange3"
                    d_p = patri_b_s - patri_a_s
                    table.add_row(
                        Text("  TOTAL PATRIMONIO", style=p_style),
                        fv(patri_a_s, decimals, p_style),
                        fv(patri_b_s, decimals, p_style),
                        fmt_delta_premium(d_p, style_override=p_style),
                        fmt_delta_premium(d_p/abs(patri_a_s)*100 if patri_a_s else 0, is_pct=True, style_override=p_style),
                        fmt_delta_premium(d_p/days_diff, style_override=p_style, decimals=1),
                        fmt_delta_premium(d_p/months_diff, style_override=p_style),
                        fmt_delta_premium((d_p/days_diff*365)/abs(patri_a_s)*100 if patri_a_s else 0, is_pct=True, style_override=p_style),
                    )
                d_cat = cb - ca
                table.add_row(f"  {cat}", fv(ca, decimals), fv(cb, decimals), 
                              fmt_delta_premium(d_cat),
                              fmt_delta_premium(d_cat/abs(ca)*100 if ca else 0, is_pct=True),
                              fmt_delta_premium(d_cat/days_diff, decimals=1),
                              fmt_delta_premium(d_cat/months_diff),
                              fmt_delta_premium((d_cat/days_diff*365)/abs(ca)*100 if ca else 0, is_pct=True))

            if not patri_shown_s:
                p_style = "bold orange3"
                d_p = patri_b_s - patri_a_s
                table.add_row(Text("  TOTAL PATRIMONIO", style=p_style), fv(patri_a_s, decimals, p_style), fv(patri_b_s, decimals, p_style),
                              fmt_delta_premium(d_p, style_override=p_style),
                              fmt_delta_premium(d_p/abs(patri_a_s)*100 if patri_a_s else 0, is_pct=True, style_override=p_style),
                              fmt_delta_premium(d_p/days_diff, style_override=p_style, decimals=1),
                              fmt_delta_premium(d_p/months_diff, style_override=p_style),
                              fmt_delta_premium((d_p/days_diff*365)/abs(patri_a_s)*100 if patri_a_s else 0, is_pct=True, style_override=p_style))

            g_style = "bold orange3"
            d_g = total_b_s - total_a_s
            table.add_row(Text("  TOTAL GENERAL", style=g_style), fv(total_a_s, decimals, g_style), fv(total_b_s, decimals, g_style),
                          fmt_delta_premium(d_g, style_override=g_style),
                          fmt_delta_premium(d_g/abs(total_a_s)*100 if total_a_s else 0, is_pct=True, style_override=g_style),
                          fmt_delta_premium(d_g/days_diff, style_override=g_style, decimals=1),
                          fmt_delta_premium(d_g/months_diff, style_override=g_style),
                          fmt_delta_premium((d_g/days_diff*365)/abs(total_a_s)*100 if total_a_s else 0, is_pct=True, style_override=g_style))

        _add_section_rows_premium("clp_a", "clp_b", "CLP MM",  decimals=0)
        table.add_section()
        _add_section_rows_premium("usd_a", "usd_b", "USD M",   decimals=0)
        table.add_section()
        _add_section_rows_premium("uf_a",  "uf_b",  "UF",      decimals=0)
        table.add_section()
        table.add_row(Text(" TIPOS DE CAMBIO ", style="bold black on sky_blue3"), "", "", "", "", "", "", "")
        table.add_row("  USD CLP", fv(usd_a, 0, "white"), fv(usd_b, 0, "white"), 
                      fmt_delta_premium(usd_b - usd_a), fmt_delta_premium((usd_b - usd_a)/usd_a*100 if usd_a else 0, True),
                      fmt_delta_premium((usd_b - usd_a)/days_diff), fmt_delta_premium((usd_b - usd_a)/months_diff),
                      fmt_delta_premium(((usd_b - usd_a)/days_diff*365)/usd_a*100 if usd_a else 0, True))
        table.add_row("  UF CLP", fv(uf_a, 0, "white"), fv(uf_b, 0, "white"), 
                      fmt_delta_premium(uf_b - uf_a), fmt_delta_premium((uf_b - uf_a)/uf_a*100 if uf_a else 0, True),
                      fmt_delta_premium((uf_b - uf_a)/days_diff), fmt_delta_premium((uf_b - uf_a)/months_diff),
                      fmt_delta_premium(((uf_b - uf_a)/days_diff*365)/uf_a*100 if uf_a else 0, True))

        _clear_content()
        _console.print(table)


        sub_opts = [
            "  Cambiar fechas",
            "  « Volver al menú principal",
        ]
        sub_idx = _print_table_menu(f"COMPARACIÓN: {label_a} vs {label_b}", sub_opts)
        if sub_idx == 1:
            continue
        else:
            return



# ──────────────────────────────────────────────────────────────
# FUNCIONES DE PRUEBA Y TRANSICIÓN
# ──────────────────────────────────────────────────────────────

def _run_transition_test():
    """Simula lo que ocurre tras correr scrapers para probar la transición a manual."""
    import time
    from playwright.sync_api import sync_playwright
    _console.print(Panel("[bold yellow]MODO PRUEBA: Simulando ejecución de Scrapers...[/bold yellow]"))
    try:
        # Recreamos el entorno de Playwright (causante común de fugas de TTY)
        with sync_playwright() as p:
            _console.print(" [cyan]• Lanzando navegador en segundo plano...[/cyan]")
            browser = p.chromium.launch(headless=True)
            _console.print(" [cyan]• Realizando petición dummy...[/cyan]")
            context = browser.new_context()
            page = context.new_page()
            page.goto("https://www.google.com", timeout=5000)
            time.sleep(1) # Simular espera
            browser.close()
            _console.print(" [bold green]• Scraper ficticio terminado correctamente.[/bold green]")
    except Exception as e:
        _console.print(f" [red]Error en simulación: {e}[/red]")
    
    _console.print("\n[bold]Ahora entraremos al flujo manual para ver si el teclado responde:[/bold]")
    time.sleep(0.5)
    prompt_manual_items(all_sequential=True)



def show_cupos_tdc():
    """Tabla de cupos de tarjetas de crédito: Deuda | Cupo | Disponible | % Disponible."""
    from rich.table import Table
    from rich.box import ROUNDED
    from rich.text import Text

    conn = init_db()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS credit_limits (
            institucion TEXT NOT NULL,
            item        TEXT NOT NULL,
            cupo        REAL NOT NULL,
            PRIMARY KEY (institucion, item)
        )
    """)
    conn.commit()

    # ── Obtener todos los ítems TdC del catálogo ──
    all_catalog = _get_unified_catalog_list()
    tdc_items = [
        (_CATALOG_TO_DB_INST.get(m['inst'], m['inst']), m['inst'], m['item'])
        for m in all_catalog if m['cat'] == 'TdC'
    ]  # (db_inst, display_inst, item)

    if not tdc_items:
        _console.print("[yellow]No hay tarjetas de crédito en el catálogo.[/yellow]")
        conn.close()
        return

    # ── Deudas actuales de la DB (monto negativo → abs para mostrar) ──
    deudas = {}
    rows = conn.execute("""
        SELECT s.institucion, s.item, s.monto, s.timestamp
        FROM saldos s
        INNER JOIN (
            SELECT institucion, item, persona, MAX(timestamp) AS max_ts
            FROM saldos WHERE ok = 1
            GROUP BY institucion, item, persona
        ) latest ON s.institucion = latest.institucion
                 AND s.item = latest.item
                 AND s.persona = latest.persona
                 AND s.timestamp = latest.max_ts
        WHERE s.ok = 1
    """).fetchall()
    deudas = {}
    ts_deudas = {}
    for inst, item, monto, ts in rows:
        deudas[(inst, item)]    = abs(float(monto)) if monto is not None else 0.0
        ts_deudas[(inst, item)] = ts

    # ── Cupos guardados ──
    cupos_db = {(inst, item): cupo
                for inst, item, cupo in conn.execute(
                    "SELECT institucion, item, cupo FROM credit_limits").fetchall()}

    # ── Preguntar cupos faltantes ──
    missing = [(db_inst, disp_inst, item)
               for db_inst, disp_inst, item in tdc_items
               if (db_inst, item) not in cupos_db]
    if missing:
        _console.print("\n[bold yellow]Cupos no configurados — ingresa el límite de cada tarjeta:[/bold yellow]")
        for db_inst, disp_inst, item in missing:
            val_str = questionary.text(
                f"  Cupo {disp_inst} {item} (CLP, ej: 5.000.000):",
                style=QUESTIONARY_STYLE
            ).ask()
            if val_str is None:
                conn.close()
                return
            try:
                cupo = abs(int(val_str.replace(".", "").replace(",", "").replace("$", "").strip()))
            except Exception:
                cupo = 0
            conn.execute("INSERT OR REPLACE INTO credit_limits (institucion, item, cupo) VALUES (?,?,?)",
                         (db_inst, item, cupo))
            conn.commit()
            cupos_db[(db_inst, item)] = cupo

    conn.close()

    while True:
        # ── Construir tabla ──
        table = Table(
            title="\n[bold sky_blue3]CUPOS TARJETAS DE CRÉDITO[/bold sky_blue3]",
            box=ROUNDED, header_style="bold sky_blue3", border_style="dim",
            title_justify="center", show_header=True,
            row_styles=["", "on grey15"],
        )
        table.add_column("Institución",   style="bold white", no_wrap=True)
        table.add_column("Tarjeta",       style="dim white",  no_wrap=True)
        table.add_column("Deuda",         justify="right", width=14)
        table.add_column("Cupo",          justify="right", width=14)
        table.add_column("Disponible",    justify="right", width=14)
        table.add_column("% Disponible",  justify="right", width=13)
        table.add_column("Última act.",   justify="right", width=14, style="dim")

        total_deuda = total_cupo = 0.0
        sorted_items = sorted(tdc_items, key=lambda x: deudas.get((x[0], x[2]), 0.0), reverse=True)

        for db_inst, disp_inst, item in sorted_items:
            deuda     = deudas.get((db_inst, item), 0.0)
            cupo      = cupos_db.get((db_inst, item), 0.0)
            disp      = cupo - deuda
            pct       = (disp / cupo * 100) if cupo > 0 else 0.0
            total_deuda += deuda
            total_cupo  += cupo
            disp_color = "bright_green" if disp > 0 else ("bright_red" if disp < 0 else "white")
            pct_color  = "bright_green" if pct  > 0 else ("bright_red" if pct  < 0 else "white")
            ts_raw     = ts_deudas.get((db_inst, item))
            ts_fmt     = _fmt_ts(ts_raw) if ts_raw else "—"
            table.add_row(
                disp_inst,
                item,
                Text(fmt_monto(deuda),       style="bright_red" if deuda > 0 else "dim"),
                Text(fmt_monto(cupo),        style="white"),
                Text(fmt_monto(disp),        style=disp_color),
                Text(f"{int(round(pct))}%",  style=pct_color),
                ts_fmt,
            )

        total_disp = total_cupo - total_deuda
        total_pct  = (total_disp / total_cupo * 100) if total_cupo > 0 else 0.0
        td_color   = "bold bright_green" if total_disp > 0 else ("bold bright_red" if total_disp < 0 else "bold white")
        tp_color   = "bold bright_green" if total_pct  > 0 else ("bold bright_red" if total_pct  < 0 else "bold white")
        table.add_section()
        ts = "bold orange3"
        table.add_row(
            Text("  TOTAL", style=ts), "",
            Text(fmt_monto(total_deuda),      style=ts),
            Text(fmt_monto(total_cupo),       style=ts),
            Text(fmt_monto(total_disp),       style=td_color),
            Text(f"{int(round(total_pct))}%", style=tp_color),
            "",
        )

        _console.print(table)

        sub_opts = ["  Editar cupos", "  « Volver"]
        sub_idx = _print_table_menu("CUPOS TdC", sub_opts)

        if sub_idx == 1:  # Editar cupos
            conn2 = init_db()
            cupos_db = {(i, it): c for i, it, c in conn2.execute(
                "SELECT institucion, item, cupo FROM credit_limits").fetchall()}
            choices = [
                questionary.Choice(
                    f"{disp_inst}  {item}  (actual: {fmt_monto(cupos_db.get((db_inst, item), 0))})",
                    value=(db_inst, item)
                ) for db_inst, disp_inst, item in tdc_items
            ]
            choices.append(questionary.Choice("« Volver", value="back"))
            sel = questionary.select("¿Cuál cupo editar?", choices=choices,
                                     style=QUESTIONARY_STYLE, pointer="»", qmark="").ask(patch_stdout=True)
            if sel and sel != "back":
                val_str = questionary.text(
                    f"Nuevo cupo para {sel[1]} (actual: {fmt_monto(cupos_db.get(sel, 0))}):",
                    style=QUESTIONARY_STYLE
                ).ask()
                if val_str:
                    try:
                        nuevo = abs(int(val_str.replace(".", "").replace(",", "").replace("$", "").strip()))
                        conn2.execute("INSERT OR REPLACE INTO credit_limits (institucion, item, cupo) VALUES (?,?,?)",
                                      (sel[0], sel[1], nuevo))
                        conn2.commit()
                        cupos_db[sel] = nuevo
                        _console.print(f"[green]✓ Cupo actualizado.[/green]")
                    except Exception:
                        pass
            conn2.close()
            continue  # Redibujar tabla con cupos actualizados
        else:
            return


# ── helpers pagos TdC ────────────────────────────────────────────────────────

def _sco_pn_content_frame(page, keywords, timeout_ms=45000):
    """Espera y retorna el Frame del iframe de Scotiabank que contiene alguno de los keywords.
    keywords puede ser str o list[str].
    """
    import time
    if isinstance(keywords, str):
        keywords = [keywords]
    try:
        page.wait_for_load_state("load", timeout=12000)
    except Exception:
        pass
    deadline = time.time() + timeout_ms / 1000
    while time.time() < deadline:
        for f in page.frames:
            try:
                text = f.evaluate("() => document.body?.innerText || ''")
                if len(text) > 150 and any(kw in text for kw in keywords):
                    return f
            except Exception:
                pass
        page.wait_for_timeout(600)
    raise TimeoutError(f"[SCO-PAGOS] iframe no cargó keywords={keywords}")


def _sco_click_ver_mas(frame, page):
    """Hace click en 'Ver más ...' si existe en el frame y espera a que el contenido cambie."""
    try:
        before = frame.evaluate("() => document.querySelectorAll('tr').length")
        frame.evaluate("""() => {
            const btn = Array.from(document.querySelectorAll('button, a'))
                .find(el => el.innerText?.trim().startsWith('Ver más'));
            if (btn) btn.click();
        }""")
        # Esperar hasta que aparezcan más filas (máx 4s)
        import time as _t
        dl = _t.time() + 4
        while _t.time() < dl:
            after = frame.evaluate("() => document.querySelectorAll('tr').length")
            if after > before:
                break
            page.wait_for_timeout(400)
    except Exception:
        pass


def _scrape_sco_pagos_card(page, card_number, max_retries=3):
    """
    Extrae estado de cuenta y pagos para una TdC Scotiabank PN.
    Retorna dict:
        periodo_hasta, pagar_hasta,
        facturado_clp (str raw), facturado_usd (str raw),
        pagado_clp    (int CLP), pagado_usd    (float USD)
    Reintentos automáticos ante fallas transitorias (hasta max_retries).
    """
    import time as _time

    BASE = ("https://www.scotiabank.cl/mfe/sweb/mfe-shell-web-cl/mfe/"
            "mfe-simple-account-statement-web-cl")

    def parse_clp(s):
        if not s: return 0
        try:
            return int(s.replace("$", "").replace("-", "").replace(".", "").replace(",", "").strip() or "0")
        except (ValueError, AttributeError):
            return 0

    def parse_usd(s):
        if not s: return 0.0
        s = s.upper().replace("USD", "").replace("-", "").replace("$", "").strip()
        # Formato puede ser "1.234,56" (CL) o "1,234.56" (US)
        if "," in s and "." in s:
            if s.index(",") < s.index("."):   # 1,234.56 → US
                s = s.replace(",", "")
            else:                              # 1.234,56 → CL
                s = s.replace(".", "").replace(",", ".")
        elif "," in s:
            s = s.replace(",", ".")
        try:
            return round(float(s), 2)
        except Exception:
            return 0.0

    def _extract_header(frame):
        """Extrae fechas y montos facturados. Busca labels con coincidencia parcial."""
        return frame.evaluate("""() => {
            const lines = document.body.innerText
                .split('\\n').map(l => l.trim()).filter(l => l.length > 0);
            const after = (partial) => {
                const i = lines.findIndex(l => l.includes(partial));
                return i !== -1 ? lines[i + 1] : null;
            };
            return {
                periodo_hasta: after('Período hasta') || after('Periodo hasta'),
                pagar_hasta:   after('Pagar hasta'),
                facturado_clp: after('Nacional'),
                facturado_usd: after('Internacional'),
            };
        }""")

    def _extract_pago_rows(frame):
        """Filas de pago/crédito en la tabla de movimientos.

        Criterios (cualquiera aplica):
          - Valor monetario negativo: formato '$-X' o '-$X' o '-X'
          - Descripción contiene keyword de pago: pago, abono, canje, devolución, nota de crédito
            (excluye 'otrospagos.com' y filas resumen)
        """
        return frame.evaluate("""() => {
            const seen = new Set(); const seenAll = new Set();
            const result = []; const allRows = [];
            const isNegMoney = c => {
                const s = c.trim();
                return /^\\$-[\\d.,]+$/.test(s) ||
                       /^-\\s*\\$?\\s*[\\d.,]+$/.test(s);
            };
            const isPagoKeyword = s => {
                const low = s.toLowerCase();
                if (/otrospagos/i.test(low)) return false; // compra en otrospagos.com, no pago
                return /\\bpago\\b|abono|canje|devoluci.n|nota de cr.dito|nota de credito/i.test(low);
            };
            const isAggregate = s => /movimiento (nacional|internacional) por facturar/i.test(s);
            document.querySelectorAll('table').forEach(tbl => {
                const hdrs = Array.from(tbl.querySelectorAll('th'))
                    .map(h => h.innerText.trim().toUpperCase());
                if (!hdrs.some(h => h.includes('FECHA') || h.includes('MONTO'))) return;
                tbl.querySelectorAll('tbody tr').forEach(r => {
                    const cells = Array.from(r.querySelectorAll('td'))
                        .map(c => c.innerText.trim());
                    const k = JSON.stringify(cells);
                    if (!seenAll.has(k)) { seenAll.add(k); allRows.push(cells); }
                    if (cells.some(isAggregate)) return;
                    const hasNeg  = cells.some(isNegMoney);
                    const hasPago = cells.some(isPagoKeyword);
                    if (!hasNeg && !hasPago) return;
                    if (!seen.has(k)) { seen.add(k); result.push(cells); }
                });
            });
            return { pago: result, all: allRows };
        }""")

    def _extract_neg_intl(frame):
        """Filas de pago en movimientos internacionales (USD).
        Misma lógica que _extract_pago_rows: negativo ($-X o -$X) + keywords de pago."""
        return frame.evaluate("""() => {
            const seen = new Set(); const result = [];
            const isNegMoney = c => {
                const s = c.trim();
                return /^\\$-[\\d.,]+$/.test(s) || /^-\\s*\\$?\\s*[\\d.,]+$/.test(s);
            };
            const isPagoKeyword = s => {
                const low = s.toLowerCase();
                if (/otrospagos/i.test(low)) return false; // compra en otrospagos.com, no pago
                return /\\bpago\\b|abono|canje|devoluci.n|nota de cr.dito|nota de credito/i.test(low);
            };
            const isAggregate = s => /movimiento (nacional|internacional) por facturar/i.test(s);
            document.querySelectorAll('table').forEach(tbl => {
                const hdrs = Array.from(tbl.querySelectorAll('th'))
                    .map(h => h.innerText.trim().toUpperCase());
                if (!hdrs.some(h => h.includes('PA') || h.includes('USD') || h.includes('INTER'))) return;
                tbl.querySelectorAll('tbody tr').forEach(r => {
                    const cells = Array.from(r.querySelectorAll('td'))
                        .map(c => c.innerText.trim());
                    if (cells.length < 4) return;
                    if (cells.some(isAggregate)) return;
                    if (!cells.some(isNegMoney) && !cells.some(isPagoKeyword)) return;
                    const k = JSON.stringify(cells);
                    if (!seen.has(k)) { seen.add(k); result.push(cells); }
                });
            });
            return result;
        }""")

    def _last_money_col(row):
        """Retorna el valor absoluto del último monto monetario de una fila de pago.
        Maneja formatos: '$-5.000.000', '-$5.000.000', '-5.000.000', '$5.000.000'
        Siempre retorna positivo (el signo no importa — ya se determinó que es pago)."""
        import re as _re
        _money = _re.compile(r'^\$?-?[\d.,]+$|^-?\$?[\d.,]+$')
        for cell in reversed(row):
            s = cell.strip() if cell else ""
            if s and _money.match(s) and any(c.isdigit() for c in s):
                return s.replace('$', '').replace('-', '').strip()  # valor absoluto sin $ ni -
        return "0"

    last_err = None
    for attempt in range(1, max_retries + 1):
        try:
            if attempt > 1:
                print(f"[SCO-PAGOS] TdC {card_number} — reintento {attempt}/{max_retries}...", flush=True)
                _time.sleep(2)

            # ── Facturados ────────────────────────────────────────────────
            page.goto(f"{BASE}/?tab=movimientos-facturados&card={card_number}")
            frame = _sco_pn_content_frame(page, ["Monto facturado", "Período hasta", "Pagar hasta"])
            _sco_click_ver_mas(frame, page)
            header = _extract_header(frame)

            if not header.get("pagar_hasta"):
                raise ValueError(f"'Pagar hasta' no encontrado en facturados (card={card_number})")

            # ── No-Facturados ─────────────────────────────────────────────
            page.goto(f"{BASE}/?tab=movimientos-no-facturados&card={card_number}")
            frame = _sco_pn_content_frame(
                page, ["Nacionales", "No facturado", "movimientos no facturados", "Próxima fecha"]
            )
            _sco_click_ver_mas(frame, page)

            # Pagos CLP en no-facturados
            nofac_result     = _extract_pago_rows(frame)
            all_nofac        = nofac_result.get("all", []) if isinstance(nofac_result, dict) else []
            pago_nofac       = nofac_result.get("pago", []) if isinstance(nofac_result, dict) else nofac_result
            pagado_clp_nofac = sum(parse_clp(_last_money_col(r)) for r in pago_nofac)

            if DEBUG:
                pago_keys = {tuple(r) for r in pago_nofac}
                print(f"[SCO-PAGOS] TdC {card_number} — no-fac rows={len(all_nofac)}, pagos={len(pago_nofac)}, total={pagado_clp_nofac:,.0f}", flush=True)

            # Pagos USD en no-facturados — click tab Internacionales
            neg_intl_nofac = []
            try:
                txt_before = frame.evaluate("() => document.body?.innerText || ''")
                frame.evaluate("""() => {
                    const t = Array.from(document.querySelectorAll('li.tab__item, button, a'))
                        .find(el => el.innerText?.trim() === 'Internacionales');
                    if (t) t.click();
                }""")
                dl = _time.time() + 12
                while _time.time() < dl:
                    txt_now = frame.evaluate("() => document.body?.innerText || ''")
                    if txt_now != txt_before and len(txt_now) > 100:
                        break
                    page.wait_for_timeout(500)
                page.wait_for_timeout(800)
                _sco_click_ver_mas(frame, page)
                neg_intl_nofac = _extract_neg_intl(frame)
            except Exception as e_intl:
                if DEBUG: print(f"[SCO-PAGOS] TdC {card_number} — intl no-facturados: {e_intl}", flush=True)

            pagado_clp = pagado_clp_nofac
            pagado_usd = sum(parse_usd(_last_money_col(r)) for r in neg_intl_nofac)

            print(f"[SCO-PAGOS] TdC {card_number} — "
                  f"fac={header.get('facturado_clp')} | "
                  f"pag_clp={pagado_clp} pag_usd={pagado_usd} | "
                  f"pagar_hasta={header.get('pagar_hasta')}", flush=True)

            return {**header, "pagado_clp": pagado_clp, "pagado_usd": pagado_usd}

        except Exception as e:
            last_err = e
            print(f"[SCO-PAGOS] TdC {card_number} intento {attempt} falló: {e}", flush=True)

    raise RuntimeError(f"[SCO-PAGOS] TdC {card_number} falló tras {max_retries} intentos: {last_err}")


def show_pagos_tdc():
    """Revisión de pagos de Tarjetas de Crédito — lee desde DB."""
    from rich.table import Table
    from rich.text import Text
    from rich import box

    MESES = ["Ene","Feb","Mar","Abr","May","Jun","Jul","Ago","Sep","Oct","Nov","Dic"]

    def _fmt_clp(n):
        if n is None or n == 0: return "—"
        return f"{int(round(n)):,}".replace(",", ".")

    def _fmt_usd(n):
        if n is None or n == 0: return "—"
        return f"{n:,.2f}"

    def _fmt_ts(ts):
        # "2026-04-27 09:37:00" o "2026-04-28T08:10:..." → "27-Abr 19:32"
        try:
            clean = ts[:16].replace("T", " ")
            dt = datetime.datetime.strptime(clean, "%Y-%m-%d %H:%M")
            return f"{dt.day:02d}-{MESES[dt.month-1]} {dt.strftime('%H:%M')}"
        except Exception:
            return ts[:16] if ts else "—"

    def _fmt_date_short(s):
        # "27/04/2026" o "2026-04-27" → "27-Abr"
        if not s: return "—"
        try:
            for fmt in ("%d/%m/%Y", "%Y-%m-%d"):
                try:
                    dt = datetime.datetime.strptime(s[:10], fmt)
                    return f"{dt.day:02d}-{MESES[dt.month-1]}"
                except ValueError:
                    continue
        except Exception:
            pass
        return s[:10]

    # Migración retroactiva: si Supabase está vacío, subir datos desde SQLite local
    _migrate_pagos_tdc_to_supabase()

    # Leer desde Supabase: todos los registros, luego filtrar el más reciente por (institucion, card_number)
    all_records = _read_supabase(
        "pagos_tdc",
        select="institucion,card_number,card_name,periodo_hasta,pagar_hasta,facturado_clp,pagado_clp,facturado_usd,pagado_usd,timestamp,no_facturado_clp",
        extra="&order=timestamp.desc"
    )

    # Quedarse solo con el registro más reciente por (institucion, card_number)
    seen = {}
    for rec in all_records:
        key = (rec.get("institucion"), rec.get("card_number"))
        if key not in seen:
            seen[key] = rec

    rows = []
    for rec in seen.values():
        rows.append((
            rec.get("institucion"), rec.get("card_number"), rec.get("card_name"),
            rec.get("periodo_hasta"), rec.get("pagar_hasta"),
            rec.get("facturado_clp"), rec.get("pagado_clp"),
            rec.get("facturado_usd"), rec.get("pagado_usd"),
            rec.get("timestamp"), rec.get("no_facturado_clp")
        ))

    if not rows:
        _console.print(
            "\n[yellow]Sin datos. Ejecuta primero Actualizar datos en algún banco con TdC.[/yellow]\n"
        )
        input("Presiona Enter para volver...")
        return

    def _parse_pagar_hasta(s):
        """Para ordenar: retorna date o date.max si no parseable."""
        if not s: return datetime.date.max
        for fmt in ("%d/%m/%Y", "%Y-%m-%d"):
            try: return datetime.datetime.strptime(s[:10], fmt).date()
            except ValueError: pass
        return datetime.date.max

    # Calcular delta y clasificar
    pendientes = []
    al_dia     = []
    for r in rows:
        institucion, card_num, card_name, hasta, pagar, fac_clp, pag_clp, fac_usd, pag_usd, ts, no_fac_clp = r
        # Pago efectivo: pagado_clp si existe, sino no_facturado_clp (ej. Santander)
        paid_clp  = pag_clp if pag_clp is not None else (no_fac_clp or 0)
        paid_usd  = pag_usd or 0
        delta_clp = (fac_clp or 0) - paid_clp
        delta_usd = (fac_usd or 0) - paid_usd
        sort_key  = _parse_pagar_hasta(pagar)
        entry = (sort_key, institucion, card_num, card_name, hasta, pagar,
                 fac_clp, paid_clp, fac_usd, paid_usd, delta_clp, delta_usd, ts)
        if delta_clp > 0 or delta_usd > 0:
            pendientes.append(entry)
        else:
            al_dia.append(entry)

    pendientes.sort(key=lambda x: x[0])
    al_dia.sort(key=lambda x: x[0])

    # Columnas idénticas para ambas tablas → alineación garantizada
    COL_SPECS = [
        ("Banco",       dict(style="bold white", no_wrap=True, min_width=16)),
        ("Tarjeta",     dict(style="white",      no_wrap=True, min_width=24)),
        ("Facturado",   dict(justify="center",   width=10)),
        ("Fac. CLP",    dict(justify="right",    width=14)),
        ("Fac. USD",    dict(justify="right",    width=12)),
        ("Pag. CLP",    dict(justify="right",    width=14)),
        ("Pag. USD",    dict(justify="right",    width=12)),
        ("Δ CLP",       dict(justify="right",    width=14)),
        ("Δ USD",       dict(justify="right",    width=12)),
        ("Pagar hasta", dict(justify="center",   width=12)),
        ("Actualizado", dict(justify="center",   width=14, style="dim")),
    ]

    def _make_table(title_markup):
        t = Table(
            title=title_markup,
            box=box.ROUNDED, header_style="bold sky_blue3", border_style="dim",
            title_justify="center", show_header=True,
            row_styles=["", "on grey15"],
        )
        for name, kwargs in COL_SPECS:
            t.add_column(name, **kwargs)
        return t

    def _add_rows(table, entries):
        for _, institucion, card_num, card_name, hasta, pagar, fac_clp, paid_clp, fac_usd, paid_usd, delta_clp, delta_usd, ts in entries:
            c_clp = "bright_red" if delta_clp > 0 else "bright_green"
            c_usd = "bright_red" if delta_usd > 0 else "bright_green"
            table.add_row(
                institucion,
                f"{card_name or 'Visa'} {card_num}",
                _fmt_date_short(hasta),
                _fmt_clp(fac_clp),
                _fmt_usd(fac_usd),
                _fmt_clp(paid_clp if paid_clp else None),
                _fmt_usd(paid_usd if paid_usd else None),
                Text(_fmt_clp(delta_clp), style=c_clp),
                Text(_fmt_usd(delta_usd), style=c_usd),
                _fmt_date_short(pagar),
                _fmt_ts(ts),
            )

    _console.print()
    if pendientes:
        t = _make_table("[bold white]PENDIENTES[/bold white]")
        _add_rows(t, pendientes)
        _console.print(t)
        _console.print()
    if al_dia:
        t = _make_table("[bold white]AL DÍA[/bold white]")
        _add_rows(t, al_dia)
        _console.print(t)

    input("\nPresiona Enter para volver al menú...")


def manage_cupos_tdc():
    """Editor de cupos CLP y USD por tarjeta — accesible desde Configuración."""
    from rich.table import Table
    from rich import box as rbox

    all_catalog = _get_unified_catalog_list()
    tdc_items = [
        (_CATALOG_TO_DB_INST.get(m['inst'], m['inst']), m['inst'], m['item'])
        for m in all_catalog if m['cat'] == 'TdC'
    ]
    if not tdc_items:
        _console.print("[yellow]No hay tarjetas en el catálogo.[/yellow]")
        return

    conn = init_db()
    conn.execute("""CREATE TABLE IF NOT EXISTS credit_limits (
        institucion TEXT NOT NULL, item TEXT NOT NULL, cupo REAL NOT NULL,
        cupo_usd REAL NOT NULL DEFAULT 0, PRIMARY KEY (institucion, item))""")
    conn.commit()
    cupos_db = {(i, it): (c, u) for i, it, c, u in
                conn.execute("SELECT institucion, item, cupo, COALESCE(cupo_usd,0) FROM credit_limits").fetchall()}

    while True:
        # Mostrar tabla actual
        table = Table(
            title="\n[bold sky_blue3]CUPOS DE TARJETAS[/bold sky_blue3]",
            box=rbox.ROUNDED, header_style="bold sky_blue3", border_style="dim",
            title_justify="center", show_header=True,
            row_styles=["", "on grey15"],
        )
        table.add_column("#",          justify="right",  width=4,  style="dim")
        table.add_column("Banco",      style="bold white", no_wrap=True)
        table.add_column("Tarjeta",    style="white",      no_wrap=True)
        table.add_column("Cupo CLP",   justify="right",  min_width=14)
        table.add_column("Cupo USD",   justify="right",  min_width=10)

        sorted_items = sorted(tdc_items, key=lambda x: x[1])
        for idx, (db_inst, disp_inst, item) in enumerate(sorted_items, 1):
            clp, usd = cupos_db.get((db_inst, item), (0, 0))
            clp_str = f"{int(round(clp)):,}".replace(",", ".") if clp else "—"
            usd_str = f"{usd:,.0f}" if usd else "—"
            table.add_row(str(idx), disp_inst, item, clp_str, usd_str)

        _console.print()
        _console.print(table)
        _console.print()

        ans = questionary.text(
            "  Número a editar (o Enter para volver):",
            qmark=""
        ).ask(patch_stdout=True)

        if not ans or not ans.strip():
            break
        try:
            idx = int(ans.strip()) - 1
            if not (0 <= idx < len(sorted_items)):
                _console.print("[red]Número fuera de rango.[/red]"); continue
        except ValueError:
            _console.print("[red]Ingresa un número.[/red]"); continue

        db_inst, disp_inst, item = sorted_items[idx]
        clp_actual, usd_actual = cupos_db.get((db_inst, item), (0, 0))

        clp_str = questionary.text(
            f"  Cupo CLP para {disp_inst} {item} (actual: {int(round(clp_actual)):,}):".replace(",", "."),
            qmark=""
        ).ask(patch_stdout=True)
        usd_str = questionary.text(
            f"  Cupo USD para {disp_inst} {item} (actual: {usd_actual:,.0f}, 0 si no aplica):",
            qmark=""
        ).ask(patch_stdout=True)

        try: new_clp = abs(int(clp_str.replace(".", "").replace(",", "").replace("$", "").strip())) if clp_str and clp_str.strip() else clp_actual
        except Exception: new_clp = clp_actual
        try: new_usd = abs(float(usd_str.replace(",", "").strip())) if usd_str and usd_str.strip() else usd_actual
        except Exception: new_usd = usd_actual

        conn.execute(
            "INSERT OR REPLACE INTO credit_limits (institucion, item, cupo, cupo_usd) VALUES (?,?,?,?)",
            (db_inst, item, new_clp, new_usd)
        )
        conn.commit()
        cupos_db[(db_inst, item)] = (new_clp, new_usd)
        _console.print(f"[bold green]  ✓ Guardado: CLP {int(new_clp):,} | USD {new_usd:,.0f}[/bold green]".replace(",", "."))

    conn.close()


def show_tdc():
    """Vista combinada: cupos + pagos de todas las TdC en una sola tabla."""
    from rich.table import Table
    from rich.text import Text
    from rich import box as rbox

    MESES = ["Ene","Feb","Mar","Abr","May","Jun","Jul","Ago","Sep","Oct","Nov","Dic"]

    def _fmt_clp(n):
        if n is None or n == 0: return "—"
        return f"{int(round(n)):,}".replace(",", ".")

    def _fmt_usd(n):
        if n is None or n == 0: return "—"
        return f"{n:,.2f}"

    def _fmt_date(s):
        if not s: return "—"
        for fmt in ("%d/%m/%Y", "%Y-%m-%d"):
            try:
                dt = datetime.datetime.strptime(s[:10], fmt)
                return f"{dt.day:02d}-{MESES[dt.month-1]}"
            except ValueError: continue
        return s[:10]

    def _parse_pagar(s):
        if not s: return datetime.date.max
        for fmt in ("%d/%m/%Y", "%Y-%m-%d"):
            try: return datetime.datetime.strptime(s[:10], fmt).date()
            except ValueError: pass
        return datetime.date.max

    # ── 1. Catálogo TdC ──────────────────────────────────────────────────────
    all_catalog = _get_unified_catalog_list()
    tdc_items = [
        (_CATALOG_TO_DB_INST.get(m['inst'], m['inst']), m['inst'], m['item'])
        for m in all_catalog if m['cat'] == 'TdC'
    ]
    if not tdc_items:
        _console.print("[yellow]No hay tarjetas en el catálogo.[/yellow]")
        return

    # ── 2. Deudas + cupos ─────────────────────────────────────────────────────
    conn = init_db()
    conn.execute("""CREATE TABLE IF NOT EXISTS credit_limits (
        institucion TEXT NOT NULL, item TEXT NOT NULL, cupo REAL NOT NULL,
        PRIMARY KEY (institucion, item))""")
    conn.commit()
    rows_db = conn.execute("""
        SELECT s.institucion, s.item, s.monto, s.timestamp
        FROM saldos s
        INNER JOIN (
            SELECT institucion, item, persona, MAX(timestamp) AS max_ts
            FROM saldos WHERE ok=1 GROUP BY institucion, item, persona
        ) latest ON s.institucion=latest.institucion AND s.item=latest.item
                 AND s.persona=latest.persona AND s.timestamp=latest.max_ts
        WHERE s.ok=1
    """).fetchall()
    deudas = {(i, it): abs(float(m)) for i, it, m, _ in rows_db if m is not None}
    ts_map  = {(i, it): ts for i, it, _, ts in rows_db}
    cupos_db = {(i, it): (c, u) for i, it, c, u in
                conn.execute("SELECT institucion, item, cupo, COALESCE(cupo_usd,0) FROM credit_limits").fetchall()}
    missing = [(db_i, d_i, it) for db_i, d_i, it in tdc_items if (db_i, it) not in cupos_db]
    if missing:
        _console.print("\n[bold yellow]Cupos no configurados — ingresa el límite CLP de cada tarjeta:[/bold yellow]")
        _console.print("[dim]  (Para configurar cupo USD también, ir a Configuración → Cupos TdC)[/dim]")
        for db_i, d_i, it in missing:
            val_str = questionary.text(f"  Cupo CLP {d_i} {it} (ej: 5.000.000):", style=QUESTIONARY_STYLE).ask()
            if val_str is None: conn.close(); return
            try: cupo = abs(int(val_str.replace(".", "").replace(",", "").replace("$", "").strip()))
            except Exception: cupo = 0
            conn.execute("INSERT OR REPLACE INTO credit_limits (institucion, item, cupo, cupo_usd) VALUES (?,?,?,0)",
                         (db_i, it, cupo))
            conn.commit()
            cupos_db[(db_i, it)] = (cupo, 0)
    conn.close()

    # ── 3. Pagos (Supabase) ───────────────────────────────────────────────────
    _migrate_pagos_tdc_to_supabase()
    pagos_raw = _read_supabase(
        "pagos_tdc",
        select="institucion,card_number,card_name,periodo_hasta,pagar_hasta,"
               "facturado_clp,pagado_clp,no_facturado_clp,facturado_usd,pagado_usd,timestamp",
        extra="&order=timestamp.desc"
    )
    pagos_map = {}
    for rec in pagos_raw:
        k = (rec.get("institucion"), str(rec.get("card_number", "")))
        if k not in pagos_map:
            pagos_map[k] = rec

    # ── 4. Construir filas ────────────────────────────────────────────────────
    pendientes, al_dia = [], []
    has_usd = False
    for db_inst, disp_inst, item in sorted(tdc_items, key=lambda x: x[1]):
        deuda    = deudas.get((db_inst, item), 0.0)
        cupo_clp, cupo_usd = cupos_db.get((db_inst, item), (0.0, 0.0))
        disp     = cupo_clp - deuda
        pct      = int(round(disp / cupo_clp * 100)) if cupo_clp > 0 else 0
        ts       = ts_map.get((db_inst, item))
        card_digits = item.split()[-1] if item else ""
        pago = pagos_map.get((db_inst, card_digits)) or pagos_map.get((disp_inst, card_digits))
        if pago:
            fac_clp   = pago.get("facturado_clp") or 0
            paid_clp  = pago.get("pagado_clp") if pago.get("pagado_clp") is not None else (pago.get("no_facturado_clp") or 0)
            fac_usd   = pago.get("facturado_usd") or 0
            paid_usd  = pago.get("pagado_usd") or 0
            delta_clp = fac_clp - paid_clp
            delta_usd = fac_usd - paid_usd
            pagar     = pago.get("pagar_hasta")
        else:
            fac_clp = paid_clp = fac_usd = paid_usd = delta_clp = delta_usd = 0
            pagar = None
        if fac_usd or delta_usd or cupo_usd: has_usd = True
        sort_key = _parse_pagar(pagar)
        entry = (sort_key, disp_inst, item, deuda, cupo_clp, cupo_usd, disp, pct,
                 fac_clp, delta_clp, fac_usd, delta_usd, pagar, ts)
        if delta_clp > 0 or delta_usd > 0:
            pendientes.append(entry)
        else:
            al_dia.append(entry)

    pendientes.sort(key=lambda x: x[0])
    al_dia.sort(key=lambda x: x[0])

    # ── 5. Dos tablas separadas: PENDIENTES / AL DÍA ─────────────────────────
    def _make_tdc_table(title):
        t = Table(
            title=title,
            box=rbox.ROUNDED, header_style="bold sky_blue3", border_style="dim",
            title_justify="center", show_header=True,
            row_styles=["", "on grey15"],
        )
        t.add_column("Banco",       style="bold white", no_wrap=True,  width=16)
        t.add_column("Tarjeta",     style="white",      no_wrap=True,  width=22)
        t.add_column("Deuda CLP",   justify="right",    no_wrap=True,  width=14)
        t.add_column("Cupo CLP",    justify="right",    no_wrap=True,  width=14)
        t.add_column("Disp. CLP",   justify="right",    no_wrap=True,  width=14)
        t.add_column("%",           justify="right",    no_wrap=True,  width=6)
        if has_usd:
            t.add_column("Cupo USD",  justify="right",  no_wrap=True,  width=11)
        t.add_column("Facturado",   justify="right",    no_wrap=True,  width=14)
        if has_usd:
            t.add_column("Fac. USD",  justify="right",  no_wrap=True,  width=11)
        t.add_column("Δ Pagar",     justify="right",    no_wrap=True,  width=14)
        if has_usd:
            t.add_column("Δ USD",     justify="right",  no_wrap=True,  width=11)
        t.add_column("Pagar hasta", justify="center",   no_wrap=True,  width=12)
        t.add_column("Act.",        justify="center",   no_wrap=True,  width=13, style="dim")
        return t

    def _data_row(entry):
        _, banco, tarjeta, deuda, cupo_clp, cupo_usd, disp, pct, fac_clp, delta_clp, fac_usd, delta_usd, pagar, ts = entry
        dc    = "bright_green" if disp > 0 else ("bright_red" if disp < 0 else "white")
        clp_c = "bright_red" if delta_clp > 0 else ("bright_green" if delta_clp < 0 else "dim")
        usd_c = "bright_red" if delta_usd > 0 else ("bright_green" if delta_usd < 0 else "dim")
        ts_fmt = _fmt_ts(ts) if ts else "—"
        cells = [
            banco,
            tarjeta,
            Text(_fmt_clp(deuda),    style="bright_red" if deuda > 0 else "dim"),
            Text(_fmt_clp(cupo_clp), style="white"),
            Text(_fmt_clp(disp),     style=dc),
            Text(f"{pct}%",          style=dc),
        ]
        if has_usd: cells.append(_fmt_usd(cupo_usd) if cupo_usd else "—")
        cells.append(_fmt_clp(fac_clp) if fac_clp else "—")
        if has_usd: cells.append(_fmt_usd(fac_usd) if fac_usd else "—")
        cells.append(Text(_fmt_clp(abs(delta_clp)) if delta_clp else "—", style=clp_c))
        if has_usd: cells.append(Text(_fmt_usd(abs(delta_usd)) if delta_usd else "—", style=usd_c))
        cells += [_fmt_date(pagar), ts_fmt]
        return cells

    _console.print()
    _console.print(Rule("[bold sky_blue3]TARJETAS DE CRÉDITO[/bold sky_blue3]", style="sky_blue3"))
    _console.print()
    if pendientes:
        t = _make_tdc_table("[bold white]PENDIENTES[/bold white]")
        for entry in pendientes:
            t.add_row(*_data_row(entry))
        _console.print(t)
        _console.print()
    if al_dia:
        t = _make_tdc_table("[bold white]AL DÍA[/bold white]")
        for entry in al_dia:
            t.add_row(*_data_row(entry))
        _console.print(t)
    if not pendientes and not al_dia:
        _console.print("[dim]Sin tarjetas registradas.[/dim]")

    input("\nPresiona Enter para volver al menú...")


def show_caja():
    """Análisis de liquidez."""

    db_data_ok = {}
    # Fuente primaria: Supabase
    try:
        rows_raw = _read_supabase("v_latest_saldos")
        for r in rows_raw:
            if r.get("source") == "historial":
                continue
            db_data_ok[(r["institucion"], r["item"], r["persona"])] = (
                r["categoria"], r["persona"], r["moneda"], r["monto"], r["timestamp"], r.get("source", "auto")
            )
    except Exception:
        pass
    # Fallback: SQLite local
    if not db_data_ok:
        db_path = _db_path()
        if os.path.exists(db_path):
            conn = init_db()
            try:
                rows_raw = conn.execute("""
                    SELECT s.institucion, s.categoria, s.persona, s.item,
                           s.moneda, s.monto, s.timestamp,
                           COALESCE(s.source, 'auto') AS source
                    FROM saldos s
                    INNER JOIN (
                        SELECT institucion, item, persona, MAX(timestamp) AS max_ts
                        FROM saldos WHERE ok = 1
                        GROUP BY institucion, item, persona
                    ) latest ON s.institucion = latest.institucion
                             AND s.item = latest.item
                             AND s.persona = latest.persona
                             AND s.timestamp = latest.max_ts
                    WHERE s.ok = 1 AND COALESCE(s.source, 'auto') != 'historial'
                """).fetchall()
                for inst, cat_raw, persona_raw, item, moneda, monto_int, ts, source in rows_raw:
                    db_data_ok[(inst, item, persona_raw)] = (cat_raw, persona_raw, moneda, monto_int, ts, source)
            except Exception:
                pass
            conn.close()

    rates_today = get_rates()
    usd = rates_today.get("USD", 0)
    uf = rates_today.get("UF", 0)

    # Notas para CxC / CxP (se muestran en columna Ítem)
    _caja_notas = {}
    try:
        import json as _json_caja
        for _ni in _NOTE_REQUIRED_ITEMS:
            _nr = _read_supabase("saldos", select="extra_data",
                                 filters={"item": _ni}, extra="&order=timestamp.desc&limit=1")
            if _nr and _nr[0].get("extra_data"):
                _ed = _nr[0]["extra_data"]
                if isinstance(_ed, str): _ed = _json_caja.loads(_ed)
                _nota = _ed.get("nota") if isinstance(_ed, dict) else None
                if _nota: _caja_notas[_ni] = _nota
    except Exception:
        pass

    # Identificadores precisos según definición del usuario (fondos líquidos considerados corto plazo)
    SHORT_TERM_INVESTMENTS = {
        ("Neat", "Neat"),
        ("Racional", "CFIETFCD"),
        ("Fraccional", "Fraccional"),
        ("Itaú PJ", "CFIETFCD"),
        ("Fintual", "Cash Owa"),
    }

    all_catalog = _get_unified_catalog_list()

    activos_items = []
    pasivos_items = []

    total_activos = 0.0
    total_pasivos = 0.0

    for item_meta in all_catalog:
        inst_name  = item_meta['inst']
        cat        = item_meta['cat']
        item_code  = item_meta['item']

        db_inst = _CATALOG_TO_DB_INST.get(inst_name, inst_name)
        db_key  = (db_inst, item_code, cat_to_persona(cat))

        if db_key not in db_data_ok:
            continue

        cat_raw, persona_raw, moneda_str, monto_str, ts, source = db_data_ok[db_key]
        try:    monto_val = float(monto_str) if monto_str is not None else 0.0
        except: monto_val = 0.0

        # Convertir a CLP
        if moneda_str == "USD":
            val_clp = monto_val * usd
        elif moneda_str == "UF":
            val_clp = monto_val * uf
        else:
            val_clp = monto_val

        if val_clp == 0:
            continue

        # Nombre para la columna Ítem (nota si existe, sino el código)
        item_label = _caja_notas.get(item_code, item_code)

        # Lógica de clasificación
        is_cash = ("Cash" in cat or "CC" in cat)  # Cash + CC PN/PJ (cuentas corrientes)
        is_short_term_inv = (inst_name, item_code) in SHORT_TERM_INVESTMENTS

        is_tdc = ("TdC" in item_code or "TdC" in cat)
        is_ldc = ("LdC" in item_code or "LdC" in cat)
        is_cxc = (item_code == "Cuentas por cobrar")
        is_cxp = (item_code == "Cuentas por pagar")

        if (is_cash and val_clp > 0) or (is_short_term_inv and val_clp > 0):
            activos_items.append((inst_name, persona_raw, item_label, val_clp, ts))
            total_activos += val_clp
        elif is_cxc and val_clp > 0:
            activos_items.append((inst_name, persona_raw, item_label, val_clp, ts))
            total_activos += val_clp
        elif (is_ldc or is_cxp) and val_clp < 0:
            pasivos_items.append((inst_name, persona_raw, item_label, val_clp, ts))
            total_pasivos += val_clp
        elif is_tdc and val_clp < 0:
            pasivos_items.append((inst_name, persona_raw, item_label, val_clp, ts))
            total_pasivos += val_clp

    # Ordenar
    activos_items.sort(key=lambda x: x[3], reverse=True)
    pasivos_items.sort(key=lambda x: x[3])  # El más negativo primero
    
    def fclp(v, style="white"):
        if v == 0: return Text("-", style="dim")
        sign = "-" if v < 0 else ""
        s = f"{sign}{int(round(abs(v))):,}".replace(",", ".")
        return Text(s, style=style)

    from datetime import datetime
    fecha_actual = datetime.now().strftime("%d %b %Y").title()
    safety_cash        = _get_config("safety_cash",        -35000000.0)
    prov_inversion     = _get_config("prov_inversion",     0.0)
    tolerance          = _get_config("tolerance",          -5.0)

    while True:
        # Construir tabla
        table = Table(
            title=f"\n[bold sky_blue3]SITUACIÓN DE CAJA — {fecha_actual}[/bold sky_blue3]", box=rich_box.ROUNDED,
            header_style="bold sky_blue3", border_style="dim",
            show_footer=False, title_justify="center"
        )
        table.add_column("Institución", style="bold white")
        table.add_column("Persona", style="dim white")
        table.add_column("Ítem", style="dim white")
        table.add_column("Monto (CLP)", justify="right")
        table.add_column("Actualizado", style="dim white", justify="right")

        # ACTIVOS
        table.add_row(Text("── Activos Corto Plazo ──", style="bold sky_blue2"), "", "", "", "")
        for inst, pers, item, v_clp, d_ts in activos_items:
            ts_f = "—"
            if d_ts:
                try:
                    dt_obj = datetime.strptime(d_ts[:16], "%Y-%m-%d %H:%M")
                    ts_f = f"{dt_obj.day:02d} {_MES[dt_obj.month-1]} {dt_obj.strftime('%H:%M')}"
                except:
                    ts_f = d_ts[:16]
            table.add_row(f"  {inst}", pers, item, fclp(v_clp, style="white"), ts_f)
        table.add_row(Text("  Subtotal Activos", style="bold bright_green"), "", "", fclp(total_activos, style="bold bright_green"), "")
        
        # PASIVOS
        table.add_section()
        table.add_row(Text("── Obligaciones Corto Plazo ──", style="bold dark_orange"), "", "", "", "")
        for inst, pers, item, v_clp, d_ts in pasivos_items:
            ts_f = "—"
            if d_ts:
                try:
                    dt_obj = datetime.strptime(d_ts[:16], "%Y-%m-%d %H:%M")
                    ts_f = f"{dt_obj.day:02d} {_MES[dt_obj.month-1]} {dt_obj.strftime('%H:%M')}"
                except:
                    ts_f = d_ts[:16]
            table.add_row(f"  {inst}", pers, item, fclp(v_clp, style="white"), ts_f)
            
        table.add_row(Text("  Subtotal Obligaciones", style="bold bright_red"), "", "", fclp(total_pasivos, style="bold bright_red"), "")
        
        # SAFETY CASH
        table.add_section()
        tol_label = f"{int(tolerance)}%"

        # LIQUIDEZ BRUTA (sin safety cash)
        liq_bruta = total_activos + total_pasivos
        st_b = "bold bright_green" if liq_bruta >= 0 else "bold bright_red"
        table.add_row(Text("── Liquidez Bruta ──", style="bold white"), "", "", fclp(liq_bruta, style=st_b), "")

        # PROVISIONES
        total_provisiones = safety_cash + prov_inversion
        table.add_section()
        table.add_row(Text("── Provisiones ──", style="bold medium_purple"), "", "", "", "")
        table.add_row("  Safety Cash",                  "", "Reserva", fclp(safety_cash,    style="white"), "")
        table.add_row("  Provisión",      "", "Reserva", fclp(prov_inversion, style="white"), "")

        # LIQUIDEZ NETA (con provisiones)
        table.add_section()
        total_pasivos_calc = total_pasivos + total_provisiones
        liq_neta = total_activos + total_pasivos_calc
        st = "bold bright_green" if liq_neta >= 0 else "bold bright_red"
        table.add_row(Text("── Liquidez Neta ──", style="bold white"), "", "", fclp(liq_neta, style=st), "")

        abs_p = abs(total_pasivos_calc)
        if abs_p != 0:
            ratio = (liq_neta / abs_p) * 100
        else:
            ratio = 0.0
        sign_ratio = "+" if ratio > 0 else ""
        ratio_str = f"{sign_ratio}{ratio:,.2f}%".replace(",", "X").replace(".", ",").replace("X", ".")
        table.add_row(Text("── Ratio Neto ──", style="bold white"), "", "", Text(ratio_str, style=st), "")

        dispo_neto = liq_neta - (tolerance / 100.0) * abs_p
        st_dn = "bold cyan" if dispo_neto >= 0 else "bold bright_red"
        table.add_row(Text(f"── Disponible Neto (tolerancia {tol_label}) ──", style="bold white"), "", "", fclp(dispo_neto, style=st_dn), "")

        _console.print(table)
        _console.print()
        
        ans = questionary.select(
            "Opciones de Caja:",
            choices=[
                questionary.Choice("Actualizar Safety Cash", value="safety"),
                questionary.Choice("Actualizar Provisión", value="provision"),
                questionary.Choice("Actualizar Tolerancia", value="tolerancia"),
                questionary.Separator(),
                questionary.Choice("« Volver", value="back"),
            ],
            style=QUESTIONARY_STYLE,
            pointer="»",
            qmark=""
        ).ask(patch_stdout=True)

        if ans == "back" or ans is None:
            break

        if ans == "safety":
            val_str = questionary.text("    Monto del Safety Cash (ej: 35.000.000):").ask()
            if val_str:
                try:
                    val = float(val_str.replace(".", "").replace(",", "").replace("$", ""))
                    safety_cash = -abs(val) if val != 0 else 0.0
                    _set_config("safety_cash", safety_cash)
                except:
                    pass
        elif ans == "provision":
            val_str = questionary.text("    Monto Provisión (ej: 10.000.000):").ask()
            if val_str:
                try:
                    val = float(val_str.replace(".", "").replace(",", "").replace("$", ""))
                    prov_inversion = -abs(val) if val != 0 else 0.0
                    _set_config("prov_inversion", prov_inversion)
                except:
                    pass
        elif ans == "tolerancia":
            tol_str = questionary.text("    Nueva tolerancia % (ej: -5):").ask()
            if tol_str:
                try:
                    tolerance = float(tol_str.replace("%", "").replace(",", "."))
                    _set_config("tolerance", tolerance)
                except:
                    pass

def _clear_content():
    """Imprime separador visual entre secciones (sin borrar historial)."""
    _console.print(Rule(style="dim #2a2a2a"))

def main():
    import os
    # Guardar estado exacto del terminal ANTES de cualquier operación de Playwright
    _save_terminal_state()
    # Asegurar desactivación de CPR
    os.environ['PROMPT_TOOLKIT_NO_CPR'] = '1'
    try:
        from prompt_toolkit.output.vt100 import Vt100_Output
        Vt100_Output.responds_to_cpr = False
    except: pass

    # Resetear scroll region (por si quedó activo de una sesión anterior) y limpiar pantalla
    import sys as _sys
    _sys.stdout.write("\033[r")   # reset scroll region → pantalla completa
    _sys.stdout.flush()
    _console.clear()

    from datetime import datetime
    import locale as _locale
    _MESES_ES = ["ene","feb","mar","abr","may","jun","jul","ago","sep","oct","nov","dic"]
    mod_time = os.path.getmtime(__file__)
    _dt = datetime.fromtimestamp(mod_time)
    fecha_mod = f"{_dt.day:02d} {_MESES_ES[_dt.month-1]} {_dt.year}, {_dt.strftime('%H:%M')}"

    _FONT = {
        'C': [" ████", "█    ", "█    ", "█    ", " ████"],
        'H': ["█   █", "█   █", "█████", "█   █", "█   █"],
        'I': ["███",   " █ ",   " █ ",   " █ ",   "███"  ],
        'R': ["████ ", "█   █", "████ ", "█ █  ", "█  ██"],
        'A': [" ███ ", "█   █", "█████", "█   █", "█   █"],
        'F': ["█████", "█    ", "████ ", "█    ", "█    "],
        'N': ["█   █", "██  █", "█ █ █", "█  ██", "█   █"],
    }
    _GAP = "  "
    _rows = [""] * 5
    for _i, _ch in enumerate("CHIRAFIN"):
        for _r in range(5):
            _rows[_r] += _FONT[_ch][_r] + (_GAP if _i < 7 else "")
    _console.print()
    for _row in _rows:
        _console.print(f"  [sky_blue3]{_row}[/sky_blue3]", highlight=False)
    _console.print()
    _console.print(f"  [dim]Código actualizado el {fecha_mod}[/dim]", highlight=False)
    _console.print(Rule(style="dim sky_blue3"))

    # Manejo de argumentos CLI
    parser = argparse.ArgumentParser(description="Gestor de Saldos Bancarios")
    parser.add_argument('targets', nargs='*', help="Nombres de bancos")
    parser.add_argument('--headless', action='store_true')
    parser.add_argument('--no-video', action='store_true')
    parser.add_argument('--force-manual', action='store_true')
    parser.add_argument('--all', action='store_true', help="Ejecutar todos los scrapers automáticos sin menú.")
    global args
    # Usar sys.argv para evitar conflictos
    args, unknown = parser.parse_known_args()

    # Modo no-interactivo: --all corre todos los scrapers y sale (sin questionary)
    # AFC excluido siempre — requiere CAPTCHA manual
    if getattr(args, 'all', False):
        bw_unlock()
        to_run = [t for t in INSTITUTION_ITEMS if t[1] != "afc"]
        run_scraping(to_run, full_update=True)
        return

    if args.targets:
        valid_keys = [item[1] for item in INSTITUTION_ITEMS]
        selected = [item for item in INSTITUTION_ITEMS if item[1] in args.targets]
        if selected:
            bw_unlock()
            run_scraping(selected)
            return
        else:
            _console.print(f"[bold red][ERROR] No es válido.[/bold red]")
            sys.exit(1)

    try:
      while True:
        _console.print(f"\n[bold sky_blue3]  MENÚ PRINCIPAL  [/bold sky_blue3]")
        _reset_terminal()
        _clear_terminal_buffer()

        _top_choices = [
            questionary.Choice("  Actualizar datos",       value="actualizar"),
            questionary.Choice("  Visualizar patrimonio",  value="visualizar"),
            questionary.Choice("  Analizar caja",          value="caja"),
            questionary.Choice("  Comparar fechas",        value="comparar"),
        ]
        if _FRACCIONAL_AVAILABLE:
            _top_choices.append(questionary.Choice("  Fraccional", value="fraccional"))
        _top_choices += [
            questionary.Choice("  Configuración",           value="config"),
            questionary.Separator(),
            questionary.Choice("  Salir",                  value="salir"),
        ]

        top_sel = questionary.select(
            "",
            choices=_top_choices,
            style=ORANGE_MENU_STYLE,
            pointer="»",
            qmark="",
        ).ask(patch_stdout=True)

        if not top_sel or top_sel == "salir":
            _console.clear()
            sys.exit(0)

        # ── ACTUALIZAR DATOS ────────────────────────────────────────────────
        elif top_sel == "actualizar":
            while True:
                sub = _print_table_menu("ACTUALIZAR DATOS", [
                    "  Actualizar todos los automáticos",
                    "  Actualizar algunos automáticos",
                    "  Actualizar algún registro particular",
                    "  « Volver",
                ])
                # sub: 1=Todos auto, 2=Algunos auto, 3=Registro particular, 4=Volver
                if sub == 1:
                    try:
                        bw_unlock()
                        run_scraping(list(INSTITUTION_ITEMS), full_update=True)
                    except Exception as e_scrape:
                        _console.print(f"\n[bold red][ERROR] Scraping falló: {e_scrape}[/bold red]")
                    finally:
                        _reset_terminal()
                        _clear_terminal_buffer()
                    _console.print("\n[bold green]✓ Actualización completada.[/bold green]")
                elif sub == 2:
                    _actualizar_algunos_automaticos()
                elif sub == 3:
                    prompt_manual_items()
                else:
                    break  # Volver al top

        # ── VISUALIZAR PATRIMONIO ───────────────────────────────────────────
        elif top_sel == "visualizar":
            sub = _print_table_menu("VISUALIZAR PATRIMONIO", [
                "  Ver tabla completa",
                "  Ver tabla resumida",
                "  « Volver",
            ])
            # sub: 1=Completa, 2=Resumida, 3=Volver
            if sub == 1:
                _clear_content()
                show_last_saldos(by_category=False, pause=True, hide_zeros=True)
            elif sub == 2:
                _clear_content()
                show_last_saldos(by_category=True, pause=False)
                input("\nPresiona Enter para volver al menú...")

        # ── ANALIZAR CAJA ───────────────────────────────────────────────────
        elif top_sel == "caja":
            sub = _print_table_menu("ANALIZAR CAJA", [
                "  Activos y Pasivos de Corto Plazo",
                "  Revisar cupos TdC",
                "  Revisar pagos de Tarjetas de Crédito",
                "  « Volver",
            ])
            if sub == 1:
                _clear_content()
                show_caja()
            elif sub == 2:
                _clear_content()
                show_cupos_tdc()
            elif sub == 3:
                _clear_content()
                show_pagos_tdc()

        # ── COMPARAR FECHAS ─────────────────────────────────────────────────
        elif top_sel == "comparar":
            _clear_content()
            show_comparison()

        # ── CONFIGURACIÓN ───────────────────────────────────────────────────
        elif top_sel == "config":
            sub = _print_table_menu("CONFIGURACIÓN", [
                "  Gestión de scrapers",
                "  Sincronizar Bitwarden",
                "  « Volver",
            ])
            if sub == 1:
                manage_scrapers()
            elif sub == 2:
                _clear_content()
                _console.print("\n[dim]Sincronizando Bitwarden...[/dim]")
                result = subprocess.run(["bw", "sync"], capture_output=True, text=True, env=bw_env())
                if result.returncode == 0:
                    _console.print("[bold green]✓ Bitwarden sincronizado correctamente[/bold green]")
                else:
                    _console.print("[bold red]✗ Error al sincronizar Bitwarden[/bold red]")
                    _console.print(f"[dim]{result.stderr.strip()}[/dim]")
                input("\nPresiona Enter para continuar...")

        # ── FRACCIONAL ──────────────────────────────────────────────────────
        elif top_sel == "fraccional" and _FRACCIONAL_AVAILABLE:
            while True:
                sub = _print_table_menu("FRACCIONAL", [
                    "  Actualizar datos",
                    "  Analizar datos",
                    "  Configurar cuotas",
                    "  Definir parámetros",
                    "  « Volver",
                ])
                if sub == 1:
                    _frac_menu_actualizar()
                elif sub == 2:
                    _frac_menu_analizar()
                elif sub == 3:
                    _frac_menu_cuotas()
                elif sub == 4:
                    _frac_menu_parametros()
                else:
                    break

    except KeyboardInterrupt:
        pass
    except Exception as e_main:
        _console.print(f"\n[bold red][ERROR CRÍTICO] {e_main}[/bold red]")
    finally:
        _reset_terminal()
        _clear_terminal_buffer()

if __name__ == "__main__":
    main()


