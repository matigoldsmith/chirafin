# Setup: Integración de Emails (Facturas) a GastoSmart

## Paso 1: Crear Contraseña de Aplicación en Google

1. Abre en tu navegador: https://myaccount.google.com/apppasswords
   - Si te pide login, usa: **owa605@gmail.com**

2. En la dropdown "Selecciona una aplicación", elige: **Correo**

3. En la dropdown "Selecciona un dispositivo", elige: **Otros (Windows, Mac, Linux)** → escribe "GastoSmart IMAP"

4. Google te mostrará una **contraseña de 16 caracteres** (formato: `xxxx xxxx xxxx xxxx`)

5. **Copia esta contraseña** (es la única vez que la ves)

## Paso 2: Actualizar el .env

Abre el archivo: `/Sessions Claude AI--GastoSmart/backend/.env`

Agrega o reemplaza estas líneas al final:

```
EMAIL_USER=owa605@gmail.com
EMAIL_PASSWORD=xxxx xxxx xxxx xxxx
EMAIL_FOLDER=INBOX
```

Reemplaza `xxxx xxxx xxxx xxxx` con la contraseña que copiaste en Paso 1.

**Ejemplo completo:**
```
EMAIL_USER=owa605@gmail.com
EMAIL_PASSWORD=abcd efgh ijkl mnop
EMAIL_FOLDER=INBOX
```

## Paso 3: Ejecutar Migraciones

```bash
cd "/Sessions Claude AI--GastoSmart/backend"

# Agregar columnas de email a la BD
python3 migrate_email_support.py

# Probar conexión IMAP
python3 imap_watcher.py
```

Si ves "✅ Conectado a Gmail", funcionó correctamente.

## Paso 4: Integrar al Flujo Automático

En tu `run_24_7.sh`, agrega antes de `--step prepare`:

```bash
# Descargar facturas de Gmail
python3 "$BACKEND/imap_watcher.py" 2>/dev/null || true
```

Ejemplo completo:

```bash
BACKEND=$(python3 -c "import glob; print(glob.glob('/sessions/*/mnt/Scripts Claude AI--GastoSmart/backend')[0])")

# Paso 0: aprender cambios Notion
python3 -W ignore "$BACKEND/notion_sync_checker.py" 2>/dev/null || true

# Paso 0.5: NUEVO - descargar facturas de Gmail
python3 "$BACKEND/imap_watcher.py" 2>/dev/null || true

# Paso 1: limpiar UUID, deduplicar, listar fotos nuevas
python3 "$BACKEND/gs_auto_processor.py" --step prepare
...
```

## Paso 5: Monitoreo

Ver si hay emails nuevos:
```bash
ls -la /tmp/gs_email_attachments/
cat /tmp/gs_email_staging.json
```

Ver que se procesen correctamente:
```bash
python3 gs_auto_processor.py --step analyze
```

## FAQ

**P: "Se restringió el acceso a un servicio"**
A: La cuenta de Gmail tiene restricciones de GCP. Usa la contraseña de aplicación (no tu contraseña de Gmail).

**P: "Error de login IMAP"**
A: Revisa que:
- EMAIL_USER sea exactamente: `owa605@gmail.com`
- EMAIL_PASSWORD sea la contraseña de 16 caracteres (con espacios)
- La contraseña esté entre comillas en el .env

**P: No ve emails antiguos**
A: `imap_watcher.py` solo descarga emails no leídos. Marca como no leído en Gmail si quieres reprocesar.

**P: ¿Qué tipo de archivos soporta?**
A: PDF, JPG, PNG, HEIC, WEBP

**P: ¿Se eliminan los emails?**
A: No. Se marcan como leído. Los attachments se descargan localmente.
