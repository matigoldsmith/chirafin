#!/bin/bash
DB="/Users/mgoldsmithd/Scripts Claude AI/GastoSmart/backend/gastosmart_v1.db"
ICLOUD="$HOME/Library/Mobile Documents/com~apple~CloudDocs/GastoSmart"

row() { printf "     %-28s %s\n" "$1" "$2"; }
sep() { printf "\n"; }

while true; do
    clear
    FOTOS=$(ls "$ICLOUD"/*.{jpg,jpeg,png,heic,HEIC,JPG,JPEG,PNG} 2>/dev/null | wc -l | tr -d ' ')
    printf "\n  \033[1m💰 GastoSmart\033[0m  —  %s\n\n" "$(date '+%H:%M:%S')"

    if [ ! -f "$DB" ]; then
        row "📷 iCloud:" "$FOTOS fotos  |  BD no existe aún"
        printf "\n"; sleep 5; continue
    fi

    ANALIZADAS=$(sqlite3    "$DB" "SELECT COUNT(*) FROM gastos;" 2>/dev/null || echo 0)
    PENDIENTES=$((FOTOS - ANALIZADAS))
    POS_GASTOS=$(sqlite3    "$DB" "SELECT COUNT(*) FROM gastos WHERE es_recibo=1;" 2>/dev/null || echo 0)
    NO_GASTOS=$(sqlite3     "$DB" "SELECT COUNT(*) FROM gastos WHERE es_recibo=0;" 2>/dev/null || echo 0)
    SYNC_OK=$(sqlite3       "$DB" "SELECT COALESCE(SUM(sync_notion),0) FROM gastos;" 2>/dev/null || echo 0)
    SYNC_PEND=$(sqlite3     "$DB" "SELECT COALESCE(SUM(1-sync_notion),0) FROM gastos;" 2>/dev/null || echo 0)
    NOTION_GASTOS=$(sqlite3 "$DB" "SELECT COUNT(*) FROM gastos WHERE sync_notion=1 AND es_recibo=1;" 2>/dev/null || echo 0)
    NOTION_NO=$(sqlite3     "$DB" "SELECT COUNT(*) FROM gastos WHERE sync_notion=1 AND es_recibo=0;" 2>/dev/null || echo 0)
    ELIMINADAS=$(sqlite3    "$DB" "SELECT COALESCE(SUM(eliminada),0) FROM gastos;" 2>/dev/null || echo 0)
    POR_ELIMINAR=$(sqlite3  "$DB" "SELECT COUNT(*) FROM gastos WHERE foto_url!='' AND sync_notion=1 AND (eliminada IS NULL OR eliminada=0);" 2>/dev/null || echo 0)

    row "📷 iCloud:"                   "$FOTOS fotos"
    row "   ├─ ✅ Analizadas:"         "$ANALIZADAS"
    row "   └─ ⏳ Pendientes:"         "$PENDIENTES"
    sep
    row "De las analizadas:"           ""
    row "   ├─ 💸 Posibles gastos:"    "$POS_GASTOS"
    row "   └─ ❌ No son gastos:"      "$NO_GASTOS"
    sep
    row "Notion ($SYNC_OK registros):" ""
    row "   ├─ 💸 Posibles gastos:"    "$NOTION_GASTOS"
    row "   ├─ ❌ No son gastos:"      "$NOTION_NO"
    row "   └─ 🔄 Pendientes sync:"    "$SYNC_PEND"
    sep
    row "Limpieza iCloud:"             ""
    row "   ├─ 🗑️  Eliminadas:"        "$ELIMINADAS"
    row "   └─ ⏳ Por eliminar:"       "$POR_ELIMINAR"
    printf "\n  ─────────────────────────────────────────\n"

    sleep 5
done
