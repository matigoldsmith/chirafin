# 🧪 Testing & Extension Guide - saldos.py

**Date:** 2026-02-27
**Phase:** Validation Testing + Bank Expansion

---

## 📋 PHASE 1: Validation Testing

Before adding new banks, verify that all current fixes and features work correctly.

### Test Matrix

| # | Institution | Feature | Command | Expected Output |
|---|-------------|---------|---------|-----------------|
| 1 | Banco de Chile | CC + TdC + LdC | `python3 saldos.py` → Select B | ✅ All 3 categories |
| 2 | Scotiabank PN | CC + TdC | `python3 saldos.py` → Select S | ✅ CC + TdC saldos |
| 3 | Scotiabank Empresas | CC PJ | `python3 saldos.py` → Select E | ✅ CC PJ saldo |
| 4 | Consorcio | CC + LdC | `python3 saldos.py` → Select C | ✅ CC + LdC |
| 5 | Itaú | CC + TdC | `python3 saldos.py` → Select I | ✅ CC + TdC |
| 6 | Santander | CC + TdC + LdC | `python3 saldos.py` → Select N | ✅ All 3 categories |
| 7 | Ripley | CC + TdC | `python3 saldos.py` → Select R | ✅ CC + TdC |
| 8 | Líder BCI | TdC | `python3 saldos.py` → Select L | ✅ TdC (CAPTCHA warning) |
| 9 | Menu Input | Parse "B S C" | `python3 saldos.py` → Enter "B S C" | ✅ Correct parsing (no crash on "G4") |

### Test Individual Features

#### A) Previous Bug Fixes ✅
```bash
# 1. Banco de Chile LdC selector fix
python3 saldos.py --debug
# → Select: B
# ✓ Verify LdC appears with correct amount (not -200.000)

# 2. Santander independent run fix
python3 saldos.py --debug
# → Select: N
# ✓ Verify no failure when running Santander alone

# 3. Menu parsing ("G4" crash fix)
python3 saldos.py
# → Enter: "G4"
# ✓ Verify menu only accepts single letters (G only)

# 4. Partial update table fix (should show ALL from DB)
python3 saldos.py
# → Select: B (only Chile)
# ✓ Verify table shows ALL previous saldos + new Chile data (not just Chile)

# 5. DB composite key fix (inst, item) for LdC collisions
python3 saldos.py
# → Run multiple times
# ✓ Verify each LdC item shows correct value (not same value for all)
```

#### B) New Refactored Features 🆕
```bash
# 1. Argparse --debug flag (headless=False, see browser)
python3 saldos.py --debug
# → Select: B
# ✓ Verify browser window opens and you can see scraping happen

# 2. Argparse --headless flag (explicit headless=True)
python3 saldos.py --headless
# → Select: B
# ✓ Verify browser runs hidden

# 3. Argparse --no-video flag (disable error video recording)
python3 saldos.py --no-video --debug
# → Intentionally make an error (e.g., modify selector)
# ✓ Verify no .webm files created in backups/error_debug/videos/

# 4. Black box error handling
# Make Scotiabank login fail (use wrong password in Bitwarden)
# ✓ Verify files created in backups/error_debug/:
#   - scotiabank_pn_TIMESTAMP.png (screenshot)
#   - scotiabank_pn_TIMESTAMP.html (DOM dump)
#   - videos/scotiabank_pn_TIMESTAMP_0.webm (video, if error)

# 5. Menu option 5 (Manual Bitwarden sync)
python3 saldos.py
# → Select: 5
# ✓ Verify "Sincronizando Bitwarden..." message
# ✓ Verify bw sync completes without TLS errors

# 6. Docstrings loaded correctly
python3 -c "import saldos; help(saldos.scrape_banco_chile)"
# ✓ Verify docstring with URLs and selectors appears
```

#### C) macOS .command File 🍎
```bash
# 1. Test double-click execution
# → Finder: double-click ejecutar_saldos.command
# ✓ Verify script runs and window stays open

# 2. Test with flags
./ejecutar_saldos.command --debug
# ✓ Verify browser opens and script runs

# 3. Verify colors and formatting
./ejecutar_saldos.command
# ✓ Verify colored output (blue, green, yellow, red)
```

