# Proyecto: Consulta de Saldos Bancarios
**Usuario:** Matias Goldsmith (mgoldsmithd) — Chile
**RUT PN:** 15.641.707-6
**RUT PJ (One Western Spa):** 77.788.417-4

---

## Arquitectura

- **`saldos.py`** — script consolidado, corre todos los bancos
- **Scripts individuales** — uno por banco, para testing y debug
- **`backups/`** — logs de ejecución con timestamp
- **`archive/`** — scripts temporales y versiones antiguas
- **`venv/`** — entorno Python

Siempre usar: `python3 <script>.py`
Nunca `python` (no existe en este sistema).

---

## Stack técnico

- **Python 3 + Playwright (sync API)** — browser automation
- **Bitwarden CLI (`bw`)** — gestión de credenciales
- **Chromium con `channel="chrome"`**, `headless=False`
- **`NODE_TLS_REJECT_UNAUTHORIZED=0`** — necesario para `bw sync`

### Patrón Bitwarden
```python
def bw_env():
    env = os.environ.copy()
    env["NODE_TLS_REJECT_UNAUTHORIZED"] = "0"
    return env

def bw_unlock():
    master = subprocess.run(["security", "find-generic-password", "-a", "bitwarden", "-s", "bitwarden-master", "-w"], capture_output=True, text=True).stdout.strip()
    result = subprocess.run(["bw", "unlock", master, "--raw"], capture_output=True, text=True, env=bw_env())
    session = result.stdout.strip()
    if session: os.environ["BW_SESSION"] = session
    else: raise Exception("No se pudo desbloquear Bitwarden")

def bw_get(field, item_name):  # field = "username" | "password"
    env = bw_env()
    result = subprocess.run(["bw", "get", field, item_name], capture_output=True, text=True, env=env)
    if "Session key is invalid" in result.stderr or not result.stdout.strip():
        bw_unlock()
        result = subprocess.run(["bw", "get", field, item_name], capture_output=True, text=True, env=bw_env())
    return result.stdout.strip()
```

---

## Bancos implementados

### CC Persona Natural

| Script | Banco | Cuenta | Bitwarden entry | URL login |
|--------|-------|--------|-----------------|-----------|
| `banco_chile.py` | Banco de Chile | CC PN 5809 | `login.portales.bancochile.cl` | `sitiospublicos.bancochile.cl/personas` → "Banco en Línea" |
| `scotiabank.py` | Scotiabank | CC PN 7002 | `Scotiabank` | `scotiabankchile.cl` → Acceso Scotia → Ingreso Personas |
| `ripley.py` | Banco Ripley | CC PN 2239 | `web.bancoripley.cl` | `web.bancoripley.cl/login` |
| `santander.py` | Santander | CC PN 2241 | `banco.santander.cl` | `mibanco.santander.cl` |
| `itau.py` | Itaú | CC PN 8792 | `banco.itau.cl` | `banco.itau.cl/wps/portal/newolb/web/login/` |
| `consorcio.py` | Consorcio | CC PN 6758 | `login.consorcio.cl` | `login.consorcio.cl/onboarding-consorcio/admin` |

### CC Persona Jurídica (One Western Spa 77.788.417-4)

| Script | Banco | Cuenta | Bitwarden entry | URL login |
|--------|-------|--------|-----------------|-----------|
| `scotiabank_empresas.py` | Scotiabank | CC PJ 7381 | `Scotiabank Empresas` | `appservtrx.scotiabank.cl/portalempresas/login` |

### Tarjetas de Crédito

