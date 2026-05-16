"""
fraccional.py — Menú principal de Fraccional
Uso: python3 fraccional.py
     (o doble click en fraccional.command)
"""
import os, sys, datetime, sqlite3
from pathlib import Path

os.environ['PROMPT_TOOLKIT_NO_CPR'] = '1'
try:
    from prompt_toolkit.output.vt100 import Vt100_Output
    Vt100_Output.responds_to_cpr = property(lambda self: False)
except Exception:
    pass

import questionary
from rich.console import Console
from rich.rule import Rule

_console = Console()
DB_PATH = Path(__file__).parent / "fraccional.db"

QUESTIONARY_STYLE = questionary.Style([
    ('qmark',       'fg:#ff8700 bold'),
    ('question',    'bold'),
    ('answer',      'fg:#ff8700 bold'),
    ('pointer',     'fg:#ff8700 bold'),
    ('highlighted', 'fg:#ff8700 bold'),
    ('selected',    'fg:#ff8700'),
    ('separator',   'fg:#444444'),
    ('instruction', 'fg:#888888'),
])

# ── Parámetros persistidos en DB ───────────────────────────────────
DEFAULT_PARAMS = {"tasa_dap": 0.05, "premium": 0.20, "max_cuotas": 12.0}

def _init_params_table(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS fraccional_params (
            key   TEXT PRIMARY KEY,
            value REAL NOT NULL
        )
    """)
    conn.commit()

def _get_params():
    params = dict(DEFAULT_PARAMS)
    if not DB_PATH.exists():
        return params
    conn = sqlite3.connect(str(DB_PATH))
    _init_params_table(conn)
    for k, v in conn.execute("SELECT key, value FROM fraccional_params").fetchall():
        if k in params:
            params[k] = v
    conn.close()
    return params

def _set_param(key, value):
    conn = sqlite3.connect(str(DB_PATH))
    _init_params_table(conn)
    conn.execute("INSERT OR REPLACE INTO fraccional_params (key, value) VALUES (?, ?)", (key, value))
    conn.commit()
    conn.close()

def _get_ultima_extraccion():
    """Retorna string con la fecha/hora de la última extracción en la DB, o None."""
    if not DB_PATH.exists():
        return None
    try:
        conn = sqlite3.connect(str(DB_PATH))
        row = conn.execute(
            "SELECT MAX(extracted_at) FROM fraccional_movimientos"
        ).fetchone()
        conn.close()
        if row and row[0]:
            # extracted_at es ISO 8601 UTC, ej: "2026-05-15T03:12:00+00:00"
            dt = datetime.datetime.fromisoformat(row[0])
            dt_local = dt.astimezone()
            _MESES = ["ene","feb","mar","abr","may","jun","jul","ago","sep","oct","nov","dic"]
            return f"{dt_local.day:02d} {_MESES[dt_local.month-1]} {dt_local.year}, {dt_local.strftime('%H:%M')}"
    except Exception:
        pass
    return None

# ── Banner ─────────────────────────────────────────────────────────
def _print_banner():
    _console.clear()
    _MESES = ["ene","feb","mar","abr","may","jun","jul","ago","sep","oct","nov","dic"]
    dt = datetime.datetime.fromtimestamp(os.path.getmtime(__file__))
    fecha_mod = f"{dt.day:02d} {_MESES[dt.month-1]} {dt.year}, {dt.strftime('%H:%M')}"
    ultima = _get_ultima_extraccion()
    _console.print()
    _console.print("  [bold orange1]FRACCIONAL[/bold orange1]")
    _console.print(f"  [dim]Código actualizado el {fecha_mod}[/dim]", highlight=False)
    if ultima:
        _console.print(f"  [dim]Última extracción: {ultima}[/dim]", highlight=False)
    else:
        _console.print(f"  [dim]Última extracción: sin datos[/dim]", highlight=False)
    _console.print(Rule(style="dim orange1"))

def _clear_content():
    _console.print(Rule(style="dim #2a2a2a"))

# ── Opción 1: Actualizar datos (scraper) ───────────────────────────
def menu_actualizar():
    _clear_content()
    _console.print("\n  Actualizando PN y PJ...\n")
    try:
        import fraccional_scraper as frac
        frac.init_db()
        frac.bw_unlock()
        extracted_at = datetime.datetime.now(datetime.timezone.utc).isoformat()

        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.launch(
                channel="chrome",
                headless=False,
                args=["--disable-blink-features=AutomationControlled"],
                ignore_default_args=["--enable-automation"],
            )
            total = 0

            # PJ
            items = frac.bw_list_search("fraccional")
            pj_bw = next(
                (i for i in items if "owa605" in str(i.get("login", {}).get("username", ""))),
                None
            )
            if pj_bw:
                total += frac.run_persona(
                    browser, "PJ",
                    pj_bw["login"]["username"], pj_bw["login"]["password"],
                    extracted_at,
                )
            else:
                _console.print("[red][PJ] Sin credenciales en Bitwarden[/red]")

            # PN
            pn_user = frac.bw_get("username", "fraccional.cl - Persona Natural")
            pn_pass = frac.bw_get("password", "fraccional.cl - Persona Natural")
            if pn_user and pn_pass:
                total += frac.run_persona(
                    browser, "PN", pn_user, pn_pass, extracted_at,
                    use_iso_context=True,
                )
            else:
                _console.print("[red][PN] Sin credenciales[/red]")

            browser.close()

        _console.print()
        _console.print(f"[bold green]✓ {total} registros guardados[/bold green]")
    except Exception as e:
        _console.print(f"[bold red]Error en scraper: {e}[/bold red]")

    input("\nPresiona Enter para continuar...")

# ── Opción 2: Analizar datos ───────────────────────────────────────
def menu_analizar():
    params = _get_params()
    try:
        import fraccional_ver as fver
        _clear_content()
        fver.menu_analisis(
            tasa_dap=params["tasa_dap"],
            premium=params["premium"],
            max_cuotas=int(params["max_cuotas"]),
        )
    except Exception as e:
        _console.print(f"[bold red]Error en análisis: {e}[/bold red]")
        import traceback; traceback.print_exc()
        input("\nPresiona Enter para volver al menú...")

# ── Opción 3: Definir parámetros ───────────────────────────────────
def menu_parametros():
    while True:
        params = _get_params()
        umbral = params["tasa_dap"] * (1 + params["premium"])
        _clear_content()
        _console.print(f"\n  [bold]Parámetros actuales[/bold]")
        _console.print(f"  Tasa DAP   : [cyan]{params['tasa_dap']*100:.1f}%[/cyan]")
        _console.print(f"  Premium    : [cyan]{params['premium']*100:.0f}%[/cyan]")
        _console.print(f"  Umbral     : [cyan]{umbral*100:.1f}%[/cyan]  [dim](= DAP × (1 + premium))[/dim]")
        _console.print(f"  Max cuotas : [cyan]{int(params['max_cuotas'])}[/cyan]\n")

        sel = questionary.select(
            "",
            choices=[
                questionary.Choice("  Cambiar tasa DAP",    value="dap"),
                questionary.Choice("  Cambiar premium",     value="premium"),
                questionary.Choice("  Cambiar max cuotas",  value="cuotas"),
                questionary.Separator(),
                questionary.Choice("  « Volver",            value="back"),
            ],
            style=QUESTIONARY_STYLE, pointer="»", qmark="",
        ).ask(patch_stdout=True)

        if not sel or sel == "back":
            return

        if sel == "dap":
            raw = questionary.text(
                f"  Nueva tasa DAP (actual {params['tasa_dap']*100:.1f}%) — ingresá el número, ej: 4.5 para 4.5%:",
                style=QUESTIONARY_STYLE,
            ).ask()
            if raw:
                try:
                    v = float(raw.replace(",", ".").replace("%", "").strip()) / 100
                    _set_param("tasa_dap", v)
                    _console.print(f"  [green]✓ tasa_dap → {v*100:.1f}%[/green]")
                except ValueError:
                    _console.print("  [red]Valor inválido[/red]")

        elif sel == "premium":
            raw = questionary.text(
                f"  Nuevo premium (actual {params['premium']*100:.0f}%) — ingresá el número, ej: 20 para 20%:",
                style=QUESTIONARY_STYLE,
            ).ask()
            if raw:
                try:
                    v = float(raw.replace(",", ".").replace("%", "").strip()) / 100
                    _set_param("premium", v)
                    _console.print(f"  [green]✓ premium → {v*100:.0f}%[/green]")
                except ValueError:
                    _console.print("  [red]Valor inválido[/red]")

        elif sel == "cuotas":
            raw = questionary.text(
                f"  Nuevo máximo de cuotas (actual {int(params['max_cuotas'])}) — ej: 12:",
                style=QUESTIONARY_STYLE,
            ).ask()
            if raw:
                try:
                    v = int(raw.strip())
                    if v < 1:
                        raise ValueError
                    _set_param("max_cuotas", float(v))
                    _console.print(f"  [green]✓ max_cuotas → {v}[/green]")
                except ValueError:
                    _console.print("  [red]Valor inválido (debe ser entero >= 1)[/red]")

# ── Opción 4: Configurar cuotas por compra ─────────────────────────
def menu_cuotas():
    import fraccional_scraper as frac
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row

    # Traer todas las compras activas con su cuotas actual
    rows = conn.execute("""
        SELECT m.purchase_confirmation_id AS pid,
               m.persona,
               m.unit_name,
               m.unit_id,
               m.confirmed_at,
               m.bid_preferred_amount AS inv,
               COALESCE(c.num_cuotas, 6) AS cuotas
        FROM fraccional_movimientos m
        LEFT JOIN fraccional_config c
               ON c.purchase_confirmation_id = m.purchase_confirmation_id
              AND c.persona = m.persona
        WHERE m.status = 'active'
          AND m.kind IN ('purchase','market')
        ORDER BY m.unit_name, m.confirmed_at
    """).fetchall()
    conn.close()

    if not rows:
        _console.print("\n[yellow]Sin compras activas.[/yellow]")
        input("\nEnter para volver...")
        return

    while True:
        _clear_content()
        _console.print("\n  [bold]Cuotas por compra[/bold]")
        _console.print("  [dim]Seleccioná una compra para cambiar sus cuotas[/dim]\n")

        # Reagrupar por propiedad para el display
        choices = []
        prev_prop = None
        for r in rows:
            prop = r["unit_name"] or r["unit_id"]
            if prop != prev_prop:
                choices.append(questionary.Separator(f"── {prop} ──"))
                prev_prop = prop
            fecha = (r["confirmed_at"] or "")[:10]
            label = (f"  {r['persona']:2}  {fecha}  "
                     f"${r['inv']:>12,.0f}  →  {r['cuotas']} cuotas")
            choices.append(questionary.Choice(label, value=dict(r)))

        choices.append(questionary.Separator())
        choices.append(questionary.Choice("  « Volver", value="back"))

        sel = questionary.select(
            "", choices=choices, style=QUESTIONARY_STYLE, pointer="»", qmark="",
        ).ask(patch_stdout=True)

        if not sel or sel == "back":
            return

        # Editar cuotas de la compra seleccionada
        pid, persona, cuotas_act = sel["pid"], sel["persona"], sel["cuotas"]
        raw = questionary.text(
            f"  Cuotas actuales: {cuotas_act}  — Nueva cantidad (Enter para mantener):",
            style=QUESTIONARY_STYLE,
        ).ask()

        if raw and raw.strip():
            try:
                n = int(raw.strip())
                if n < 1:
                    raise ValueError
                frac.set_num_cuotas(pid, persona, n)
                _console.print(f"  [green]✓ Actualizado a {n} cuotas[/green]")
                # Refrescar rows para reflejar el cambio
                conn2 = sqlite3.connect(str(DB_PATH))
                conn2.row_factory = sqlite3.Row
                rows = conn2.execute("""
                    SELECT m.purchase_confirmation_id AS pid,
                           m.persona,
                           m.unit_name,
                           m.unit_id,
                           m.confirmed_at,
                           m.bid_preferred_amount AS inv,
                           COALESCE(c.num_cuotas, 6) AS cuotas
                    FROM fraccional_movimientos m
                    LEFT JOIN fraccional_config c
                           ON c.purchase_confirmation_id = m.purchase_confirmation_id
                          AND c.persona = m.persona
                    WHERE m.status = 'active'
                      AND m.kind IN ('purchase','market')
                    ORDER BY m.unit_name, m.confirmed_at
                """).fetchall()
                conn2.close()
            except ValueError:
                _console.print("  [red]Valor inválido (debe ser entero >= 1)[/red]")


# ── Main ───────────────────────────────────────────────────────────
def main():
    _print_banner()
    try:
        while True:
            _console.print(f"\n[bold sky_blue3]  MENÚ PRINCIPAL  [/bold sky_blue3]")
            sel = questionary.select(
                "",
                choices=[
                    questionary.Choice("  Actualizar datos",    value="actualizar"),
                    questionary.Choice("  Analizar datos",      value="analizar"),
                    questionary.Separator(),
                    questionary.Choice("  Configurar cuotas",   value="cuotas"),
                    questionary.Choice("  Definir parámetros",  value="params"),
                    questionary.Separator(),
                    questionary.Choice("  Salir",               value="salir"),
                ],
                style=QUESTIONARY_STYLE, pointer="»", qmark="",
            ).ask(patch_stdout=True)

            if not sel or sel == "salir":
                _console.clear()
                sys.exit(0)

            elif sel == "actualizar":
                menu_actualizar()
                _print_banner()

            elif sel == "analizar":
                menu_analizar()
                _print_banner()

            elif sel == "cuotas":
                menu_cuotas()
                _print_banner()

            elif sel == "params":
                menu_parametros()
                _print_banner()

    except KeyboardInterrupt:
        _console.print("\n[dim]Saliendo...[/dim]")
        sys.exit(0)

if __name__ == "__main__":
    main()
