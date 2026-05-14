#!/usr/bin/env python3
"""
IMAP Watcher para GastoSmart
============================
Descarga facturas/boletas de Gmail via IMAP.
Soporta PDFs e imágenes (HEIC, JPG, PNG).

REQUISITOS
----------
1. En tu Google Account: generar Contraseña de Aplicación
   - Ir a: https://myaccount.google.com/apppasswords
   - Seleccionar "Correo" y "Otros (ingresa un nombre personalizado)"
   - Copiar la contraseña de 16 caracteres

2. Actualizar .env:
   EMAIL_USER=owa605@gmail.com
   EMAIL_PASSWORD=xxxx xxxx xxxx xxxx

3. En Gmail: crear etiqueta "GastoSmart" (opcional, por defecto busca en INBOX)

FLUJO
-----
1. Conecta a Gmail via IMAP
2. Busca emails con attachments (.pdf, .jpg, .png, .heic)
3. Descarga attachments a /tmp/gs_email_attachments/
4. Marca como leído (para no reprocesar)
5. Genera /tmp/gs_email_staging.json con metadata

USO
---
python3 imap_watcher.py
"""

import imaplib
import email
from email.header import decode_header
import os
import json
import sys
from pathlib import Path
from dotenv import dotenv_values
import hashlib
from datetime import datetime

# ── Config ──
ENV_PATH = os.path.join(os.path.dirname(__file__), '.env')
config = dotenv_values(ENV_PATH)

EMAIL_USER = config.get('EMAIL_USER', '').strip()
EMAIL_PASSWORD = config.get('EMAIL_PASSWORD', '').strip()
EMAIL_FOLDER = config.get('EMAIL_FOLDER', 'INBOX').strip()

# Remitentes permitidos (vacío = todos)
ALLOWED_SENDERS = [s.strip().lower() for s in config.get('EMAIL_ALLOWED_SENDERS', '').split(',') if s.strip()]
# Remitentes bloqueados (corredoras de bolsa, cartolas, etc.)
BLOCKED_SENDERS = [s.strip().lower() for s in config.get('EMAIL_BLOCKED_SENDERS', '').split(',') if s.strip()]

EMAIL_ATTACHMENTS_DIR = '/tmp/gs_email_attachments'
STAGING_FILE = '/tmp/gs_email_staging.json'

# Crear directorio si no existe
Path(EMAIL_ATTACHMENTS_DIR).mkdir(parents=True, exist_ok=True)

ALLOWED_EXTENSIONS = {'.pdf', '.jpg', '.jpeg', '.png', '.heic', '.webp'}

def sha256(data: bytes) -> str:
    """Calcula SHA256 de datos."""
    return hashlib.sha256(data).hexdigest()

def connect_imap():
    """Conecta a Gmail via IMAP."""
    if not EMAIL_USER or not EMAIL_PASSWORD:
        print("❌ ERROR: EMAIL_USER y EMAIL_PASSWORD no configurados en .env")
        print("   Genera contraseña de aplicación en: https://myaccount.google.com/apppasswords")
        return None

    try:
        imap = imaplib.IMAP4_SSL('imap.gmail.com', 993)
        imap.login(EMAIL_USER, EMAIL_PASSWORD)
        print(f"✅ Conectado a Gmail ({EMAIL_USER})")
        return imap
    except imaplib.IMAP4.error as e:
        print(f"❌ Error de login IMAP: {e}")
        print("   Revisa que EMAIL_PASSWORD sea una contraseña de aplicación (16 caracteres)")
        return None

