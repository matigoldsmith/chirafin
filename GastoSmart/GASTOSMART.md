# 🧠 GastoSmart: Documentación Integral del Proyecto

GastoSmart es un sistema de automatización financiera personal diseñado para procesar recibos, facturas y tickets físicos mediante Inteligencia Artificial (Gemini), almacenando los datos en Supabase y sincronizándolos automáticamente con Notion.

---

## 🏗️ Arquitectura del Sistema

El sistema se divide en tres capas principales:

### 1. Capa de Ingesta (Watcher)
- **Script:** `backend/watcher.py`
- **Función:** Monitorea una carpeta de iCloud (`~/Library/Mobile Documents/com~apple~CloudDocs/GastoSmart`).
- **Proceso:**
    1. Detecta archivos nuevos (PNG, JPG, HEIC).
    2. Calcula un `hash` único para evitar duplicados.
    3. Sube la imagen a **Supabase Storage**.
    4. Envía la imagen a **Gemini 1.5/2.0** para extraer: Comercio, Fecha, Monto, Moneda y Categoría.
    5. Calcula el **Tipo de Cambio (FX)** automático usando `currency_utils.py`.
    6. Guarda todo en la base de datos local `gastosmart_v1.db` (SQLite).
    7. Elimina el archivo original de iCloud para liberar espacio.

### 2. Capa de Backend y Sincronización
- **`backend/notion_bridge.py`:** Envía los datos procesados a Notion. Implementa **Smart Mapping**, lo que permite renombrar columnas en Notion (ej: "FX", "T/C") sin romper el sistema.
- **`backend/processor.py`:** Gestiona las llamadas a la API de Google Gemini, incluyendo lógica de rotación de modelos para evitar cuotas saturadas.
- **`backend/currency_utils.py`:** Consulta APIs de tipos de cambio (CLP, USD, BRL, EUR) para normalizar todos los gastos a Pesos Chilenos (CLP).
- **`backend/notion_learning.py`:** Sistema de "aprendizaje" que permite corregir nombres de comercios o categorías directamente en Notion y que el sistema los aprenda para la próxima vez.

### 3. Capa de Visualización (Monitor)
- **Mission Control (`monitor.py`):** Interfaz de terminal de alta fidelidad. He rediseñado este componente para ofrecer **Transparencia Total**:
    - **[Σ] Universo Total:** La suma real de fotos capturadas + fotos pendientes. Es tu objetivo final.
    - **[IN] Pendientes IC:** Archivos en la carpeta iCloud esperando procesamiento.
    - **[DB] A salvo en DB:** Registros que ya pasaron por el motor y están seguros en tu base de datos local (ya no ocupan espacio en iCloud).
    - **Estados de Actividad:**
        - **PEND (Amarillo):** Foto subida a Supabase, esperando turno de procesamiento por IA.
        - **GASTO (Verde):** Recibo confirmado por Gemini.
        - **--- (Gris):** Foto analizada y descartada por no ser gasto.

---

## 🔍 La Verdad de los Números (Matemática del Avance)

Para entender los porcentajes en el Mission Control, considera este flujo:
1. **Punto de Partida:** Subiste ~3,250 archivos a iCloud.
2. **Procesamiento:** El Watcher toma un archivo, lo registra en DB y **lo borra de iCloud**.
3. **Cálculo:** 
    - `Universo Total` = Pendientes en iCloud + Registrados en DB.
    - `% Avance` = Registrados en DB / Universo Total.

---

## 🛠️ Configuración (Setup)

### Variables de Entorno (`.env`)
El archivo `.env` en la carpeta `backend/` debe contener:
- `GEMINI_API_KEY`: Tu llave de Google AI Studio.
- `NOTION_TOKEN`: Token interno de integración de Notion.
- `NOTION_DATABASE_ID`: El ID de tu base de datos de Notion.
- `SUPABASE_URL` / `SUPABASE_KEY`: Credenciales de tu proyecto en Supabase.
- `ICLOUD_INPUT_PATH`: Ruta absoluta a tu carpeta de GastoSmart en iCloud.

### Base de Datos Local
Ubicada en `backend/gastosmart_v1.db`. Contiene las tablas:
- `gastos`: El registro histórico de cada archivo procesado.
- `aprendizaje`: Reglas de corrección automática basadas en tus ediciones anteriores.

