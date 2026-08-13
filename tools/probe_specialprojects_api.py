import json, requests, pathlib
BASE='https://specialprojects.capi24.com/Elections/v1'
out=pathlib.Path('sp_probe'); out.mkdir(exist_ok=True)
s=requests.Session(); s.headers['User-Agent']='Netwerk24-election-api-audit/1.0'

def get(name,url):
    try:
        r=s.get(url,timeout=30)
        body=r.text
        print(name,r.status_code,r.headers.get('content-type'),body[:500])
        (out/f'{name}.txt').write_text(f'STATUS {r.status_code}\nURL {r.url}\nETAG {r.headers.get("etag")}\nCONTENT-TYPE {r.headers.get("content-type")}\n\n{body}',encoding='utf-8')
        if r.ok:
            try: return r.json()
            except: return None
    except Exception as e:
        print(name,'EXC',repr(e)); (out/f'{name}.txt').write_text(repr(e),encoding='utf-8')

live=get('live_lge',f'{BASE}/ElectionMap/LGE')
# Known live/baseline years + plausible by-election year containers
for year in [2021,2022,2023,2024,2025,2026]:
    prov=get(f'map_{year}_prov5',f'{BASE}/ElectionMap/LGE/{year}/5')
    party=get(f'party_{year}_prov5',f'{BASE}/ElectionParty/LGE/{year}/5')
    # Candidate municipality path forms derived from KZN213 / ward 52103001
    for m in ['213','5213','52103','KZN213']:
        get(f'map_{year}_m_{m}',f'{BASE}/ElectionMap/LGE/{year}/5/{m}')
        get(f'party_{year}_m_{m}',f'{BASE}/ElectionParty/LGE/{year}/5/{m}')
        get(f'party_{year}_w_{m}',f'{BASE}/ElectionParty/LGE/{year}/5/{m}/52103001')
        get(f'map_{year}_w_{m}',f'{BASE}/ElectionMap/LGE/{year}/5/{m}/52103001')
get('breadcrumb_2021_5213',f'{BASE}/ElectionDetail/breadcrumb/LGE/2021/5/5213')