---

## 📊 PHASE 2: Identify New Banks

**Current institutions (9):**
- Banco de Chile ✅
- Scotiabank PN ✅
- Scotiabank Empresas ✅
- Consorcio ✅
- Itaú ✅
- Santander ✅
- Banco Ripley ✅
- Líder BCI ✅

**Potential Chilean banks to add:**

| Bank | Login URL | Account Type | Priority | Notes |
|------|-----------|--------------|----------|-------|
| **Falabella** | `bancofalabella.cl` | CC | High | Popular, easy login |
| **BCI** | `bci.cl` | CC | High | Major bank |
| **BBVA** | `bbva.cl` | CC | High | Major bank |
| **Actinver** | `actinver.cl` | CC | Medium | Investment bank |
| **Banco Security** | `bancosecurity.cl` | CC | Medium | Smaller bank |
| **Banco Corpbanca** | `corpbanca.cl` | CC | Low | Merged with BCI |
| **Monexcb** | `monexcb.cl` | CC | Low | Very niche |
| **Artigas** | `bancoartigas.cl` | CC | Low | Regional |

### Where to start?

**Top 3 candidates** (based on usage + ease):
1. **Falabella** - High popularity, similar login pattern to others
2. **BCI** - Major bank, likely straightforward portal
3. **BBVA** - International bank standard, easier to navigate

---

## 🛠️ PHASE 3: Adding New Banks (Template)

### Step-by-Step Template

#### 1. **Research & Reconnaissance**
```python
# For each new bank, document:
- Login URL
- Home/dashboard URL
- CC balance URL
- TdC balance URL (if exists)
- LdC balance URL (if exists)
- CSS/XPath selectors for each
- Anti-bot measures (JavaScript, CAPTCHA, etc.)
```

#### 2. **Create Bitwarden Entry**
```bash
# In Bitwarden:
# Name: "Banco [Name]" (e.g., "Banco Falabella")
# Username: [your username/RUT]
# Password: [your password]
```

#### 3. **Add to INSTITUTION_ITEMS**
```python
# In saldos.py, add to INSTITUTION_ITEMS dict:

"Banco Falabella": {
    "CC PN": [
        ("CC 1234", "Cuenta Corriente 1234"),
        ("CC 5678", "Cuenta Corriente 5678"),
    ],
    "TdC": [
        ("TdC 9012", "Tarjeta Crédito 9012"),
    ],
    "LdC": [],  # If not applicable
},
```

#### 4. **Create Scraper Function**
```python
def scrape_banco_falabella(context, resultados):
    """
    Extrae saldos desde Banco Falabella.

    URLs procesadas:
    - Login: https://bancofalabella.cl/login
    - Dashboard: https://bancofalabella.cl/cuenta-corriente

    Selectores clave:
    - CC: div.balance-item:has-text("Disponible") → span.amount
    - TdC: div.credit-card.active → span.used-amount

    Retorna: True si éxito, False si falló
    """
    try:
        page = context.new_page()
        setup_console_logging(page, "Banco Falabella")

        # 1. Login
        page.goto("https://bancofalabella.cl/login", wait_until="load")
        page.fill("input#username", bw_get("username", "Banco Falabella"))
        page.fill("input#password", bw_get("password", "Banco Falabella"))
        page.click("button[type='submit']")
        page.wait_for_load_state("load")

        # 2. Navigate to CC balance
        page.goto("https://bancofalabella.cl/cuenta-corriente")

        # 3. Extract CC saldos
        for cc_code, cc_name in INSTITUTION_ITEMS.get("Banco Falabella", {}).get("CC PN", []):
            try:
                amount_text = page.locator("div.balance-item:has-text('Disponible') span.amount").first.text_content()
                amount = int(amount_text.replace(".", "").replace(",", "").strip())
                resultados.append({
                    "institucion": "Banco Falabella",
                    "categoria": "CC PN",
                    "item": cc_code,
                    "monto": amount,
                    "ok": True,
                })
            except Exception as e:
                logger.error(f"❌ Falabella CC error: {e}")
                save_error_debug(page, "Banco Falabella")
                resultados.append({
                    "institucion": "Banco Falabella",
                    "categoria": "CC PN",
                    "item": cc_code,
                    "monto": 0,
                    "ok": False,
                })

        # 4. Navigate to TdC balance (if applicable)
        if INSTITUTION_ITEMS.get("Banco Falabella", {}).get("TdC"):
            try:
                page.goto("https://bancofalabella.cl/tarjeta-credito")
                amount_text = page.locator("div.credit-card.active span.used-amount").text_content()
                amount = -int(amount_text.replace(".", "").replace(",", "").strip())
                resultados.append({
                    "institucion": "Banco Falabella",
                    "categoria": "TdC",
                    "item": "TdC 9012",
                    "monto": amount,
                    "ok": True,
                })
            except Exception as e:
                logger.error(f"❌ Falabella TdC error: {e}")
                resultados.append({
                    "institucion": "Banco Falabella",
                    "categoria": "TdC",
                    "item": "TdC 9012",
                    "monto": 0,
                    "ok": False,
                })

        page.close()
        return True

    except Exception as e:
        logger.error(f"❌ Falabella error: {e}")
        save_error_debug(page, "Banco Falabella")
        return False
```

