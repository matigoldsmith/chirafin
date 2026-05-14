# Proyecto: Consulta de Saldos Bancarios
**Usuario:** Matias Goldsmith (mgoldsmithd) — Chile
**RUT PN:** 15.641.707-6
**RUT PJ (One Western Spa):** 77.788.417-4

## Fuente de datos (SIEMPRE usar esta)
**CRÍTICO:** Leer SIEMPRE desde Supabase (proyecto "Patrimonio", ID: `mbovlripktxzjqyerizg`, región us-east-2).
Nunca leer desde SQLite local (`saldos.db`) salvo que Supabase no esté disponible.
Tablas: `public.saldos`, `public.catalog_manual`, `public.currency_rates`, `public.tir_investments`, `public.tir_dividends`

---

## Clasificación de patrimonio (SIEMPRE usar esta)

Al mostrar cualquier resumen o visualización de patrimonio, SIEMPRE usar las categorías y criterios de chirafin (`cat_to_short()` en saldos.py):

**Categorías en orden** (CAT_ORDER):
1. **Cash** — CCs bancarias, savings, checking, cash PN/PJ
2. **Fondos líquidos** — Inversiones Líquidas PN/PJ + Fondos líquidos PJ + TdC (negativo) + LdC (negativo). **Las TdC se netean aquí, NO se muestran separadas.**
3. **Fondos Inmobiliarios** — Fraccional PN/PJ, HDZ (Dorco), WBuild
4. **Propiedades de Inversión** — Icom + CH Itaú (Cívico) + CH BdChile (New) + CH Santander (Like 50%) — neto UF
5. **Inversión en startups** — Woperty SAFE
6. **Fondos previsionales** — AFP Modelo + AFC + Fintual APV
7. **Casa** — Taihuén neto (valor − CH Consorcio) — UF

**Totales que muestra chirafin:**
- TOTAL PATRIMONIO INVERSIONES = categorías 1–6
- TOTAL GENERAL = todo (incluye Casa)
- Columnas: CLP MM · USD M · UF · %

**CRÍTICO:** Nunca mostrar TdC como línea separada. Siempre netear dentro de Fondos líquidos.

---

## Preferencias del usuario

- **Path del proyecto**: `/Users/mgoldsmithd/Scripts Claude AI` (NO en Documents ni Desktop)
- **Comando base**: `cd "/Users/mgoldsmithd/Scripts Claude AI" && source venv/bin/activate && PROMPT_TOOLKIT_NO_CPR=1 python3 saldos.py`
- **Terminal siempre**: el usuario prefiere ejecutar desde terminal (no acceso directo en escritorio)
- **Comando completo siempre**: cuando se le pida ejecutar algo, dar el comando completo copiable, nunca solo el nombre del script
- **Ventanas siempre visibles**: headless=False por defecto — el usuario quiere ver el navegador mientras corre. Solo usar --headless si se pide explícitamente
- **Nueva extracción → Claude Chrome SIEMPRE**: cuando hay que aprender a extraer datos de un sitio nuevo, usar Claude in Chrome para navegar e inspeccionar selectores en vivo. NUNCA escribir un script de debug separado para esto.

---

## STATUS ACTUAL (14 May 2026) ✅ FUNCIONANDO

**Cambios críticos 14 May 2026 — scraper_config Supabase-first + SCO-PAGOS fixes + menu homologation:**

1. **`scraper_config` tabla en Supabase:** `_seed_scraper_config()` y `_reload_inactive_keys()` leen primero Supabase, SQLite como fallback.
2. **`manage_scrapers()` unificado:** 3 modos — "Configurar todos los automáticos" / "Configurar solo de inversiones" / "Configurar solo bancarios". UI idéntica (misma función `_render_table()` + `_save_changes()`). 🔴 OFF mostrado en rojo.
3. **`in_bancarios` columna en `scraper_config`:** Supabase + SQLite con migration automática en `init_db()`.
4. **CxC/CxP ocultas cuando cero:** `_NOTE_REQUIRED_ITEMS = {"Cuentas por cobrar", "Cuentas por pagar"}` — siempre se saltan en `show_last_saldos()` cuando monto=0.
5. **SCO-PAGOS `_extract_pago_rows()` fix:** `isPagoKeyword` ahora incluye `\\bpago\\b` (word boundary). "PAGO EN EFECTIVO", "PAGO" → ✅. `otrospagos.com` excluido explícitamente (es compra, no pago, a menos que monto sea negativo).
6. **Debug SCO-PAGOS mejorado:** Imprime TODOS los movimientos no-facturados con `✅ PAGO` / `❌  no ` por fila + total sumado. (Ya no solo primeras 8.)
7. **Menú homologación completa:** Todos los `questionary.select/checkbox` usan "« Volver", `qmark=""`, `patch_stdout=True`, estilo QUESTIONARY_STYLE. Backup: `backups/saldos_20260514_supabase_cfg_sco_fix_homolog.py`.

---

## STATUS ANTERIOR (13 May 2026) ✅ FUNCIONANDO

**Cambios críticos 13 May 2026 — UI Configuración + Scotiabank pagos TdC fix:**

1. **Menú principal — Configuración:**
   - Sin ⚙ emoji en "Configuración" (consistencia visual)
   - Un solo separador antes de "Salir" (ya no hay doble separador aislando Configuración)
   - Submenú Configuración tiene 2 opciones: "Gestión de scrapers" y "Sincronizar Bitwarden"
   - Sincronizar Bitwarden: corre `bw sync` y muestra resultado con Rich inline

2. **`manage_scrapers()` reescrito:**
   - Tabla y checkboxes separados en **Persona Natural** y **Persona Jurídica**
   - TIR/semi-automáticos (HDZ, WBuild) excluidos — no se gestionan aquí
   - Sin columna "Última lectura" (simplificado)
   - Flujo: checkbox primero (solo scrapers) → select separado para Guardar/Volver/Salir
   - "Volver atrás" con Enter funciona correctamente (ya no requiere Space+Enter)
   - `_PJ_KEYS = {"global66_pj", "btg_pj", "fintual_pj", "fraccional_pj", "itau_pj", "scotiabank_pj"}`

3. **Selector de scrapers (Actualizar específicos):**
   - Eliminado "Salir del programa" del checkbox — solo "Volver atrás"

4. **`prompt_failed_items()` — columna Persona:**
   - Columna "P." (PN/PJ) aparece automáticamente solo cuando hay ambigüedad (misma institución en ambos tipos)
   - Útil para Global 66 (PJ), Fraccional (PN y PJ), etc.

5. **`_scrape_sco_pagos_card()` — Scotiabank PN TdC 3134 y 2730 — fix completo:**
   - **Problema anterior**: buscaba filas con valores monetarios negativos para detectar pagos. Scotiabank muestra pagos como valores positivos con descripción "PAGO"/"ABONO"/etc.
   - **Nueva lógica de detección** (`_extract_pago_rows`): fila es pago si CUALQUIERA de estas condiciones aplica:
     - Valor monetario negativo (`-$X`)
     - Descripción contiene: `pago` (excepto `otrospagos.com`), `abono`, `canje`, `devolución`/`devolucion`, `nota de crédito`/`nota de credito`
   - **Monto siempre positivo**: `_last_money_col` retorna `lstrip('-')` — el signo no importa para la suma
   - **Busca en ambos tabs**: `movimientos-facturados` (pago del estado de cuenta) + `movimientos-no-facturados` (abonos período actual)
   - Debug logging: primeras 5 filas de cada tab se imprimen para diagnóstico
   - `_extract_neg_intl` también actualizado con regex `isNegMoney` más estricto
   - `parse_clp` con try/except (safe ante texto inesperado)

6. **Backup**: `backups/saldos_20260513_ui_scrapers_sco_pagos.py`

---

## STATUS ANTERIOR (6 May 2026) ✅ FUNCIONANDO

**Cambios críticos 6 May 2026 — BTG workaround CLP + Supabase precision fix:**

1. **BTG Workaround de precio (cuando scraper falla):**
   - `_BTG_ITEM_TICKERS`: cambiado de tickers USD (SPY/QQQ/ACWI) a tickers de Bolsa de Comercio de Santiago: `CFISP500.SN`, `CFINASDAQ.SN`, `CFIETFGE.SN`
   - Precios directamente en CLP → sin ajuste FX → error ~0.3% (vs ~1.9% con tickers USD)
   - `yfinance` instalado en venv (era la razón por la que el workaround no corría)
   - Bug strict mode corregido: `page.locator("#rut").is_visible()` → `.first.is_visible()` en `scrape_btg()` (2 lugares)
   - El workaround se activa automáticamente cuando BTG PN o PJ tiene ítems con `ok=False`

2. **Supabase columna `monto` migrada de `real` a `double precision`:**
   - `real` (float32) serializa JSON con 6 dígitos significativos → valores terminaban en "00" (ej: 96.696.400 en vez de 96.696.371)
   - Migración: drop 3 vistas dependientes → ALTER COLUMN → recrear vistas
   - Vistas recreadas: `v_latest_saldos`, `v_latest_saldos_all`, `vista_saldos_consolidados`
   - Datos históricos conservados tal cual (no se corrigieron hacia atrás)

3. **Backup**: `backups/saldos_20260506_btg_workaround_clp_tickers.py`

---

## STATUS ANTERIOR (29 Apr 2026) ✅ FUNCIONANDO

**Cambios críticos 29 Apr 2026 — pagos_tdc Santander + vista split:**
1. **Santander pagos TdC 4765 y 8098**: Nuevo bloque en `scrape_santander()` antes de `return True`.
   - `facturado_clp` = SALDO INICIAL de `/bill` (`td.cdk-column-amountCharge` de la fila con "SALDO INICIAL")
   - `no_facturado_clp` = suma de abonos con prefijo `+` en `td.cdk-column-paymentAmount` de `/bill`
   - `periodo_hasta` + `pagar_hasta` = de `/billed` via `.mat-select-min-line` y `span.margin-0` (nth siguiente al label)
   - 4765 = "Worldmember Visa", 8098 = "Worldmember Amex"; contexto Angular preserva tarjeta activa al navegar child routes
   - Para 8098: vuelve a `/detail` + click `.swiper-button-next` antes de navegar a `/billed` y `/bill`
