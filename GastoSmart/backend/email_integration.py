#!/usr/bin/env python3
"""
Integración de Emails a GastoSmart
===================================
Módulo para leer emails descargados por imap_watcher.py
y agregarlos al flujo de procesamiento de gs_auto_processor.py

Funciona en conjunto con:
- imap_watcher.py: descarga attachments
- gs_auto_processor.py: procesa fotos + emails
"""

import json
import os
from pathlib import Path

EMAIL_ATTACHMENTS_DIR = '/tmp/gs_email_attachments'
STAGING_FILE = '/tmp/gs_email_staging.json'

def get_email_files():
    """
    Lee /tmp/gs_email_staging.json y retorna lista de archivos
    con metadata de email (source_type='email').

    Validaciones:
    - Schema JSON correcto (emails[], attachments[], campos requeridos)
    - Archivo existe en disco antes de incluir
    - Logging de conteo

    Formato:
    [
      {
        "path": "/tmp/gs_email_attachments/abc123.pdf",
        "fname": "factura_2026-03-20.pdf",
        "hash": "abc123",
        "source_type": "email",
        "email_id": "Message-ID",
        "email_from": "vendor@example.com",
        "email_subject": "Factura Nro. 12345"
      },
      ...
    ]
    """
    email_files = []

    if not os.path.exists(STAGING_FILE):
        print(f"ℹ️  No emails encontrados: {STAGING_FILE} no existe")
        return email_files

    try:
        with open(STAGING_FILE) as f:
            staging = json.load(f)
    except json.JSONDecodeError as e:
        print(f"⚠️  JSON error en {STAGING_FILE}: {e}")
        return email_files
    except Exception as e:
        print(f"⚠️  Error leyendo {STAGING_FILE}: {e}")
        return email_files

    # Schema validation
    if not isinstance(staging, dict):
        print(f"⚠️  Staging file debe ser dict, obtuvo {type(staging)}")
        return email_files

    emails = staging.get('emails', [])
    if not isinstance(emails, list):
        print(f"⚠️  'emails' debe ser lista, obtuvo {type(emails)}")
        return email_files

    total_emails = len(emails)
    total_attachments = 0
    files_that_exist = 0

    for email in emails:
        # Validate email schema
        if not isinstance(email, dict):
            print(f"⚠️  Email debe ser dict, saltando")
            continue

        required_email_keys = ['email_id', 'from', 'subject', 'attachments']
        for key in required_email_keys:
            if key not in email:
                print(f"⚠️  Email falta key '{key}', saltando")
                continue

        attachments = email.get('attachments', [])
        if not isinstance(attachments, list):
            print(f"⚠️  Email 'attachments' debe ser lista, saltando")
            continue

        for attachment in attachments:
            # Validate attachment schema
            if not isinstance(attachment, dict):
                print(f"⚠️  Attachment debe ser dict, saltando")
                continue

            required_attach_keys = ['path', 'hash']
            if not all(key in attachment for key in required_attach_keys):
                print(f"⚠️  Attachment falta keys requeridas, saltando")
                continue

            attach_path = attachment['path']
            total_attachments += 1

            # Check if file exists before including
            if not os.path.exists(attach_path):
                print(f"⚠️  Attachment no existe: {attach_path}")
                continue

            files_that_exist += 1
            email_files.append({
                'path': attach_path,
                'fname': os.path.basename(attach_path),
                'hash': attachment['hash'],
                'source_type': 'email',
                'email_id': email['email_id'],
                'email_from': email['from'],
                'email_subject': email['subject']
            })

    print(f"ℹ️  Email stats: {total_emails} emails, {total_attachments} attachments, {files_that_exist} archivos existentes")
    return email_files

def clear_staging():
    """Limpia el archivo de staging después de procesar."""
    if os.path.exists(STAGING_FILE):
        try:
            os.remove(STAGING_FILE)
            print(f"✓ Staging limpiado: {STAGING_FILE}")
        except PermissionError as e:
            print(f"⚠️  Permiso denegado al limpiar staging: {e}")
        except Exception as e:
            print(f"⚠️  No se pudo limpiar staging: {e}")

def merge_sources(photo_files, email_files):
    """Mezcla fotos de iCloud + emails en una sola lista."""
    all_files = photo_files.copy()

    # Agregar source_type a fotos (por defecto 'photo')
    for f in all_files:
        if 'source_type' not in f:
            f['source_type'] = 'photo'

    # Agregar emails (ya tienen source_type='email')
    all_files.extend(email_files)

    return all_files
