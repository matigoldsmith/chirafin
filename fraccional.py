"""
fraccional.py — Exporta movimientos de Fraccional.cl a BD local (SQLite)
  1. Login PJ (owa605@gmail.com) → /app/movements → Exportar CSV → parsea → guarda
  2. Login PN (matigd@gmail.com) → lo mismo
Cada registro incluye columna `persona` (PJ|PN) y `extracted_at` (timestamp de extracción).

Uso:
  cd "/Users/mgoldsmithd/Scripts Claude AI" && source venv/bin/activate
  python3 fraccional.py
  python3 fraccional.py --solo pn      # solo Persona Natural
  python3 fraccional.py --solo pj      # solo Persona Jurídica
  python3 fraccional.py --headless     # sin ventana visible
  python3 fraccional.py --metrics      # solo mostrar métricas (sin descargar)
"""

import os, sys, re, subprocess, json, csv, io, time, argparse, datetime, math, hashlib, sqlite3
from pathlib import Path
from rich.console import Console
from rich.rule import Rule
from rich.panel import Panel
from rich.table import Table
from rich import box as rich_box

os.environ['PROMPT_TOOLKIT_NO_CPR'] = '1'

_console = Console()

DB_PATH = Path(__file__).parent / "fraccional.db"
TABLE   = "fraccional_movimientos"

# ── Mapeo CSV → columnas DB ──────────────────────────────────────
# El CSV de Fraccional usa ; como separador. Columnas reales (verificadas May 2026):
# ID;ID Activo;Nombre Activo;Fecha de confirmación;Monto al momento de reserva;
# Moneda al momento de reserva;Cantidad de Fracciones;Monto al momento de pago;
# Comisión al momento de pago;Moneda al momento de pago;Tipo de movimiento;Estado;
# Fecha de venta;Fecha de reembolso;Fecha de transformación;Fecha de finalización;
# Fecha de reinversión;Valor a la fecha;Fecha de valorización;Inversión original;
# Ganancia/Pérdida;Ganancia/Pérdida %;Moneda G/P;Comentarios
CSV_MAP = {
    "ID":                           "purchase_confirmation_id",
    "ID Activo":                    "unit_id",
    "Nombre Activo":                "unit_name",
    "Fecha de confirmación":        "confirmed_at",
    "Monto al momento de reserva":  "bid_amount",
    "Moneda al momento de reserva": "bid_currency",
    "Cantidad de Fracciones":       "bid_token_quantity",
    "Monto al momento de pago":     "bid_preferred_amount",
    "Comisión al momento de pago":  "bid_preferred_amount_fee",
    "Moneda al momento de pago":    "bid_preferred_currency",
    "Tipo de movimiento":           "kind",
    "Estado":                       "status",
    "Fecha de venta":               "amend_sold_at",
    "Fecha de reembolso":           "amend_refunded_at",
    "Fecha de transformación":      "amend_transformed_at",
    "Fecha de finalización":        "end_at",
    "Fecha de reinversión":         "amend_redeployed_at",
    "Valor a la fecha":             "current_value",
    "Fecha de valorización":        "value_at",
    "Inversión original":           "original_investment",
    "Ganancia/Pérdida":             "pnl_amount",
    "Ganancia/Pérdida %":           "pnl_percentage",
    "Comentarios":                  "comments",
    # "Moneda G/P" no tiene columna en DB — se ignora
}

# Columnas válidas en DB (para filtrar antes de upsert)
KNOWN_DB_COLS = {
    "purchase_confirmation_id", "persona", "kind", "unit_id", "unit_name",
    "confirmed_at", "value_at", "end_at", "disabled_at",
    "amend_sold_at", "amend_finished_at", "amend_refunded_at",
    "amend_redeployed_at", "amend_transformed_at", "amend_exiled_at",
    "status", "comments", "should_convert", "to_indicator",
    "bid_amount", "bid_currency", "bid_token_quantity",
    "bid_preferred_amount", "bid_preferred_currency", "bid_preferred_amount_fee",
    "original_investment", "current_value",
    "pnl_amount", "pnl_percentage", "pnl_amount_with_fee", "pnl_percentage_with_fee",
    "extracted_at", "variant_hash",
}