2. **Itaú TdC 6132**: `pagado_clp` (DB) = pagos del ciclo actual (negativos en `/compras-pesos`); `no_facturado_clp` (DB) = pago período anterior histórico
3. **`_san_clp()`**: elimina también comas para manejar formato `$15.001.630,00`
4. **`show_pagos_tdc()` split en dos tablas**:
   - PENDIENTES (Δ CLP > 0 o Δ USD > 0) y AL DÍA (Δ ≤ 0), cada una ordenada por `pagar_hasta` ascendente
   - Columnas idénticas (mismo `COL_SPECS`) → alineación garantizada
   - Color de Δ por celda independiente: rojo si > 0, verde si ≤ 0 (no heredado de la tabla)
   - Lee `no_facturado_clp` de DB; delta usa `pagado_clp` si no es NULL, sino `no_facturado_clp`
5. **Backup**: `backups/saldos_20260429_pagos_tdc_split_tables_ok.py`

---

## STATUS ANTERIOR (25 Apr 2026) ✅ FUNCIONANDO

**Cambios críticos 25 Apr 2026:**
1. **Header CHIRAFIN**: Eliminado "Gestión Patrimonial Personal". Ahora muestra `Código actualizado el DD Mon YYYY, HH:MM` (timestamp de modificación del archivo).
2. **Sin clears entre vistas**: Eliminados todos los `_console.clear()` intermedios. Solo queda el clear inicial al arrancar. El scrollback del terminal funciona sin restricciones.
3. **Reset scroll region al arrancar**: `sys.stdout.write("\033[r")` antes del clear inicial — limpia cualquier scroll region activa de sesiones anteriores.
4. **Separador CHIRAFIN entre vistas**: `_clear_content()` imprime `Rule("CHIRAFIN", style="dim sky_blue3")` antes de cada vista. Llamado desde el main loop (no internamente en cada función) para evitar duplicados.
5. **Eliminados todos los `_print_mini_header()` calls**: El indicador "☁ Supabase" ya no aparece en ninguna vista (fue removido de `show_last_saldos`, `show_summary_by_category`, `show_caja`, `show_comparison`).
6. **Línea separadora bajo banner**: `Rule(style="dim sky_blue3")` entre la fecha y el contenido al iniciar.
7. **Colores cupos TdC corregidos**: Disponible y % Disponible: verde si >0, rojo si <0, blanco si =0. Aplica también al TOTAL.
8. **"Prov. Inversión en Caja" → "Provisión"**: Renombrado en tabla Caja, menú opciones y handler (3 lugares).
9. **Selector de instituciones mejorado**: Ordenado alfabéticamente dentro de PN/PJ. Labels: "Santander MG", "Santander DL", "Global 66". Sin sufijos (PN)/(PJ) en cada ítem.
10. **Tabla completa hide_zeros**: Filas con monto=0 no se muestran. Al final de la tabla aparece `[dim]En cero: Inst / Item · ...[/dim]`.
11. **"Actualizar todos los manuales" eliminado** del submenú de ACTUALIZAR DATOS.
12. **Supabase ↔ SQLite espejados**: Historial Supabase limpiado para coincidir con SQLite (solo 2025-05-08 + desde Mar 11 2026). Edit catálogo ahora también hace PATCH a Supabase en renombres retroactivos.
13. **save_to_db sin zero-protection**: Solo omite valores `None`. Guarda 0 si el scraper lee 0.
14. **Backup**: `backups/saldos_20260425_split_screen_supabase_fixes.py`

---

## STATUS ANTERIOR (28 Mar 2026) ✅ FUNCIONANDO

### ✅ REQUISITO: Tablas Siempre Actualizadas
**CRÍTICO:** Cada vez que se muestra una tabla (en cualquiera de sus versiones), SIEMPRE obtiene los datos más recientes de la base de datos. NO hay cachés de datos históricos. Implementación:
- `show_last_saldos()` → Usa `SELECT MAX(timestamp)` para cada (institucion, item, persona) — garantiza registro más reciente
- `print_table()` → Datos recién scrapeados, sin caché — construido desde `resultados` dinámico
- `_print_table_rows()` → Resetea `_LAST_TABLE_MAPPING = []` cada llamada (línea 636)
- `generate_dashboard()` → Lee datos frescos de DB para HTML interactivo

**Confirmado funcionando:**
- ✅ Itaú PN (CC 8792, RUT 156417076)
- ✅ Itaú PJ (CC 5735, RUT 777884174) — ✅ confirmado 28 Mar 2026
- ✅ AFP Modelo (RUT 156417076)
- ✅ AFC (Fondo de Cesantía) — CAPTCHA manual solo checkbox
- ✅ BTG Pactual PN/PJ (Inversiones Líquidas)
- ✅ Charles Schwabb (BRK/B, QQQ — USD)
- ✅ Wealthfront (ONEQ — USD)
- ✅ Racional (CFIETFCD — CLP)
- ✅ Fraccional PN/PJ (Fondos Inmobiliarios)
- ✅ Harvard FCU (Checking 5440, Savings 5400 — USD) — ✅ confirmado 6 Mar 2026
- ✅ Fintual PN/PJ (Inversiones Líquidas)
- ✅ Global66 PJ (Cash PJ — CLP 5441 + USD 6038) — MFA automático vía Gmail IMAP ✅ confirmado 14 Apr 2026
- ✅ Dorco (Tucson I, Kansas I, Tucson II — USD) — TIR semi-automático
- ✅ WBuild (José Ignacio — USD) — TIR semi-automático
- ✅ Neat (Cash PN — CLP) — suma depósitos "En progreso" de combos válidos ✅ confirmado 25 Mar 2026
- ✅ Santander (CC 2241, TdC 4765/8098, LdC) — ✅ login fix 25 Mar 2026

**Arquitectura de Navegación (4 Pilares):**
1. **1) REGISTRAR DATOS**: Flujos de actualización (Scrapers, Selección específica, Ingreso manual rápido).
2. **2) VISUALIZAR**: Reportes (Tabla Rich en terminal y Dashboard Web interactivo).
3. **3) GESTIONAR CATÁLOGO**: Administración del catálogo manual (Crear nuevos ítems, editar o eliminar existentes).
4. **4) ACTUALIZAR BW**: Sincronización de secretos con Bitwarden.
5. **5) MANTENIMIENTO**: Herramientas integradas para migración de datos históricos y descarga de tipos de cambio.

**Cambios críticos Mar 2026:**
1. **Navegación Estandarizada**: Todos los `questionary.select` y `checkbox` incluyen "Volver atrás" (retorna al paso anterior) y "Salir del programa" (`sys.exit(0)`).
2. **Visual Premium**: Global `QUESTIONARY_STYLE` con colores `fg:#5f87ff` y puntero interactivo `pointer="»"`. Menú principal con `Panel` de Rich.
3. **Gestión de Registros Manuales**: Nueva opción "Editar/Eliminar registros manuales" (tecla `m`) que permite modificar o borrar entradas del `catalog_manual` de la DB.
4. **Normalización Itaú**: Unificación de nombre "Itaú Empresas" → "Itaú". La distinción PN/PJ se maneja por la columna "Persona".
5. **Corrección `moneda_choices`**: Se solucionó bug donde se intentaba añadir a la lista antes de definirla.
6. **Flush de Output**: Agregado `flush=True` en prints de reintento para ver progreso en tiempo real.
7. **PN/PJ Separators**: Inclusión de separadores visuales `—— Persona Natural ——` en todos los menús de selección de instituciones.
8. **DB key collision fix**: Clave compuesta `(institucion, item, persona)` para evitar sobreescritura de montos.
9. **UI Consistency**: Eliminación de sufijos `(PJ)` redundantes en nombres de ítems y listas.
10. **Dashboard Web**: Apertura automática desde el menú principal (Pilar 2).
11. **Comparativa de Deltas Avanzada**: Renombrado "Comparar deltas". Muestra Δ/día (1 decimal), Δ/mes y Δ% Anual. Subtítulo con tiempo transcurrido exacto (días/meses).
12. **Persistencia de Configuración**: Nueva tabla `script_config` en SQLite para guardar `safety_cash` y `tolerance`. Incluye reintentos con timeout y logging de errores para evitar pérdidas de datos.
13. **Formatos UI Unificados**: Fecha en "Situación de Caja" unificada a `DD Mon HH:MM` para consistencia con la tabla principal. Porcentajes en comparativa mostrados sin decimales para mayor limpieza visual.

**Cambios críticos Feb 2026:**
1. **Navegación limpia de cookies** — ambos (PN y PJ): `itau.cl` → "Acceso clientes" (`#dropdown_acceso-clientes`) → "Personas" (`a[href*='newolb']`) → login
2. **Búsqueda Bitwarden PJ** — busca `"itau"` + filtra `"empresa"` en name (encuentra "Itau Empresas" con RUT 777884174)
3. **Contexto aislado PJ** — `new_context()` sin cookies heredadas de PN
4. **Emoji alignment** — `📝` en lugar de `✏️` (consistencia 2-char)

---

## PROYECTOS COMPLETADOS

### ✅ 20260228 - Itaú PN/PJ Login Fix (COMPLETADO)

**Estado:** ✅ FUNCIONANDO 100%
**Fecha:** 28 Feb 2026
**Archivo de confirmación:** `backups/20260228_CONFIRMADO_Funcionando.txt`

**Qué se arregló:**
- Itaú PN (RUT 156417076, CC 8792): ✅ Login funciona
- Itaú Empresas/PJ (RUT 777884174, CC 5735): ✅ Login funciona

**Problemas resueltos:**
1. **Navegación limpia de cookies**
   - Antes: iba directo a URL de login, precargaba RUT anterior
   - Ahora: navega `itau.cl` → "Acceso clientes" → "Personas" → login
   - Selector botón: `#dropdown_acceso-clientes` (evita strict mode)
   - Selector link: `a[href*='newolb'][href*='login']` (portal correcto)

2. **Búsqueda Bitwarden PJ**
   - Antes: buscaba `"banco.itau.cl"` (no encontraba "Itau Empresas")
   - Ahora: busca `"itau"` + filtra `"empresa"` en name

3. **Contexto aislado PJ**
   - `new_context()` sin heredar cookies de PN

**Cambios en código:**
- `scrape_itau()` PN y `scrape_itau_pj()` PJ: ambos usan mismo flujo de navegación
- CLAUDE.md actualizado con detalles completos
- Item #26 agregado a "Problemas conocidos / fixes"

