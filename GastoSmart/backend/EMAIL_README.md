# 📧 GastoSmart + Emails (Facturas)

## Resumen

Sistema automático para procesar **facturas y boletas vía email** usando la misma arquitectura de GastoSmart.

**Fuentes:**
- ✅ Fotos del iPhone (iCloud) - existente
- ✅ Attachments de Gmail - NUEVO

**Flujo:**
```
Gmail IMAP → imap_watcher.py → /tmp/gs_email_attachments/
                                 ↓
            gs_auto_processor --step prepare/analyze/upload/sync
                                 ↓
            BD local → Supabase → Notion
```

---

## Configuración (5 minutos)

### 1️⃣ Contraseña de Aplicación Google

1. Ve a: https://myaccount.google.com/apppasswords
2. Selecciona: **Correo** → **Otros (GastoSmart IMAP)**
3. Copia la contraseña de 16 caracteres

### 2️⃣ Actualizar .env

Abre: `/Sessions Claude AI--GastoSmart/backend/.env`

Agrega:
```
EMAIL_USER=owa605@gmail.com
EMAIL_PASSWORD=xxxx xxxx xxxx xxxx
EMAIL_FOLDER=INBOX
```

### 3️⃣ Ejecutar Setup

```bash
cd "/Sessions Claude AI--GastoSmart/backend"
bash activate_email.sh
```

---

## Archivos Nuevos

| Archivo | Propósito |
|---------|----------|
| `imap_watcher.py` | Descarga attachments de Gmail |
| `email_integration.py` | Integración al flujo prepare |
| `migrate_email_support.py` | Agrega columnas a BD |
| `test_email_setup.py` | Valida configuración |
| `activate_email.sh` | Script setup automático |
| `SETUP_EMAIL.md` | Instrucciones detalladas |

---

## Cambios a Archivos Existentes

### gs_auto_processor.py
- ✅ Importa `email_integration`
- ✅ `step_prepare`: integra emails + fotos
- ✅ `step_upload`: guarda source_type, email_id, etc.

### gastosmart_v1.db
- ✅ Columnas nuevas:
  - `source_type` (photo | email)
  - `email_id` (unique)
  - `email_from`
  - `email_subject`

---

## Uso

### Opción 1: Manual
```bash
python3 imap_watcher.py              # Descargar emails
python3 gs_auto_processor.py --step prepare
python3 gs_auto_processor.py --step analyze
python3 gs_auto_processor.py --step upload
python3 gs_auto_processor.py --step sync
```

### Opción 2: Automático (24/7)
Modifica `run_24_7.sh`:
```bash
# Agregar al inicio (antes de --step prepare):
python3 "$BACKEND/imap_watcher.py" 2>/dev/null || true
```

---

## Información Técnica

**Formatos soportados:** PDF, JPG, PNG, HEIC, WEBP

**Análisis IA:** Usa el mismo processor_v2.py (Gemini/Haiku)

**Deduplicación:** Automática por hash para fotos + emails

**Notion:** Sincroniza con campo `source_type` para filtros

**Supabase:** PDFs se suben como "documentos" igual que fotos

---

## Troubleshooting

| Problema | Solución |
|----------|----------|
| "Se restringió el acceso" | Usar contraseña de aplicación (no Gmail password) |
| "Error de login IMAP" | Verificar EMAIL_USER y EMAIL_PASSWORD en .env |
| No ve emails antiguos | Solo descarga no leídos; marca como no leído en Gmail |
| No procesa PDFs | Verifica: `ls /tmp/gs_email_attachments/` |

---

## FAQ

**P: ¿Los emails se eliminan después de procesar?**
A: No. Se marcan como "leído" en Gmail. Los archivos se guardan localmente.

**P: ¿Puedo cambiar la carpeta de búsqueda?**
A: Sí, modifica `EMAIL_FOLDER` en .env (ej: `GastoSmart`, `Facturas`)

**P: ¿Se pueden usar múltiples cuentas de correo?**
A: Actualmente solo 1. Contacta para extensión.

**P: ¿Funciona con Outlook, Yahoo, etc?**
A: Sí, si soportan IMAP. Modifica `imap.gmail.com` en imap_watcher.py

---

## Logs

Ver qué está sucediendo:
```bash
# Archivos de staging
cat /tmp/gs_email_staging.json

# Attachments descargados
ls /tmp/gs_email_attachments/

# Resultados análisis
cat /tmp/gs_resultados.json | grep email_from

# BD local
sqlite3 /tmp/gastosmart_*.db "SELECT source_type, email_from, fecha, comercio FROM gastos WHERE source_type='email'"
```
