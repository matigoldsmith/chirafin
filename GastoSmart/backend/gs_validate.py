#!/usr/bin/env python3
"""
GastoSmart — Validador de gs_resultados.json
=============================================
Corre antes de --step upload para detectar problemas en el JSON generado por Claude vision.
Aborta si hay errores críticos. Imprime advertencias para cosas revisables.

Uso:
    python3 gs_validate.py                        # valida gs_resultados.json estándar
    python3 gs_validate.py --file /ruta/a.json    # valida un archivo específico
    python3 gs_validate.py --fix                  # intenta corregir problemas y sobreescribe

Salida:
    Exit 0 → todo OK (o solo advertencias)
    Exit 1 → errores críticos encontrados
"""

import json
import os
import sys
import argparse
import re
from datetime import datetime

# ── Configuración ──────────────────────────────────────────────────────────────
BACKEND     = os.path.dirname(os.path.abspath(__file__))
RESULTADOS  = os.path.join(BACKEND, 'gs_resultados.json')

CATEGORIAS_VALIDAS = {
    'Viajes',
    'Representación',
    'Supermercado / Insumos',
    'Cuentas',
    'Servicios Profesionales',
    'Gastos Comunes',
    'Inversiones',
    'Software',
    'Otros',
}

MONEDAS_VALIDAS = {'CLP', 'USD', 'EUR', 'BRL', 'ARS', 'UF', 'GBP', 'JPY'}
MONEDAS_ENTERAS = {'CLP', 'BRL'}   # nunca deben tener decimales

CAMPOS_REQUERIDOS = ['path', 'fname', 'hash', 'es_recibo']
CAMPOS_RECIBO     = ['fecha', 'comercio', 'monto', 'moneda', 'categoria_sugerida']


def validar(resultados: list, fix: bool = False) -> tuple[list, list, bool]:
    """
    Valida la lista de resultados.
    Retorna (errores_criticos, advertencias, fue_modificado).
    Si fix=True intenta corregir lo que puede.
    """
    errores   = []   # bloquean el upload
    warnings  = []   # no bloquean, pero se reportan
    modified  = False

    for i, item in enumerate(resultados):
        ctx = f"[{i}] {item.get('fname', '?')}"

        # ── 1. Campos requeridos siempre ──────────────────────────────────────
        for campo in CAMPOS_REQUERIDOS:
            if campo not in item:
                errores.append(f"{ctx}: falta campo '{campo}'")

        if 'es_recibo' not in item:
            continue  # sin es_recibo no podemos seguir

        es_recibo = item.get('es_recibo')

        # ── 2. No-recibos: no necesitamos más ─────────────────────────────────
        if not es_recibo:
            # Limpiar campos que no aplican (evita confusión en BD)
            for campo in CAMPOS_RECIBO:
                val = item.get(campo)
                if val and val not in (0, '', None):
                    warnings.append(f"{ctx}: es_recibo=false pero tiene {campo}='{val}' — se ignorará")
            continue

        # ── 3. Recibos: validar campos obligatorios ───────────────────────────
        for campo in CAMPOS_RECIBO:
            if campo not in item or item[campo] in (None, '', 0):
                if campo == 'monto':
                    errores.append(f"{ctx}: monto faltante o 0 en recibo")
                elif campo == 'comercio':
                    warnings.append(f"{ctx}: comercio vacío — se registrará como 'Desconocido'")
                    if fix:
                        item['comercio'] = 'Desconocido'
                        modified = True
                elif campo == 'fecha':
                    warnings.append(f"{ctx}: fecha vacía — se dejará null (aceptable)")
                elif campo == 'moneda':
                    warnings.append(f"{ctx}: moneda vacía — se asumirá CLP")
                    if fix:
                        item['moneda'] = 'CLP'
                        modified = True

        # ── 4. Moneda ─────────────────────────────────────────────────────────
        moneda = (item.get('moneda') or 'CLP').upper()
        if moneda not in MONEDAS_VALIDAS:
            warnings.append(f"{ctx}: moneda '{moneda}' no reconocida (se aceptará igual)")

        # ── 5. Monto ─────────────────────────────────────────────────────────
        monto = item.get('monto')
        if monto is not None:
            try:
                monto_f = float(monto)
            except (TypeError, ValueError):
                errores.append(f"{ctx}: monto '{monto}' no es número")
                continue

            # CLP y BRL no deben tener decimales
            if moneda in MONEDAS_ENTERAS and monto_f != int(monto_f):
                warnings.append(f"{ctx}: {moneda} tiene decimales ({monto_f}) — se forzará entero")
                if fix:
                    item['monto'] = int(round(monto_f))
                    modified = True

            # Monto negativo
            if monto_f < 0:
                errores.append(f"{ctx}: monto negativo ({monto_f})")

            # Monto 0 en recibo
            if monto_f == 0:
                warnings.append(f"{ctx}: monto=0 en recibo — ¿imagen ilegible?")

        # ── 6. Fecha ──────────────────────────────────────────────────────────
        fecha = item.get('fecha')
        if fecha:
            if not re.match(r'^\d{4}-\d{2}-\d{2}$', str(fecha)):
                errores.append(f"{ctx}: fecha '{fecha}' no es formato YYYY-MM-DD")
            else:
                try:
                    dt = datetime.strptime(fecha, '%Y-%m-%d')
                    hoy = datetime.today()
                    # Fecha futura sospechosa (más de 7 días)
                    if (dt - hoy).days > 7:
                        warnings.append(f"{ctx}: fecha '{fecha}' es futura — ¿error de OCR?")
                    # Fecha muy antigua (antes de 2020)
                    if dt.year < 2020:
                        warnings.append(f"{ctx}: fecha '{fecha}' es anterior a 2020 — verificar")
                except ValueError:
                    errores.append(f"{ctx}: fecha '{fecha}' inválida")

        # ── 7. Categoría ──────────────────────────────────────────────────────
        cat = item.get('categoria_sugerida', '')
        if cat and cat not in CATEGORIAS_VALIDAS:
            warnings.append(f"{ctx}: categoría '{cat}' no está en la lista fija → se usará 'Otros'")
            if fix:
                item['categoria_sugerida'] = 'Otros'
                modified = True

        # ── 8. Hash ───────────────────────────────────────────────────────────
        h = item.get('hash', '')
        if not re.match(r'^[a-f0-9]{64}$', h):
            errores.append(f"{ctx}: hash '{h[:20]}...' no parece SHA256 válido")

        # ── 9. Path existe en disco ──────────────────────────────────────────
        path = item.get('path', '')
        if path and not os.path.exists(path):
            errores.append(f"{ctx}: archivo no existe en disco: {path}")

    # ── 10. Duplicados de hash dentro del propio JSON ─────────────────────────
    hashes = [item.get('hash') for item in resultados if item.get('hash')]
    vistos = {}
    for i, h in enumerate(hashes):
        if h in vistos:
            warnings.append(f"Hash duplicado en JSON: {h[:16]}... (índices {vistos[h]} y {i})")
        else:
            vistos[h] = i

    return errores, warnings, modified


