#!/bin/bash
# Activar soporte de emails en GastoSmart

set -e  # Salir en error

BACKEND="$(dirname "$(cd "$(dirname "$0")" && pwd)")/backend"
cd "$BACKEND"

echo "🚀 Activando soporte de emails en GastoSmart"
echo "=============================================="
echo ""

# Test 1: Verificar .env
echo "📋 Paso 1: Verificando .env..."
python3 test_email_setup.py
echo ""

# Test 2: Migración de BD
echo "🗄️  Paso 2: Ejecutando migración de BD..."
python3 migrate_email_support.py
echo ""

# Test 3: Probar IMAP
echo "🔌 Paso 3: Intentando conexión IMAP..."
python3 imap_watcher.py
echo ""

echo "✅ Setup completado"
echo ""
echo "Próximos pasos:"
echo "1. Verifica que haya archivos en: /tmp/gs_email_attachments/"
echo "2. Ejecuta el ciclo completo: bash run_24_7.sh"
echo "3. O procesa manualmente: python3 gs_auto_processor.py --step analyze"
