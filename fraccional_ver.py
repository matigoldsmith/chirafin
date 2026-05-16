"""
fraccional_ver.py — Visor de métricas locales de Fraccional.cl
================================================================
Lee directamente desde fraccional.db (sin scraping).
Uso:
  python3 fraccional_ver.py                  # pregunta por parámetros (Enter para default)
  python3 fraccional_ver.py --tasa-dap 0.04 --max-cuotas 24 --premium 0.15
  python3 fraccional_ver.py --por-propiedad
PARÁMETROS CONFIGURABLES
------------------------
  --tasa-dap TASA    : Tasa de Depósito a Plazo (costo de oportunidad). Default: 0.05 (5%)
  --max-cuotas N     : Máximo de cuotas sin interés permitido por el marketplace. Default: 12
  --premium P        : Sobrerrentabilidad requerida sobre tasa_dap (ej: 0.2 = 20%). Default: 0.2
  --por-propiedad    : Vista agregada por propiedad (sin recomendaciones)
"""
import argparse
import datetime
import sqlite3
from collections import defaultdict
from pathlib import Path
from rich.console import Console
from rich.rule import Rule
from rich.table import Table
from rich import box as rich_box
_console = Console()
DB_PATH = Path(__file__).parent / "fraccional.db"
TABLE = "fraccional_movimientos"
KIND_COMPRA = ("purchase", "market")
# ----------------------------------------------------------------------
# BASE DE DATOS
# ----------------------------------------------------------------------
def _db_conn():
    if not DB_PATH.exists():
        _console.print(f"[red]No existe {DB_PATH.name}. Corré fraccional.py primero.[/red]")
        raise SystemExit(1)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE IF NOT EXISTS fraccional_config (
            purchase_confirmation_id TEXT NOT NULL,
            persona                  TEXT NOT NULL,
            num_cuotas               INTEGER NOT NULL DEFAULT 6,
            PRIMARY KEY (purchase_confirmation_id, persona)
        )
    """)
    conn.commit()
    return conn
def get_num_cuotas(conn, pid, persona):
    row = conn.execute(
        "SELECT num_cuotas FROM fraccional_config WHERE purchase_confirmation_id=? AND persona=?",
        (pid, persona)
    ).fetchone()
    return row["num_cuotas"] if row else 6
# ----------------------------------------------------------------------
# FORMATEO
# ----------------------------------------------------------------------
def fmtnum(v, signed=False):
    if v is None:
        return "—"
    sign = "+" if signed and v > 0 else ("-" if v < 0 else "")
    return sign + f"{abs(v):,.0f}".replace(",", "X").replace(".", ",").replace("X", ".")
def colored(v, fmt_fn=None, signed=False):
    if v is None:
        return "[dim]—[/dim]"
    s = fmt_fn(v) if fmt_fn else fmtnum(v, signed=signed)
    c = "green" if v >= 0 else "red"
    return f"[{c}]{s}[/{c}]"
pct_str = lambda v: f"{v:+.1f}%"
pp_str = lambda v: f"{v:+.1f}pp"
# ----------------------------------------------------------------------
# XIRR
# ----------------------------------------------------------------------
def xirr(cashflows, dates):
    if len(cashflows) < 2:
        return None
    d0 = dates[0]
    years = [(d - d0).days / 365.0 for d in dates]
    def npv(r):
        try:
            return sum(cf / (1 + r) ** t for cf, t in zip(cashflows, years))
        except Exception:
            return float("inf")
    def dnpv(r):
        try:
            return sum(-t * cf / (1 + r) ** (t + 1) for cf, t in zip(cashflows, years))
        except Exception:
            return 0.0
    def _try(guess):
        r = guess
        for _ in range(200):
            try:
                f = npv(r)
                df = dnpv(r)
                if not isinstance(f, (int, float)) or not isinstance(df, (int, float)):
                    return None
                if abs(df) < 1e-14:
                    break
                r_new = r - f / df
                if not isinstance(r_new, (int, float)) or r_new != r_new:
                    return None
                if abs(r_new - r) < 1e-9:
                    return r_new if -0.9999 < r_new < 100 else None
                r = r_new
            except Exception:
                return None
        return None
    for guess in (0.1, 0.5, -0.1, 2.0, 0.01):
        result = _try(guess)
        if result is not None:
            return result
    return None
# ----------------------------------------------------------------------
# ATRIBUCIÓN DE MOVEMENTS
# ----------------------------------------------------------------------
def _atribuir_movements(conn, unit_id, persona):
    rows = conn.execute(f"""
        SELECT purchase_confirmation_id AS pid,
               kind,
               confirmed_at,
               MAX(bid_token_quantity)  AS fracciones,
               SUM(original_investment) AS capital,
               SUM(current_value)       AS valor
        FROM {TABLE}
        WHERE unit_id=? AND persona=? AND status='active'
        GROUP BY purchase_confirmation_id, kind, confirmed_at
        ORDER BY confirmed_at
    """, (unit_id, persona)).fetchall()
    fracc_por_pid = {}
    atrib = {}
    for r in rows:
        pid = r["pid"]
        kind = r["kind"]
        fracc = r["fracciones"] or 0
        cap = r["capital"] or 0
        val = r["valor"] or 0
        if kind in KIND_COMPRA:
            fracc_por_pid[pid] = fracc_por_pid.get(pid, 0) + fracc
            if pid not in atrib:
                atrib[pid] = {"capital_mov": 0.0, "valor_mov": 0.0}
        elif kind == "movement":
            fracc_compras = {p: f for p, f in fracc_por_pid.items() if p in atrib}
            total_fracc_compras = sum(fracc_compras.values())
            if total_fracc_compras > 0:
                for cpid, cf in fracc_compras.items():
                    peso = cf / total_fracc_compras
                    atrib[cpid]["capital_mov"] += cap * peso
                    atrib[cpid]["valor_mov"] += val * peso
            fracc_por_pid[pid] = fracc_por_pid.get(pid, 0) + fracc
    return atrib
# ----------------------------------------------------------------------
# MESES RESTANTES DE HOLD
# ----------------------------------------------------------------------
def calcular_meses_restantes_hold(inv_inicial, valor_actual, fecha_dt, tir_b_pct, umbral, max_months=120):
    """
    Para la inversión REAL ya existente: ¿cuántos meses más desde HOY
    hay que mantenerla para que TIR A supere el umbral?

    Proyecta valor_actual creciendo a tir_b. Encuentra el primer mes futuro
    donde xirr([-inv_inicial en fecha_dt, +val_proyectado en ese_mes]) > umbral.
    Retorna meses desde hoy, o None si no se alcanza en max_months.
    """
    if tir_b_pct is None or inv_inicial <= 0 or valor_actual <= 0 or fecha_dt is None:
        return None
    tir_b = tir_b_pct / 100
    hoy = datetime.date.today()
    for t in range(1, max_months + 1):
        exit_date = hoy + datetime.timedelta(days=30 * t)
        val_exit = valor_actual * (1 + tir_b) ** (t / 12)
        rate = xirr([-inv_inicial, val_exit], [fecha_dt, exit_date])
        if rate is not None and rate > umbral:
            return t
    return None
# ----------------------------------------------------------------------
# DESCRIPCIÓN DE TIRS (al inicio)
# ----------------------------------------------------------------------
def print_descripcion_tirs(tasa_dap, premium, max_cuotas):
    umbral = tasa_dap * (1 + premium)
    _console.print("\n[bold cyan]Definicion de las TIR utilizadas:[/bold cyan]")
    _console.print("  [green]TIR A[/green] : Rentabilidad al [bold]contado incluyendo comision[/bold]. Pago todo el dia 1.")
    _console.print("  [green]TIR B[/green] : Rentabilidad al [bold]contado excluyendo comision[/bold]. Mide la calidad del activo.")
    _console.print("  [green]TIR C[/green] : Rentabilidad [bold]pagando en cuotas (incluye comision)[/bold]. Evalua el apalancamiento.")
    _console.print("  [green]TIR D[/green] : Rentabilidad [bold]pagando en cuotas (excluye comision)[/bold]. Compara con DAP.")
    _console.print(f"\n[bold]Parametros actuales:[/bold] tasa DAP = {tasa_dap*100:.1f}%, Premium = {premium*100:.0f}% -> Umbral = {umbral*100:.1f}%, Max cuotas = {max_cuotas}\n")
# ----------------------------------------------------------------------
# FUNCIÓN PARA CALCULAR TIR DE PORTAFOLIO
# ----------------------------------------------------------------------
def calcular_tir_portafolio(flujos_por_fecha):
    if not flujos_por_fecha:
        return None
    by_date = defaultdict(float)
    for fecha, flujo in flujos_por_fecha:
        by_date[fecha] += flujo
    sorted_dates = sorted(by_date)
    cfs = [by_date[d] for d in sorted_dates]
    rate = xirr(cfs, sorted_dates)
    return rate * 100 if rate is not None else None
# ----------------------------------------------------------------------
# VISTA POR COMPRA (TODO JUNTO)
# ----------------------------------------------------------------------
def view_por_purchase(tasa_dap, max_cuotas, premium):
    print_descripcion_tirs(tasa_dap, premium, max_cuotas)
    conn = _db_conn()
    _view_por_purchase_unificado(conn, tasa_dap, max_cuotas, premium)
    conn.close()
def _view_por_purchase_unificado(conn, tasa_dap, max_cuotas, premium):
    compras = conn.execute(f"""
        WITH latest AS (
            SELECT purchase_confirmation_id, persona, MAX(extracted_at) AS max_ts
            FROM {TABLE} GROUP BY purchase_confirmation_id, persona
        )
        SELECT
            m.purchase_confirmation_id          AS pid,
            m.persona,
            m.unit_id,
            m.unit_name,
            m.kind,
            m.confirmed_at,
            MAX(m.bid_preferred_amount_fee)     AS comision,
            MAX(m.bid_preferred_amount)
              + MAX(m.bid_preferred_amount_fee) AS capital,
            SUM(m.current_value)                AS valor_actual
        FROM {TABLE} m
        JOIN latest l ON m.purchase_confirmation_id = l.purchase_confirmation_id
                      AND m.persona = l.persona AND m.extracted_at = l.max_ts
        WHERE m.status = 'active' AND m.kind IN ('purchase','market')
        GROUP BY m.purchase_confirmation_id, m.persona, m.unit_id, m.unit_name,
                 m.kind, m.confirmed_at
        ORDER BY m.unit_name, m.confirmed_at
    """).fetchall()
    if not compras:
        _console.print("[yellow]Sin datos para mostrar.[/yellow]")
        return
    # Pre-calcular movements
    cache_atrib = {}
    for r in compras:
        key = (r["unit_id"], r["persona"])
        if key not in cache_atrib:
            cache_atrib[key] = _atribuir_movements(conn, r["unit_id"], r["persona"])
    today = datetime.date.today()
    umbral = tasa_dap * (1 + premium)
    grupos = {
        "esperar": [],
        "vender_malo": [],
        "vender_bajo_dap": [],
        "comprar_urgente": [],
        "comprar_sin_restriccion": [],
        "comprar_apalancado": [],
        "mantener": [],
    }
    # Acumuladores para totales por tipo y flujos de portafolio
    totales_por_tipo = {
        "PN": {"inv": 0.0, "val": 0.0, "dias_ponderado": 0.0, "peso_inv": 0.0, "flujos_a": [], "flujos_b": [], "flujos_c": [], "flujos_d": []},
        "PJ": {"inv": 0.0, "val": 0.0, "dias_ponderado": 0.0, "peso_inv": 0.0, "flujos_a": [], "flujos_b": [], "flujos_c": [], "flujos_d": []},
    }
    # Acumuladores para resumen por propiedad (sin distinguir PN/PJ)
    totales_por_propiedad = defaultdict(lambda: {
        "inv": 0.0, "val": 0.0, "dias_ponderado": 0.0, "peso_inv": 0.0,
        "flujos_a": [], "flujos_b": [], "flujos_c": [], "flujos_d": [],
        "name": ""
    })
    for row in compras:
        inv_inicial = row["capital"] or 0
        comision = row["comision"] or 0
        valor = row["valor_actual"] or 0
        pid = row["pid"]
        persona = row["persona"]
        unit_id = row["unit_id"]
        unit_name = row["unit_name"] or ""
        cuotas_reales = get_num_cuotas(conn, pid, persona)
        fecha = str(row["confirmed_at"] or "")[:10]
        atrib = cache_atrib.get((row["unit_id"], persona), {}).get(pid, {})
        val_mov = atrib.get("valor_mov", 0.0)
        valor_actual = valor + val_mov
        inv_sin_com = inv_inicial - comision
        try:
            fecha_dt = datetime.date.fromisoformat(fecha)
            dias = (today - fecha_dt).days
        except Exception:
            fecha_dt = None
            dias = 0
        ganancia = valor_actual - inv_inicial if inv_inicial > 0 else None
        rent_pct = (valor_actual / inv_inicial - 1) * 100 if inv_inicial > 0 else None
        tir_a = tir_b = tir_c = tir_d = None
        meses_restantes = None
        # Acumular flujos para portafolio por tipo y por propiedad
        if fecha_dt and dias > 30:
            if inv_inicial > 0:
                # TIR A
                totales_por_tipo[persona]["flujos_a"].append((fecha_dt, -inv_inicial))
                totales_por_tipo[persona]["flujos_a"].append((today, valor_actual))
                totales_por_propiedad[unit_id]["flujos_a"].append((fecha_dt, -inv_inicial))
                totales_por_propiedad[unit_id]["flujos_a"].append((today, valor_actual))
                rate = xirr([-inv_inicial, valor_actual], [fecha_dt, today])
                tir_a = rate * 100 if rate is not None else None
                # TIR C real (cuotas empiezan en mes 1, no día 0)
                cuota_m = inv_inicial / cuotas_reales
                fechas_cuotas = [fecha_dt + datetime.timedelta(days=30 * i) for i in range(1, cuotas_reales + 1)]
                for fc in fechas_cuotas:
                    totales_por_tipo[persona]["flujos_c"].append((fc, -cuota_m))
                    totales_por_propiedad[unit_id]["flujos_c"].append((fc, -cuota_m))
                totales_por_tipo[persona]["flujos_c"].append((today, valor_actual))
                totales_por_propiedad[unit_id]["flujos_c"].append((today, valor_actual))
                rate_c = xirr([-cuota_m] * cuotas_reales + [valor_actual], fechas_cuotas + [today])
                tir_c = rate_c * 100 if rate_c is not None else None
            if inv_sin_com > 0:
                # TIR B
                totales_por_tipo[persona]["flujos_b"].append((fecha_dt, -inv_sin_com))
                totales_por_tipo[persona]["flujos_b"].append((today, valor_actual))
                totales_por_propiedad[unit_id]["flujos_b"].append((fecha_dt, -inv_sin_com))
                totales_por_propiedad[unit_id]["flujos_b"].append((today, valor_actual))
                rate_b = xirr([-inv_sin_com, valor_actual], [fecha_dt, today])
                tir_b = rate_b * 100 if rate_b is not None else None
                # TIR D real (cuotas empiezan en mes 1, no día 0)
                cuota_m = inv_sin_com / cuotas_reales
                fechas_cuotas = [fecha_dt + datetime.timedelta(days=30 * i) for i in range(1, cuotas_reales + 1)]
                for fc in fechas_cuotas:
                    totales_por_tipo[persona]["flujos_d"].append((fc, -cuota_m))
                    totales_por_propiedad[unit_id]["flujos_d"].append((fc, -cuota_m))
                totales_por_tipo[persona]["flujos_d"].append((today, valor_actual))
                totales_por_propiedad[unit_id]["flujos_d"].append((today, valor_actual))
                rate_d = xirr([-cuota_m] * cuotas_reales + [valor_actual], fechas_cuotas + [today])
                tir_d = rate_d * 100 if rate_d is not None else None
        # --- Árbol de decisión ---
        tir_a_dec = tir_a / 100 if tir_a is not None else None
        tir_b_dec = tir_b / 100 if tir_b is not None else None
        tir_d_dec = tir_d / 100 if tir_d is not None else None
        tir_c_dec = tir_c / 100 if tir_c is not None else None
        grupo = None
        if dias <= 90:
            grupo = "esperar"
        elif dias > 90:
            # Guard: sin datos suficientes para calcular TIR -> esperar más
            if tir_b_dec is None and tir_d_dec is None and tir_a_dec is None:
                grupo = "esperar"
            elif tir_b_dec is not None and tir_b_dec < 0:
                grupo = "vender_malo"
            elif tir_d_dec is not None and tir_d_dec < tasa_dap:
                grupo = "vender_bajo_dap"
            elif tir_a_dec is not None and tir_a_dec > umbral * 1.5:
                grupo = "comprar_urgente"
            elif tir_a_dec is not None and tir_a_dec > umbral:
                grupo = "comprar_sin_restriccion"
            elif tir_c_dec is not None and tir_c_dec > umbral:
                grupo = "comprar_apalancado"
            else:
                grupo = "mantener"
            # Meses restantes de hold: solo para grupos que no son compra urgente ni ya superan umbral
            if grupo in ("mantener", "comprar_apalancado", "esperar"):
                meses_restantes = calcular_meses_restantes_hold(
                    inv_inicial, valor_actual, fecha_dt, tir_b, umbral
                )
        # Guardar fila
        fila = (
            row["pid"],
            persona,
            str(unit_name)[:45],
            str(unit_id),
            fecha,
            dias,
            cuotas_reales,
            inv_inicial,
            valor_actual,
            ganancia,
            rent_pct,
            tir_a,       # idx 11
            tir_b,       # idx 12
            tir_c,       # idx 13
            tir_d,       # idx 14
            meses_restantes,  # idx 15
        )
        grupos[grupo].append(fila)
        # Acumular por tipo
        totales_por_tipo[persona]["inv"] += inv_inicial
        totales_por_tipo[persona]["val"] += valor_actual
        totales_por_tipo[persona]["dias_ponderado"] += dias * inv_inicial
        totales_por_tipo[persona]["peso_inv"] += inv_inicial
        # Acumular por propiedad
        totales_por_propiedad[unit_id]["inv"] += inv_inicial
        totales_por_propiedad[unit_id]["val"] += valor_actual
        totales_por_propiedad[unit_id]["dias_ponderado"] += dias * inv_inicial
        totales_por_propiedad[unit_id]["peso_inv"] += inv_inicial
        totales_por_propiedad[unit_id]["name"] = unit_name
    # Calcular días promedio por tipo
    for tp in totales_por_tipo:
        if totales_por_tipo[tp]["peso_inv"] > 0:
            totales_por_tipo[tp]["dias_prom"] = totales_por_tipo[tp]["dias_ponderado"] / totales_por_tipo[tp]["peso_inv"]
        else:
            totales_por_tipo[tp]["dias_prom"] = 0
    # Calcular días promedio por propiedad
    for prop in totales_por_propiedad:
        if totales_por_propiedad[prop]["peso_inv"] > 0:
            totales_por_propiedad[prop]["dias_prom"] = totales_por_propiedad[prop]["dias_ponderado"] / totales_por_propiedad[prop]["peso_inv"]
        else:
            totales_por_propiedad[prop]["dias_prom"] = 0
    # Ordenar grupos
    grupos["esperar"].sort(key=lambda x: x[15] if x[15] is not None else 9999)   # menor hold primero
    grupos["vender_malo"].sort(key=lambda x: x[9] if x[9] is not None else 0)
    grupos["vender_bajo_dap"].sort(key=lambda x: x[9] if x[9] is not None else 0)
    grupos["comprar_urgente"].sort(key=lambda x: x[11] if x[11] is not None else -999, reverse=True)
    grupos["comprar_sin_restriccion"].sort(key=lambda x: x[11] if x[11] is not None else -999, reverse=True)
    grupos["comprar_apalancado"].sort(key=lambda x: x[13] if x[13] is not None else -999, reverse=True)
    grupos["mantener"].sort(key=lambda x: x[15] if x[15] is not None else 9999)  # menor hold primero
    # Helper: agrega filas de compra por propiedad usando TIRs de portafolio
    def _agregar_compra_por_prop(filas_grupo):
        by_uid = defaultdict(list)
        for fila in filas_grupo:
            by_uid[fila[3]].append(fila)
        result = []
        for uid, rows in by_uid.items():
            inv_total = sum(r[7] for r in rows)
            val_total = sum(r[8] for r in rows)
            gan_total = val_total - inv_total
            rent = (val_total / inv_total - 1) * 100 if inv_total > 0 else None
            dias_pond = sum(r[5] * r[7] for r in rows)
            dias_prom = int(dias_pond / inv_total) if inv_total > 0 else 0
            prop_data = totales_por_propiedad.get(uid, {})
            result.append({
                "uid": uid, "name": rows[0][2], "n": len(rows), "dias": dias_prom,
                "inv": inv_total, "val": val_total, "gan": gan_total, "rent": rent,
                "ta": calcular_tir_portafolio(prop_data.get("flujos_a", [])),
                "tb": calcular_tir_portafolio(prop_data.get("flujos_b", [])),
                "tc": calcular_tir_portafolio(prop_data.get("flujos_c", [])),
                "td": calcular_tir_portafolio(prop_data.get("flujos_d", [])),
            })
        result.sort(key=lambda x: x["ta"] if x["ta"] is not None else -999, reverse=True)
        return result

    # Mostrar tablas por recomendación
    COMPRA_CLAVES = {"comprar_urgente", "comprar_sin_restriccion"}
    orden_desc = [
        ("comprar_urgente",        "INTENTAR COMPRAR (prioridad)",               f"TIR A > {umbral*1.5*100:.1f}% (1.5x umbral) -> oportunidad destacada, usar max {max_cuotas} cuotas"),
        ("comprar_sin_restriccion","INTENTAR COMPRAR (menor prioridad)",         f"TIR A > umbral {umbral*100:.1f}% -> usar max {max_cuotas} cuotas"),
        ("vender_malo",            "VENDER (activo malo)",                       "TIR B < 0 -> el activo destruye valor incluso sin comision"),
        ("vender_bajo_dap",        "VENDER (rinde menos que DAP)",               f"TIR D < tasa_dap {tasa_dap*100:.1f}% -> ni apalancado supera el costo de oportunidad"),
        ("comprar_apalancado",     "COMPRAR mas (solo apalancado)",              f"TIR A <= umbral pero TIR C supera umbral -> usar max {max_cuotas} cuotas"),
        ("mantener",               "MANTENER",                                   "No cumple criterio de venta ni de compra"),
        ("esperar",                "ESPERAR (poca data)",                        "dias <= 90 o TIR no calculable -> evaluar mas adelante"),
    ]
    _console.print("\n[bold sky_blue3]Resumen por recomendacion (PN y PJ juntos)[/bold sky_blue3]")
    for clave, titulo, condicion in orden_desc:
        if not grupos[clave]:
            continue
        _console.print(f"\n[bold]{titulo}[/bold]")
        _console.print(f"[dim]{condicion}[/dim]")

        if clave in COMPRA_CLAVES:
            # Tabla agregada por propiedad
            filas_prop = _agregar_compra_por_prop(grupos[clave])
            t = Table(box=rich_box.SIMPLE_HEAVY, show_header=True, header_style="bold", highlight=True)
            t.add_column("Activo", style="white", max_width=45, overflow="ellipsis")
            t.add_column("ID", style="dim", width=6, overflow="ellipsis")
            t.add_column("#", justify="right", style="dim", width=3)
            t.add_column("Dias prom", justify="right", width=9)
            t.add_column("Inv. total", justify="right", style="cyan", width=11)
            t.add_column("Valor act", justify="right", style="green", width=11)
            t.add_column("Ganancia", justify="right", width=11)
            t.add_column("Rent.%", justify="right", width=6)
            t.add_column("TIR A", justify="right", width=7)
            t.add_column("TIR B", justify="right", width=7)
            t.add_column("TIR C", justify="right", width=7)
            t.add_column("TIR D", justify="right", width=7)
            for p in filas_prop:
                t.add_row(
                    p["name"], p["uid"], str(p["n"]), str(p["dias"]),
                    fmtnum(p["inv"]), fmtnum(p["val"]),
                    colored(p["gan"], signed=True), colored(p["rent"], pct_str),
                    colored(p["ta"], pct_str), colored(p["tb"], pct_str),
                    colored(p["tc"], pct_str), colored(p["td"], pct_str),
                )
            _console.print(t)
        else:
            # Tabla por purchase
            t = Table(box=rich_box.SIMPLE_HEAVY, show_header=True, header_style="bold", highlight=True)
            t.add_column("ID Compra", style="dim", max_width=12, overflow="ellipsis")
            t.add_column("Tipo", style="dim", width=4)
            t.add_column("Activo", style="white", max_width=45, overflow="ellipsis")
            t.add_column("ID", style="dim", width=6, overflow="ellipsis")
            t.add_column("Fecha", style="dim", width=10)
            t.add_column("Dias", justify="right", width=4)
            t.add_column("Cuotas", justify="right", style="dim", width=5)
            t.add_column("Inv. ini", justify="right", style="cyan", width=11)
            t.add_column("Valor act", justify="right", style="green", width=11)
            t.add_column("Ganancia", justify="right", width=11)
            t.add_column("Rent.%", justify="right", width=6)
            t.add_column("TIR A", justify="right", width=7)
            t.add_column("TIR B", justify="right", width=7)
            t.add_column("TIR C", justify="right", width=7)
            t.add_column("TIR D", justify="right", width=7)
            show_hold = clave in ("mantener", "comprar_apalancado", "esperar")
            if show_hold:
                t.add_column("Hold (m)", justify="right", style="yellow", width=9)
            for fila in grupos[clave]:
                (pid, tipo, name, uid, fecha, dias, cuotas, inv, val, gan, rent, ta, tb, tc, td, hold_m) = fila
                row_cells = [
                    pid[-12:] if len(pid) > 12 else pid,
                    tipo, name, uid, fecha, str(dias), str(cuotas),
                    fmtnum(inv), fmtnum(val), colored(gan, signed=True), colored(rent, pct_str),
                    colored(ta, pct_str), colored(tb, pct_str), colored(tc, pct_str), colored(td, pct_str),
                ]
                if show_hold:
                    row_cells.append(str(hold_m) if hold_m is not None else "[dim]∞[/dim]")
                t.add_row(*row_cells)
            _console.print(t)
    # --- Tabla resumen por tipo (PN, PJ, TOTAL) ---
    _console.print("\n[bold sky_blue3]Resumen por tipo de persona (portafolio)[/bold sky_blue3]")
    resumen_tabla = Table(box=rich_box.SIMPLE_HEAVY, show_header=True, header_style="bold")
    resumen_tabla.add_column("Tipo", style="dim", width=6)
    resumen_tabla.add_column("Inv. total", justify="right", style="cyan", width=13)
    resumen_tabla.add_column("Valor actual", justify="right", style="green", width=13)
    resumen_tabla.add_column("Ganancia", justify="right", width=13)
    resumen_tabla.add_column("Rent. %", justify="right", width=8)
    resumen_tabla.add_column("Dias prom", justify="right", width=8)
    resumen_tabla.add_column("TIR A", justify="right", width=10)
    resumen_tabla.add_column("TIR B", justify="right", width=10)
    resumen_tabla.add_column("TIR C", justify="right", width=10)
    resumen_tabla.add_column("TIR D", justify="right", width=10)
    for tipo in ("PN", "PJ"):
        inv = totales_por_tipo[tipo]["inv"]
        val = totales_por_tipo[tipo]["val"]
        gan = val - inv
        rent = (val / inv - 1) * 100 if inv > 0 else None
        dias_prom = totales_por_tipo[tipo]["dias_prom"]
        tir_a = calcular_tir_portafolio(totales_por_tipo[tipo]["flujos_a"])
        tir_b = calcular_tir_portafolio(totales_por_tipo[tipo]["flujos_b"])
        tir_c = calcular_tir_portafolio(totales_por_tipo[tipo]["flujos_c"])
        tir_d = calcular_tir_portafolio(totales_por_tipo[tipo]["flujos_d"])
        resumen_tabla.add_row(
            tipo,
            fmtnum(inv),
            fmtnum(val),
            colored(gan, signed=True),
            colored(rent, pct_str),
            f"{dias_prom:.0f}",
            colored(tir_a, pct_str),
            colored(tir_b, pct_str),
            colored(tir_c, pct_str),
            colored(tir_d, pct_str),
        )
    # Total (PN+PJ)
    inv_total = sum(t["inv"] for t in totales_por_tipo.values())
    val_total = sum(t["val"] for t in totales_por_tipo.values())
    gan_total = val_total - inv_total
    rent_total = (val_total / inv_total - 1) * 100 if inv_total > 0 else None
    dias_total_pond = sum(t["dias_ponderado"] for t in totales_por_tipo.values())
    peso_total = sum(t["peso_inv"] for t in totales_por_tipo.values())
    dias_prom_total = dias_total_pond / peso_total if peso_total > 0 else 0
    flujos_a_total = totales_por_tipo["PN"]["flujos_a"] + totales_por_tipo["PJ"]["flujos_a"]
    flujos_b_total = totales_por_tipo["PN"]["flujos_b"] + totales_por_tipo["PJ"]["flujos_b"]
    flujos_c_total = totales_por_tipo["PN"]["flujos_c"] + totales_por_tipo["PJ"]["flujos_c"]
    flujos_d_total = totales_por_tipo["PN"]["flujos_d"] + totales_por_tipo["PJ"]["flujos_d"]
    tir_a_total = calcular_tir_portafolio(flujos_a_total)
    tir_b_total = calcular_tir_portafolio(flujos_b_total)
    tir_c_total = calcular_tir_portafolio(flujos_c_total)
    tir_d_total = calcular_tir_portafolio(flujos_d_total)
    resumen_tabla.add_row(*[""] * 10)
    resumen_tabla.add_section()
    resumen_tabla.add_row(
        "[bold]TOTAL[/bold]",
        f"[bold cyan]{fmtnum(inv_total)}[/bold cyan]",
        f"[bold green]{fmtnum(val_total)}[/bold green]",
        colored(gan_total, signed=True),
        colored(rent_total, pct_str),
        f"{dias_prom_total:.0f}",
        colored(tir_a_total, pct_str),
        colored(tir_b_total, pct_str),
        colored(tir_c_total, pct_str),
        colored(tir_d_total, pct_str),
    )
    _console.print(resumen_tabla)
    # --- Tabla resumen por propiedad ---
    _console.print("\n[bold sky_blue3]Resumen por propiedad (portafolio total PN+PJ)[/bold sky_blue3]")
    prop_table = Table(box=rich_box.SIMPLE_HEAVY, show_header=True, header_style="bold")
    prop_table.add_column("Propiedad", style="white", max_width=35, overflow="ellipsis")
    prop_table.add_column("ID", style="dim", width=10, overflow="ellipsis")
    prop_table.add_column("Inv. total", justify="right", style="cyan", width=13)
    prop_table.add_column("Valor actual", justify="right", style="green", width=13)
    prop_table.add_column("Ganancia", justify="right", width=13)
    prop_table.add_column("Rent. %", justify="right", width=8)
    prop_table.add_column("Dias prom", justify="right", width=8)
    prop_table.add_column("TIR A", justify="right", width=10)
    prop_table.add_column("TIR B", justify="right", width=10)
    prop_table.add_column("TIR C", justify="right", width=10)
    prop_table.add_column("TIR D", justify="right", width=10)
    for unit_id, data in sorted(totales_por_propiedad.items(), key=lambda x: x[1]["inv"], reverse=True):
        inv = data["inv"]
        val = data["val"]
        gan = val - inv
        rent = (val / inv - 1) * 100 if inv > 0 else None
        dias_prom = data["dias_prom"]
        tir_a = calcular_tir_portafolio(data["flujos_a"])
        tir_b = calcular_tir_portafolio(data["flujos_b"])
        tir_c = calcular_tir_portafolio(data["flujos_c"])
        tir_d = calcular_tir_portafolio(data["flujos_d"])
        prop_table.add_row(
            data["name"][:35],
            unit_id,
            fmtnum(inv),
            fmtnum(val),
            colored(gan, signed=True),
            colored(rent, pct_str),
            f"{dias_prom:.0f}",
            colored(tir_a, pct_str),
            colored(tir_b, pct_str),
            colored(tir_c, pct_str),
            colored(tir_d, pct_str),
        )
    prop_table.add_row(*[""] * 11)
    prop_table.add_section()
    prop_table.add_row(
        "[bold]TOTAL[/bold]", "",
        f"[bold cyan]{fmtnum(inv_total)}[/bold cyan]",
        f"[bold green]{fmtnum(val_total)}[/bold green]",
        colored(gan_total, signed=True),
        colored(rent_total, pct_str),
        f"{dias_prom_total:.0f}",
        colored(tir_a_total, pct_str),
        colored(tir_b_total, pct_str),
        colored(tir_c_total, pct_str),
        colored(tir_d_total, pct_str),
    )
    _console.print(prop_table)
    # Leyenda final
    _console.print("\n[bold cyan]Leyenda de TIR:[/bold cyan]")
    _console.print("  [green]TIR A[/green] = contado + comision  |  [green]TIR B[/green] = contado sin comision  |  [green]TIR C[/green] = cuotas + comision  |  [green]TIR D[/green] = cuotas sin comision")
# ----------------------------------------------------------------------
# VISTA POR PROPIEDAD
# ----------------------------------------------------------------------
def view_por_propiedad(tasa_dap):
    conn = _db_conn()
    rows = conn.execute(f"""
        WITH latest AS (
            SELECT purchase_confirmation_id, persona, MAX(extracted_at) AS max_ts
            FROM {TABLE} GROUP BY purchase_confirmation_id, persona
        )
        SELECT
            m.persona,
            m.unit_id,
            m.unit_name,
            MIN(CASE WHEN m.kind IN ('purchase','market') THEN m.confirmed_at END) AS primera_compra,
            SUM(CASE WHEN m.kind IN ('purchase','market') THEN
                MAX(m.bid_preferred_amount) + MAX(m.bid_preferred_amount_fee)
            ELSE 0 END) AS inv_inicial,
            SUM(m.current_value) AS valor_actual,
            COUNT(DISTINCT CASE WHEN m.kind IN ('purchase','market')
                                THEN m.purchase_confirmation_id END) AS n_compras
        FROM {TABLE} m
        JOIN latest l ON m.purchase_confirmation_id = l.purchase_confirmation_id
                      AND m.persona = l.persona AND m.extracted_at = l.max_ts
        WHERE m.status = 'active'
        GROUP BY m.persona, m.unit_id, m.unit_name
        ORDER BY m.unit_name
    """).fetchall()
    if not rows:
        _console.print("[yellow]Sin datos.[/yellow]")
        conn.close()
        return
    today = datetime.date.today()
    t = Table(
        box=rich_box.SIMPLE_HEAVY,
        show_header=True,
        header_style="bold sky_blue3",
        title=f"[bold sky_blue3]Por propiedad — CLP[/bold sky_blue3]  [dim]DAP ref: {tasa_dap*100:.1f}%[/dim]"
    )
    t.add_column("Propiedad", style="white", max_width=40, overflow="ellipsis")
    t.add_column("P", style="dim", width=3)
    t.add_column("1a compra", style="dim", width=10)
    t.add_column("Dias", justify="right", width=5)
    t.add_column("Compras", justify="right", style="dim", width=8)
    t.add_column("Inv. inicial", justify="right", style="cyan", width=13)
    t.add_column("Valor actual", justify="right", style="green", width=13)
    t.add_column("Ganancia", justify="right", width=13)
    t.add_column("Rent. %", justify="right", width=8)
    t.add_column("TIR anual", justify="right", width=9)
    t.add_column("vs DAP", justify="right", width=9)
    for r in rows:
        inv_inicial = r["inv_inicial"] or 0
        valor_actual = r["valor_actual"] or 0
        fecha = str(r["primera_compra"] or "")[:10]
        try:
            dias = (today - datetime.date.fromisoformat(fecha)).days
        except Exception:
            dias = 0
        ganancia = (valor_actual - inv_inicial) if inv_inicial > 0 else None
        rent_pct = (valor_actual / inv_inicial - 1) * 100 if inv_inicial > 0 else None
        tir = ((valor_actual / inv_inicial) ** (365 / dias) - 1) * 100 \
              if inv_inicial > 0 and dias > 30 else None
        vs_dap = (tir - tasa_dap * 100) if tir is not None else None
        t.add_row(
            str(r["unit_name"] or "")[:39],
            str(r["persona"]),
            fecha,
            str(dias),
            str(r["n_compras"]),
            fmtnum(inv_inicial),
            fmtnum(valor_actual),
            colored(ganancia, signed=True),
            colored(rent_pct, pct_str),
            colored(tir, pct_str),
            colored(vs_dap, pp_str),
        )
    _console.print(t)
    conn.close()
# ----------------------------------------------------------------------
# MAIN INTERACTIVO
# ----------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(
        description="Ver metricas Fraccional (sin scraping) con recomendaciones de inversion"
    )
    parser.add_argument("--tasa-dap", type=float, help="Tasa DAP (costo de oportunidad)")
    parser.add_argument("--max-cuotas", type=int, help="Maximo de cuotas sin interes")
    parser.add_argument("--premium", type=float, help="Sobrerrentabilidad requerida (ej: 0.2 = 20%%)")
    parser.add_argument("--por-propiedad", action="store_true", help="Vista agregada por propiedad")
    args = parser.parse_args()
    if args.por_propiedad:
        tasa_dap = args.tasa_dap if args.tasa_dap is not None else 0.05
        _console.print(Rule("[bold sky_blue3]FRACCIONAL — Ver metricas locales[/bold sky_blue3]", style="sky_blue3"))
        view_por_propiedad(tasa_dap)
        return
    # Modo interactivo
    if args.tasa_dap is None or args.max_cuotas is None or args.premium is None:
        _console.print("[yellow]Modo interactivo. Presiona Enter para usar el valor por defecto.[/yellow]\n")
        try:
            tasa_dap = float(input("Tasa DAP (default 0.05 = 5%): ").strip() or "0.05")
        except ValueError:
            tasa_dap = 0.05
        try:
            max_cuotas = int(input("Maximo cuotas sin interes (default 12): ").strip() or "12")
        except ValueError:
            max_cuotas = 12
        try:
            premium = float(input("Premium requerido (default 0.2 = 20%): ").strip() or "0.2")
        except ValueError:
            premium = 0.2
    else:
        tasa_dap = args.tasa_dap
        max_cuotas = args.max_cuotas
        premium = args.premium
    _console.print(Rule("[bold sky_blue3]FRACCIONAL — Ver metricas locales[/bold sky_blue3]", style="sky_blue3"))
    view_por_purchase(tasa_dap, max_cuotas, premium)
if __name__ == "__main__":
    main()