# Campos numéricos — limpiar $, puntos miles, coma decimal
NUMERIC_FIELDS = {
    "bid_amount", "bid_token_quantity", "bid_preferred_amount",
    "bid_preferred_amount_fee", "current_value", "pnl_amount",
    "pnl_percentage", "pnl_amount_with_fee", "pnl_percentage_with_fee",
    "original_investment",
}

# ── SQLite ──────────────────────────────────────────────────────
def _db_conn():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = _db_conn()
    conn.execute(f"""
        CREATE TABLE IF NOT EXISTS {TABLE} (
            purchase_confirmation_id TEXT NOT NULL,
            persona                  TEXT NOT NULL,
            variant_hash             TEXT NOT NULL DEFAULT '',
            kind                     TEXT,
            status                   TEXT,
            unit_id                  TEXT,
            unit_name                TEXT,
            comments                 TEXT,
            confirmed_at             TEXT,
            value_at                 TEXT,
            end_at                   TEXT,
            amend_sold_at            TEXT,
            amend_refunded_at        TEXT,
            amend_redeployed_at      TEXT,
            amend_transformed_at     TEXT,
            extracted_at             TEXT,
            bid_amount               REAL,
            bid_currency             TEXT,
            bid_token_quantity       REAL,
            bid_preferred_amount     REAL,
            bid_preferred_currency   TEXT,
            bid_preferred_amount_fee REAL,
            original_investment      REAL,
            current_value            REAL,
            pnl_amount               REAL,
            pnl_percentage           REAL,
            PRIMARY KEY (purchase_confirmation_id, persona, variant_hash)
        )
    """)
    # Tabla de configuración por purchase: num_cuotas editable
    conn.execute("""
        CREATE TABLE IF NOT EXISTS fraccional_config (
            purchase_confirmation_id TEXT NOT NULL,
            persona                  TEXT NOT NULL,
            num_cuotas               INTEGER NOT NULL DEFAULT 6,
            PRIMARY KEY (purchase_confirmation_id, persona)
        )
    """)
    conn.commit()
    conn.close()

def get_num_cuotas(pid: str, persona: str) -> int:
    """Retorna num_cuotas configurado para un purchase, o 6 por defecto."""
    conn = _db_conn()
    row = conn.execute(
        "SELECT num_cuotas FROM fraccional_config WHERE purchase_confirmation_id=? AND persona=?",
        (pid, persona)
    ).fetchone()
    conn.close()
    return row["num_cuotas"] if row else 6

def set_num_cuotas(pid: str, persona: str, n: int):
    conn = _db_conn()
    conn.execute(
        "INSERT INTO fraccional_config (purchase_confirmation_id, persona, num_cuotas) VALUES (?,?,?) "
        "ON CONFLICT(purchase_confirmation_id, persona) DO UPDATE SET num_cuotas=excluded.num_cuotas",
        (pid, persona, n)
    )
    conn.commit()
    conn.close()

def upsert_rows(rows: list[dict]):
    if not rows:
        return
    cols = list(rows[0].keys())
    placeholders = ", ".join("?" * len(cols))
    col_list     = ", ".join(cols)
    updates      = ", ".join(f"{c}=excluded.{c}" for c in cols
                             if c not in ("purchase_confirmation_id", "persona", "variant_hash"))
    sql = (
        f"INSERT INTO {TABLE} ({col_list}) VALUES ({placeholders}) "
        f"ON CONFLICT(purchase_confirmation_id, persona, variant_hash) DO UPDATE SET {updates}"
    )
    conn = _db_conn()
    conn.executemany(sql, [[r.get(c) for c in cols] for r in rows])
    conn.commit()
    conn.close()

# ── Bitwarden ────────────────────────────────────────────────────
def bw_env():
    env = os.environ.copy()
    env["NODE_TLS_REJECT_UNAUTHORIZED"] = "0"
    return env

