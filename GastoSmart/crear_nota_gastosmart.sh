#!/bin/bash
# Corre este script en tu Mac para crear la nota en la app Notas
osascript << 'EOF'
tell application "Notes"
    make new note at folder "Notes" with properties {name:"GastoSmart - Ejecución Manual", body:"GastoSmart - Ejecución Manual

REQUISITOS
• Python 3.9+
• pip install supabase python-dotenv requests --break-system-packages
• Archivo .env en la carpeta backend con: ANTHROPIC_API_KEY, GEMINI_API_KEY, NOTION_TOKEN, NOTION_DATABASE_ID, SUPABASE_URL, SUPABASE_KEY

FLUJO COMPLETO (1 solo comando)
cd \"/Users/mgoldsmithd/Scripts Claude AI/GastoSmart/backend\"
python3 gs_auto_processor.py

COMANDOS INDIVIDUALES
--step dedup     → limpieza de duplicados (seguro correr en cualquier momento)
--step fix_fx    → recalcula FX para registros sin tipo_cambio
--step reglas    → muestra reglas de aprendizaje activas

ARCHIVOS TEMPORALES
/tmp/gs_prepare.json      → fotos detectadas (prepare → analyze)
/tmp/gs_resultados.json   → resultados IA (analyze → upload)
/tmp/gastosmart_<uid>.db  → copia local BD

SCRIPTS RELACIONADOS
processor_v2.py           → análisis de imágenes Gemini/Haiku
notion_bridge.py          → sync con Notion
fx_helper.py              → conversión de monedas a CLP
run_24_7.sh               → ciclo automático cada hora
reset_all.py              → limpia todo (¡destructivo!)
consistency_checker.py    → verifica sync entre sistemas"}
end tell
EOF
echo "✓ Nota creada en app Notas"
