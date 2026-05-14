# 🔧 Sistema de Auto-Fix para GastoSmart

## Descripción

Sistema automático que detecta, registra y repara errores en los scripts de GastoSmart. Cada vez que se ejecuta un script:

1. **Detecta** errores y excepciones
2. **Registra** en ERROR_LOG.json con timestamp
3. **Intenta reparar** automáticamente
4. **Documenta** los fixes en FIX_HISTORY.md

---

## Scripts Nuevos

### 1. `error_tracker.py` - Gestor de Errores

Detecta, registra y repara problemas del sistema.

**Uso:**
```bash
# Diagnóstico completo
python3 error_tracker.py diagnose

# Auto-reparación
python3 error_tracker.py fix

# Ver últimos errores
python3 error_tracker.py errors

# Limpiar log
python3 error_tracker.py clear
```

**Qué verifica:**
- ✅ Dependencias Python (supabase, notion-client, google-generativeai, etc.)
- ✅ Variables de entorno (.env)
- ✅ Integridad de BD local
- ✅ Archivos críticos

**Qué repara automáticamente:**
- Instala dependencias faltantes
- Crea .env si no existe
- Reinicializa BD corrupta

---

### 2. `run_with_autofix.py` - Ejecutor con Auto-Reparación

Ejecuta el pipeline completo (5 pasos) y auto-repara errores.

**Uso:**
```bash
python3 run_with_autofix.py
```

**Qué hace:**
1. Diagnóstico inicial del sistema
2. Ejecuta 5 pasos del pipeline:
   - Step 0: Notion Sync Checker
   - Step 1: Prepare (limpiar, deduplicar, listar)
   - Step 2: Analyze (Gemini/Haiku)
   - Step 3: Upload (Supabase + BD)
   - Step 4: Sync Notion
   - Step 5: Cleanup (iCloud)

3. Si un paso falla:
   - Registra el error con contexto
   - Intenta identificar el tipo (ModuleNotFound, FileNotFound, etc.)
   - Aplica fix automático si es posible
   - **Continúa con siguiente paso** (no detiene)

4. Guarda resumen en RUN_LOG.json

---

### 3. `status_dashboard.py` - Dashboard de Estado

Muestra la salud general del sistema en una pantalla.

**Uso:**
```bash
python3 status_dashboard.py
```

**Información que muestra:**
- 🟢🟡🔴 Score de salud (0-100%)
- Último run (fecha, pasos exitosos/fallidos)
- Resumen de errores (total, abiertos, solucionados)
- Diagnóstico crítico (BD, .env, scripts)
- Recomendaciones automáticas

---

### 4. `run_24_7_with_autofix.sh` - Versión mejorada de run_24_7.sh

Script bash que ejecuta el pipeline cada 5 minutos con auto-fix.

**Uso:**
```bash
# En background
nohup bash run_24_7_with_autofix.sh &

# Verificar status
python3 status_dashboard.py
```

---

## Archivos Generados

### ERROR_LOG.json
Log de todos los errores encontrados. Formato:
```json
[
  {
    "timestamp": "2026-03-20T19:52:00",
    "script": "gs_auto_processor.py",
    "error_type": "ModuleNotFound",
    "error_msg": "No module named 'supabase'",
    "solution": "pip install supabase",
    "fixed": true,
    "fix_timestamp": "2026-03-20T19:52:05"
  }
]
```

### RUN_LOG.json
Log de cada ejecución del pipeline. Incluye:
- Timestamp
- Pasos ejecutados (success/fail)
- Fixes aplicados
- Output/stderr de cada paso

### FIX_HISTORY.md
Registro cronológico de todas las reparaciones realizadas.

---

## Workflow Recomendado

### Daily
```bash
# Ver estado actual
python3 status_dashboard.py

# Si hay problemas
python3 error_tracker.py diagnose
python3 error_tracker.py fix
```

### Weekly
```bash
# Limpiar logs antiguos
python3 error_tracker.py clear
```

### Automático (24/7)
```bash
bash run_24_7_with_autofix.sh
```

---

## Tipos de Errores Detectados

| Tipo | Detección | Fix Automático |
|------|-----------|----------------|
| **ModuleNotFound** | "No module named X" | ✅ pip install |
| **FileNotFound** | "No such file" | ⚠️ Reporta |
| **ConnectionError** | Timeout, API fail | ⚠️ Reporta |
| **ConfigError** | KeyError, AttributeError | ⚠️ Reporta |
| **DatabaseError** | BD corrupta/vacía | ✅ Reset BD |

---

## Ejemplos de Uso

### Escenario 1: Error silencioso en Step 3

```
▶️  Step 3: Upload
❌ Error en Step 3
  ModuleNotFoundError: No module named 'supabase'

🔧 Analizando error...
  → Error de módulo faltante
  → Auto-instalando dependencias...
  ✅ Dependencias instaladas

[Auto-reintenta Step 3...]
✅ Step 3 completado
```

### Escenario 2: Ver última ejecución

```bash
$ python3 status_dashboard.py

🟢 SALUD DEL SISTEMA: 95%

▶️  ÚLTIMO RUN
   Hace: 2 horas
   Resultado: 5/6 pasos exitosos
   ⚠️  1 paso fallido
   🔧 Fixes aplicados: 1

❌ Abiertos: 1
```

---

## Notas

- **Sin downtime:** Los errores se reparan mientras el sistema sigue corriendo
- **Auto-escalable:** Detecta nuevas dependencias automáticamente
- **Auditable:** Todo queda registrado en JSON
- **Smart retry:** Continúa con siguientes pasos después de error

---

## Comandos Rápidos

```bash
# Diagnóstico + auto-fix en 1 comando
python3 error_tracker.py diagnose && python3 error_tracker.py fix

# Ver últimos 10 errores
python3 error_tracker.py errors

# Ejecutar pipeline con reporte detallado
python3 run_with_autofix.py | tee run_output.log

# Monitor contínuo (cada 5 min)
watch -n 300 'python3 status_dashboard.py'
```

---

## Changelog

### v1.0 (2026-03-20)
- ✅ error_tracker.py - Diagnóstico y auto-fix
- ✅ run_with_autofix.py - Pipeline resiliente
- ✅ status_dashboard.py - Monitoreo visual
- ✅ run_24_7_with_autofix.sh - Automatización 24/7
