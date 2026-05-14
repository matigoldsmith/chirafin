#!/bin/bash
# GastoSmart 24/7 con Auto-Fix - Procesamiento automático + error tracking + sync Notion
cd "$(dirname "$0")"

LOG="$(dirname "$0")/watcher.log"
ERROR_LOG="$(dirname "$0")/ERROR_LOG.json"

# Rotar log si supera 5MB
if [ -f "$LOG" ] && [ $(wc -c < "$LOG") -gt 5242880 ]; then
    mv "$LOG" "${LOG}.bak"
    echo "[$(date)] Log rotado (>5MB)" > "$LOG"
fi

log() { echo "$1" >> "$LOG"; }

log "🚀 GastoSmart 24/7 con Auto-Fix iniciado: $(date)"

# Verificación inicial
python3 -W ignore error_tracker.py diagnose >> "$LOG" 2>&1

while true; do
    log "--- $(date) ---"

    # Ejecutar pipeline con auto-fix
    # Captura output y lo guarda en log
    python3 -W ignore run_with_autofix.py 2>&1 | tee -a "$LOG"

    # Ver estado del sistema
    python3 -W ignore status_dashboard.py >> "$LOG" 2>&1

    # Si hay errores abiertos, registrarlos en el log
    OPEN_ERRORS=$(python3 -c "
import json
import os
try:
    with open('ERROR_LOG.json') as f:
        errors = json.load(f)
    open_count = sum(1 for e in errors if not e['fixed'])
    print(open_count)
except:
    print(0)
" 2>/dev/null)

    if [ "$OPEN_ERRORS" -gt 0 ]; then
        log "⚠️  $OPEN_ERRORS errores abiertos - revisar ERROR_LOG.json"
    fi

    log "⏳ Esperando 5 minutos para siguiente iteración..."
    sleep 300
done