def bw_unlock():
    _console.print("[yellow][[KEY]][/yellow] Desbloqueando Bitwarden...")
    master = subprocess.run(
        ["security", "find-generic-password", "-a", "bitwarden", "-s", "bitwarden-master", "-w"],
        capture_output=True, text=True
    ).stdout.strip()
    result = subprocess.run(["bw", "unlock", master, "--raw"],
                            capture_output=True, text=True, env=bw_env())
    session = result.stdout.strip()
    if session:
        os.environ["BW_SESSION"] = session
        _console.print("[green][[OK]][/green]  Bitwarden desbloqueado")
    else:
        raise Exception("No se pudo desbloquear Bitwarden")

def bw_get(field, item_name):
    env = bw_env()
    r = subprocess.run(["bw", "get", field, item_name], capture_output=True, text=True, env=env)
    if "Session key is invalid" in r.stderr or not r.stdout.strip():
        bw_unlock()
        r = subprocess.run(["bw", "get", field, item_name], capture_output=True, text=True, env=bw_env())
    return r.stdout.strip()

def bw_list_search(term):
    env = bw_env()
    r = subprocess.run(["bw", "list", "items", "--search", term], capture_output=True, text=True, env=env)
    if "Session key is invalid" in r.stderr or not r.stdout.strip():
        bw_unlock()
        r = subprocess.run(["bw", "list", "items", "--search", term], capture_output=True, text=True, env=bw_env())
    return json.loads(r.stdout) if r.stdout.strip() else []

# ── Login / Logout Fraccional ────────────────────────────────────
def _logout(page, label=""):
    try:
        page.goto("https://www.fraccional.cl/app/auth/logout",
                  wait_until="domcontentloaded", timeout=15000)
        page.wait_for_timeout(2000)
        btn = page.locator("button", has_text=re.compile(r"Cerrar sesión", re.IGNORECASE))
        if btn.count() > 0:
            btn.first.click()
            page.wait_for_timeout(1500)
            _console.print(f"[dim]  [{label}] logout OK[/dim]")
    except Exception as e:
        _console.print(f"[dim]  [{label}] logout error (ignorado): {e}[/dim]")

def _login(page, username, password, label=""):
    _console.print(f"[sky_blue3]  [{label}][/sky_blue3] Navegando al login...")
    page.goto("https://www.fraccional.cl/app/auth",
              wait_until="domcontentloaded", timeout=30000)
    page.wait_for_timeout(1500)

    page.wait_for_selector('input[type="email"]', state="visible", timeout=15000)
    page.locator('input[type="email"]').first.fill(username)
    page.wait_for_timeout(400)
    page.locator('button[type="submit"]').first.click()
    _console.print(f"[sky_blue3]  [{label}][/sky_blue3] Email enviado, esperando password...")

    page.wait_for_selector('input[type="password"]', state="visible", timeout=15000)
    page.locator('input[type="password"]').first.fill(password)
    page.wait_for_timeout(400)
    page.locator('button[type="submit"]').first.click()
    _console.print(f"[sky_blue3]  [{label}][/sky_blue3] Credenciales enviadas, esperando app...")

    page.wait_for_url(lambda url: "/app" in url and "/auth" not in url, timeout=60000)
    page.wait_for_timeout(2000)
    _console.print(f"[green]  [{label}][/green] Login OK — {page.url}")

# ── Exportar CSV ─────────────────────────────────────────────────
def _export_and_read(page, label="", tmp_dir=None):
    """
    Navega a /app/movements, hace click en 'Exportar CSV',
    espera el download, lee el archivo y retorna lista de dicts.
    """
    _console.print(f"[sky_blue3]  [{label}][/sky_blue3] Navegando a Movimientos...")
    page.goto("https://www.fraccional.cl/app/movements?include_fee=yes",
              wait_until="domcontentloaded", timeout=30000)
    page.wait_for_timeout(3000)

    # Esperar que el botón Exportar CSV esté visible
    try:
        page.wait_for_selector(
            "button:has-text('Exportar CSV'), button:has-text('Export CSV')",
            state="visible", timeout=20000
        )
        _console.print(f"[sky_blue3]  [{label}][/sky_blue3] Botón 'Exportar CSV' encontrado")
    except Exception:
        _console.print(f"[yellow]  [{label}] Botón no encontrado — intentando igual...[/yellow]")


    # Guardar CSV en carpeta backups del proyecto
    out_dir = tmp_dir or str(Path(__file__).parent / "backups")
    Path(out_dir).mkdir(exist_ok=True)
    ts      = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = Path(out_dir) / f"fraccional_{label.lower()}_{ts}.csv"

    _console.print(f"[sky_blue3]  [{label}][/sky_blue3] Click en Exportar CSV...")
    btn = page.locator("button", has_text=re.compile(r"exportar csv", re.IGNORECASE)).first
    if btn.count() == 0:
        btn = page.locator("button", has_text=re.compile(r"export", re.IGNORECASE)).first

    with page.expect_download(timeout=60000) as dl_info:
        btn.click()

    download = dl_info.value
    download.save_as(str(out_path))
    _console.print(f"[green]  [{label}][/green] CSV descargado → {out_path.name}")

    return str(out_path)

