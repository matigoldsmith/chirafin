import os
import sqlite3
import subprocess
import datetime
from playwright.sync_api import sync_playwright

# ══════════════════════════════════════════════════════════════
# BITWARDEN
# ══════════════════════════════════════════════════════════════

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
        capture_output=True, text=True, env=bw_env()
    )
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


# ══════════════════════════════════════════════════════════════
# OUTPUT HELPERS
# ══════════════════════════════════════════════════════════════

CAT_LABELS = {
    "CC PN": "Cuenta Corriente PN",
    "CC PJ": "Cuenta Corriente PJ",
    "TdC":   "Tarjeta de Crédito",
}
CAT_ORDER = ["CC PN", "CC PJ", "TdC"]

def parse_int(s):
    try:
        return int(str(s).strip().replace(".", "").replace(",", "").replace(" ", ""))
    except:
        return None

def fmt_monto(val):
    if val is None:
        return None
    if val < 0:
        return f"-{abs(val):,}".replace(",", ".")
    return f"{val:,}".replace(",", ".")

def add_result(resultados, bank_key, inst, cat, item, monto_str, ok=True):
    monto_int = parse_int(monto_str) if ok else None
    monto_fmt = fmt_monto(monto_int) if monto_int is not None else ("No se pudo obtener" if not ok else monto_str)
    resultados.append({
        "bank_key": bank_key,
        "inst":     inst,
        "cat":      cat,
        "item":     item,
        "moneda":   "CLP",
        "monto":    monto_fmt,
        "monto_int": monto_int,
        "ok":       ok and monto_int is not None,
    })

def print_preliminary(inst, cat, item, monto_str, ok=True):
    label = f"{inst} – {item}"
    if ok:
        val = parse_int(monto_str)
        monto_disp = fmt_monto(val) if val is not None else monto_str
        print(f"  ✅ {label:<30}  CLP  {monto_disp:>15}", flush=True)
    else:
        print(f"  ❌ {label:<30}  No se pudo obtener", flush=True)

def print_table(resultados):
    col_inst  = max(14, max((len(r["inst"]) for r in resultados), default=0))
    col_cat   = max(19, max((len(CAT_LABELS.get(r["cat"], r["cat"])) for r in resultados), default=0))
    col_item  = max(9,  max((len(r["item"]) for r in resultados), default=0))
    col_monto = max(15, max((len(r["monto"]) for r in resultados), default=0) + 2)
    W = col_inst + col_cat + col_item + 6 + col_monto + 14

    sep = "  " + "─" * (W - 2)
    hdr = f"  {'Institución':{col_inst}}  {'Categoría':{col_cat}}  {'Item':{col_item}}  {'Moneda':<6}  {'Monto':>{col_monto}}"

    print("\n" + "═" * W)
    print(" 💰  RESUMEN DE SALDOS".center(W))
    print("═" * W)
    print(hdr)
    print(sep)

    for cat in CAT_ORDER:
        items = [r for r in resultados if r["cat"] == cat]
        if not items:
            continue
        cat_lbl = CAT_LABELS[cat]
        for r in items:
            monto_disp = r["monto"] if r["ok"] else "No se pudo obtener"
            print(f"  {r['inst']:{col_inst}}  {cat_lbl:{col_cat}}  {r['item']:{col_item}}  {r['moneda']:<6}  {monto_disp:>{col_monto}}")

    print("═" * W)


# ══════════════════════════════════════════════════════════════
# SQLITE STORAGE
# ══════════════════════════════════════════════════════════════

