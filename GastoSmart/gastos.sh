#!/bin/bash
# ==============================================================================
# GASTOS - GastoSmart CLI
# Uso: gastos        → dashboard completo (tabla + 30 logs en pantalla)
#      gastos reset  → resetea todas las bases de datos
#      gastos test   → prueba conexión AI
#      gastos stop   → detiene el watcher
#      gastos logs   → últimas líneas del log
# ==============================================================================

BACKEND="/Users/mgoldsmithd/Scripts Claude AI/GastoSmart/backend"
LOG="$BACKEND/watcher.log"
DB="$BACKEND/gastosmart_v1.db"
ICLOUD="$HOME/Library/Mobile Documents/com~apple~CloudDocs/GastoSmart"
CMD=${1:-""}

SEP="  ──────────────────────────────────────────"
LOG_LINES=28        # líneas de log visibles bajo la tabla

# ── Obtiene todos los stats en una sola llamada Python ────────────────────────
get_stats() {
    FOTOS=$(ls "$ICLOUD"/*.{jpg,jpeg,png,heic,HEIC,JPG,JPEG,PNG} 2>/dev/null | wc -l | tr -d ' ')
    FOTOS=${FOTOS:-0}
    if [ -f "$DB" ]; then
        read ANALIZADAS POS_GASTOS NO_GASTOS SYNC_OK SYNC_PEND \
             NOTION_GASTOS NOTION_NO ELIMINADAS POR_ELIMINAR < <(
            python3 - "$DB" 2>/dev/null <<'PY'
import sys, sqlite3
c = sqlite3.connect(sys.argv[1], timeout=3).cursor()
def q(s): return c.execute(s).fetchone()[0]
print(
    q("SELECT COUNT(*) FROM gastos"),
    q("SELECT COUNT(*) FROM gastos WHERE es_recibo=1"),
    q("SELECT COUNT(*) FROM gastos WHERE es_recibo=0"),
    q("SELECT COALESCE(SUM(sync_notion),0) FROM gastos"),
    q("SELECT COALESCE(SUM(1-sync_notion),0) FROM gastos"),
    q("SELECT COUNT(*) FROM gastos WHERE sync_notion=1 AND es_recibo=1"),
    q("SELECT COUNT(*) FROM gastos WHERE sync_notion=1 AND es_recibo=0"),
    q("SELECT COALESCE(SUM(eliminada),0) FROM gastos"),
    q("SELECT COUNT(*) FROM gastos WHERE foto_url!='' AND sync_notion=1 AND (eliminada IS NULL OR eliminada=0)")
)
PY
        )
        ANALIZADAS=${ANALIZADAS:-0}
    else
        ANALIZADAS=0; PENDIENTES=$FOTOS; POS_GASTOS=0; NO_GASTOS=0
        SYNC_OK=0; SYNC_PEND=0; NOTION_GASTOS=0; NOTION_NO=0
        ELIMINADAS=0; POR_ELIMINAR=0
    fi
}

# ── Imprime una fila con valor alineado a columna fija ────────────────────────
# Uso: row "  label" "valor"
row() {
    local COL=42
    printf "%s\033[K\033[${COL}G%s\033[K\n" "$1" "$2"
}

# ── Dibuja el dashboard completo (tabla + logs) ───────────────────────────────
draw_dashboard() {
    local NOW COLS TOTAL_LINES
    NOW=$(date '+%H:%M:%S')
    COLS=$(tput cols 2>/dev/null || echo 80)
    get_stats

    # Ir al inicio de pantalla (sin limpiar)
    printf '\033[H'

    # ── Tabla stats ──
    printf "\n  \033[1m💰 GastoSmart\033[0m  —  %s\033[K\n" "$NOW"
    printf "%s\033[K\n" "$SEP"

    if [ ! -f "$DB" ]; then
        row "  iCloud:" "$FOTOS fotos  |  BD no existe"
        for i in $(seq 1 15); do printf "\033[K\n"; done
    else
        row "  iCloud (por procesar):"   "$FOTOS fotos"
        row "  Total analizadas (BD):"  "$ANALIZADAS"
        printf "\033[K\n"
        row "  De las analizadas:"      ""
        row "     ├─  Posibles gastos:" "$POS_GASTOS"
        row "     └─  No son gastos:"   "$NO_GASTOS"
        printf "\033[K\n"
        row "  Notion ($SYNC_OK registros):" ""
        row "     ├─  Posibles gastos:" "$NOTION_GASTOS"
        row "     ├─  No son gastos:"   "$NOTION_NO"
        row "     └─  Pendientes sync:" "$SYNC_PEND"
        printf "\033[K\n"
        row "  Limpieza iCloud:"        ""
        row "     ├─  Eliminadas:"      "$ELIMINADAS"
        row "     └─  Por eliminar:"    "$POR_ELIMINAR"
    fi
    printf "%s\033[K\n" "$SEP"

    # ── Estado modelos AI ──
    printf "\033[K\n"
    local STATUS_FILE="$BACKEND/.model_status.json"
    if [ -f "$STATUS_FILE" ]; then
        python3 - "$STATUS_FILE" <<'PY'
import sys, json, time
try:
    s = json.load(open(sys.argv[1]))
    now = time.time()
    models = [
        ("Gemini 3 Flash",        "gemini-3-flash-preview"),
        ("Gemini 2.5 Flash",      "gemini-2.5-flash"),
        ("Gemini 2.5 Flash-Lite", "gemini-2.5-flash-lite"),
        ("Gemini 2.0 Flash",      "gemini-2.0-flash"),
        ("Gemini 2.5 Pro",        "gemini-2.5-pro"),
    ]
    last = s.get("_last_used", "")
    print(f"  \033[2mModelos AI:\033[0m\033[K")
    for i, (label, key) in enumerate(models):
        info = s.get(key, {})
        ok   = info.get("ok", True)
        until = info.get("until", 0)
        active = " ← activo" if last == label else ""
        keys_ok    = info.get("keys_ok", None)
        keys_total = info.get("keys_total", None)
        keys_str   = f" [{keys_ok}/{keys_total} keys]" if keys_total else ""
        if ok:
            status = f"\033[0;32mOK{keys_str}{active}\033[0m"
        else:
            mins = max(0, int((until - now) / 60))
            secs = max(0, int((until - now) % 60))
            status = f"\033[0;31mSin quota ({mins}m {secs}s){keys_str}{active}\033[0m"
        branch = "└─" if i == len(models)-1 else "├─"
        print(f"     {branch}  {label+':':<22} {status}\033[K")
except Exception as e:
    print(f"  Modelos AI: sin datos\033[K")
PY
    else
        printf "  \033[2mModelos AI: iniciando...\033[0m\033[K\n"
        for i in $(seq 1 4); do printf "\033[K\n"; done
    fi

    # ── Separador logs ──
    printf "\033[K\n"
    printf "  \033[2m── logs ──────────────────────────────────────\033[0m\033[K\n"

    # ── Últimos LOG_LINES del log ──
    local shown=0
    if [ -f "$LOG" ]; then
        while IFS= read -r line; do
            # Truncar a ancho de terminal menos 2
            printf "  %.$(( COLS - 2 ))s\033[K\n" "$line"
            shown=$((shown + 1))
        done < <(tail -${LOG_LINES} "$LOG" 2>/dev/null)
    fi
    # Rellenar líneas vacías si el log tiene menos de LOG_LINES
    while [ $shown -lt $LOG_LINES ]; do
        printf "\033[K\n"
        shown=$((shown + 1))
    done
}

restore_terminal() {
    printf '\033[?25h'   # mostrar cursor
    printf '\033[r'      # reset scroll region
    clear
}

do_sync() {
    cd "$BACKEND" && python3 << 'PYEOF'
import sys, sqlite3, os
sys.path.insert(0, '.')
from dotenv import dotenv_values
config = dotenv_values('.env')
for k, v in config.items(): os.environ[k] = v
from notion_bridge import sync_to_notion
DB = os.getenv("DB_PATH")
conn = sqlite3.connect(DB)
conn.row_factory = sqlite3.Row
rows = list(conn.execute('SELECT * FROM gastos WHERE sync_notion = 0 ORDER BY id'))
synced = 0
for row in rows:
    data = dict(row)
    if sync_to_notion(data):
        conn.execute('UPDATE gastos SET sync_notion=1 WHERE id=?', (data['id'],))
        synced += 1
conn.commit()
conn.close()
print(f"  ✅ {synced} sincronizados a Notion")
PYEOF
}

# ==============================================================================
case "$CMD" in
    "reset")
        echo -e "\033[1;33m⚠️  Reseteando bases de datos...\033[0m"
        cd "$BACKEND" && python3 reset_all.py
        ;;

    "test")
        echo -e "\033[0;36m🔬 Testeando modelos AI...\033[0m"
        cd "$BACKEND" && python3 -W ignore << 'PYEOF'
import os, sys, time, warnings
warnings.filterwarnings("ignore")
sys.path.insert(0, os.getcwd())
from dotenv import dotenv_values
config = dotenv_values('.env')
for k, v in config.items(): os.environ[k] = v

GEMINI_MODELS = [
    ('gemini-3-flash-preview', 'Gemini 3 Flash'),
    ('gemini-2.5-flash',       'Gemini 2.5 Flash'),
    ('gemini-2.5-flash-lite',  'Gemini 2.5 Flash-Lite'),
    ('gemini-2.0-flash',       'Gemini 2.0 Flash'),
    ('gemini-2.5-pro',         'Gemini 2.5 Pro'),
]

print("Gemini:")
try:
    import google.generativeai as genai
    genai.configure(api_key=config.get('GEMINI_API_KEY'))
    any_ok = False
    for model_id, label in GEMINI_MODELS:
        try:
            genai.GenerativeModel(model_id).generate_content(
                'Di: OK', generation_config={"max_output_tokens": 5})
            print(f"  ✅ {label}")
            any_ok = True
        except Exception as e:
            err = str(e)
            if '429' in err or 'quota' in err.lower() or 'resource_exhausted' in err.lower():
                print(f"  ⚠️  {label}: quota saturada")
            elif '404' in err or 'not found' in err.lower():
                print(f"  ❌ {label}: no existe")
            else:
                print(f"  ❌ {label}: {err[:50]}")
        time.sleep(0.2)
except Exception as e:
    print(f"  ❌ Error general: {e}")

print("OpenAI:")
try:
    from openai import OpenAI
    r = OpenAI(api_key=config.get('OPENAI_API_KEY')).chat.completions.create(
        model='gpt-4o-mini', max_tokens=5,
        messages=[{'role':'user','content':'Di: OK'}])
    print(f"  ✅ GPT-4o-mini: OK")
except Exception as e:
    err = str(e)
    if '429' in err or 'insufficient_quota' in err or 'quota' in err.lower():
        print(f"  ⚠️  GPT-4o-mini: sin créditos")
    else:
        print(f"  ❌ GPT-4o-mini: {err[:60]}")
PYEOF
        ;;

    "stop")
        pkill -f "run_24_7.sh" 2>/dev/null
        pkill -f "watcher.py" 2>/dev/null
        echo -e "\033[0;32m✓ Detenido\033[0m"
        ;;

    "logs")
        tail -80 "$LOG" 2>/dev/null || echo "No hay logs aún"
        ;;

    *)
        # ── Sync automático de pendientes antes de arrancar ────────────────
        if [ -f "$DB" ]; then
            PENDING=$(python3 -c "
import sqlite3
c=sqlite3.connect('$DB',timeout=3).cursor()
c.execute('SELECT COUNT(*) FROM gastos WHERE sync_notion=0')
print(c.fetchone()[0])" 2>/dev/null || echo 0)
            if [ "$PENDING" -gt 0 ]; then
                echo -e "\033[0;36m🔄 Sincronizando $PENDING registros a Notion...\033[0m"
                do_sync
            fi
        fi

        # ── Arrancar watcher en background → escribe al log ───────────────
        cd "$BACKEND"
        bash run_24_7.sh >> "$LOG" 2>&1 &
        WATCHER_PID=$!

        # ── Limpiar terminal y ocultar cursor ─────────────────────────────
        clear
        printf '\033[?25l'
        trap "kill $WATCHER_PID 2>/dev/null; restore_terminal" EXIT INT TERM

        # ── Dashboard: refresca cada 5 seg ────────────────────────────────
        while kill -0 $WATCHER_PID 2>/dev/null; do
            draw_dashboard
            sleep 5
        done

        restore_terminal
        echo "Watcher terminó."
        ;;
esac