# ── Parsear CSV ──────────────────────────────────────────────────
def _parse_clean(val, field):
    """Limpia y tipea un valor según su campo."""
    if val is None or str(val).strip() in ("", "-", "N/A", "null"):
        return None

    s = str(val).strip()

    # Numérico
    if field in NUMERIC_FIELDS:
        # Quitar símbolo $, %, espacios
        s = re.sub(r"[\$%\s]", "", s)
        # Formato chileno: 1.234.567,89 → 1234567.89
        if re.search(r"\d\.\d{3}", s) and "," in s:
            s = s.replace(".", "").replace(",", ".")
        elif re.search(r"\d\.\d{3}", s):
            s = s.replace(".", "")
        elif "," in s:
            s = s.replace(",", ".")
        try:
            v = float(s)
            return None if (math.isnan(v) or math.isinf(v)) else v
        except ValueError:
            return None

    # Timestamp — el CSV de Fraccional ya trae ISO 8601 (con tz), pasar directo
    TIMESTAMP_FIELDS = {
        "confirmed_at", "value_at", "end_at", "disabled_at",
        "amend_sold_at", "amend_finished_at", "amend_refunded_at",
        "amend_redeployed_at", "amend_transformed_at", "amend_exiled_at",
    }
    if field in TIMESTAMP_FIELDS:
        return s if s else None

    return s

def parse_csv_file(path: str, persona: str, extracted_at: str) -> list[dict]:
    """
    Lee el CSV, aplica CSV_MAP para renombrar columnas,
    limpia tipos, agrega `persona` y `extracted_at`.
    Retorna lista de dicts listos para guardar en BD local.
    """
    rows = []
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f, delimiter=";")
        raw_cols = reader.fieldnames or []
        _console.print(f"[dim]  Columnas CSV ({len(raw_cols)}): {raw_cols}[/dim]")

        for raw_row in reader:
            record = {}
            for raw_col, raw_val in raw_row.items():
                if raw_col is None:          # columna extra por trailing ;
                    continue
                col = raw_col.strip()
                db_col = CSV_MAP.get(col)
                if db_col:
                    record[db_col] = _parse_clean(raw_val, db_col)
                # columnas no mapeadas se ignoran (no existen en DB)

            if not record.get("purchase_confirmation_id"):
                _console.print(f"[yellow]  Row sin ID — omitida[/yellow]")
                continue

            record["persona"]      = persona
            record["extracted_at"] = extracted_at

            # variant_hash: SHA-256 de los 4 campos que varían entre variantes del mismo ID.
            # Garantiza PK única sin perder ninguna fila.
            _hash_src = "|".join(str(record.get(f) or "") for f in
                                 ("current_value", "original_investment", "pnl_amount", "pnl_percentage"))
            record["variant_hash"] = hashlib.sha256(_hash_src.encode()).hexdigest()[:16]

            # Filtrar solo columnas que existen en la tabla
            record = {k: v for k, v in record.items() if k in KNOWN_DB_COLS}
            rows.append(record)

    # Eliminar duplicados exactos (todas las columnas iguales) — no debería haber,
    # pero protección ante CSVs con filas repetidas idénticas.
    seen_hashes: set[str] = set()
    deduped = []
    for r in rows:
        key = (r["purchase_confirmation_id"], r["persona"], r["variant_hash"])
        if key not in seen_hashes:
            seen_hashes.add(key)
            deduped.append(r)

    if len(deduped) < len(rows):
        _console.print(f"[dim]  Filas exactamente duplicadas omitidas: {len(rows) - len(deduped)}[/dim]")

    return deduped