| Script | Banco | TdC | Selector clave |
|--------|-------|-----|----------------|
| `banco_chile.py` | Banco de Chile | TdC 7164 | `p.lead-title:has-text("Utilizado") → span.number` |
| `scotiabank.py` | Scotiabank | TdC 3134, 2730 | iframe `#iframe-stage` → `div.saldo:has-text("Cupo utilizado") → h1.saldo__text` |
| `ripley.py` | Banco Ripley | TdC 9647 | `div:has-text("Titular ****9647") → div.min-w-[76px]:has-text("Utilizado") → span.label-md` |
| `santander.py` | Santander | TdC 4765, 8098 | `div.used-amount` (8098 = "Worldmember Amex" en mat-select) |
| `itau.py` | Itaú | TdC 6132 | `p.monto-saldo.nth(1)` (nth(0) = disponible, nth(1) = utilizado) |
| `lider_bci.py` | Líder BCI | TdC 5037 | `table.balance.first → td.first` |

---

## Selectores clave por banco

### Scotiabank PN (`scotiabankchile.cl`)
- RUT: `data-testid="inputDni"`
- Password: `data-testid="inputPassword"`
- CC saldo: `page.get_by_text("-$").first` (saldo en rojo = descubierto)
- **IMPORTANTE**: Agregar `add_init_script` webdriver spoofing NO es necesario aquí

### Santander (`mibanco.santander.cl`)
- **CRÍTICO**: `page.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")` — sin esto falla detección
- Esperar `wait_for_timeout(3000)` entre click Ingresar y `wait_for_url`
- CC: `p.red-primary-santander`
- Post-login: navegar a `#/private/saldos/main/mi-cuenta` con `wait_for_timeout(4000)`

### Scotiabank Empresas (`appservtrx.scotiabank.cl`)
- RUT Empresa: hardcoded `77.788.417-4`
- RUT Usuario: bw `Scotiabank Empresas` username
- Password: `#INP_COMMON_PASSWORD_PASS` (NO usar placeholder="Clave" — hay 3 elementos)
- Saldo CC: `#DISPONIBLE_CTA_DK`
- Número cuenta: `#NRO_CTA_DK`
- Post-login URL: `**/portalempresas/home**` → navegar a `/home/products`

### Líder BCI (`liderbciserviciosfinancieros.cl`)
- **CAPTCHA Cloudflare Turnstile** — no se puede auto-resolver
- Siempre imprimir aviso + esperar 90s incondicional post-click
- Selector deuda: `table.balance.first → td.first`

### Itaú (`banco.itau.cl`)
- RUT: `input#loginNameID`
- Password: `input#pswdId`
- CC saldo: `small.itau-card-text:has-text("Saldo disponible para uso") → h6.itau-card-title`
- TdC utilizado: `p.monto-saldo.nth(1)` (nth(0) es disponible)

---

## Formato de output (tabla)

Columnas: `Institución | Categoría | Item | Moneda | Monto`
- Moneda siempre `CLP`
- Monto **alineado a la derecha**, sin signo `$`
- Negativo para deudas (TdC y CC en descubierto)
- Subtotales por categoría
- Backup automático en `backups/YYYY-MM-DD_HH-MM.txt`

---

## Convenciones de nombres

- Categorías: `CC PN`, `CC PJ`, `TdC`
- Items: `CC 7002`, `TdC 3134`, etc.
- Instituciones: `Banco de Chile`, `Scotiabank`, `Banco Ripley`, `Santander`, `Itaú`, `Consorcio`, `Líder BCI`

---

## Problemas conocidos / fixes

1. **`bw sync` falla TLS** → usar `NODE_TLS_REJECT_UNAUTHORIZED=0 bw sync`
2. **Santander falla en consolidado** → necesita `add_init_script` webdriver per-page + `wait_for_timeout(3000)` post-click
3. **Itaú TdC `.first` da disponible** → usar `.nth(1)` para utilizado
4. **Scotiabank Empresas password placeholder ambiguo** → usar `#INP_COMMON_PASSWORD_PASS`
5. **Líder BCI `table.balance` strict mode** → usar `.first` en el locator

---

## Pendiente

- Itaú CC PJ (en progreso — navegar a portal empresas Itaú)
- Verificar Scotiabank PN (posible regresión)
