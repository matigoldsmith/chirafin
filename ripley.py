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
    rut = bw_get("username", "web.bancoripley.cl")
    password = bw_get("password", "web.bancoripley.cl")

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

    print("1. Navegando a Banco Ripley...")
    page.goto("https://web.bancoripley.cl/login")
    page.wait_for_timeout(2000)

    print("2. Ingresando RUT...")
    page.get_by_role("textbox").wait_for(timeout=10000)
    page.get_by_role("textbox").click()
    page.get_by_role("textbox").press_sequentially(rut, delay=50)
    page.get_by_role("textbox").press("Tab")
    page.wait_for_timeout(1000)
    page.get_by_role("button", name="Continuar").click(force=True)
    page.wait_for_timeout(1500)

    print("3. Ingresando contraseña...")
    page.get_by_role("textbox").wait_for(timeout=8000)
    page.get_by_role("textbox").fill(password)
    page.get_by_role("button", name="Continuar").click()
    page.wait_for_timeout(2000)

    print("4. Esperando home...")
    page.wait_for_url("**/home**", timeout=30000)
    page.wait_for_timeout(7000)

    print("5. Buscando saldo CC...")
    # h5.h5-xs nth(0) → CC ****2239
    # h5.h5-xs nth(1) → Disponible TC ****9647 (NO es la deuda)
    # Deuda TC: div con texto "Utilizado" → padre → span.label-md
    page.locator("h5.h5-xs").first.wait_for(timeout=30000)
    saldo = page.locator("h5.h5-xs").first.text_content()

    print("6. Buscando deuda TdC 9647...")
    tdc_card = page.locator("div", has_text="Titular ****9647").first
    utilizado_row = tdc_card.locator("div.min-w-\\[76px\\]", has_text="Utilizado")
    utilizado_row.wait_for(state="visible", timeout=15000)
    deuda_raw = utilizado_row.locator("xpath=..").locator("span.label-md").text_content()
    deuda = deuda_raw.strip().replace("$ ", "").replace("$", "").strip()

    print_table("Banco Ripley", [
        ("Banco Ripley", "Cuenta Corriente PN", "CC 2239", "CLP", saldo.strip()),
        ("Banco Ripley", "Tarjeta de Crédito", "TdC 9647", "CLP", "-" + deuda)
    ])

    context.close()
    browser.close()


with sync_playwright() as playwright:
    run(playwright)