def fetch_emails_with_attachments(imap, folder=EMAIL_FOLDER):
    """Busca emails con attachments en la carpeta especificada."""
    try:
        imap.select(folder, readonly=True)  # readonly: no modifica estado de emails
        print(f"📂 Buscando en carpeta: {folder}")
    except imaplib.IMAP4.error as e:
        print(f"⚠️  Carpeta '{folder}' no encontrada. Usando INBOX")
        imap.select('INBOX', readonly=True)

    # Buscar emails desde fecha configurable (default: 7 días para no colgarse)
    from datetime import datetime, timedelta
    since_override = config.get('EMAIL_SINCE_DATE', '').strip()
    since_days = int(config.get('EMAIL_SINCE_DAYS', '7'))
    if since_override:
        since_date = datetime.strptime(since_override, '%Y-%m-%d').strftime('%d-%b-%Y')
    else:
        since_date = (datetime.now() - timedelta(days=since_days)).strftime('%d-%b-%Y')

    if ALLOWED_SENDERS:
        all_ids = set()
        for sender in ALLOWED_SENDERS:
            _, nums = imap.search(None, f'(SINCE {since_date} FROM "{sender}")')
            all_ids.update(nums[0].split())
        message_numbers = [b' '.join(sorted(all_ids))]
        print(f"🔍 Filtro remitentes: {', '.join(ALLOWED_SENDERS)}")
    else:
        _, message_numbers = imap.search(None, f'(SINCE {since_date})')
    email_ids = message_numbers[0].split()

    # Limitar a los 50 más recientes para evitar timeouts
    MAX_EMAILS = int(config.get('EMAIL_MAX_FETCH', '50'))
    if len(email_ids) > MAX_EMAILS:
        print(f"⚠️  {len(email_ids)} emails encontrados — procesando solo los {MAX_EMAILS} más recientes")
        email_ids = email_ids[-MAX_EMAILS:]

    if not email_ids:
        print(f"✓ No hay emails (últimos {since_days} días)")
        return []

    print(f"📬 Procesando {len(email_ids)} emails desde {since_date}")

    emails_with_files = []

    for email_id in email_ids:
        _, msg_data = imap.fetch(email_id, '(RFC822)')
        raw_email = msg_data[0][1] if isinstance(msg_data[0], tuple) else msg_data[0]
        msg = email.message_from_bytes(raw_email)

        # Metadata del email
        subject = decode_header(msg.get('Subject', 'Sin asunto'))[0][0]
        if isinstance(subject, bytes):
            subject = subject.decode('utf-8', errors='ignore')

        from_addr = msg.get('From', 'desconocido')
        date_str = msg.get('Date', '')
        msg_id = msg.get('Message-ID', f"email_{email_id.decode()}")

        # Saltar remitentes bloqueados
        if BLOCKED_SENDERS and any(b in from_addr.lower() for b in BLOCKED_SENDERS):
            continue

        # Extraer attachments
        attachments = []
        for part in msg.walk():
            if part.get_content_disposition() == 'attachment':
                filename = part.get_filename()
                if not filename:
                    continue

                # Validar extensión
                file_ext = os.path.splitext(filename)[1].lower()
                if file_ext not in ALLOWED_EXTENSIONS:
                    continue

                try:
                    file_data = part.get_payload(decode=True)
                    file_hash = sha256(file_data)

                    # Guardar archivo
                    output_path = os.path.join(EMAIL_ATTACHMENTS_DIR, f"{file_hash}{file_ext}")

                    # Evitar sobrescribir si ya existe
                    if not os.path.exists(output_path):
                        with open(output_path, 'wb') as f:
                            f.write(file_data)
                        print(f"   📄 Descargado: {filename} → {output_path}")

                    attachments.append({
                        'filename': filename,
                        'path': output_path,
                        'hash': file_hash,
                        'size': len(file_data)
                    })

                except Exception as e:
                    print(f"   ⚠️  Error descargando {filename}: {e}")

        if attachments:
            emails_with_files.append({
                'email_id': msg_id,
                'subject': subject,
                'from': from_addr,
                'date': date_str,
                'attachments': attachments
            })

            # No marcamos como leído — la deduplicación es por hash del archivo

    return emails_with_files

def save_staging(emails_data):
    """Guarda metadata de emails en /tmp/gs_email_staging.json"""
    if not emails_data:
        print("✓ No hay archivos para procesar")
        return

    staging = {
        'timestamp': datetime.now().isoformat(),
        'email_count': len(emails_data),
        'emails': emails_data
    }

    with open(STAGING_FILE, 'w') as f:
        json.dump(staging, f, indent=2, default=str)

    print(f"✅ Datos guardados en: {STAGING_FILE}")
    print(f"📊 {len(emails_data)} email(s) con {sum(len(e['attachments']) for e in emails_data)} archivo(s) total")

def main():
    print("\n🔍 GastoSmart IMAP Watcher")
    print("=" * 50)

    imap = connect_imap()
    if not imap:
        sys.exit(1)

    try:
        emails = fetch_emails_with_attachments(imap)
        save_staging(emails)
    finally:
        imap.close()
        imap.logout()

    print("\n✅ IMAP Watcher completado")

if __name__ == '__main__':
    main()