def main():
    parser = argparse.ArgumentParser(description='Validar gs_resultados.json antes de upload')
    parser.add_argument('--file', default=RESULTADOS, help='Ruta al JSON a validar')
    parser.add_argument('--fix',  action='store_true',   help='Corregir automáticamente lo que se pueda')
    args = parser.parse_args()

    if not os.path.exists(args.file):
        print(f"❌ Archivo no encontrado: {args.file}")
        sys.exit(1)

    try:
        with open(args.file) as f:
            resultados = json.load(f)
    except json.JSONDecodeError as e:
        print(f"❌ JSON inválido: {e}")
        sys.exit(1)

    if not isinstance(resultados, list):
        print(f"❌ El JSON debe ser una lista, no {type(resultados).__name__}")
        sys.exit(1)

    print(f"\n{'='*60}")
    print(f"GS VALIDATE — {os.path.basename(args.file)}")
    print(f"{'='*60}")
    print(f"  Entradas: {len(resultados)}")
    recibos   = sum(1 for r in resultados if r.get('es_recibo'))
    no_recibos = len(resultados) - recibos
    print(f"  Recibos: {recibos}  |  No-recibos: {no_recibos}")
    print()

    errores, warnings, modified = validar(resultados, fix=args.fix)

    if warnings:
        print(f"⚠️  ADVERTENCIAS ({len(warnings)}):")
        for w in warnings:
            print(f"   · {w}")
        print()

    if errores:
        print(f"❌ ERRORES CRÍTICOS ({len(errores)}) — upload bloqueado:")
        for e in errores:
            print(f"   · {e}")
        print()
        if args.fix and modified:
            with open(args.file, 'w') as f:
                json.dump(resultados, f, ensure_ascii=False, indent=2)
            print(f"💾 Correcciones guardadas en {args.file}")
            print("   Re-corre sin --fix para verificar que no quedan errores.")
        sys.exit(1)

    if modified and args.fix:
        with open(args.file, 'w') as f:
            json.dump(resultados, f, ensure_ascii=False, indent=2)
        print(f"💾 Correcciones guardadas en {args.file}")

    print(f"✅ Validación OK{' (con correcciones aplicadas)' if modified else ''}")
    print(f"{'='*60}\n")
    sys.exit(0)


if __name__ == '__main__':
    main()