---

## 🚀 Cómo Ejecutar el Proyecto

### 1. Iniciar el Vigilante (Watcher)
Es el motor que procesa los archivos. Se recomienda ejecutarlo en una pestaña de terminal permanente:
```bash
cd "/Users/mgoldsmithd/Scripts Claude AI/GastoSmart/backend"
python3 watcher.py
```

### 2. Ver el Panel de Control (Monitor)
Para ver el progreso de las subidas y el estado de la IA en tiempo real:
```bash
python3 monitor.py
```

---

## 📝 Reglas de Mantenimiento (Para otros desarrolladores)

1. **Inteligencia de Columnas (Smart Mapping):** Si agregas una columna a Notion, búscala por "sinónimo" en `notion_bridge.py`. El sistema escanea los nombres de las propiedades de Notion y busca coincidencias (ej: `FX`, `Tipo Cambio`, `T/C`).
2. **Archivos Bloqueados:** El mensaje `SKIP: Archivo bloqueado` es normal en macOS. Significa que iCloud está sincronizando. El sistema reintentará procesarlo solo.
3. **Rotación de Logs:** El archivo `watcher.log` puede crecer mucho. Se recomienda truncarlo periódicamente (`tail -n 5000`).
4. **Resync Forzado:** Si necesitas re-enviar datos a Notion después de un cambio de lógica, ejecuta:
   `sqlite3 gastosmart_v1.db "UPDATE gastos SET sync_notion = 0 WHERE es_recibo = 1;"`

---

## 📈 Estado Actual (13 Mar 2026 — sesión Claude Cowork)
- **Progreso:** ~19% analizado por IA (445 / 2377 en DB). Universo total ≈ 2378.
- **Notion:** 291 entradas válidas sincronizadas. Limpieza de ~510 entradas sin ID y duplicados ejecutada.
- **IA:** Rotación de modelos activa. Freno global de cuota implementado (5 min de cooldown al saturarse).
- **Auto-Mantenimiento:** Logs bajo 5MB automático. Sanity Check cada 30 min incluye Notion Health Check.
- **Backups:** Versión estable en `backups/2026-03-13_v1/`.

---

## 🔧 Cambios Técnicos (13 Mar 2026)

### Fixes aplicados en sesión Claude Cowork:
1. **Freno de cuota Gemini (`watcher.py`):** Variable global `_quota_cooldown_until`. Cuando Gemini devuelve QUOTA, el sistema espera 5 minutos antes de reintentar. Evita el loop de requests infinitos.
2. **Fix loop aprendizaje (`notion_learning.py`):** `notion_es_recibo` ahora lee la columna correcta `"Es recibo según Gemini"` (select). El bug anterior (leer un checkbox inexistente) causaba que los mismos registros se re-aprendieran cada 5 minutos.
3. **Fix ID_Hash vs ID (`notion_learning.py`):** El sistema de aprendizaje ahora busca el hash en ambas columnas `"ID_Hash"` y `"ID"` para compatibilidad con registros nuevos y viejos.
4. **Notion Health Check (`watcher.py`):** Función `check_notion_health()` añadida al ciclo de Sanity (cada 30 min). Detecta y advierte sobre entradas sin ID o duplicados en Notion.
5. **Script de limpieza (`notion_cleanup.py`):** Herramienta para archivar entradas basura en Notion. Ejecutar manualmente cuando el Health Check detecte problemas.
6. **Fix indentación Sanity Check (`watcher.py`):** El bloque de análisis IA estaba fuera del `else` por error de indentación. Corregido.
7. **Reset Error AI:** 274 registros con `estado = 'Error AI'` reseteados a `'Analizando...'` para reintento automático.
8. **Reconciliación DB/Notion:** Corregidos 41 registros con sync_notion=0 que ya estaban en Notion, y 20 con sync_notion=1 que no estaban.

### Flujo del monitor (`monitor.py`):
- Alias `gastos` en `~/.zshrc` para lanzar rápido.
- Logs dinámicos que llenan el espacio disponible de la terminal.
- Muestra: PROGRESO (DB/Universo %), iCloud pendientes, IA procesados/cola, Notion sync, Gemini cuota + timestamp de saturación, último éxito.

---
*Documentación actualizada por Claude Cowork (Anthropic).*
