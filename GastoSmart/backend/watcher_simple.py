#!/usr/bin/env python3
"""Watcher simplificado - ejecuta el proceso_file directamente sin sanity check"""

import os
import sys

# Cambiar a directorio correcto
os.chdir(os.path.dirname(os.path.abspath(__file__)))

# Importar desde watcher
from watcher import *

if __name__ == "__main__":
    log_info("SYSTEM", "GastoSmart iniciando (modo simplificado)...")
    log_info("SYSTEM", f"Escaneando: {ICLOUD_INPUT_PATH}")
    
    # Procesar archivos existentes
    files = [f for f in os.listdir(ICLOUD_INPUT_PATH) if not f.startswith('.') and f.lower().endswith(('.png', '.jpg', '.jpeg', '.heic'))]
    if files:
        log_info("SYSTEM", f"Encontrados {len(files)} archivos pendientes")
        os.environ["GS_MASS_UPLOAD"] = "1"
        for idx, f in enumerate(files):
            file_path = os.path.join(ICLOUD_INPUT_PATH, f)
            if os.path.exists(file_path):
                try:
                    process_file(file_path, idx, len(files))
                except Exception as e:
                    log_info("ERROR", f"{f}: {str(e)[:100]}")
                    continue
    
    log_info("SYSTEM", "Ciclo inicial completado")
