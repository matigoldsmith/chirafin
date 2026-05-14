#!/usr/bin/env python3
"""Push chirafin_v2.html to GitHub Pages (matigoldsmith/chirafin)"""
import base64, json, subprocess, os, urllib.request

TOKEN_FILE = os.path.expanduser('/Users/mgoldsmithd/Scripts Claude AI/.github_token')

def bw_env():
    env = os.environ.copy()
    env["NODE_TLS_REJECT_UNAUTHORIZED"] = "0"
    return env

def bw_unlock():
    master = subprocess.run(["security","find-generic-password","-a","bitwarden","-s","bitwarden-master","-w"],capture_output=True,text=True).stdout.strip()
    result = subprocess.run(["bw","unlock",master,"--raw"],capture_output=True,text=True,env=bw_env())
    session = result.stdout.strip()
    if session: os.environ["BW_SESSION"] = session
    else: raise Exception("No se pudo desbloquear Bitwarden")

def bw_get(field, item_name):
    env = bw_env()
    result = subprocess.run(["bw","get",field,item_name],capture_output=True,text=True,env=env)
    if "Session key is invalid" in result.stderr or not result.stdout.strip():
        bw_unlock()
        result = subprocess.run(["bw","get",field,item_name],capture_output=True,text=True,env=bw_env())
    return result.stdout.strip()

def get_token():
    # 1. Try saved token file
    if os.path.exists(TOKEN_FILE):
        t = open(TOKEN_FILE).read().strip()
        if t: return t
    # 2. Try Bitwarden
    for name in ["github.com", "GitHub", "github"]:
        try:
            t = bw_get("password", name)
            if t: break
        except: t = None
    # 3. Manual input
    if not t:
        t = input("GitHub Personal Access Token: ").strip()
    # Save for future sessions
    if t:
        with open(TOKEN_FILE, 'w') as f: f.write(t)
        os.chmod(TOKEN_FILE, 0o600)
        print(f"Token guardado en {TOKEN_FILE}")
    return t

def gh_push(token, path_local, path_remote, message):
    with open(path_local, 'rb') as f:
        content_b64 = base64.b64encode(f.read()).decode()
    headers = {'Authorization': f'token {token}', 'Accept': 'application/vnd.github.v3+json', 'Content-Type': 'application/json'}
    url = f'https://api.github.com/repos/matigoldsmith/chirafin/contents/{path_remote}'
    try:
        req = urllib.request.Request(url, headers=headers)
        resp = json.loads(urllib.request.urlopen(req).read())
        sha = resp['sha']
    except: sha = None
    data = {'message': message, 'content': content_b64}
    if sha: data['sha'] = sha
    req2 = urllib.request.Request(url, data=json.dumps(data).encode(), method='PUT', headers=headers)
    resp2 = json.loads(urllib.request.urlopen(req2).read())
    print(f"  ✓ {path_remote} → {resp2['commit']['sha'][:8]}")

BASE = '/Users/mgoldsmithd/Scripts Claude AI'
token = get_token()
if not token: raise SystemExit("Sin token")
msg = "chirafin: TdC combined view with summary + pending first"
gh_push(token, f'{BASE}/chirafin_v2.html', 'index.html', msg)
gh_push(token, f'{BASE}/chirafin_v2.html', 'v2.html', msg)
print("¡Listo! https://matigoldsmith.github.io/chirafin/")