# ── Mostrar resumen ──────────────────────────────────────────────
def _print_summary(persona: str, rows: list[dict], saved: int):
    t = Table(box=rich_box.SIMPLE_HEAVY, show_header=True, header_style="bold sky_blue3",
              title=f"[bold sky_blue3]{persona}[/bold sky_blue3] — {saved} registros guardados")
    t.add_column("Activo",    style="white",  max_width=40)
    t.add_column("Tipo",      style="cyan",   width=10)
    t.add_column("Fecha",     style="dim",    width=12)
    t.add_column("Monto",     style="green",  justify="right", width=14)
    t.add_column("Estado",    style="yellow", width=8)

    for r in rows[:10]:
        unit  = str(r.get("unit_name") or "—")[:38]
        kind  = str(r.get("kind") or "—")
        fecha = str(r.get("confirmed_at") or "—")[:10]
        monto = r.get("bid_amount")
        monto_str = f"${monto:,.0f}".replace(",", ".") if monto else "—"
        estado = str(r.get("status") or "—")
        t.add_row(unit, kind, fecha, monto_str, estado)

    if len(rows) > 10:
        t.add_row(f"[dim]... y {len(rows)-10} más[/dim]", "", "", "", "")

    _console.print(t)

# ── Métricas por purchase_id ──────────────────────────────────────
# Nota sobre comisión:
#   `original_investment` (variante principal) = bid_preferred_amount + bid_preferred_amount_fee
#   Es decir, la comisión YA ESTÁ incluida en original_investment.
#   → rentabilidad_total = valor_actual - original_investment  (sin restar fee por separado)
#   → rentabilidad_pct   = valor_actual / original_investment  - 1
#
# Solo se calcula rentabilidad para kind IN ('purchase','market').
# kind='movement' = reinversión de arriendo, no es una compra directa.

KIND_COMPRA = ("purchase", "market")