def init_db(db_path):
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS saldos (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp   TEXT    NOT NULL,
            institucion TEXT    NOT NULL,
            categoria   TEXT    NOT NULL,
            item        TEXT    NOT NULL,
            moneda      TEXT    NOT NULL,
            monto       INTEGER,
            ok          INTEGER NOT NULL DEFAULT 1
        )
    """)
    conn.commit()
    return conn

def save_to_db(resultados):
    script_dir = os.path.dirname(os.path.abspath(__file__))
    db_path    = os.path.join(script_dir, "saldos.db")
    ts         = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn       = init_db(db_path)
    for r in resultados:
        conn.execute(
            "INSERT INTO saldos (timestamp, institucion, categoria, item, moneda, monto, ok) VALUES (?,?,?,?,?,?,?)",
            (ts, r["inst"], CAT_LABELS.get(r["cat"], r["cat"]), r["item"], r["moneda"],
             r["monto_int"], 1 if r["ok"] else 0)
        )
    conn.commit()
    conn.close()
    saved = sum(1 for r in resultados if r["ok"])
    print(f"\n💾 Guardado en saldos.db — {saved} registros OK, timestamp: {ts}")


# ══════════════════════════════════════════════════════════════
# SCRAPING POR BANCO
# Cada función: (context, resultados) → True si todo OK, False si algo falló
# ══════════════════════════════════════════════════════════════

def scrape_lider_bci(context, resultados):
    key = "lider_bci"
    try:
        page = context.new_page()
        page.goto("https://www.liderbciserviciosfinancieros.cl/login")
        page.wait_for_timeout(2000)
        page.get_by_placeholder("Rut").wait_for(timeout=10000)
        page.get_by_placeholder("Rut").fill(bw_get("username", "liderbciserviciosfinancieros.cl"))
        page.wait_for_timeout(500)
        page.get_by_placeholder("Clave de internet").fill(bw_get("password", "liderbciserviciosfinancieros.cl"))
        page.wait_for_timeout(500)
        page.get_by_role("button", name="Ingresar").click()
        page.wait_for_timeout(2000)
        print("  ⚠️  CAPTCHA posible — resuélvelo en el browser si aparece (90s)...", flush=True)
        page.wait_for_url("**/dashboard**", timeout=90000)
        page.wait_for_timeout(2000)
        try:
            page.locator("dialog button").first.wait_for(state="visible", timeout=5000)
            page.locator("dialog button").first.click()
            page.wait_for_timeout(500)
        except:
            pass
        page.locator("table.balance").first.wait_for(state="visible", timeout=15000)
        deuda_raw = page.locator("table.balance td").first.text_content().strip()
        deuda = deuda_raw.replace("$", "").replace(".", "").strip()
        monto = f"-{deuda}" if deuda not in ("0", "") else "0"
        add_result(resultados, key, "Líder BCI", "TdC", "TdC 5037", monto)
        print_preliminary("Líder BCI", "TdC", "TdC 5037", monto)
        page.close()
        return True
    except Exception as e:
        add_result(resultados, key, "Líder BCI", "TdC", "TdC 5037", "error", ok=False)
        print_preliminary("Líder BCI", "TdC", "TdC 5037", str(e), ok=False)
        try: page.close()
        except: pass
        return False


def scrape_banco_chile(context, resultados):
    key = "banco_chile"
    added = set()
    try:
        page = context.new_page()
        page.goto("https://sitiospublicos.bancochile.cl/personas")
        page.wait_for_timeout(2000)
        page.get_by_role("link", name="Banco en Línea").click()
        page.get_by_role("textbox", name="RUT RUT").wait_for(timeout=15000)
        page.get_by_role("textbox", name="RUT RUT").fill(bw_get("username", "login.portales.bancochile.cl"))
        page.get_by_role("textbox", name="Contraseña Contraseña").fill(bw_get("password", "login.portales.bancochile.cl"))
        page.wait_for_timeout(1000)
        page.get_by_role("textbox", name="RUT RUT").press("Tab")
        page.get_by_role("button", name="Ingresar a cuenta").click()
        try:
            page.get_by_role("link", name="No ver Más").wait_for(timeout=10000)
            page.get_by_role("link", name="No ver Más").click()
        except:
            pass
        page.locator(".monto-cuenta").nth(1).wait_for(timeout=20000)
        saldo_cc = page.locator(".monto-cuenta").nth(1).text_content().strip().replace("$", "").strip()
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
        monto = f"-{deuda}" if deuda != "0" else "0"
        add_result(resultados, key, "Banco de Chile", "TdC", "TdC 7164", monto)
        print_preliminary("Banco de Chile", "TdC", "TdC 7164", monto)
        added.add("TdC 7164")
        page.close()
        return True
    except Exception as e:
        for item, cat in [("CC 5809","CC PN"), ("TdC 7164","TdC")]:
            if item not in added:
                add_result(resultados, key, "Banco de Chile", cat, item, "error", ok=False)
                print_preliminary("Banco de Chile", cat, item, str(e), ok=False)
        try: page.close()
        except: pass
        return False


def scrape_scotiabank_pn(context, resultados):
    """
    Nuevo portal: banco.scotiabank.cl (migrado desde scotiabankchile.cl)
    Login: data-testid="inputDni" / "inputPassword" — igual que antes
    CC saldo: iframe#iframe-stage → p.TextCaption__text--bold "Saldo disponible" → siguiente columna
    TdC: mismo iframe con URL mfe-simple-account-statement-web-cl (por verificar)
    """
    key = "scotiabank_pn"
    added = set()
    try:
        page = context.new_page()
        page.goto("https://www.scotiabankchile.cl/")
        page.wait_for_timeout(2000)
        page.get_by_text("Acceso Scotia").click()
        page.get_by_role("link", name="Ingreso Personas").click()
        page.get_by_test_id("inputDni").wait_for(timeout=15000)
        page.get_by_test_id("inputDni").fill(bw_get("username", "Scotiabank"))
        page.get_by_test_id("inputDni").press("Tab")
        page.wait_for_timeout(1000)
        page.get_by_test_id("inputPassword").fill(bw_get("password", "Scotiabank"))
        page.wait_for_timeout(1000)
        page.get_by_role("button", name="Ingresar").click()
        page.wait_for_load_state("networkidle", timeout=25000)
        page.wait_for_timeout(1000)

        # Navegar directamente a la página de saldos CC
        page.goto("https://www.scotiabank.cl/mfe/sweb/mfe-shell-web-cl/mfe/mfe/ltmnsw/mfe-accounts-balancesmovements-web/?tab=saldos&type=CTACTE")
        page.wait_for_timeout(4000)
        frame = page.frame_locator("iframe#iframe-stage")
        saldo_label = frame.locator("p.TextCaption__text--bold", has_text="Saldo disponible")
        saldo_label.wait_for(state="visible", timeout=20000)
        saldo_raw = saldo_label.locator(
            "xpath=ancestor::div[contains(@class,'Column__container')]/following-sibling::div[1]/p"
        ).text_content().strip()
        saldo = saldo_raw.replace("$", "").strip()
        add_result(resultados, key, "Scotiabank", "CC PN", "CC 7002", saldo)
        print_preliminary("Scotiabank", "CC PN", "CC 7002", saldo)
        added.add("CC 7002")

        # TdC
        def get_cupo(card_number):
            url = f"https://www.scotiabank.cl/mfe/sweb/mfe-shell-web-cl/mfe/mfe-simple-account-statement-web-cl/?tab=saldo&card={card_number}"
            page.goto(url)
            page.wait_for_timeout(4000)
            f = page.frame_locator("iframe#iframe-stage")
            cupo = f.locator("div.saldo", has_text="Cupo utilizado").first
            cupo.wait_for(state="visible", timeout=15000)
            return cupo.locator("h1.saldo__text").text_content().strip()

        for card, item in [("3134", "TdC 3134"), ("2730", "TdC 2730")]:
            deuda = get_cupo(card).replace("$", "").strip()
            monto = f"-{deuda}" if deuda != "0" else "0"
            add_result(resultados, key, "Scotiabank", "TdC", item, monto)
            print_preliminary("Scotiabank", "TdC", item, monto)
            added.add(item)

        page.close()
        return True
    except Exception as e:
        for item, cat in [("CC 7002","CC PN"), ("TdC 3134","TdC"), ("TdC 2730","TdC")]:
            if item not in added:
                add_result(resultados, key, "Scotiabank", cat, item, "error", ok=False)
                print_preliminary("Scotiabank", cat, item, str(e), ok=False)
        try: page.close()
        except: pass
        return False


def scrape_banco_ripley(context, resultados):
    key = "banco_ripley"
    added = set()
    try:
        page = context.new_page()
        page.goto("https://web.bancoripley.cl/login")
        page.wait_for_timeout(2000)
        page.get_by_role("textbox").wait_for(timeout=10000)
        page.get_by_role("textbox").click()
        page.get_by_role("textbox").press_sequentially(bw_get("username", "web.bancoripley.cl"), delay=50)
        page.get_by_role("textbox").press("Tab")
        page.wait_for_timeout(1000)
        page.get_by_role("button", name="Continuar").click(force=True)
        page.wait_for_timeout(1500)
        page.get_by_role("textbox").wait_for(timeout=8000)
        page.get_by_role("textbox").fill(bw_get("password", "web.bancoripley.cl"))
        page.get_by_role("button", name="Continuar").click()
        page.wait_for_url("**/home**", timeout=30000)
        page.wait_for_timeout(7000)
        page.locator("h5.h5-xs").first.wait_for(timeout=30000)
        saldo = page.locator("h5.h5-xs").first.text_content().strip().replace("$", "").strip()
        add_result(resultados, key, "Banco Ripley", "CC PN", "CC 2239", saldo)
        print_preliminary("Banco Ripley", "CC PN", "CC 2239", saldo)
        added.add("CC 2239")
        # TdC
        tdc_card = page.locator("div", has_text="Titular ****9647").first
        utilizado_row = tdc_card.locator("div.min-w-\\[76px\\]", has_text="Utilizado")
        utilizado_row.wait_for(state="visible", timeout=15000)
        deuda_rip = utilizado_row.locator("xpath=..").locator("span.label-md").text_content().strip().replace("$ ", "").replace("$", "").strip()
        monto = f"-{deuda_rip}" if deuda_rip != "0" else "0"
        add_result(resultados, key, "Banco Ripley", "TdC", "TdC 9647", monto)
        print_preliminary("Banco Ripley", "TdC", "TdC 9647", monto)
        added.add("TdC 9647")
        page.close()
        return True
    except Exception as e:
        for item, cat in [("CC 2239","CC PN"), ("TdC 9647","TdC")]:
            if item not in added:
                add_result(resultados, key, "Banco Ripley", cat, item, "error", ok=False)
                print_preliminary("Banco Ripley", cat, item, str(e), ok=False)
        try: page.close()
        except: pass
        return False


def scrape_santander(context, resultados):
    key = "santander"
    added = set()
    try:
        page = context.new_page()
        page.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
        page.goto("https://mibanco.santander.cl")
        page.wait_for_timeout(3000)
        page.locator("input").first.wait_for(timeout=10000)
        page.locator("input").first.fill(bw_get("username", "banco.santander.cl"))
        page.wait_for_timeout(500)
        page.locator("input[type='password']").first.fill(bw_get("password", "banco.santander.cl"))
        page.wait_for_timeout(500)
        page.get_by_role("button", name="Ingresar").click()
        page.wait_for_timeout(3000)
        page.wait_for_url("**/frame/**", timeout=25000)
        page.wait_for_timeout(2000)
        page.goto("https://mibanco.santander.cl/UI.Web.HB/Private_new/frame/#/private/saldos/main/mi-cuenta")
        page.wait_for_timeout(4000)
        page.locator("p.red-primary-santander").wait_for(timeout=25000)
        saldo = page.locator("p.red-primary-santander").first.text_content().strip().replace("$", "").strip()
        add_result(resultados, key, "Santander", "CC PN", "CC 2241", saldo)
        print_preliminary("Santander", "CC PN", "CC 2241", saldo)
        added.add("CC 2241")
        # TdC
        page.goto("https://mibanco.santander.cl/UI.Web.HB/Private_new/frame/#/private/VerDatosTarjeta/main")
        page.wait_for_timeout(3000)
        page.locator("div.used-amount").first.wait_for(state="visible", timeout=15000)
        deuda_4765 = page.locator("div.used-amount").first.text_content().strip().replace("$", "").strip()
        monto_4765 = f"-{deuda_4765}" if deuda_4765 != "0" else "0"
        add_result(resultados, key, "Santander", "TdC", "TdC 4765", monto_4765)
        print_preliminary("Santander", "TdC", "TdC 4765", monto_4765)
        added.add("TdC 4765")
        page.locator("mat-select").click()
        page.wait_for_timeout(1000)
        page.get_by_text("Worldmember Amex").click()
        page.wait_for_timeout(2000)
        page.locator("div.used-amount").first.wait_for(state="visible", timeout=15000)
        deuda_8098 = page.locator("div.used-amount").first.text_content().strip().replace("$", "").strip()
        monto_8098 = f"-{deuda_8098}" if deuda_8098 != "0" else "0"
        add_result(resultados, key, "Santander", "TdC", "TdC 8098", monto_8098)
        print_preliminary("Santander", "TdC", "TdC 8098", monto_8098)
        added.add("TdC 8098")
        page.close()
        return True
    except Exception as e:
        for item, cat in [("CC 2241","CC PN"), ("TdC 4765","TdC"), ("TdC 8098","TdC")]:
            if item not in added:
                add_result(resultados, key, "Santander", cat, item, "error", ok=False)
                print_preliminary("Santander", cat, item, str(e), ok=False)
        try: page.close()
        except: pass
        return False


def scrape_itau(context, resultados):
    key = "itau"
    added = set()
    try:
        page = context.new_page()
        page.goto("https://banco.itau.cl/wps/portal/newolb/web/login/")
        page.wait_for_load_state("load", timeout=15000)
        page.wait_for_timeout(2000)
        page.locator("input#loginNameID").wait_for(state="visible", timeout=10000)
        page.locator("input#loginNameID").press_sequentially(bw_get("username", "banco.itau.cl"), delay=50)
        page.wait_for_timeout(500)
        page.locator("input#pswdId").press_sequentially(bw_get("password", "banco.itau.cl"), delay=50)
        page.wait_for_timeout(500)
        page.locator("input#btnLoginPortal").click()
        page.wait_for_load_state("load", timeout=15000)
        page.wait_for_timeout(3000)
        page.goto("https://banco.itau.cl/wps/myportal/newolb/web/cuentas/cuenta-corriente/saldos/")
        page.wait_for_timeout(3000)
        label = page.locator("small.itau-card-text", has_text="Saldo disponible para uso")
        label.wait_for(timeout=15000)
        saldo = label.locator("xpath=..").locator("h6.itau-card-title").text_content().strip().replace("$", "").strip()
        add_result(resultados, key, "Itaú", "CC PN", "CC 8792", saldo)
        print_preliminary("Itaú", "CC PN", "CC 8792", saldo)
        added.add("CC 8792")
        # TdC
        page.goto("https://banco.itau.cl/wps/myportal/newolb/web/tarjeta-credito/resumen/deuda/")
        page.wait_for_timeout(3000)
        page.locator("p.monto-saldo").nth(1).wait_for(state="visible", timeout=15000)
        deuda_raw = page.locator("p.monto-saldo").nth(1).text_content().strip().replace("$ ", "").replace("$", "").strip()
        monto = f"-{deuda_raw}" if deuda_raw != "0" else "0"
        add_result(resultados, key, "Itaú", "TdC", "TdC 6132", monto)
        print_preliminary("Itaú", "TdC", "TdC 6132", monto)
        added.add("TdC 6132")
        page.close()
        return True
    except Exception as e:
        for item, cat in [("CC 8792","CC PN"), ("TdC 6132","TdC")]:
            if item not in added:
                add_result(resultados, key, "Itaú", cat, item, "error", ok=False)
                print_preliminary("Itaú", cat, item, str(e), ok=False)
        try: page.close()
        except: pass
        return False


def scrape_consorcio(context, resultados):
    key = "consorcio"
    try:
        page = context.new_page()
        page.goto("https://login.consorcio.cl/onboarding-consorcio/admin")
        page.wait_for_load_state("load", timeout=15000)
        page.wait_for_timeout(1500)
        page.locator("input#input-rut").wait_for(state="visible", timeout=10000)
        page.locator("input#input-rut").press_sequentially(bw_get("username", "login.consorcio.cl"), delay=50)
        page.wait_for_timeout(500)
        page.locator("input#input-new-pass").press_sequentially(bw_get("password", "login.consorcio.cl"), delay=50)
        page.wait_for_timeout(500)
        page.get_by_role("button", name="Ingresar").click()
        page.wait_for_load_state("load", timeout=15000)
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
        add_result(resultados, key, "Consorcio", "CC PN", "CC 6758", saldo)
        print_preliminary("Consorcio", "CC PN", "CC 6758", saldo)
        page.close()
        return True
    except Exception as e:
        add_result(resultados, key, "Consorcio", "CC PN", "CC 6758", "error", ok=False)
        print_preliminary("Consorcio", "CC PN", "CC 6758", str(e), ok=False)
        try: page.close()
        except: pass
        return False


def scrape_scotiabank_pj(context, resultados):
    key = "scotiabank_pj"
    try:
        page = context.new_page()
        page.goto("https://appservtrx.scotiabank.cl/portalempresas/login")
        page.wait_for_timeout(2000)
        page.get_by_placeholder("RUT Empresa").fill("77.788.417-4")
        page.get_by_placeholder("RUT Usuario").fill(bw_get("username", "Scotiabank Empresas"))
        page.locator("#INP_COMMON_PASSWORD_PASS").fill(bw_get("password", "Scotiabank Empresas"))
        page.wait_for_timeout(500)
        page.get_by_role("button", name="Ingresar").click()
        page.wait_for_url("**/portalempresas/home**", timeout=15000)
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
        add_result(resultados, key, "Scotiabank", "CC PJ", "CC 7381", saldo)
        print_preliminary("Scotiabank", "CC PJ", "CC 7381", saldo)
        page.close()
        return True
    except Exception as e:
        add_result(resultados, key, "Scotiabank", "CC PJ", "CC 7381", "error", ok=False)
        print_preliminary("Scotiabank", "CC PJ", "CC 7381", str(e), ok=False)
        try: page.close()
        except: pass
        return False


# ══════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════

# Bancos con CAPTCHA — siempre se ejecutan primero si están seleccionados
CAPTCHA_KEYS = {"lider_bci"}

# Lista maestra ordenada alfabéticamente (para menú y output)
ALL_BANKS = sorted([
    ("Banco de Chile",      scrape_banco_chile,   "banco_chile"),
    ("Banco Ripley",        scrape_banco_ripley,  "banco_ripley"),
    ("Consorcio",           scrape_consorcio,     "consorcio"),
    ("Itaú",                scrape_itau,          "itau"),
    ("Líder BCI",           scrape_lider_bci,     "lider_bci"),
    ("Santander",           scrape_santander,     "santander"),
    ("Scotiabank Empresas", scrape_scotiabank_pj, "scotiabank_pj"),
    ("Scotiabank PN",       scrape_scotiabank_pn, "scotiabank_pn"),
], key=lambda x: x[0])

# ── Paso 1: Desbloquear Bitwarden ──────────────────────────────
print("\n" + "═" * 44)
print("      💰  CONSULTA DE SALDOS")
print("═" * 44)
bw_unlock()

# ── Paso 2: Selección de instituciones ────────────────────────
print("\n¿Qué instituciones consultar?\n")
print(f"  {'0)':<5} Todas")
for i, (name, _, _) in enumerate(ALL_BANKS, 1):
    print(f"  {str(i)+')':<5} {name}")
print()
raw = input("Ingresa números separados por coma (ej: 1,3,5) o 0 para todo: ").strip()

if raw == "0" or raw == "":
    selected = list(ALL_BANKS)
else:
    indices  = [int(x.strip()) for x in raw.split(",") if x.strip().isdigit()]
    selected = [ALL_BANKS[i-1] for i in indices if 1 <= i <= len(ALL_BANKS)]
    if not selected:
        print("Selección inválida, se consultarán todas.")
        selected = list(ALL_BANKS)

# Orden de ejecución: CAPTCHA primero, el resto mantiene orden alfabético
captcha_first   = [b for b in selected if b[2] in CAPTCHA_KEYS]
rest            = [b for b in selected if b[2] not in CAPTCHA_KEYS]
execution_order = captcha_first + rest

print(f"\n→ Consultando: {', '.join(n for n,_,_ in execution_order)}\n")

# ── Paso 3: Ejecución ─────────────────────────────────────────
with sync_playwright() as p:
    browser = p.chromium.launch(
        channel="chrome",
        headless=False,
        args=["--disable-blink-features=AutomationControlled"]
    )
    context = browser.new_context(viewport={"width": 1280, "height": 800})
    resultados = []

    failed = []
    for name, func, key in execution_order:
        print(f"\nConsultando {name}...", flush=True)
        ok = func(context, resultados)
        if not ok:
            failed.append((name, func, key))

    # — Retry loop —
    while failed:
        print(f"\n{'─'*44}")
        print(f"❌ Fallaron {len(failed)} banco(s):")
        for name, _, _ in failed:
            print(f"  • {name}")
        resp = input("¿Reintentar los fallidos? (s/n): ").strip().lower()
        if resp != "s":
            break
        still_failed = []
        for name, func, key in failed:
            resultados = [r for r in resultados if not (r["bank_key"] == key and not r["ok"])]
            print(f"\nReintentando {name}...", flush=True)
            ok = func(context, resultados)
            if not ok:
                still_failed.append((name, func, key))
        failed = still_failed

    context.close()
    browser.close()

# ── Paso 4: Output final (orden por categoría, luego alfabético) ──
resultados.sort(key=lambda r: (CAT_ORDER.index(r["cat"]) if r["cat"] in CAT_ORDER else 99, r["inst"]))
print_table(resultados)
save_to_db(resultados)
