#!/bin/bash
# GastoSmart 24/7 - Procesamiento automático + sync Notion
cd "$(dirname "$0")"

LOG="$(dirname "$0")/watcher.log"

# Rotar log si supera 5MB
if [ -f "$LOG" ] && [ $(wc -c < "$LOG") -gt 5242880 ]; then
    mv "$LOG" "${LOG}.bak"
    echo "[$(date)] Log rotado (>5MB)" > "$LOG"
fi

log() { echo "$1" >> "$LOG"; }

log "🚀 GastoSmart 24/7 iniciado: $(date)"

# Auto-instalar dependencias faltantes (solo corre si falta algo, no cada vez)
python3 -c "import google.generativeai, anthropic, supabase, PIL, fitz, pillow_heif" 2>/dev/null || {
    log "📦 Instalando dependencias faltantes..."
    pip3 install -r "$(dirname "$0")/requirements.txt" -q --break-system-packages 2>/dev/null || \
    pip3 install -r "$(dirname "$0")/requirements.txt" -q
    log "✅ Dependencias listas"
}

while true; do
    log "--- $(date) ---"

    # 0. Aplicar updates pendientes del VM (retry uploads fallidos, etc.)
    python3 -W ignore pending_updates.py apply 2>/dev/null | grep -v "^$" >> "$LOG" || true

    # 1a. Descargar facturas de Gmail — solo una vez al día a las 10am
    EMAIL_STAMP="$(dirname "$0")/.email_last_run"
    TODAY=$(date +%Y-%m-%d)
    HOUR=$(date +%H)
    if [ "$HOUR" -ge 10 ] && ([ ! -f "$EMAIL_STAMP" ] || [ "$(cat "$EMAIL_STAMP")" != "$TODAY" ]); then
        log "📧 Revisando correo (10am diario)..."
        python3 -W ignore imap_watcher.py 2>/dev/null >> "$LOG" || true
        echo "$TODAY" > "$EMAIL_STAMP"
    fi

    # 1b. Procesar nuevas fotos de iCloud
    python3 -W ignore watcher.py 2>&1 | grep -v "FutureWarning\|google.generativeai\|generative-ai-python\|end of life\|google.api_core\|deprecated\|README\|switch to" >> "$LOG"

    # 2. Detectar cambios manuales en Notion (fecha/moneda) y recalcular FX
    python3 -W ignore notion_sync_checker.py 2>&1 | grep -v "FutureWarning\|google.generativeai\|generative-ai-python\|end of life\|google.api_core\|deprecated\|README\|switch to" >> "$LOG"

    # 3. Sincronizar nuevos registros a Notion
    python3 - << 'SYNC' 2>&1 >> "$LOG"
import sys, sqlite3, os
sys.path.insert(0, '.')
from dotenv import load_dotenv, dotenv_values
_env = os.path.join(os.getcwd(), '.env')
config = dotenv_values(_env)
for k, v in config.items(): os.environ.setdefault(k, v)
from notion_bridge import sync_to_notion

DB = os.getenv("DB_PATH")
conn = sqlite3.connect(DB)
conn.row_factory = __import__('sqlite3').Row
rows = list(conn.execute('SELECT * FROM gastos WHERE sync_notion = 0 ORDER BY id'))
synced = 0
for row in rows:
    data = dict(row)
    if sync_to_notion(data):
        conn.execute('UPDATE gastos SET sync_notion=1 WHERE id=?', (data['id'],))
        synced += 1
conn.commit()
conn.close()
print(f"✓ Notion: {synced} nuevos registros sincronizados")
SYNC

    # 4. Limpiar fotos de iCloud que ya están 100% procesadas
    python3 - << 'CLEANUP' 2>&1 >> "$LOG"
import sys, sqlite3, os
from dotenv import dotenv_values
config = dotenv_values(os.path.join(os.getcwd(), '.env'))
for k, v in config.items(): os.environ.setdefault(k, v)

DB = os.getenv("DB_PATH")
ICLOUD = os.getenv("ICLOUD_INPUT_PATH")

conn = sqlite3.connect(DB)
# Condiciones: hash existe + foto_url no vacía + sync_notion = 1
rows = conn.execute("""
    SELECT hash, foto_path FROM gastos
    WHERE hash IS NOT NULL
      AND foto_url IS NOT NULL AND foto_url != ''
      AND sync_notion = 1
""").fetchall()
conn.close()

conn2 = sqlite3.connect(DB)
borradas = 0
for hash_val, foto_path in rows:
    fname = os.path.basename(foto_path) if foto_path else None
    if not fname:
        continue
    icloud_file = os.path.join(ICLOUD, fname)
    if os.path.exists(icloud_file):
        os.remove(icloud_file)
        conn2.execute("UPDATE gastos SET eliminada=1 WHERE hash=?", (hash_val,))
        print(f"  🗑️  Borrado: {fname}")
        borradas += 1
conn2.commit()
conn2.close()

if borradas:
    print(f"✓ Limpieza: {borradas} fotos borradas de iCloud")
else:
    print(f"✓ Limpieza: nada que borrar")
CLEANUP

    log "⏳ Esperando 5 minutos..."
    sleep 300
done