**Verificación:**
- Opción 2 → 4 (Itaú PN): ✅ Funciona
- Opción 2 → 8 (Itaú Empresas): ✅ Funciona

---

---

## 📂 Otros Proyectos
- **GastoSmart**: Consultar [GASTOSMART.md](file:///Users/mgoldsmithd/Scripts Claude AI/GastoSmart/GASTOSMART.md) para detalles de arquitectura, setup y monitorización de gastos.

## Arquitectura de Saldos Bancarios
- **`saldos.py`** — script principal que importa lógica de `core/`.
- **`core/`** — motor del sistema (DB, utilidades, constantes).
- **`web/`** — servidor FastAPI y dashboard web.

**COMANDOS DE EJECUCIÓN:**
- **Terminal UI**: `source venv/bin/activate && python3 saldos.py`
- **Web Dashboard**: `source venv/bin/activate && uvicorn web.server:app --reload`

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

## Items manuales (sin scraper)

Definidos en `MANUAL_ITEMS`. Se muestran al final de "Actualizar todo" (opción 1) con su último valor en DB. Enter vacío = mantener valor anterior.

| Institución | Categoría | Item | Moneda | Persona |
|-------------|-----------|------|--------|---------|
| Itaú | Inversiones Líquidas PJ | CFINASDAQ | CLP | PJ |
| Itaú | Inversiones Líquidas PJ | CFIETFCD | CLP | PJ |
| Itaú | Inversiones Líquidas PJ | Caja | CLP | PJ |

---

## Bancos implementados

### Previsional / AFP / AFC

| Script | Institución | Item | Bitwarden entry | Notas |
|--------|-------------|------|-----------------|-------|
| (en saldos.py) | AFP Modelo | Cuenta Obligatoria | `nueva.afpmodelo.cl` (usuario: `156417076`, clave: 6 dígitos) | RUT debe formatearse: `format_rut_cl("156417076")` → `"15.641.707-6"` |
| (en saldos.py) | AFC | Fondo de Cesantía | `webafiliados.afc.cl` (usuario: `156417076`, clave: password AFC) | CAPTCHA: usuario solo marca checkbox; script hace clic en "Clave AFC" automáticamente. Corre justo después de Líder BCI (ambos tienen CAPTCHA). Pos en INSTITUTION_ITEMS: entre Líder BCI y Santander |

### Fondos Inmobiliarios

| Script | Institución | Categoría | Items | Bitwarden entry | URL login |
|--------|-------------|-----------|-------|-----------------|-----------|
| (en saldos.py) | Fraccional | Fondos Inmobiliarios (PN) | Fraccional | `fraccional.cl - Persona Natural` (user: `matigd@gmail.com`) | `www.fraccional.cl/app/auth` |
| (en saldos.py) | Fraccional | Fondos Inmobiliarios PJ | Fraccional | `www.fraccional.cl - Persona Jurídica` (user: `owa605@gmail.com`) | `www.fraccional.cl/app/auth` |

#### Inversiones TIR Semi-automáticas (Dorco / WBuild) ✅ implementado 8 Mar 2026

**Concepto:** Inversiones inmobiliarias con TIR fija conocida. No requieren scraping web — el valor se calcula automáticamente con la fórmula de interés compuesto diario.

**Fórmula:**
```
daily_rate = (1 + TIR_anual)^(1/365) - 1
valor_bruto = nominal × (1 + daily_rate)^días_transcurridos
valor_neto  = valor_bruto - Σdividendos_pagados
```

**Datos almacenados en DB:**
- `tir_investments`: `(institucion, item, nominal_usd, fecha_inversion, tir_anual)` — parámetros inmutables
- `tir_dividends`: `(institucion, item, fecha, monto_usd)` — historial de dividendos pagados

**Inversiones actuales (seed inicial):**
| Institución | Item | Nominal USD | Fecha inversión | TIR anual |
|-------------|------|-------------|-----------------|-----------|
| Dorco | Tucson I | $30,000 | 2023-04-17 | 12.90% |
| Dorco | Kansas I | $40,000 | 2023-10-27 | 11.93% |
| Dorco | Tucson II | $20,000 | 2025-05-03 | 11.50% |
| WBuild | José Ignacio | $30,000 | 2025-09-10 | 18.00% |

**Dividendo conocido:** Dorco Tucson II — $600 (fecha aproximada: 2025-12-01)

**Flujo de actualización (integrado en menú 2 — Actualización específica):**
1. Usuario selecciona Dorco o WBuild en el menú de selección específica
2. `_is_tir_item()` detecta que es TIR → llama `_update_tir_item()` directamente (sin menú Scraper/Manual)
3. Se muestra panel informativo: nominal, fecha, TIR%, días, dividendos previos, valor calculado
4. Pregunta: "¿Hubo algún dividendo nuevo?" → si Sí: pide monto + fecha → guarda en `tir_dividends`
5. Recalcula valor y guarda en `saldos` con `source='auto'`

**NO hay menú separado "Registrar dividendo"** — el registro de dividendo es un paso dentro del flujo de actualización de cada ítem TIR.

**Funciones clave:**
- `_is_tir_item(inst, item)` — verifica si es TIR consultando DB
- `calc_tir_value(inst, item)` — calcula valor actual, retorna `(net_value, info_dict)`
- `_update_tir_item(...)` — flujo completo de actualización con pregunta de dividendo
- `_scrape_tir_institution(inst)` — itera todos los ítems TIR de una institución
- `scrape_dorco(context, resultados)` y `scrape_wbuild(context, resultados)` — wrappers en INSTITUTION_ITEMS

**Integración en run_scraping:** `TIR_KEYS = {"dorco", "wbuild"}` — estas instituciones se ejecutan ANTES del browser (sin Playwright), luego el browser se abre solo para los demás. Si solo se seleccionan TIR, no se abre ningún browser.

**Fuente en tabla:** `_get_source_emoji()` retorna `"TIR"` para inst in ("dorco", "wbuild").

**Fraccional scraper (PN y PJ, CLP) — mismo flujo para ambos:**
- Logout URL: `https://www.fraccional.cl/app/auth/logout` → pantalla "Antes de irte..." → click `button:has-text("Cerrar sesión")` → home
- Login URL: `https://www.fraccional.cl/app/auth`
- Login dos pasos: `input[type='email']` → `button[type='submit']` ("Continuar") → `input[type='password']` → `button[type='submit']`
- Logout al INICIO (antes de login) y al FINAL (en finally) — siempre
- Post-login: `wait_for_url(lambda url: "/app" in url and "/auth" not in url, timeout=60000)`
- Extracción: `h2` que contiene "Total patrimonio" → `span[class*="mt-0.5"]` → texto `"$5.601.536,88881054"`
- Parse: `lstrip("$")` → `split(",")[0]` → `replace(".", "")` → `int()` → `5601536`
- Fallback selector: `span[class*="text-4xl"]` si `mt-0.5` no encuentra
- PN: `bank_key`: `"fraccional"`; cat: `"Fondos Inmobiliarios"`; Persona en tabla: PN
- PJ: `bank_key`: `"fraccional_pj"`; cat: `"Fondos Inmobiliarios PJ"`; Persona en tabla: PJ; **contexto aislado** (`context.browser.new_context()`); Bitwarden: `bw list items --search fraccional` + filtrar `"owa605"` in username (NO `bw get "...Jurídica"` — falla por acento en CLI)
- `cat_to_short("Fondos Inmobiliarios PJ")` → `"Fondos Inmobiliarios"` (display en tabla = mismo nombre para ambos, Persona columna los distingue)

### Cash / Cuentas en el Exterior

| Script | Institución | Categoría | Items | Bitwarden entry | URL login |
|--------|-------------|-----------|-------|-----------------|-----------|
| (en saldos.py) | Harvard FCU | Cash (PN) | Checking 5440, Savings 5400 | `bw list --search harvardfcu` → entry `my.harvardfcu.org` (user: `275654`) | `my.harvardfcu.org` |
| (en saldos.py) | Global66 | Cash PJ | CLP 5441, USD 6038 | `empresas.global66.com` (user: `owa605.g66@gmail.com`) | `empresas.global66.com/auth/log-in` |

**Harvard FCU scraper (PN, USD) — ✅ confirmado 6 Mar 2026:**
- Bitwarden: `bw list items --search harvardfcu` → toma `items[0]` → entry se llama `my.harvardfcu.org`
- Login URL: `https://my.harvardfcu.org/` → redirige a `/Authentication` si no hay sesión
- Login check: `"authentication" in page.url.lower()`
- Inputs: `#username` (click → `press_sequentially(delay=80)`) + `#password` (click → `press_sequentially`)
  - **CRÍTICO**: NO usar `fill("")` antes de `press_sequentially` — rompe el estado del floating-label input (jQuery + `irisv-textfield__input`)
  - Cloudflare Turnstile auto-pasa en Playwright (no requiere intervención manual)
- Submit: `button[type='submit']` → click → esperar `"dashboard" in page.url.lower()`
- Sesión persistente: `harvard.json` (storage_state); si hay sesión válida, salta el login directo
- Dashboard URL: `https://my.harvardfcu.org/DashboardV2`
- Extracción JS: `#module_accounts li[id^="account_"]` → buscar span con "5440"/"5400" → `.balance-double span` primero
- Parseo USD: formato US (`$5,001.50`) → `replace('$','').replace(',','')` → `float`
- Categoría: `"Cash"`; `bank_key`: `"harvard"`

**Global66 Empresas scraper (PJ, CLP + USD) — ✅ MFA automático vía Gmail IMAP (14 Apr 2026):**
- Bitwarden: `bw_get("username/password", "empresas.global66.com")` — entry se llama `empresas.global66.com` (user: `owa605.g66@gmail.com`)
- Login URL: `https://empresas.global66.com/auth/log-in`
- Login: `input[type="email"]` + `input[type="password"]` → `button:has-text("Iniciar sesión")`
- MFA: **automático** — selecciona canal "correo electrónico" y lee el código desde Gmail via IMAP
  - Función canal: `_g66_select_email_channel(page)` — busca botón con texto "correo"/"Correo"/"email" y lo clickea
  - Gmail: `owa605.g66@gmail.com` — carpeta `[Gmail]/All Mail` (los emails de G66 saltean el inbox)
  - App password Gmail en código (requiere 2FA activado en la cuenta)
  - Función IMAP: `_get_g66_otp_from_gmail(after_dt, timeout_s=90)` — espera 5s, luego polling cada 3s
  - Selector MFA: 6x `input[type="tel"].gui-input` — llenado automático
  - **CRÍTICO strip HTML**: el email tiene colores CSS `#203478` que el regex matchea antes que el OTP → usar `HTMLParser` para strip antes del regex
  - El OTP aparece en el HTML como `&nbsp;160457` → en texto plano queda solo el número
- Post-login: `wait_for_url("**/home**", timeout=30000)`
- Extracción: `p.text-3xl` → detectar moneda por contexto del card (innerText contiene "USD" o "CLP")
  - Card CLP → item `"CLP 5441"` (cuenta No. 10155441)
  - Card USD → item `"USD 6038"` (cuenta No. 8338136038)
- Parseo CLP: `replace('.', '')` → int (puntos = miles en formato chileno)
- Parseo USD: `replace(',', '')` → float (comas = miles en formato US)
- Contexto aislado (`context.browser.new_context()`); `bank_key`: `"global66_pj"`
- Categoría: `"Cash PJ"` → `cat_to_short()` mapea a `"Cash"` para tabla

### Inversiones Líquidas

| Script | Institución | Categoría | Items | Bitwarden entry | URL login |
|--------|-------------|-----------|-------|-----------------|-----------|
| (en saldos.py) | BTG Pactual | Inversiones Líquidas (PN) | CFISP500, CFINASDAQ, CFIETFGE | `app.btgpactual.cl - Persona Natural` | `app.btgpactual.cl/login` |
| (en saldos.py) | BTG Pactual | Inversiones Líquidas PJ | CFISP500, CFINASDAQ, CFIETFGE | `bw list items --search btgpactual` → filtrar `"777884174"` en username | `app.btgpactual.cl/login` |
| (en saldos.py) | Charles Schwabb | Inversiones Líquidas (PN) | BRK/B, QQQ | `client.schwab.com` (username: `matiasgoldsmithd`) | `client.schwab.com/Areas/Access/Login` |
| (en saldos.py) | Racional | Inversiones Líquidas (PN) | CFIETFCD | `racional-prod.firebaseapp.com` (username: `matigd@gmail.com`) | `app.racional.cl` |
| (en saldos.py) | Wealthfront | Inversiones Líquidas (PN) | ONEQ | `wealthfront.com` (username: `matigd@gmail.com`) | `wealthfront.com/login` |
| (en saldos.py) | Fintual | Inversiones Líquidas (PN) | Risky Norris (CLP), VOO, IVV, BRK.B (USD) | `fintual.cl - Persona Natural` (username: `matigd@gmail.com`) | `fintual.cl/app/goals` |
| (en saldos.py) | Fintual | Previsional (PN) | Risky Norris APV (CLP) | `fintual.cl - Persona Natural` (username: `matigd@gmail.com`) | `fintual.cl/app/goals` |
| (en saldos.py) | Fintual | Inversiones Líquidas PJ | Risky Norris (CLP) — fondo "Patrimonial" en sitio | `bw list --search fintual` → filtrar `"owa605"` en username | `fintual.cl/app/goals` |

**Racional scraper (PN, CLP):**
- Login URL: `https://app.racional.cl` — form estándar email + password
- Login: `input[type='email']` → `fill(username)` → `input[type='password']` → `fill(password)` → checkbox `input[type='checkbox']`.first con `check(force=True)` → `get_by_role("button", name="Iniciar sesión")`
- Checkbox "Mantener sesión": usar `check(force=True)` (checkbox custom, visualmente oculto); fallback click en `text=Mantener sesi`
- Post-login: `wait_for_url(lambda url: "login" not in url and "racional" in url, timeout=120000)`
- Post-login: navega a `/tabs/home` si no está ya en esa URL
- Extracción: `.investment-amount.smaller-total` (selector único en home = "Total Inversiones") → `text_content().strip()` → `lstrip("$")`
- Antes (deprecado): `span.portfolio-name` con texto `"DtdC"` → traversal DOM 8 niveles → regex `/$[\d.]+/g` — reemplazado Mar 2026
- Item: `CFIETFCD` (= "Total Inversiones" en Racional home); `bank_key`: `"racional"`
- Bitwarden: `racional-prod.firebaseapp.com`

**Wealthfront scraper (PN, USD):**
- Login URL: `https://www.wealthfront.com/login` — form estándar email + password
- Login: `input[type='email']` → `fill(username)` → `input[type='password']` → `fill(password)` → `button[type='submit']`; si falla → fallback manual
- Post-login: `wait_for_url("**/dashboard**", timeout=120000)`
- Account URL: `https://www.wealthfront.com/accounts/289918` (Cuenta Base)
- Extracción: `page.evaluate()` JS — TreeWalker busca nodo de texto `"US stocks"` → sube árbol hasta 6 niveles → extrae primer `$valor` válido (≠ $0.00)
- Item: `ONEQ` (= sección "US stocks" en Cuenta Base); `bank_key`: `"wealthfront"`
- Bitwarden: `wealthfront.com`

**Fintual scraper (PN, CLP + USD):**
- **Sesión persistente**: usa contexto aislado + `fintual_session.json` (storage state). MFA solo la primera vez o al expirar la sesión. Después del éxito: `iso_context.storage_state(path=_FINTUAL_STATE)`. Al iniciar: `new_context(storage_state=path)` si existe el archivo.
- Login URL: `https://fintual.cl/f/sign-in/` (nueva URL desde Mar 2026). Navega directo a `/app/goals`; si redirige a `/f/sign-in` → hace login.
- Login 1 solo paso: `input[name="email"]` (type=`text`, NO type=`email`) + `input[name="password"]` + `button[type="submit"]` primero ("Entrar")
- Post-login: `wait_for_url(lambda url: "/app/" in url, timeout=120000)` → si no en `/app/goals` → `goto` explícito → `wait_for_selector(".goal-item--no-shadow")`
- Extracción CLP: `page.evaluate()` → `document.querySelectorAll('.goal-item.goal-item--no-shadow')` → `{name, detail, balance}` por cada card
  - `Risky Norris` = `sum(balance)` donde `detail == "Largo plazo"` (Depositado + Ganancias)
  - `Risky Norris APV` = `sum(balance)` donde `"APV" in name` (APV + APV Regimen A)
  - Selectores: `.goal-item__info-name`, `.goal-item__info-detail`, `.goal-item__balance`
- Extracción USD: `page.evaluate()` → `document.querySelectorAll('.asset-row')` → `{symbol, balance}` por fila
  - Selectores: `.asset-row__info-symbol`, `.asset-row__balance`
  - Items: VOO, IVV, BRK.B
- Parse CLP: `"$ 295.001.270"` → strip `"$"` y espacios → remove `"."` → `int` (puntos = miles)
- Parse USD: `"US $13.493,36"` → strip `"US"`, `"$"` → remove `"."` → replace `","` con `"."` → `float` (puntos = miles, coma = decimal)
- Items y categorías: `Risky Norris` (Inversiones Líquidas, CLP), `Risky Norris APV` (Previsional, CLP), `VOO/IVV/BRK.B` (Inversiones Líquidas, USD)
- Bitwarden: `fintual.cl - Persona Natural` (matigd@gmail.com); `bank_key`: `"fintual"`

**Fintual PJ scraper (One Western, CLP):**
- Mismo login flow que PN (mismos selectores, misma URL)
- Fondo en sitio: `"Patrimonial"` (detail `"Largo plazo"`) → registrado como item `"Risky Norris"`, cat `"Inversiones Líquidas PJ"`
- Extracción: `.goal-item.goal-item--no-shadow` → filtrar `"Patrimonial" in name` → `sum(balance)`
- Sesión persistente: `fintual_pj_session.json`; contexto aislado
- Bitwarden: `bw list items --search fintual` + filtrar `"owa605"` in username (NO `bw get "...Jurídica"` — falla por acento)
- `bank_key`: `"fintual_pj"`

**Charles Schwabb scraper (PN, USD):**
- Login URL: `https://client.schwab.com/Areas/Access/Login` — form dentro de iframe `#lmsIframe` (cross-origin, accesible desde Playwright con `frame_locator`)
- Login: intenta auto-login `frame_locator("#lmsIframe")` → `#loginIdInput` (username) → `#passwordInput` (password) → `#btnLogin`; si falla → imprime aviso y espera login manual del usuario
- MFA/login manual: si auto-login falla o hay MFA, el usuario lo completa en la ventana (headless=False); script espera `wait_for_url("**/app/accounts/**", timeout=120000)`
- Post-login: `https://client.schwab.com/app/accounts/summary/`
- Selector posiciones: `wait_for_selector("tr.positions-parent-row", timeout=30000)`
- Extracción Market Value: `page.evaluate()` JS → busca span con texto exacto = símbolo en `tr.positions-parent-row` → sube al `<tr>` → `<td>` que empieza con "Market Value" → span sin clase `sr-only` → valor limpio (`"$59.18"`, sin `‡`)
- Parsing USD: `","` = miles, `"."` = decimal (convención Schwab/US) → `"$3,039.79"` → remover `$` y `,` → `float("3039.79")`
- Display: redondeado al entero más cercano, `"."` como separador de miles → `"3.040"`
- Moneda: `"USD"` — función `add_result_usd`; `print_preliminary(..., moneda="USD")`
- Items: `BRK/B`, `QQQ`; `bank_key`: `"schwab"`

**BTG Pactual scraper (PN y PJ):**
- Login: `#rut`.first + `press_sequentially(rut_formateado)` → `#password`.first + `press_sequentially(pwd)` → click `button:has-text("Iniciar sesión")`
- RUT formateado: `format_rut_cl()` — e.g. `15.641.707-6` o `77.788.417-4`
- Post-login: `wait_for_url("**/portfolio**", timeout=30000)` + `wait_for_timeout(4000)`
- Expandir fondos: `wait_for_selector("div.dropdown__header", state="visible", timeout=15000)` → si `"--open"` NOT in class → click
- Extraer valor: `page.evaluate()` JS — busca `div.table__row` donde un span contiene el nombre del fondo → toma last `div.table__row-cell--has-details` → texto → parsear `$` y `.`
- Fondos: `"S&P 500"→CFISP500`, `"Nasdaq"→CFINASDAQ`, `"Global Equities"→CFIETFGE`
- PJ Bitwarden: usar `bw list items --search btgpactual` + filtrar por `"777884174"` in username (NO `bw get "...Jurídica"` — falla por acento en CLI)
- PJ contexto: `iso_context = context.browser.new_context()` + `add_init_script(webdriver spoof)` → `page = iso_context.new_page()` → cleanup en `finally` con `page.close()` + `iso_context.close()`
- `bank_key`: PN = `"btg"`, PJ = `"btg_pj"`

### CC Persona Natural

| Script | Banco | Cuenta | Bitwarden entry | URL login |
|--------|-------|--------|-----------------|-----------|
| `banco_chile.py` | Banco de Chile | CC PN 5809 | `login.portales.bancochile.cl` | `sitiospublicos.bancochile.cl/personas` → "Banco en Línea" |
| `scotiabank.py` | Scotiabank | CC PN 7002 | `Scotiabank PN` | `www.scotiabank.cl/login/personas/?nocache=true` |
| `ripley.py` | Banco Ripley | CC PN 2239 | `web.bancoripley.cl` | `web.bancoripley.cl/login` |
| `santander.py` | Santander | CC PN 2241 | `banco.santander.cl` | `mibanco.santander.cl` |
| `itau.py` | Itaú | CC PN 8792 | `banco.itau.cl` | `banco.itau.cl/wps/portal/newolb/web/login/` |
| `consorcio.py` | Consorcio | CC PN 6758 | `login.consorcio.cl` | `login.consorcio.cl/onboarding-consorcio/admin` |

### CC Persona Jurídica (One Western Spa 77.788.417-4)

| Script | Banco | Cuenta | Bitwarden entry | URL login |
|--------|-------|--------|-----------------|-----------|
| `scotiabank_empresas.py` | Scotiabank | CC PJ 7381 | `Scotiabank Empresas` | `appservtrx.scotiabank.cl/portalempresas/login` |
| (en saldos.py) | Itaú Empresas | CC PJ 5735 | `Itau Empresas` | `banco.itau.cl/wps/portal/newiol/web/login/` |

### Tarjetas de Crédito

| Script | Banco | TdC | Selector clave |
|--------|-------|-----|----------------|
| `banco_chile.py` | Banco de Chile | TdC 7164 | `p.lead-title:has-text("Utilizado") → span.number` |
| `scotiabank.py` | Scotiabank | TdC 3134, 2730 | iframe `#iframe-stage` → `div.saldo:has-text("Cupo utilizado") → h1.saldo__text` |
| `ripley.py` | Banco Ripley | TdC 9647 | `div:has-text("Titular ****9647") → div.min-w-[76px]:has-text("Utilizado") → span.label-md` |
| `santander.py` | Santander | TdC 4765, 8098 | `div.used-amount` (8098 = "Worldmember Amex" en mat-select) |
| `itau.py` | Itaú | TdC 6132 | `p.monto-saldo.nth(1)` (nth(0) = disponible, nth(1) = utilizado) |
| `lider_bci.py` | Líder BCI | TdC 5037 | `table.balance.first → td.first` |

### Líneas de Crédito (LdC PN)

| Banco | URL (post-login) | Selector valor |
|-------|-----------------|----------------|
| Banco de Chile | `#/movimientos/linea/saldos-movimientos/` | `fenix-movimientos-cuenta p.list-item:has-text("Monto utilizado") → xpath=following-sibling::span[1]` |
| Banco Ripley | ❌ No tiene LdC | — |
| Consorcio | `/spi/hall-banco/ultimos-movimientos#/?acc=4320116774` | `span.cns-body-sm:has-text("Cupo Utilizado") → xpath=following-sibling::span[1]` |
| Itaú | `/wps/myportal/newolb/web/cuentas/linea-credito/saldos/` | `span[name="LCMontoUtilizado"]` |
| Santander | `#/private/saldos/main/mi-cuenta` | `div.container-amounts p.monto-contable.monto-font` (`.first`) |
| Scotiabank PN | `?tab=saldos&type=LICRED` (mismo iframe que CC) | `iframe#iframe-stage` → `p.TextCaption__text:has-text("Saldo utilizado")` → `xpath=ancestor::div[contains(@class,'Column__container')]/following-sibling::div[1]/p` |

### Créditos Hipotecarios (CH PN) — moneda UF ✅ confirmado Feb 2026

| Banco | Navegación | Selector valor |
|-------|-----------|----------------|
| Consorcio | `personas.consorcio.cl/spi` → hover `#itemHeader1` → click `div.card-header-spi-text-header:has-text("Mis Créditos Hipotecarios")` → navega a `servicios.bancoconsorcio.cl` | `td.tac.ng-binding` first → "UF 9.590,97" |
| Banco de Chile | `goto #/credito-hipotecario/main/consulta/informe` | `page.evaluate()` — busca `p` con texto exacto "Costo Total del Prepago (UF)", sube al ancestor `div.col-4`, toma `nextElementSibling.textContent` → "3.027,582" |
| Itaú | `goto /creditos/credito-hipotecario/consultar-creditos` | `td.bold:has-text("Saldo actual")` → `xpath=following-sibling::td[1]` → "UF 2.706,70" |

**CRÍTICO Consorcio CH**: `servicios.bancoconsorcio.cl` es dominio distinto a `consorcio.cl` — la sesión NO se transfiere con goto directo. Hay que navegar via menú hover desde `personas.consorcio.cl/spi`. Luego `wait_for_timeout(8000)` sin `wait_for_url` (el redirect intermedio puede no coincidir con el patrón).

**CRÍTICO Banco de Chile CH**: Página SPA — `ancestor::` xpath no funciona en Playwright encadenado desde locator (busca dentro del elemento, no hacia arriba). Usar `page.evaluate()` con JS puro. Además esperar con `locator("p", has_text="Costo Total...").wait_for(state="attached")` antes del evaluate, porque el render SPA puede tardar más que `wait_for_load_state("load")`.

---

## Selectores clave por banco

### Scotiabank PN — NUEVO PORTAL (`banco.scotiabank.cl`, migrado ~Feb 2026) ✅
- Login URL directa: `https://www.scotiabank.cl/login/personas/?nocache=true`
- RUT: `data-testid="inputDni"` — tipear **sin puntos ni guión** (`156417076`), el campo auto-formatea
- Password: `data-testid="inputPassword"` — `press_sequentially(pwd, delay=80)`
- Flujo: click RUT → `press_sequentially(rut_clean)` → Tab → `press_sequentially(pwd)` → Tab → poll `btn.is_enabled()` → click
- Post-login: `wait_for_load_state("load")` + `wait_for_timeout(4000)` — **NO usar networkidle** (portal hace polling constante)
- Post-login popup: cerrar con `button[aria-label='Close']` etc. (try/except)
- CC saldo URL: `https://www.scotiabank.cl/mfe/sweb/mfe-shell-web-cl/mfe/mfe/ltmnsw/mfe-accounts-balancesmovements-web/?tab=saldos&type=CTACTE`
- CC saldo selector: `iframe#iframe-stage` → `p.TextCaption__text--bold:has-text("Saldo disponible")` → `xpath=ancestor::div[contains(@class,'Column__container')]/following-sibling::div[1]/p`
- TdC URL: `https://www.scotiabank.cl/mfe/sweb/mfe-shell-web-cl/mfe/mfe-simple-account-statement-web-cl/?tab=saldo&card={card_number}`
- TdC selector: `iframe#iframe-stage` → `div.saldo:has-text("Cupo utilizado") → h1.saldo__text` ✅ confirmado
- **IMPORTANTE**: `add_init_script` webdriver spoofing NO es necesario

- **Santander (`mibanco.santander.cl`)** ✅ revisado Mar 2026
- **CRÍTICO**: `page.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")` — sin esto falla detección
- Login: `page.goto("https://banco.santander.cl/personas")` → click `a#btnIngresar` → esperar panel `#login-santander-evg` → `frame_locator("#login-frame")`
- RUT: `input#rut` dentro del iframe — tipear sin puntos ni guión (`156417076`)
- Password: `input#pass` dentro del iframe
- Botón: `get_by_role("button", name="Ingresar")` o `button:has-text("Ingresar")` dentro del iframe
- Post-login: `wait_for_url("**/private/**", timeout=45000)` + `wait_for_load_state("networkidle")`
- **CC saldo**: navegar a `#/private/main` → `.box-product` con "Cuenta Corriente" (filter `has_not_text="Dólar"`) → p.amount-pipe-4 o `.amount` ✅
- **TdC**: navegar a `#/private/Saldos_TC/main/detail` → `.used-amount p.amount-pipe-3` o `.used` ✅
  - TdC 4765: primera tarjeta activa (`swiper-slide-active`)
  - TdC 8098: click `.swiper-button-next` → misma selector

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

### AFC (`webafiliados.afc.cl`) ✅ implementado Mar 2026
- **CAPTCHA reCAPTCHA** — usuario marca checkbox "No soy un robot", script hace el resto automáticamente
- URL: `https://webafiliados.afc.cl/WUI.AAP.OVIRTUAL/Default.aspx`
- Flujo: página inicial → usuario resuelve CAPTCHA → script hace clic en "Clave AFC" (loop retry 2s, máx 90s) → form login → RUT + clave → dashboard
- Bitwarden: `webafiliados.afc.cl` — username: `156417076` (RUT sin formato), password: clave AFC
- Botón "Clave AFC": `page.locator("a, button, input[type='button'], input[type='submit']").filter(has_text=re.compile(r"Clave AFC", re.IGNORECASE)).first`
- Error CAPTCHA no resuelto: `page.locator("text=Verificar casilla de seguridad").is_visible()` → si True, reintentar
- Form login: `#txtRutTrabajador` (RUT), `#txtPwdTrabajador` (clave), `#btnIngresar` (submit) — los labels NO tienen `for`, usar IDs directamente
- Dashboard URL: `**/Portada.aspx**`
- Modal post-login "seguridad AFC": cerrar con `button.modal-close` (clase única del ×; NO usar `button.close` ni `button[aria-label="Close"]` — esos matchean otra alerta diferente)
- Saldo: `h1.saldo_total` con texto "Saldo total: $12.692.099" → `page.get_by_role("heading", name=re.compile(r"Saldo total"))` → parsear `\$([\d\.]+)` → reemplazar `.` → int
- Categoría: Previsional / Fondo de Cesantía / PN / CLP
- `CAPTCHA_KEYS = {"lider_bci", "afc"}` — ambos requieren intervención manual (solo checkbox para AFC)

### Itaú PN (`banco.itau.cl/wps/portal/newolb`) ✅ revisado Feb 2026
- **Navegación CRÍTICA** (limpia cookies viejas): `www.itau.cl` → click `button:has-text("Acceso clientes")` → click `text=Personas` → llega a `newolb/web/login/`
  - **IMPORTANTE**: NO ir directo a `newolb/web/login/` porque precarga RUT anterior por cookies. Debe pasar por `itau.cl` → "Acceso clientes" → "Personas"
- Bitwarden entry: buscar `banco.itau.cl`, filtrar por `"empresa" NOT in name` (es PN, no PJ)
- RUT: `#loginNameID`; Password: `#pswdId`; Submit: `#btnLoginPortal` (es `input[type=submit]`)
- CC saldo: `small.itau-card-text:has-text("Saldo disponible para uso") → h6.itau-card-title`
- TdC utilizado: `p.monto-saldo.nth(1)` (nth(0) es disponible)

### Itaú Empresas (`banco.itau.cl/wps/portal/newiol`) ✅ confirmado Mar 28 2026
- **Portal correcto**: `newiol` (Empresas), NO `newolb` (Personas)
- **Navegación**: `www.itau.cl` → click `#dropdown_acceso-clientes` → click `a[href*='newiol/web/login']` (opción "Empresas")
- **Toggle CRÍTICO**: `page.evaluate("document.getElementById('new-switch-login').click()")` — NO usar `locator().click()`: `<span id="sliderEmpresa">` intercepta pointer events y causa timeout en Playwright
- **3 campos de login** (visibles tras activar toggle):
  1. `#rut_empresaID` = `777884174` (RUT empresa — campo auto-formatea a `77.788.417-4`)
  2. `#rut_usuarioID` = `156417076` hardcoded (RUT personal Matias — NO auto-formatea)
  3. `#claveId` = password desde Bitwarden `Itau Empresas`
- **Bitwarden**: `bw_unlock()` + `bw list items --search itau` + filtrar `"empresa" in name` → get by ID
- **Submit**: `page.get_by_role("button", name="Ingresar").first`
- **Post-login**: `wait_for_url(lambda url: "newiol/web/h" in url, timeout=45000)`
- **Espera tabla**: `wait_for_load_state("networkidle")` + `wait_for_selector("a:has-text('0230845735')")` — NO usar `a:has-text('Cuenta Corriente')` (6 matches en menú nav)
- **Extracción**: JS `querySelectorAll('a').find(a => a.textContent.includes('0230845735'))` → `closest('tr')` → último `<td>`
- Cuenta confirmada: CC 0230845735 (= CC 5735)
- Contexto aislado (`context.browser.new_context(...)`) para no heredar cookies de Itaú PN

---

## Formato de output (tabla)

Columnas: `Categoría | Institución | Persona | Item | Moneda | Monto | Última act.`
- Moneda `CLP` o `UF` según categoría (CH siempre UF)
- Monto **alineado a la derecha**, sin signo `$`
- UF: mostrar redondeado al entero más cercano en tabla; DB guarda float exacto (negativo para CH)
- Negativo para deudas (TdC, LdC, CH en descubierto/saldo)
- **CH siempre negativo** — es deuda hipotecaria; `add_result_uf` niega automáticamente si positivo
- Subtotales por categoría; CH subtotal en UF, gran total solo CLP
- `0` se muestra vacío en tabla (más limpio)
- Sin semáforos de frescura — solo timestamp con emoji de fuente: `🤖` (auto) o `✏️` (manual)
- Tabla NO se muestra automáticamente — solo con opción 4 del menú o explícitamente
- Datos guardados en `saldos.db` (SQLite) — tabla `saldos` con columnas: id, timestamp, institucion, categoria, persona, item, moneda, monto (REAL), ok, source (TEXT: 'auto'|'manual')

---

## Convenciones de nombres

- Categorías: `CC PN`, `CC PJ`, `TdC`
- Items: `CC 7002`, `TdC 3134`, etc.
- Instituciones: `Banco de Chile`, `Scotiabank`, `Banco Ripley`, `Santander`, `Itaú`, `Consorcio`, `Líder BCI`

---

## Problemas conocidos / fixes

1. **`bw sync` falla TLS** → usar `NODE_TLS_REJECT_UNAUTHORIZED=0 bw sync`
2. **Santander falla al correr solo (independiente)** → se agregó `context.add_init_script` global en `run_scraping` y `wait_for_selector("#btnIngresar")` antes del click; el anti-detection aplica a todas las páginas del contexto
3. **Itaú TdC `.first` da disponible** → usar `.nth(1)` para utilizado
4. **Scotiabank Empresas password placeholder ambiguo** → usar `#INP_COMMON_PASSWORD_PASS`
5. **Líder BCI `table.balance` strict mode** → usar `.first` en el locator
6. **Scotiabank PN nombre Bitwarden** → debe ser `Scotiabank PN` (no `Scotiabank`, que hace match ambiguo con `Scotiabank Empresas`)
7. **Scotiabank PN `bw get` devuelve vacío** → el vault local del CLI queda desactualizado tras renombrar entries en la app; solución: `bw sync` dentro de `bw_unlock()` tras setear BW_SESSION
8. **Scotiabank PN botón Ingresar disabled con JS setNativeValue** → usar `click()` + `press_sequentially(rut_clean)` con RUT sin formatear; el campo auto-formatea y actualiza React correctamente
9. **Scotiabank PN `wait_for_load_state("networkidle")` timeout** → el nuevo portal hace polling constante; usar `wait_for_load_state("load")` + `wait_for_timeout(4000)`
10. **Banco de Chile LdC selector falla** → se aumentó wait a 5s, se agregó `wait_for_load_state("load")`, `.first` en locator, y estrategia alternativa de selector (span dentro del padre)
11. **Menú ahora usa números** → `select_institutions()` y `manual_entry()` usan 1,2,3... (antes letras). Formato manual: "3" para toda la institución, "3.2" para item específico
12. **Tabla NO se muestra automáticamente** → ni al arrancar ni después de scraping. Solo con opción 4 del menú
13. **LdC no aparecía en orden correcto** → agregado a `CAT_ORDER` y a `cat_priority` en `show_last_saldos`
14. **LdC, TdC, CH entrada manual negada** → `manual_entry` y fallback manual de `run_scraping` tratan LdC, TdC, CH como deuda → negativo
15. **Banco de Chile login cambió** (Feb 2026) → selectores nuevos: `input[name="userRut"]`, `input[name="userPassword"]`, `button#ppriv_per-login-click-ingresar-login`
16. **Itaú Empresas login cambió** (Feb 2026) → toggle: `#sliderEmpresa` → `#new-switch-login` (con opacity:0, usar `click(force=True)`); botón: `get_by_role("Ingresar")` → `button.wpfBlueButton` (ID ofuscado; `#btnLoginPortalEmpresas` ya no existe)
17. **CH UF storage** → DB columna `monto REAL` (no INTEGER); guardar float exacto (9590.97), no centésimas. Display en tabla redondea al entero más cercano. Totales CH en UF, gran total CLP excluye CH.
18. **Consorcio CH dominio distinto** → `servicios.bancoconsorcio.cl` ≠ `consorcio.cl`; goto directo da "Cierre de Sesión". Fix: hover `#itemHeader1` + click `div.card-header-spi-text-header` + `wait_for_timeout(8000)` (sin `wait_for_url`).
19. **Banco de Chile CH ancestor xpath falla en Playwright** → `locator.locator("xpath=ancestor::...")` no puede subir el árbol desde un locator. Fix: `page.evaluate()` con JS puro para traversar el DOM. Además usar `locator.wait_for(state="attached")` antes del evaluate (SPA renderiza después del load).
20. **CH guardado como negativo** (Feb 2026) → `add_result_uf` niega automáticamente si cat=="CH" y valor > 0. DB guarda -9590.97. Tabla muestra -9.591.
21. **DB columna `source`** (Feb 2026) → nueva columna `source TEXT DEFAULT 'auto'` en tabla `saldos`. Valores: 'auto' (scraping) | 'manual' (entrada a mano). Migración automática en `init_db()`.
22. **Semáforos de frescura eliminados** (Feb 2026) → columna "Act." quitada de la tabla. Reemplazado por emoji en "Última act.": 🤖 = auto, ✏️ = manual.
23. **Auto-reintento en `run_scraping`** (Feb 2026) → si hay bancos fallidos, se reintenta 1 vez automáticamente sin preguntar al usuario. Si sigue fallando, se ofrece entrada manual.
24. **Itaú Empresas saldo retry interno** (Feb 2026) → si el saldo no se lee con ningún selector, recarga la página y reintenta 1 vez más (total 2 intentos).
25. **INSTITUTION_ITEMS reordenado** (Feb 2026) → PN primero (alfabético): Banco de Chile, Banco Ripley, Consorcio, Itaú, Líder BCI, Santander, Scotiabank PN. PJ después: Itaú Empresas, Scotiabank Empresas.
26. **Itaú navegación limpia de cookies** (Feb 28 2026) ✅ RESUELTO → ambos (PN y PJ):
    - Navegar: `itau.cl` → click `#dropdown_acceso-clientes` → click `a[href*='newolb'][href*='login']`
    - Evita RUT precargado por cookies (strict mode: múltiples botones encontrados)
    - Bitwarden PJ: buscar `"itau"` + filtrar `"empresa"` in name (no buscar "banco.itau.cl" que no contiene "Itau Empresas")
    - Contexto aislado PJ: `new_context()` sin heredar cookies PN
27. **AFP Modelo Bitwarden entry name incorrecto** (Mar 1 2026) ✅ RESUELTO → entry en Bitwarden NO se llama "AFP Modelo" sino `"nueva.afpmodelo.cl"`
    - Usar `bw_get("username", "nueva.afpmodelo.cl")` y `bw_get("password", "nueva.afpmodelo.cl")`
    - Usuario en Bitwarden: `156417076` (RUT sin puntos ni guión)
28. **AFP Modelo RUT requiere formato con puntos** (Mar 1 2026) ✅ RESUELTO → el portal Vue valida RUT con formato `"15.641.707-6"`
    - Bitwarden devuelve `"156417076"` pero el campo Vue necesita `"15.641.707-6"` (con puntos y guión)
    - Solución: función `format_rut_cl()` convierte automáticamente antes de tipear
    - Campos confirmados: `#rut` (texto formateado), `#password` (6 dígitos), `button[type="submit"]` (disabled hasta que Vue valide)
    - Monto: `h2.card-balance` (único elemento en página, contiene `"$65.630.736"`)
29. **AFC script pedía clic manual en "Clave AFC"** (Mar 1 2026) ✅ RESUELTO → script hace clic automáticamente
    - Antes: script pedía al usuario hacer clic en "Clave AFC" después del CAPTCHA
    - Ahora: usuario solo resuelve el checkbox reCAPTCHA; el script hace el resto
    - Implementación: loop retry hasta 90s, intenta clic en "Clave AFC", verifica que no aparezca "Verificar casilla de seguridad"
    - Si CAPTCHA aún no resuelto → espera 2s y reintenta; cuando CAPTCHA ok → clic funciona y continúa login
30. **BTG Pactual PJ Bitwarden encoding** (Mar 1 2026) ✅ RESUELTO → `bw get "app.btgpactual.cl - Persona Jurídica"` falla por la "í" (acento) en el CLI
    - Fix: `bw list items --search btgpactual` en Python + filtrar por `"777884174"` in username (RUT PJ)
    - Parsear JSON con `json.loads()` y tomar `item["login"]["username"]` + `item["login"]["password"]`
31. **BTG Pactual PJ cookie interference** (Mar 1 2026) ✅ RESUELTO → primer intento falla por cookies de sesión PN heredadas
    - Fix: contexto aislado `context.browser.new_context()` + webdriver spoof init script para PJ
    - Patron: `iso_context = None; page = None` antes del try → cleanup en finally
32. **BTG PN/PJ DB key collision** (Mar 1 2026) ✅ RESUELTO → ambos usan `(inst="BTG Pactual", item="CFISP500")` → PJ sobreescribía PN en `db_data_ok`
    - Fix: cambiar clave de `(institucion, item)` a `(institucion, item, persona)` en `show_last_saldos`
    - SQL `GROUP BY` incluye `persona`; INNER JOIN también matchea por `persona`; dict Python usa tupla de 3
    - `db_key` en el loop del catálogo: `(db_inst, item_code, cat_to_persona(cat))`
33. **`triple_click()` no existe en Playwright Python** (advertencia permanente) → NUNCA usar. Alternativa para limpiar campo y reescribir: `click()` + `keyboard.press("Control+a")` + `press_sequentially()`. Para campos Vue/React siempre usar `press_sequentially()` (no `fill()` que no dispara onChange).
34. **BTG `goto` timeout 30s en primer intento** (Mar 1 2026) ✅ RESUELTO → BTG carga lento sin caché previa. Fix: `timeout=60000` en `page.goto()`
35. **Santander login falla silenciosamente** (Mar 25 2026) ✅ RESUELTO → `perform_fill()` usaba `.fill()` para RUT y clave; campos Angular/React ignoran `.fill()` y no disparan `onChange`. Fix: reemplazado por `.click()` + pausa + `.press_sequentially()` para ambos campos (mismo patrón que Harvard FCU y Scotiabank PN).
36. **Neat login — formulario cambió de Angular a HTML plano** (Mar 25 2026) ✅ RESUELTO → El formulario de `/inicia-sesion` dejó de usar Angular `formControlName` y pasó a HTML nativo (`name`/`id`). Selectores actualizados:
    - Email: `input[formcontrolname="email"]` → `input[name="email"]`
    - Password: `input[formcontrolname="password"]` → `input[name="password"]`
    - Botón: `button:has-text("INICIAR SESIÓN")` → `button[type="submit"]`
    - Confirmado en vivo con Claude in Chrome (25 Mar 2026)
37. **Racional — extracción cambiada a Total Inversiones** (Mar 25 2026) ✅ RESUELTO → Antes traversaba DOM desde `span.portfolio-name` con texto "DtdC" (frágil, dependía de card visible). Ahora usa selector directo `.investment-amount.smaller-total` en `/tabs/home` (= campo "Total Inversiones", único en la página). Más robusto y simple.
38. **Itaú PJ — login y extracción completos** (Mar 25-28 2026)
40. **BTG workaround — yfinance no instalado** (May 6 2026) ✅ RESUELTO
    - El workaround corría pero `import yfinance` lanzaba ModuleNotFoundError → silenciosamente retornaba None → no corregía nada
    - Fix: `pip install yfinance --break-system-packages` en el venv

41. **BTG workaround — tickers USD incorrectos** (May 6 2026) ✅ RESUELTO
    - Usaba SPY/QQQ/ACWI (USD) → error ~1.9% por variación FX no capturada
    - Fix: usar `CFISP500.SN`, `CFINASDAQ.SN`, `CFIETFGE.SN` (Bolsa de Comercio, CLP) → error ~0.3%

42. **BTG `#rut` strict mode violation** (May 6 2026) ✅ RESUELTO
    - `page.locator("#rut").is_visible()` lanzaba error en `scrape_btg()` porque matcheaba 2 elementos
    - Fix: `.first.is_visible()` en 2 lugares dentro de `perform_login_btg()` y el loop de retry

43. **Supabase `monto` columna `real` → precisión 6 dígitos** (May 6 2026) ✅ RESUELTO
    - `real` (float32) serializa JSON con 6 dígitos → valores terminaban en "00" en display
    - Fix: `ALTER TABLE saldos ALTER COLUMN monto TYPE double precision` (con drop/recreate de 3 vistas)
    - Datos históricos no corregidos (solo aplica a valores nuevos)

39. **Global66 PJ — MFA automatizado vía Gmail IMAP** (Apr 14 2026) ✅ RESUELTO
    - Antes: MFA manual (usuario ingresaba código en terminal)
    - Ahora: selecciona canal "correo", lee OTP desde `owa605.g66@gmail.com` vía IMAP automáticamente
    - Bug crítico: regex `\b\d{6}\b` en HTML raw matcheaba colores CSS `#203478` antes que el OTP → fix: HTMLParser strip antes del regex
    - Bug scraper lookup: catalog_manual tenía entrada `('Global 66', 'CLP 5441', bank_key='global_66')` con espacio y key distinta a INSTITUTION_ITEMS → agregada pasada 3 con normalización de espacios/case
    - Gmail: carpeta `[Gmail]/All Mail` (no INBOX — los emails de G66 saltean inbox)
    - Backup pre-fix: `backups/saldos_20260413_pre_g66_imap.py` ✅ RESUELTO → Múltiples fixes acumulados:
    - **Portal**: `newiol` (Empresas), NO `newolb` (Personas)
    - **Toggle**: activar "Quiero acceder con RUT empresa" via JS: `page.evaluate("document.getElementById('new-switch-login').click()")` — CRÍTICO: `<span id="sliderEmpresa">` intercepts pointer events, NO se puede usar `locator().click()` directamente
    - **3 campos**: `#rut_empresaID` (777884174, auto-formatea a 77.788.417-4) + `#rut_usuarioID` (156417076 hardcoded, NO auto-formatea) + `#claveId` (clave internet)
    - **Credenciales**: `bw_unlock()` + `bw list items --search itau` → filtrar `"empresa" in name` → get by ID (igual que BTG PJ). RUT personal hardcoded `"156417076"`, NO desde Bitwarden
    - **Espera post-login**: `networkidle` + `wait_for_selector("a:has-text('0230845735')")` — NO usar `a:has-text('Cuenta Corriente')` (matchea 6 links del menú nav)
    - **Extracción**: JS `querySelectorAll('a').find(a => a.textContent.includes('0230845735'))` → `closest('tr')` → último `<td>`

---

---

## SESIÓN 7 MAR 2026 — Harvard FCU + UX Improvements

### ✅ COMPLETADO: Harvard FCU Scraper

**Estado:** ✅ Funcionando 100% automático (6 Mar 2026)

**Problema resuelto:**
- Bitwarden: entry se llama `my.harvardfcu.org` (no `"Harvard FCU"`)
- Solución: `bw list items --search harvardfcu` + JSON parse (patrón BTG PJ)
- Login: `#username` + `#password` (input floating-label jQuery) + Cloudflare auto-pass
- Bug: NO usar `fill("")` → rompe el estado JS del input → solución: `.click()` → pausa 300ms → `press_sequentially()`
- Extracción: `#module_accounts li[id^="account_"] → .balance-double span` (confirmado en vivo)
- Parsing USD: formato US `$5,001.50` → remover `$` y `,` → `float`
- Documentación: actualizado CLAUDE.md con selectores, flow y Bitwarden entry name

**Backups:**
- `backups/saldos_20260306_205156_harvard_ok.py` — scraper OK

### ✅ COMPLETADO: `prompt_failed_items()` Function

**Propósito:** Reemplazar diálogo crudo de `input()` con UI bonita para actualizar items fallidos

**Qué hace:**
- Muestra solo items con ERROR (no todos)
- Tabla con Rich panel (rojo): Institución | Item | Último Valor | Moneda | Motivo error
- Checkboxes questionary para seleccionar cuáles actualizar
- Pre-rellena último valor conocido de cada item (desde DB)
- Solo pide valores para los seleccionados (no uno por uno)
- Mejor UX general, menos "feo"

**Ubicación:** `saldos.py` línea ~4350, integrado en `run_scraping()` (línea ~5120)

**Backups:**
- `backups/saldos_20260307_043431_prompt_failed_items.py` — función OK

---

## Pendiente (Próximas Sesiones)

### 🔄 EN PROGRESO: Itaú PJ — CFIETFCD y CFINASDAQ (Shares × Price)

**Contexto:**
- Actualmente en MANUAL_ITEMS (hardcoded, valores viejos)
- Son fondos de inversión: `Valor Total = Cantidad de Acciones × Precio por Acción`
- El número de acciones RARA VEZ cambia, pero el precio SÍ cambia frecuentemente
- Hoy: número de acciones no cambia → mañana: precio cambia automáticamente

**Arquitectura planeada:**
1. **Almacenar acciones:** Nueva columna `extra_data` (JSON) en `catalog_manual` para guardar `{shares: 1234}`
2. **Buscar precio online:** Google Finance / Yahoo Finance (user aún no especificó URL/API exacta)
3. **Flujo manual actualizado:**
   - Pregunta 1: "¿Cuántas acciones tienes de CFIETFCD?" → pre-rellena con anterior, enter para mantener
   - Pregunta 2: "¿Buscar precio online o ingresar manualmente?"
   - Si online: fetch precio → calcula `valor = acciones × precio`
   - Si manual: pide el precio → calcula `valor = acciones × precio`
   - Guarda: valor total + shares

**FALTA:**
- Cómo buscar el precio online (URL/API/method TBD por user)
- Implementar DB migration (nueva columna `extra_data`)
- Crear función `get_fund_price(ticker)` con el método proporcionado
- Modificar `_quick_update_balance()` para estos items especiales

**Notas para próxima sesión:**
- User dirá "te dare una forma de hacerlo" → preguntarle cuál es el método exacto
- Probablemente requiera scraping de Google Finance o un ticker/API específico
- Considerar si también Wealthfront (ONEQ) podría necesitar el mismo tratamiento

---

## Cambios Técnicos Internos (7 Mar 2026)

1. **Harvard FCU Bitwarden:** cambio crítico de `bw_get("username", "Harvard FCU")` → `bw list items --search harvardfcu` + JSON
2. **Login inputs:** reemplazado `fill("")` + `press_sequentially()` → `.click()` + pausa + `press_sequentially()`
3. **UX manual fallidos:** reemplazado diálogo crudo de `input()` con función `prompt_failed_items()` usando questionary + Rich
4. **Documentación:** actualizado CLAUDE.md con Harvard FCU selectores, Bitwarden entry name, y nuevas features
5. **Estandarización de Menús** (7 Mar 2026 - tarde) ✅ — TODOS los `questionary.select` y `checkbox` ahora tienen opciones de navegación
   - Fix: agregado "Volver atrás" + "Salir" al menú de `_run_update_batch()` (línea 4659)
   - Verificación completa: 11 menús total, todos con opciones de "Volver"/"Salir" o equivalentes
   - Menús con "Volver atrás" + "Salir": add_new_manual_type, prompt_manual_items, prompt_failed_items, select_institutions_for_scraping, manual_entry
   - Menús con "« Volver": manage_manual_records (hierarchical), _run_update_batch (nueva fix)
   - Menús con "Omitir institución" / "Skip": manual_entry items
   - Usuario feedback: "es importante siempre tener un volver" — implementado

---

## Cambios Técnicos Internos (8 Mar 2026) — Sesión Tarde

5. **Inversiones TIR semi-automáticas (Dorco / WBuild):**
   - Nuevas tablas DB: `tir_investments` + `tir_dividends` con seed inicial de 4 inversiones
   - `calc_tir_value()`: fórmula compuesto diario `(1+TIR)^(1/365)^días - Σdiv`
   - `_update_tir_item()`: flujo integrado con pregunta de dividendo en el update
   - `_is_tir_item()`: detección automática en `_quick_update_balance()` y `_run_update_batch()`
   - `TIR_KEYS = {"dorco", "wbuild"}`: `run_scraping()` separa TIR (sin browser) del resto
   - `_get_source_emoji()` actualizado: muestra `"TIR"` para Dorco/WBuild
   - **UX clave**: dividendo como PASO dentro de la actualización específica (no menú separado)
   - Backup: `backups/saldos_20260308_tir_pre.py` (antes de la implementación)

## Cambios Técnicos Internos (8 Mar 2026)

1. **Global66 Empresas scraper:** `scrape_global66_pj()` + entrada en `INSTITUTION_ITEMS`
   - MFA manual (6 dígitos vía WhatsApp) — no automatizable por ahora
   - Contexto aislado, `bank_key: "global66_pj"`, cat `"Cash PJ"`
   - Bitwarden entry: `empresas.global66.com` (directo, sin buscar por lista)
2. **`run_scraping` con `full_update` param:** solo llama `_update_semiautomatics_auto()` cuando `full_update=True` o se incluyó `itau_pj`
3. **Neat scraper fixes:**
   - Scroll Angular-aware: `mat-sidenav-content` como contenedor scrollable (no `window`)
   - Detección dual InProgress: CSS class `InProgress` OR badge text `"en progreso"`
   - Selector fallback columnas: `[class*="d-none"]` + filter any `d-md-*` class
   - Ya no saltea filas con < 3 columnas — incluye diagnóstico `colCount`/`rowClasses`
4. **Backup:** `backups/saldos_20260308_080030_global66_added.py`

---

## SESIÓN 7 MAR 2026 (Tarde-Noche) — Shares × Price Logic ✅ IMPLEMENTADO

### ✅ COMPLETADO: Lógica Shares × Price para CFIETFCD/CFINASDAQ

**Estado:** ✅ Implementado — **APLICA EN TODOS LOS MENÚS**

**Detalle crucial:** La lógica de Shares × Price se **dispara automáticamente SOLO** cuando intenta actualizar CFIETFCD o CFINASDAQ, en **CUALQUIER CONTEXTO** (menú 1, 2, 3, 4, etc.):

**Flujo (siempre igual):**
1. Pregunta: "¿Cuántas acciones tienes de [CFIETFCD/CFINASDAQ]?"
2. Pregunta: "¿Scrapper (Yahoo Finance) o manual para precio?"
3. Si manual: pide precio actual
4. Si scrapper: intenta buscar en Yahoo Finance automáticamente → si falla, pide manual
5. Calcula automáticamente: `Valor Total = Acciones × Precio`
6. Guarda en DB

**Funciones helper (líneas ~4500-4630):**
- `_is_shares_price_item(item)` — Verifica si es CFIETFCD o CFINASDAQ
- `get_fund_price(ticker)` — **MEJORADO 7 Mar** — Búsqueda multi-estrategia en Yahoo Finance (líneas 4500-4560)
- `_update_shares_price_item(inst, cat, item, moneda, bank_key)` — Ejecuta lógica especial

**Mejora Yahoo Finance (7 Mar 2026) ✅:**
- **Estrategia 1**: Busca múltiples campos: regularMarketPrice, currentPrice, navPrice, bidPrice, askPrice, previousClose, fiftyTwoWeekHigh
- **Estrategia 2**: Fallback histórico inteligente: 1d → 5d → 1mo → 3mo
- **Validación**: Verifica valores > 0 y no NaN
- **Debug mejorado**: Imprime exactamente qué campo/período funcionó
- **Test script**: `test_yahoo_finance_improved.py` — Prueba ambos tickers con output detallado

**Integración en contextos:**
- ✅ `_quick_update_balance()` — Llamada desde múltiples menús
- ✅ `prompt_failed_items()` — Para items fallidos
- ✅ `manual_entry()` — Para ingreso manual
- ✅ `test_shares_price_logic()` — Función de prueba interactiva (menú 8)

**Importante:** La lógica **SOLO aplica a estos dos items**. Todos los demás usan flujo normal (pedir monto directo).

**Emoji en tabla:**
- 🤖 = Automático (scraping)
- 🏷️ = Semi-automático (Shares × Price: precio buscado en Yahoo Finance, acciones ingresadas manualmente) — CFIETFCD/CFINASDAQ
- ✏️ = Manual (entrada a mano)

La función `_get_source_emoji(item, source)` determina automáticamente el emoji correcto según el item y la fuente de datos. Aplicado en `show_last_saldos()`, dashboard HTML, y markdown export — mantiene coherencia en todas las vistas. 🏷️ es un emoji "etiqueta de precio" compacto que mantiene alineación consistente en la tabla.

**Backups:**
- `backups/saldos_20260307_yahoo_improved.py` (versión con función mejorada)
- `test_shares_price_demo.py` (demostración sin internet)
- `test_yahoo_finance_improved.py` (test detallado de Yahoo Finance)

---

## SESIÓN 13 MAR 2026 — Comparador Histórico ✅ IMPLEMENTADO

### ✅ COMPLETADO: `show_comparison()` — Comparar dos fechas históricas

**Backup:** `backups/saldos_20260313_comparador_ok.py`

**Qué hace:**
- Elige dos fechas (date_a y date_b) con `_pick_snapshot_date()`
- Muestra tabla de 3 secciones (CLP MM / USD M / UF) con columnas: Categoría | Fecha A | Fecha B | Δ | Δ%
- TOTAL PATRIMONIO (excluye Mi casa) y TOTAL GENERAL en bold naranja
- Submenú para cambiar fechas o volver al menú principal

**Fix crítico — acumulación correcta usando catálogo:**
- **Problema**: `_fetch_snapshot_dict()` leía TODOS los items de la DB incluyendo entradas stale (`New → New` con UF 43.000), duplicados de "Mi casa" y datos de instituciones antiguas — causaba totales inflados 2-14x
- **Solución**: Reemplazado por flujo idéntico al RESUMEN:
  1. `_build_db_data_ok(ts_filter)` — SQL con `MAX(timestamp) <= fecha` por (inst, item, persona)
  2. Iteración sobre `_get_unified_catalog_list()` — solo items del catálogo vigente
  3. Lookup histórico por `db_key = (db_inst, item_code, cat_to_persona(cat))`
- **Resultado**: valores comparador = valores RESUMEN exactamente (diff ≤1 por redondeo)

**Diseño:**
- Sin columna de notas ("+2 nuevos" eliminada)
- Δ y Δ% en bold para filas de totales (`fd(..., bold=True)`, `fp(..., bold=True)`)
- Acceso desde menú principal: VISUALIZACIÓN → Comparar (idx 11)

## Pendiente Original

- Santander: validar que el fix del `context.add_init_script` resuelve el fallo al correr solo (parece estable)
- Validar AFP Modelo en próxima ejecución real (AFC ✅ confirmado Mar 1 2026)
- Validar BTG Pactual PN + PJ en próxima ejecución completa (fix DB key collision ✅ Mar 1 2026)
- Harvard FCU ✅ confirmado 7 Mar 2026 — scraper completamente automático (Bitwarden search, Cloudflare auto-pass, UI mejorada)
- Shares × Price ✅ IMPLEMENTADO 7 Mar 2026 — lógica inteligente que detecta CFIETFCD/CFINASDAQ automáticamente en todos los menús
