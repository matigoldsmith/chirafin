import os
import random
import subprocess
from playwright.sync_api import Playwright, sync_playwright


def human_delay(min_ms=500, max_ms=1500):
    """Pausa aleatoria para simular comportamiento humano."""
    import time
    time.sleep(random.uniform(min_ms / 1000, max_ms / 1000))


def human_type(page, selector, text):
    """Escribe tecla por tecla con delays aleatorios."""
    selector.click()
    human_delay(300, 700)
    selector.type(text, delay=random.randint(80, 180))


def bw_env():
    """Entorno con SSL desactivado para Bitwarden."""
    env = os.environ.copy()
    env["NODE_TLS_REJECT_UNAUTHORIZED"] = "0"
    return env


def bw_unlock():
    """Desbloquea Bitwarden usando el master password del Keychain de Mac."""
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
    """Obtiene usuario o contraseña desde Bitwarden, renovando sesión si es necesario."""
    env = bw_env()
    result = subprocess.run(
        ["bw", "get", field, item_name],
        capture_output=True, text=True, env=env
    )
    # Si la sesión expiró, desbloquear y reintentar
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
    # Obtener credenciales desde Bitwarden
    print("Obteniendo credenciales desde Bitwarden...")
    rut = bw_get("username", "login.portales.bancochile.cl")
    password = bw_get("password", "login.portales.bancochile.cl")

    if not rut or not password:
        print("❌ No se pudieron obtener las credenciales. Verificá que BW_SESSION esté seteado.")
        return

    browser = playwright.chromium.launch(
        channel="chrome",
        headless=False,
        args=["--disable-blink-features=AutomationControlled"]
    )
    context = browser.new_context(viewport={"width": 1280, "height": 800})
    page = context.new_page()
    page.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")

    print("1. Navegando a Banco de Chile...")
    page.goto("https://sitiospublicos.bancochile.cl/personas")
    page.wait_for_timeout(2000)

    print("2. Clickeando Banco en Linea...")
    human_delay(500, 1200)
    page.get_by_role("link", name="Banco en Línea").click()
    page.get_by_role("textbox", name="RUT RUT").wait_for(timeout=15000)

    print("3. Llenando RUT...")
    rut_field = page.get_by_role("textbox", name="RUT RUT")
    rut_field.click()
    human_delay(300, 600)
    rut_field.fill(rut)
    rut_field.press("Tab")
    human_delay(400, 900)

    print("4. Llenando contrasena...")
    pass_field = page.get_by_role("textbox", name="Contraseña Contraseña")
    pass_field.click()
    human_delay(300, 600)
    pass_field.fill(password)
    human_delay(500, 1000)

    print("5. Clickeando Ingresar...")
    page.get_by_role("button", name="Ingresar a cuenta").click()

    print("6. Cerrando popup...")
    try:
        page.get_by_role("link", name="No ver Más").wait_for(timeout=10000)
        page.get_by_role("link", name="No ver Más").click()
    except:
        pass

    print("7. Buscando saldo CC...")
    page.locator(".monto-cuenta").nth(1).wait_for(timeout=20000)
    saldo_cc = page.locator(".monto-cuenta").nth(1).text_content()

    print("8. Navegando a TdC...")
    page.goto("https://portalpersonas.bancochile.cl/mibancochile-web/front/persona/index.html#/tarjeta-credito/consultar/saldos")
    page.wait_for_timeout(3000)

    print("9. Buscando deuda TdC...")
    # p.lead-title con "Utilizado" → padre → span.number (primero = Nacional CLP)
    utilizado_label = page.locator("p.lead-title", has_text="Utilizado").first
    utilizado_label.wait_for(state="visible", timeout=15000)
    deuda_raw = utilizado_label.locator("xpath=..").locator("span.number").text_content()
    deuda = deuda_raw.strip().replace("$ ", "").replace("$", "").strip()

    print_table("Banco de Chile", [
        ("Banco de Chile", "Cuenta Corriente PN", "CC 5809", "CLP", saldo_cc.strip()),
        ("Banco de Chile", "Tarjeta de Crédito", "TdC 7164", "CLP", "-" + deuda)
    ])

    context.close()
    browser.close()


with sync_playwright() as playwright:
    run(playwright)
