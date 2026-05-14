#!/usr/bin/env python3
"""Commit + push de saldos.py y CLAUDE.md al repo chirafin en GitHub."""
import subprocess, os, datetime, sys

BASE    = '/Users/mgoldsmithd/Scripts Claude AI'
TOKEN_F = os.path.join(BASE, '.github_token')
REPO    = 'matigoldsmith/chirafin'

def get_token():
    if os.path.exists(TOKEN_F):
        t = open(TOKEN_F).read().strip()
        if t: return t
    t = input("GitHub Personal Access Token: ").strip()
    if t:
        with open(TOKEN_F, 'w') as f: f.write(t)
        os.chmod(TOKEN_F, 0o600)
    return t

def run(cmd, **kw):
    return subprocess.run(cmd, cwd=BASE, capture_output=True, text=True, **kw)

token = get_token()
if not token:
    print("Sin token. Abortando."); sys.exit(1)

# Configurar remote con token
remote_url = f'https://{token}@github.com/{REPO}.git'
run(['git', 'remote', 'set-url', 'origin', remote_url])

# Stage cambios relevantes
run(['git', 'add', 'saldos.py', 'CLAUDE.md', '.gitignore'])

# Verificar si hay algo que commitear
status = run(['git', 'status', '--porcelain'])
if status.stdout.strip():
    date_str = datetime.datetime.now().strftime('%d %b %H:%M')
    msg = f"chirafin: backup {date_str}"
    result = run(['git', 'commit', '-m', msg])
    if result.returncode != 0:
        print(f"Error al commitear:\n{result.stderr}")
        sys.exit(1)
    print(f"  Commit: {msg}")
else:
    print("  Sin cambios nuevos — solo push.")

# Push
print("  Pushing...")
result = run(['git', 'push', 'origin', 'master'], timeout=60)
if result.returncode == 0:
    print("✓ Push exitoso → https://github.com/matigoldsmith/chirafin")
else:
    print(f"✗ Push falló:\n{result.stderr}")
    sys.exit(1)
