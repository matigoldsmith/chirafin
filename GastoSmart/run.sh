#!/bin/bash

# --- GastoSmart Starter ---
# Este script lanza el motor en segundo plano y el monitor en primer plano.

echo "🚀 Iniciando GastoSmart V3.2..."

# 0. Determinar la ruta base del proyecto
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$SCRIPT_DIR"

# 1. Matar procesos previos para evitar duplicados
pkill -f "backend/watcher.py" 2>/dev/null
sleep 1

# 2. Iniciar el motor (Watcher) en segundo plano
# Redirigimos la salida a /dev/null porque el monitor ya lee el log directamente
python3 backend/watcher.py > /dev/null 2>&1 &
WATCHER_PID=$!

echo "✅ Motor iniciado (PID: $WATCHER_PID)"
echo "🖥️ Abriendo monitor..."
sleep 2

# 3. Iniciar el monitor en primer plano
python3 backend/monitor.py

# Al cerrar el monitor con Ctrl+C, matamos también el watcher
kill $WATCHER_PID 2>/dev/null
echo -e "\n🛑 GastoSmart detenido."