#### 5. **Add to run_scraping() Menu**
```python
# In run_scraping(), add to institution_list:

elif "F" in selected:
    banks_to_run.append("Banco Falabella")

# And add to the dispatcher:
elif institucion == "Banco Falabella":
    scrape_banco_falabella(context, resultados)
```

#### 6. **Update CLAUDE.md**
Add new bank to the compatibility table in CLAUDE.md with:
- Bank name
- Account types (CC PN, CC PJ, TdC, LdC)
- Bitwarden entry name
- Login URL
- Key selectors

#### 7. **Test**
```bash
# Test just the new bank
python3 saldos.py --debug
# → Select: F
# ✓ Verify CC and TdC scraped correctly
# ✓ Verify amounts appear in table
# ✓ Verify DB saves correctly
```

---

## 🔍 Common Scraper Patterns

### Pattern 1: Simple Login + Navigate
```python
# Most direct approach
page.goto(login_url)
page.fill("input#user", username)
page.fill("input#pass", password)
page.click("button.submit")
page.wait_for_load_state("load")
amount = page.locator("div.balance").first.text_content()
```

### Pattern 2: Login in iframe
```python
# When login form is in iframe
frame = page.frame_locator("#login-frame")
frame.locator("input#rut").fill(rut)
frame.locator("input#pass").press_sequentially(pwd, delay=60)
frame.locator("button").click()
page.wait_for_url("**dashboard**")
```

### Pattern 3: Multiple CC Accounts
```python
# Loop through account tabs
for i, (cc_code, cc_name) in enumerate(account_list):
    if i > 0:
        page.click(f"button[aria-label='Account {i}']")
        page.wait_for_timeout(500)
    amount = page.locator("div.account-balance").first.text_content()
```

### Pattern 4: TdC Carousel
```python
# When TdCs are in carousel/swiper
for card_num in card_list:
    page.click("button.swiper-button-next")
    page.wait_for_timeout(300)
    amount = page.locator("span.used").text_content()
```

---

## ✅ Checklist Before Adding New Bank

- [ ] Bitwarden entry created
- [ ] Login flow tested manually in `--debug` mode
- [ ] All selectors identified and tested
- [ ] Anti-bot measures documented
- [ ] INSTITUTION_ITEMS updated
- [ ] Scraper function written with docstring
- [ ] run_scraping() menu option added
- [ ] CLAUDE.md updated
- [ ] Test run successful
- [ ] Data appears in table
- [ ] Database persists correctly

---

## 📝 Next Steps

1. **Run Test Matrix** - Execute all tests above
2. **Document any failures** - Update CLAUDE.md with known issues
3. **Choose first new bank** - Recommend: Falabella or BCI
4. **Research & create entry** - Find login page, test credentials
5. **Implement scraper** - Follow template above
6. **Test & validate** - Verify before moving to next bank

---

**Version:** 2026-02-27
**Status:** 🔄 Ready for Testing & Extension
