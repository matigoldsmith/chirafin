# ✅ Sistema de Auto-Fix para GastoSmart

## Resumen

Creé un sistema que **detecta, registra y repara automáticamente** errores en los scripts. Cada error queda documentado con timestamp y solución aplicada.

---

## 🎯 Uso Directo (Sin Configuración)

### Ver salud del sistema
```bash
cd "Scripts Claude AI--GastoSmart"
python3 status_dashboard.py
```

**Muestra:**
- Porcentaje de salud (🟢🟡🔴)
- Último run
- Errores abiertos/solucionados
- Recomendaciones automáticas

### Diagnosticar problemas
```bash
python3 error_tracker.py diagnose
```

**Verifica:**
- ✅ Módulos Python instalados
- ✅ Variables .env configuradas
- ✅ BD local íntegra
- ✅ Scripts críticos presentes

### Auto-reparar
```bash
python3 error_tracker.py fix
```

**Repara automáticamente:**
- Instala dependencias faltantes
- Crea .env si no existe
- Reinicializa BD corrupta

### Ver histórico de errores
```bash
python3 error_tracker.py errors
```

---

## 🚀 Ejecutar Pipeline con Auto-Fix

Ejecuta los 5 pasos del procesamiento:
```bash
python3 run_with_autofix.py
```

**Qué hace:**
1. Diagnóstico inicial
2. Step 1-5 (procesamiento)
3. Si hay error → registra + auto-repara + continúa
4. Resumen final con fixes aplicados

**Archivos generados:**
- `ERROR_LOG.json` - Todos los errores
- `RUN_LOG.json` - Detalles de última ejecución
- `FIX_HISTORY.md` - Cronología de fixes

---

## ⚙️ Automatización 24/7

**Opción 1: Ejecutar cada 5 min (con auto-fix)**
```bash
bash run_24_7_with_autofix.sh &
```

**Opción 2: Usar script original (sin auto-fix)**
```bash
bash run_24_7.sh &
```

---

## 📊 Archivos Nuevos

| Archivo | Propósito |
|---------|-----------|
| `error_tracker.py` | Diagnosticar y reparar |
| `run_with_autofix.py` | Ejecutar pipeline con auto-fix |
| `status_dashboard.py` | Ver estado del sistema |
| `run_24_7_with_autofix.sh` | Automatización 24/7 |
| `AUTO_FIX_README.md` | Documentación técnica |
| `ERROR_LOG.json` | Log de errores (auto-generado) |
| `RUN_LOG.json` | Log de execuciones (auto-generado) |
| `FIX_HISTORY.md` | Cronología de fixes (auto-generado) |

---

## 🔧 Cómo Funciona

Cuando un script falla:

```
Script falla
    ↓
Error_tracker lo detecta
    ↓
Identifica el tipo (ModuleNotFound, FileNotFound, etc.)
    ↓
Registra en ERROR_LOG.json
    ↓
Intenta auto-reparar (instala módulos, etc.)
    ↓
Continúa con siguiente paso
    ↓
Documenta todo en FIX_HISTORY.md
```

---

## 💡 Casos Comunes Resueltos

### "No module named 'supabase'"
✅ Auto-detecta → pip install supabase → continúa

### "FileNotFoundError"
📋 Registra en log → reporta en dashboard

### "ConnectionError"
📋 Registra en log → muestra en recomendaciones

### "BD corrupta"
✅ Auto-detecta → reset_db.py → continúa

---

## 🎬 Comenzar Ahora

```bash
cd "Scripts Claude AI--GastoSmart"

# 1. Ver estado actual
python3 status_dashboard.py

# 2. Si hay problemas
python3 error_tracker.py diagnose
python3 error_tracker.py fix

# 3. Ejecutar pipeline
python3 run_with_autofix.py

# 4. Automatizar 24/7 (opcional)
bash run_24_7_with_autofix.sh &
```

---

## ✨ Ventajas

✅ **Sin downtime** - Los errores se reparan mientras sigue corriendo
✅ **Auditable** - Todo en JSON, versionable
✅ **Smart** - Detecta nuevos problemas automáticamente
✅ **Documentado** - Cronología completa de fixes
✅ **Sin configuración** - Funciona out-of-the-box

---

Ver `AUTO_FIX_README.md` para documentación técnica completa.
