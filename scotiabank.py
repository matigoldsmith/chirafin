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
    result = subprocess.run(["bw", "unlock", master, "--raw"], capture_output=True, text=True, env=bw_env())
    session = result.stdout.strip()
    if session:
        os.environ["BW_SESSION"] = session
        print("✅ Bitwarden desbloqueado")
    else:
        raise Exception("No se pudo desbloquear Bitwarden")

def bw_get(field, item_name):
    env = bw_env()
    result = subprocess.run(["bw", "get", field, item_name], capture_output=True, text=True, env=env)
    if "Session key is invalid" in result.stderr or not result.stdout.strip():
        bw_unlock()
        result = subprocess.run(["bw", "get", field, item_name], capture_output=True, text=True, env=bw_env())
    return result.stdout.strip()

def fmt_monto(s):
    try:
        v = int(str(s).strip().replace(".", "").replace(",", "").replace(" ", ""))
        return f"-{abs(v):,}".replace(",", ".") if v < 0 else f"{v:,}".replace(",", ".")
    except:
        return s

def print_table(rows):
    """rows = list of (inst, cat, item, moneda, monto_str)"""
    col_inst  = max(14, max(len(r[0]) for r in rows))
    col_cat   = max(19, max(len(r[1]) for r in rows))
    col_item  = max(9,  max(len(r[2]) for r in rows))
    col_monto = max(15, max(len(r[4]) for r in rows) + 2)
    W   = col_inst + col_cat + col_item + col_monto + 20
    sep = "  " + "─" * (W - 2)

    print("\n" + "═" * W)
    print(" 💰  SCOTIABANK".center(W))
    print("═" * W)
    print(f"  {'Institución':{col_inst}}  {'Categoría':{col_cat}}  {'Item':{col_item}}  {'Moneda':<6}  {'Monto':>{col_monto}}")
    print(sep)
    total = 0
    for inst, cat, item, moneda, monto in rows:
        v = fmt_monto(monto)
        try:
            total += int(monto.replace(".", "").replace(",", "").replace(" ", ""))
        except:
            pass
        print(f"  {inst:{col_inst}}  {cat:{col_cat}}  {item:{col_item}}  {moneda:<6}  {v:>{col_monto}}")
    print(sep)
    print(f"  {'TOTAL':{col_inst + col_cat + col_item + 4}}  {'CLP':<6}  {fmt_monto(str(total)):>{col_monto}}")
    print("═" * W)


def run(playwright: Playwright) -> None:
    print("Obteniendo credenciales desde Bitwarden...")
    rut      = bw_get("username", "Scotiabank")
    password = bw_get("password", "Scotiabank")
    if not rut or not password:
        print("❌ No se pudieron obtener las credenciales.")
        return

    browser = playwright.chromium.launch(
        channel="chrome", headless=False,
        args=["--disable-blink-features=AutomationControlled"]
    )
    context = browser.new_context(viewport={"width": 1280, "height": 800})
    page    = context.new_page()

    print("1. Navegando a Scotiabank...")
    page.goto("https://www.scotiabankchile.cl/")
    page.wait_for_timeout(2000)

    print("2. Abriendo login...")
    page.get_by_text("Acceso Scotia").click()
    page.get_by_role("link", name="Ingreso Personas").click()
    page.get_by_test_id("inputDni").wait_for(timeout=8000)

    print("3. Llenando credenciales...")
    page.get_by_test_id("inputDni").fill(rut)
    page.get_by_test_id("inputDni").press("Tab")
    page.wait_for_timeout(1000)
    page.get_by_test_id("inputPassword").fill(password)
    page.wait_for_timeout(1000)

    print("4. Ingresando...")
    page.get_by_role("button", name="Ingresar").click()
    page.wait_for_load_state("networkidle", timeout=20000)
    page.wait_for_timeout(1000)

    print("5. Cerrando popup si aparece...")
    try:
        page.get_by_role("button", name="Cerrar", exact=True).wait_for(state="visible", timeout=5000)
        page.get_by_role("button", name="Cerrar", exact=True).click()
        page.wait_for_timeout(1000)
    except:
        pass

    print("6. Leyendo saldo CC...")
    page.get_by_text("-$").first.wait_for(timeout=15000)
    saldo_raw = page.get_by_text("-$").first.text_content().strip().replace("$", "").strip()

    def get_cupo(card_number):
        url = f"https://www.scotiabank.cl/mfe/sweb/mfe-shell-web-cl/mfe/mfe-simple-account-statement-web-cl/?tab=saldo&card={card_number}"
        page.goto(url)
        page.wait_for_timeout(4000)
        frame = page.frame_locator("iframe#iframe-stage")
        cupo  = frame.locator("div.saldo", has_text="Cupo utilizado").first
        cupo.wait_for(state="visible", timeout=15000)
        return cupo.locator("h1.saldo__text").text_content().strip()

    print("7. Leyendo TdC 3134...")
    d3134 = get_cupo("3134").replace("$", "").strip()
    m3134 = f"-{d3134}" if d3134 != "0" else "0"

    print("8. Leyendo TdC 2730...")
    d2730 = get_cupo("2730").replace("$", "").strip()
    m2730 = f"-{d2730}" if d2730 != "0" else "0"

    context.close()
    browser.close()

    rows = [
        ("Scotiabank", "Cuenta Corriente PN", "CC 7002",  "CLP", saldo_raw),
        ("Scotiabank", "Tarjeta de Crédito",  "TdC 3134", "CLP", m3134),
        ("Scotiabank", "Tarjeta de Crédito",  "TdC 2730", "CLP", m2730),
    ]
    print_table(rows)


with sync_playwright() as playwright:
    run(playwright)
