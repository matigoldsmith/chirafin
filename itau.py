import os
import subprocess
from playwright.sync_api import Playwright, sync_playwright


def bw_env():
    env = os.environ.copy()
    env["NODE_TLS_REJECT_UNAUTHORIZED"] = "0"
    return env


def bw_unlock():
    print("🔑 Desbloqueando Bitwarden...")
    master = subprocess.run(
        ["security", "find-generic-password", "-a", "bitwarden", "-s", "bitwarden-master", "-w"],
        capture_output=True, text=True
    ).stdout.strip()
    result = subprocess.run(
        ["bw", "unlock", master, "--raw"],
        capture_output=True, text=True,
        env=bw_env()
    )
    session = result.stdout.strip()
    if session:
        os.environ["BW_SESSION"] = session
        print("✅ Bitwarden desbloqueado")
    else:
        raise Exception("No se pudo desbloquear Bitwarden")


def bw_get(field, item_name):
    env = bw_env()
    result = subprocess.run(
        ["bw", "get", field, item_name],
        capture_output=True, text=True, env=env
    )
    if "Session key is invalid" in result.stderr or not result.stdout.strip():
        bw_unlock()
        result = subprocess.run(
            ["bw", "get", field, item_name],
            capture_output=True, text=True, env=bw_env()
        )
    return result.stdout.strip()


def fmt_monto(s):
    try:
        v = int(str(s).strip().replace(".", "").replace(",", "").replace(" ", ""))
        return f"-{abs(v):,}".replace(",", ".") if v < 0 else f"{v:,}".replace(",", ".")
    except:
        return s


def print_table(title, rows):
    """rows = list of (inst, cat, item, moneda, monto_str)"""
    col_inst  = max(14, max(len(r[0]) for r in rows))
    col_cat   = max(19, max(len(r[1]) for r in rows))
    col_item  = max(9,  max(len(r[2]) for r in rows))
    col_monto = max(15, max(len(fmt_monto(r[4])) for r in rows) + 2)
    W   = col_inst + col_cat + col_item + col_monto + 20
    sep = "  " + "─" * (W - 2)
    print("\n" + "═" * W)
    print(f" 💰  {title}".center(W))
    print("═" * W)
    print(f"  {'Institución':{col_inst}}  {'Categoría':{col_cat}}  {'Item':{col_item}}  {'Moneda':<6}  {'Monto':>{col_monto}}")
    print(sep)
    total = 0
    for inst, cat, item, moneda, monto in rows:
        v = fmt_monto(monto)
        try:
            total += int(str(monto).replace(".", "").replace(",", "").replace(" ", ""))
        except:
            pass
        print(f"  {inst:{col_inst}}  {cat:{col_cat}}  {item:{col_item}}  {moneda:<6}  {v:>{col_monto}}")
    print(sep)
    print(f"  {'TOTAL':{col_inst + col_cat + col_item + 4}}  {'CLP':<6}  {fmt_monto(str(total)):>{col_monto}}")
    print("═" * W)


def run(playwright: Playwright) -> None:
    print("Obteniendo credenciales desde Bitwarden...")
    rut = bw_get("username", "banco.itau.cl")
    password = bw_get("password", "banco.itau.cl")

    if not rut or not password:
        print("❌ No se pudieron obtener las credenciales.")
        return

    browser = playwright.chromium.launch(
        channel="chrome",
        headless=False,
        args=["--disable-blink-features=AutomationControlled"]
    )
    context = browser.new_context(viewport={"width": 1280, "height": 800})
    page = context.new_page()
    page.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")

    print("1. Navegando a Itaú...")
    page.goto("https://banco.itau.cl/wps/portal/newolb/web/login/")
    page.wait_for_load_state("load", timeout=15000)
    page.wait_for_timeout(2000)

    print("2. Ingresando RUT...")
    page.locator("input#loginNameID").wait_for(state="visible", timeout=10000)
    page.locator("input#loginNameID").press_sequentially(rut, delay=50)
    page.wait_for_timeout(500)

    print("3. Ingresando contraseña...")
    page.locator("input#pswdId").wait_for(state="visible", timeout=5000)
    page.locator("input#pswdId").press_sequentially(password, delay=50)
    page.wait_for_timeout(500)

    print("4. Ingresando...")
    page.locator("input#btnLoginPortal").click()
    page.wait_for_load_state("load", timeout=15000)
    page.wait_for_timeout(3000)

    print("5. Navegando a saldos CC...")
    page.goto("https://banco.itau.cl/wps/myportal/newolb/web/cuentas/cuenta-corriente/saldos/")
    page.wait_for_timeout(3000)

    print("6. Buscando saldo CC...")
    label = page.locator("small.itau-card-text", has_text="Saldo disponible para uso")
    label.wait_for(timeout=15000)
    saldo = label.locator("xpath=..").locator("h6.itau-card-title").text_content()

    print("7. Navegando a TdC...")
    page.goto("https://banco.itau.cl/wps/myportal/newolb/web/tarjeta-credito/resumen/deuda/")
    page.wait_for_timeout(3000)

    print("8. Buscando deuda TdC 6132...")
    page.locator("p.monto-saldo").nth(1).wait_for(state="visible", timeout=15000)
    deuda_raw = page.locator("p.monto-saldo").nth(1).text_content().strip()
    deuda = deuda_raw.replace("$ ", "").replace("$", "").strip()

    print_table("Itaú", [
        ("Itaú", "Cuenta Corriente PN", "CC 8792", "CLP", saldo.strip()),
        ("Itaú", "Tarjeta de Crédito", "TdC 6132", "CLP", "-" + deuda)
    ])

    context.close()
    browser.close()


with sync_playwright() as playwright:
    run(playwright)
