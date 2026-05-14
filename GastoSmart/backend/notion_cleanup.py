"""Script de limpieza de Notion: archiva entradas sin ID y duplicados."""
import os, requests, time
from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, ".env"))

token = os.getenv('NOTION_TOKEN')
db_id = os.getenv('NOTION_DATABASE_ID')
headers = {'Authorization': f'Bearer {token}', 'Content-Type': 'application/json', 'Notion-Version': '2022-06-28'}

print('Descargando todas las entradas de Notion...')
all_pages = []
has_more, cursor = True, None
while has_more:
    payload = {'page_size': 100}
    if cursor: payload['start_cursor'] = cursor
    r = requests.post(f'https://api.notion.com/v1/databases/{db_id}/query', headers=headers, json=payload)
    data = r.json()
    all_pages.extend(data.get('results', []))
    has_more = data.get('has_more', False)
    cursor = data.get('next_cursor')
print(f'Total descargadas: {len(all_pages)}')

no_id, by_id = [], {}
for p in all_pages:
    props = p['properties']
    hash_id = ''
    for field in ['ID_Hash', 'ID']:
        rt = props.get(field, {}).get('rich_text', [])
        if rt:
            val = rt[0].get('text', {}).get('content', '')
            if val:
                hash_id = val
                break
    if not hash_id:
        no_id.append(p['id'])
    else:
        by_id.setdefault(hash_id, []).append((p['id'], p.get('last_edited_time', '')))

to_archive = no_id[:]
for pages in by_id.values():
    if len(pages) > 1:
        for pid, _ in sorted(pages, key=lambda x: x[1], reverse=True)[1:]:
            to_archive.append(pid)

print(f'Sin ID: {len(no_id)} | Duplicados extra: {len(to_archive)-len(no_id)} | Total a archivar: {len(to_archive)}')

ok, err = 0, 0
for i, pid in enumerate(to_archive):
    r = requests.patch(f'https://api.notion.com/v1/pages/{pid}', headers=headers, json={'archived': True})
    if r.status_code in [200, 201]:
        ok += 1
    else:
        err += 1
    if (i + 1) % 50 == 0:
        print(f'  {i+1}/{len(to_archive)} archivados...')
    time.sleep(0.34)

print(f'\nFINAL → Archivados: {ok} | Errores: {err}')
print(f'Entradas válidas en Notion: {len(by_id)}')
