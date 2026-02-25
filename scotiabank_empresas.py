import os
import subprocess
from playwright.sync_api import Playwright, sync_playwright


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
    rut_usuario = bw_get("username", "Scotiabank Empresas")
    password    = bw_get("password", "Scotiabank Empresas")

    if not rut_usuario or not password:
        print("❌ No se pudieron obtener las credenciales.")
        return

    browser = playwright.chromium.launch(
        channel="chrome",
        headless=False,
        args=["--disable-blink-features=AutomationControlled"]
    )
    context = browser.new_context(viewport={"width": 1280, "height": 800})
    page = context.new_page()

    print("1. Navegando a Scotiabank Empresas...")
    page.goto("https://appservtrx.scotiabank.cl/portalempresas/login")
    page.wait_for_timeout(2000)

    print("2. Llenando credenciales...")
    page.get_by_placeholder("RUT Empresa").fill("77.788.417-4")
    page.get_by_placeholder("RUT Usuario").fill(rut_usuario)
    page.locator("#INP_COMMON_PASSWORD_PASS").fill(password)
    page.wait_for_timeout(500)

    print("3. Ingresando...")
    page.get_by_role("button", name="Ingresar").click()
    page.wait_for_url("**/portalempresas/home**", timeout=15000)
    page.wait_for_timeout(1000)

    print("4. Cerrando modal de términos si aparece...")
    try:
        page.get_by_role("button", name="Aceptar").wait_for(state="visible", timeout=3000)
        page.get_by_role("button", name="Aceptar").click()
        page.wait_for_timeout(500)
    except:
        pass

    print("5. Navegando a cuentas...")
    page.goto("https://appservtrx.scotiabank.cl/portalempresas/home/products")
    page.locator("#DISPONIBLE_CTA_DK").wait_for(state="visible", timeout=15000)

    num_cuenta = page.locator("#NRO_CTA_DK").inner_text().strip()      # (****7381)
    saldo_raw  = page.locator("#DISPONIBLE_CTA_DK").inner_text().strip()
    saldo      = saldo_raw.replace("$", "").strip()

    print_table("Scotiabank Empresas", [
        ("Scotiabank", "Cuenta Corriente PJ", "CC 7381", "CLP", saldo)
    ])

    context.close()
    browser.close()


with sync_playwright() as playwright:
    run(playwright)
