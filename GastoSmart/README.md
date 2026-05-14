# GastoSmart 💰

Sistema automático de clasificación de gastos a partir de fotos de recibos en iCloud.

**Última actualización:** 2026-03-15

---

## Cómo usar

```bash
gastos              # arranca el dashboard + watcher
gastos stop         # detiene todo
gastos test         # verifica que los 3 modelos AI funcionen
gastos reset        # borra BD + Supabase + Notion (⚠️ irreversible)
gastos logs         # ver últimas líneas del log
```

---

## Flujo automático (cada 5 min)

```
Fotos iCloud → AI (Gemini → Claude → OpenAI) → BD local → Supabase + Notion
```

1. Watcher detecta fotos nuevas en la carpeta iCloud
2. Las analiza con IA → determina si es recibo y extrae datos
3. Sube la imagen a Supabase (URL permanente)
4. Sincroniza el registro a Notion
5. Borra la foto de iCloud (una vez procesada y sincronizada)

---

## Modelos AI (cadena de fallback)

| Orden | Modelo              | Estado  |
|-------|---------------------|---------|
| 1°    | Gemini 3 Flash      | Preview |
| 2°    | Gemini 2.5 Flash    | GA      |
| 3°    | Gemini 2.5 Flash-Lite | GA    |
| 4°    | Gemini 2.0 Flash    | Jun 2026|
| 5°    | Gemini 2.5 Pro      | GA      |
| 6°    | Claude Haiku 4.5    | GA      |
| 7°    | GPT-4o-mini         | GA      |

Si un modelo tiene quota agotada → espera 5 min y pasa al siguiente.

---

## Paths (Mac)

| Qué                | Dónde |
|--------------------|-------|
| Scripts            | `/Users/mgoldsmithd/Scripts Claude AI/GastoSmart/` |
| Backend            | `.../GastoSmart/backend/` |
| BD local           | `.../backend/gastosmart_v1.db` |
| Log                | `.../backend/watcher.log` |
| .env (API keys)    | `.../backend/.env` |
| Fotos iCloud       | `~/Library/Mobile Documents/com~apple~CloudDocs/GastoSmart/` |
| Comando `gastos`   | `~/.zshrc` → alias a `gastos.sh` |

---

## API Keys necesarias (.env)

```
GEMINI_API_KEY=...
ANTHROPIC_API_KEY=...
OPENAI_API_KEY=...
NOTION_TOKEN=...
NOTION_DATABASE_ID=...
SUPABASE_URL=...
SUPABASE_KEY=...
DB_PATH=/Users/mgoldsmithd/Scripts Claude AI/GastoSmart/backend/gastosmart_v1.db
ICLOUD_INPUT_PATH=/Users/mgoldsmithd/Library/Mobile Documents/com~apple~CloudDocs/GastoSmart
```

---

## Archivos clave

| Archivo                   | Función |
|---------------------------|---------|
| `gastos.sh`               | CLI principal + dashboard |
| `backend/run_24_7.sh`     | Loop principal (watcher + sync + limpieza) |
| `backend/watcher.py`      | Procesa fotos nuevas de iCloud |
| `backend/processor_v2.py` | Motor IA (Gemini/Claude/OpenAI) |
| `backend/notion_bridge.py`| Sincroniza registros a Notion |
| `backend/notion_sync_checker.py` | Detecta cambios manuales en Notion y re-procesa |
| `backend/fx_helper.py`    | Conversión de monedas a CLP |
| `backend/reset_all.py`    | Limpia BD + Supabase + Notion |

---

## Correcciones en Notion

Cuando la IA se equivoca, puedes corregirlo directamente en Notion:

**La IA dijo NO-recibo pero SÍ lo es:**
→ Cambia `Estado` a `Pendiente` + `Es recibo según Gemini` a `Sí`
→ El sistema re-descarga la imagen desde Supabase y la re-analiza ✅

**La IA dijo SÍ-recibo pero NO lo es:**
→ Cambia `Estado` a `Pendiente` + `Es recibo según Gemini` a `No`
→ El sistema limpia los campos y aprende que ese tipo de imagen no es recibo ✅

**La IA marcó como `Imagen rechazada` (Estado en Notion):**
→ El sistema detecta automáticamente, limpia los campos en BD y aprende ✅

El aprendizaje queda guardado en la tabla `aprendizaje` de la BD y se usa en futuros análisis.

---

## Base de datos (SQLite)

Tabla `gastos`:
- `hash` — SHA256 del archivo (ID único, también en Notion)
- `foto_path` — path original en iCloud
- `foto_url` — URL permanente en Supabase
- `es_recibo` — 1 si la IA determinó que es recibo
- `comercio` — nombre del negocio (Title Case)
- `fecha`, `monto`, `moneda`, `categoria`
- `monto_clp`, `tipo_cambio` — convertido a pesos chilenos
- `sync_notion` — 1 si ya está en Notion
- `eliminada` — 1 si ya se borró de iCloud

Tabla `aprendizaje`:
- `patron` — texto de referencia (nombre de comercio en minúsculas)
- `comercio_limpio` — nombre corregido
- `categoria_fija` — categoría confirmada
- `es_recibo_fijo` — 0 o 1 según lo que el usuario corrigió

---

## Backups

Los backups de la BD se guardan en `backend/backups/` con timestamp.
Para hacer un backup manual: copiar `gastosmart_v1.db` con fecha.

---

## Estado actual (2026-03-15)

- Registros en BD: **1,662**
- Posibles gastos: **33**
- Aprendizajes acumulados: **30**
- Imágenes en Supabase: **1,662**
- Notas eliminadas de iCloud: **~888**