def show_metrics(persona_filter: str = None, tasa_dap: float = 0.05):
    """
    Agrega los 4 variantes por purchase_id y muestra métricas.
    Solo usa la extracción más reciente por (purchase_id, persona).
    tasa_dap: rentabilidad de referencia (depósito a plazo), default 5%.
    """
    conn  = _db_conn()
    where = f"AND m.persona = '{persona_filter}'" if persona_filter else ""
    rows  = conn.execute(f"""
        WITH latest AS (
            SELECT purchase_confirmation_id, persona, MAX(extracted_at) AS max_ts
            FROM {TABLE}
            GROUP BY purchase_confirmation_id, persona
        )
        SELECT
            m.purchase_confirmation_id          AS pid,
            m.persona,
            m.unit_id,
            m.unit_name,
            m.bid_currency,
            m.kind,
            m.confirmed_at,
            MAX(m.bid_preferred_amount_fee)     AS comision,
            SUM(m.original_investment)          AS capital,
            SUM(m.current_value)                AS valor_actual
        FROM {TABLE} m
        JOIN latest l
          ON m.purchase_confirmation_id = l.purchase_confirmation_id
         AND m.persona = l.persona
         AND m.extracted_at = l.max_ts
        WHERE m.status = 'active' {where}
        GROUP BY m.purchase_confirmation_id, m.persona, m.unit_id, m.unit_name,
                 m.bid_currency, m.kind, m.confirmed_at
        ORDER BY m.unit_name, m.confirmed_at
    """).fetchall()
    conn.close()

    if not rows:
        _console.print("[yellow]Sin datos en la BD local. Corré primero sin --metrics.[/yellow]")
        return

    today = datetime.date.today()

    def fmtnum(v):
        if v is None: return "—"
        return f"{v:,.0f}".replace(",", "X").replace(".", ",").replace("X", ".")

    def colored(v, fmt=None):
        s = fmt(v) if fmt else fmtnum(v)
        c = "green" if v >= 0 else "red"
        return f"[{c}]{s}[/{c}]"

    t = Table(box=rich_box.SIMPLE_HEAVY, show_header=True, header_style="bold sky_blue3",
              title=f"[bold sky_blue3]Métricas por inversión[/bold sky_blue3]  [dim](DAP ref: {tasa_dap*100:.1f}%)[/dim]")
    t.add_column("Activo",        style="white", max_width=32)
    t.add_column("P",             style="dim",   width=3)
    t.add_column("Kind",          style="dim",   width=9)
    t.add_column("Moneda",        style="dim",   width=5)
    t.add_column("Fecha",         style="dim",   width=10)
    t.add_column("Días",          justify="right", width=5)
    t.add_column("Cuotas",        justify="right", style="dim", width=7)
    t.add_column("Capital",       justify="right", style="cyan", width=12)
    t.add_column("Valor act.",    justify="right", style="green", width=12)
    t.add_column("Rent. total",   justify="right", width=12)
    t.add_column("Rent. %",       justify="right", width=8)
    t.add_column("TIR anual",     justify="right", width=9)
    t.add_column("vs DAP",        justify="right", width=8)

    for r in rows:
        capital      = r["capital"] or 0
        valor        = r["valor_actual"] or 0
        comision     = r["comision"] or 0
        fecha        = str(r["confirmed_at"] or "")[:10]
        moneda       = str(r["bid_currency"] or "")
        kind         = str(r["kind"] or "")
        pid          = r["pid"]
        persona      = r["persona"]
        num_cuotas   = get_num_cuotas(pid, persona)

        # Días desde inversión
        try:
            inv_date = datetime.date.fromisoformat(fecha)
            dias = (today - inv_date).days
        except Exception:
            dias = 0

        # Rentabilidad (solo para compras directas — purchase / market)
        es_compra = kind in KIND_COMPRA
        if es_compra and capital > 0:
            # original_investment ya incluye comisión → no restar fee por separado
            rent_total = valor - capital
            rent_pct   = (valor / capital - 1) * 100
        else:
            rent_total = None
            rent_pct   = None

        # TIR anualizada simple: (valor/capital)^(365/días) - 1
        if capital > 0 and dias > 30:
            tir = ((valor / capital) ** (365 / dias) - 1) * 100
        else:
            tir = None

        # Diferencial vs DAP
        vs_dap = (tir - tasa_dap * 100) if tir is not None else None

        # Formateo
        rent_str     = colored(rent_total) if rent_total is not None else "[dim]—[/dim]"
        rent_pct_str = colored(rent_pct, lambda v: f"{v:+.1f}%") if rent_pct is not None else "[dim]—[/dim]"
        tir_str      = colored(tir, lambda v: f"{v:+.1f}%") if tir is not None else "[dim]—[/dim]"
        vs_dap_str   = colored(vs_dap, lambda v: f"{v:+.1f}pp") if vs_dap is not None else "[dim]—[/dim]"

        t.add_row(
            str(r["unit_name"] or "")[:31],
            persona,
            kind,
            moneda,
            fecha,
            str(dias),
            str(num_cuotas),
            fmtnum(capital),
            fmtnum(valor),
            rent_str,
            rent_pct_str,
            tir_str,
            vs_dap_str,
        )

    _console.print(t)

# ── Runner principal ─────────────────────────────────────────────
def run_persona(browser, persona: str, username: str, password: str,
                extracted_at: str, use_iso_context: bool = False):
    """
    Ejecuta el flujo completo para un perfil (PN o PJ).
    use_iso_context=True → contexto aislado (para PJ cuando PN ya corrió antes).
    """
    label = persona
    page  = None
    ctx   = None

    try:
        _console.print()
        _console.print(Panel(
            f"[bold]{'Persona Jurídica — One Western' if persona == 'PJ' else 'Persona Natural — Matias'}[/bold]",
            style="sky_blue3", expand=False
        ))

        ctx_opts = {"viewport": {"width": 1280, "height": 800}}
        if use_iso_context:
            ctx = browser.new_context(**ctx_opts)
        else:
            ctx = browser.new_context(**ctx_opts)
        ctx.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
        page = ctx.new_page()

        # Logout previo (por si quedó sesión)
        _logout(page, label)

        # Login
        _login(page, username, password, label)

        # Exportar CSV
        csv_path = _export_and_read(page, label)

        # Parsear
        _console.print(f"[sky_blue3]  [{label}][/sky_blue3] Parseando CSV...")
        rows = parse_csv_file(csv_path, persona, extracted_at)
        _console.print(f"[sky_blue3]  [{label}][/sky_blue3] {len(rows)} registros parseados")

        if not rows:
            _console.print(f"[yellow]  [{label}] Sin registros — nada que guardar[/yellow]")
            return 0

        # Guardar en SQLite
        upsert_rows(rows)
        _console.print(f"[green]  [{label}][/green] ✓ {len(rows)} registros en BD local")
        _print_summary(persona, rows, len(rows))
        return len(rows)

    except Exception as e:
        _console.print(f"[red]  [{label}] ERROR: {e}[/red]")
        import traceback; traceback.print_exc()
        return 0

    finally:
        if page:
            try: _logout(page, label)
            except: pass
            try: page.close()
            except: pass
        if ctx:
            try: ctx.close()
            except: pass

