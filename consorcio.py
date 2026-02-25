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
    rut = bw_get("username", "login.consorcio.cl")
    password = bw_get("password", "login.consorcio.cl")

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

    print("1. Navegando a Consorcio...")
    page.goto("https://login.consorcio.cl/onboarding-consorcio/admin")
    page.wait_for_load_state("load", timeout=15000)
    page.wait_for_timeout(1500)

    print("2. Ingresando RUT...")
    page.locator("input#input-rut").wait_for(state="visible", timeout=10000)
    page.locator("input#input-rut").press_sequentially(rut, delay=50)
    page.wait_for_timeout(500)

    print("3. Ingresando clave...")
    page.locator("input#input-new-pass").wait_for(state="visible", timeout=5000)
    page.locator("input#input-new-pass").press_sequentially(password, delay=50)
    page.wait_for_timeout(500)

    print("4. Ingresando...")
    page.get_by_role("button", name="Ingresar").click()
    page.wait_for_load_state("load", timeout=15000)
    page.wait_for_timeout(3000)

    print("5. Cerrando popup si aparece...")
    try:
        page.locator("button.btn-cerrar-modal").wait_for(state="visible", timeout=5000)
        page.locator("button.btn-cerrar-modal").click()
        page.wait_for_timeout(500)
    except:
        pass

    print("6. Buscando saldo CC...")
    # El saldo aparece directo en el home tras el login
    # div.elastic-card contiene la CC → p.elastic-card--product-info tiene el monto
    cc_card = page.locator("div.elastic-card", has_text="Cuenta Corriente")
    cc_card.wait_for(state="visible", timeout=15000)
    saldo = cc_card.locator("p.elastic-card--product-info").text_content()

    print_table("Consorcio", [
        ("Consorcio", "Cuenta Corriente PN", "CC 6758", "CLP", saldo.strip())
    ])

    context.close()
    browser.close()


with sync_playwright() as playwright:
    run(playwright)