# ── main ─────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="Fraccional → BD local")
    parser.add_argument("--solo",     choices=["pn", "pj"], help="Correr solo PN o PJ")
    parser.add_argument("--headless", action="store_true",  help="Sin ventana visible")
    parser.add_argument("--metrics",   action="store_true",  help="Solo mostrar métricas (sin descargar)")
    parser.add_argument("--tasa-dap", type=float, default=0.05, metavar="TASA",
                        help="Rentabilidad DAP de referencia, ej: 0.05 = 5%% (default: 0.05)")
    args = parser.parse_args()

    init_db()

    tasa_dap = args.tasa_dap

    if args.metrics:
        _console.print(Rule("[bold sky_blue3]FRACCIONAL — Métricas[/bold sky_blue3]", style="sky_blue3"))
        show_metrics(
            persona_filter="PJ" if args.solo == "pj" else ("PN" if args.solo == "pn" else None),
            tasa_dap=tasa_dap,
        )
        return

    _console.print(Rule("[bold sky_blue3]FRACCIONAL → BD local[/bold sky_blue3]", style="sky_blue3"))
    extracted_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
    _console.print(f"[dim]extracted_at = {extracted_at}[/dim]")

    # Desbloquear Bitwarden de entrada para que las búsquedas posteriores sean rápidas
    bw_unlock()

    run_pj = args.solo in (None, "pj")
    run_pn = args.solo in (None, "pn")

    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch(
            channel="chrome",
            headless=args.headless,
            args=["--disable-blink-features=AutomationControlled"],
            ignore_default_args=["--enable-automation"],
        )

        total = 0

        # ── PJ ──────────────────────────────────────────────────
        if run_pj:
            _console.print("\n[sky_blue3][[PJ]][/sky_blue3] Obteniendo credenciales PJ...")
            items  = bw_list_search("fraccional")
            pj_bw  = next((i for i in items if "owa605" in str(i.get("login", {}).get("username", ""))), None)
            if not pj_bw:
                _console.print("[red][PJ] No encontré credenciales en Bitwarden[/red]")
            else:
                total += run_persona(
                    browser, "PJ",
                    pj_bw["login"]["username"],
                    pj_bw["login"]["password"],
                    extracted_at
                )

        # ── PN ──────────────────────────────────────────────────
        if run_pn:
            _console.print("\n[sky_blue3][[PN]][/sky_blue3] Obteniendo credenciales PN...")
            pn_user = bw_get("username", "fraccional.cl - Persona Natural")
            pn_pass = bw_get("password", "fraccional.cl - Persona Natural")
            if not pn_user or not pn_pass:
                _console.print("[red][PN] Sin credenciales PN[/red]")
            else:
                total += run_persona(
                    browser, "PN",
                    pn_user, pn_pass,
                    extracted_at,
                    use_iso_context=True
                )

        browser.close()

    _console.print()
    _console.print(Rule(f"[bold green]Listo — {total} registros guardados en {DB_PATH.name}[/bold green]",
                        style="green"))
    _console.print()
    show_metrics(
        persona_filter="PJ" if args.solo == "pj" else ("PN" if args.solo == "pn" else None),
        tasa_dap=tasa_dap,
    )

if __name__ == "__main__":
    main()
