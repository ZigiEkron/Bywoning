import json, requests, pathlib, re
BASE='https://specialprojects.capi24.com/Elections/v1'
s=requests.Session(); s.headers['User-Agent']='Netwerk24-election-api-audit/1.1'
out=pathlib.Path('sp_fast'); out.mkdir(exist_ok=True)

def get(name,path):
    url=BASE+path
    try:
        r=s.get(url,timeout=10)
        txt=r.text
        print('\n###',name,r.status_code,url,'\n',txt[:1500],flush=True)
        (out/f'{name}.txt').write_text(f'{r.status_code}\n{url}\n{txt}',encoding='utf-8')
        if r.ok:
            try:return r.json()
            except:return txt
    except Exception as e:
        print(name,'ERROR',repr(e),flush=True)
        (out/f'{name}.txt').write_text(repr(e),encoding='utf-8')
        return None

live=get('live','/ElectionMap/LGE')
print('LIVE=',json.dumps(live)[:3000],flush=True)
years=[]
if isinstance(live,list):
    for x in live:
        if isinstance(x,int) or (isinstance(x,str) and x.isdigit()): years.append(int(x))
        elif isinstance(x,dict):
            for v in x.values():
                if isinstance(v,int) and 1990<v<2100: years.append(v)
                if isinstance(v,str):
                    m=re.search(r'20\d{2}',v)
                    if m: years.append(int(m.group()))
if not years: years=[2021,2022,2023,2024,2025,2026]
for y in sorted(set(years)):
    d=get(f'prov5_{y}',f'/ElectionMap/LGE/{y}/5')
    blob=json.dumps(d,ensure_ascii=False) if d is not None else ''
    if '52103' in blob or 'KZN213' in blob or 'UMZUMBE' in blob.upper():
        print('FOUND UMZUMBE IN',y,flush=True)
        # derive likely municipality codes from objects mentioning target
        print(blob[:10000],flush=True)
        for m in ['213','5213','52103']:
            get(f'map_m_{y}_{m}',f'/ElectionMap/LGE/{y}/5/{m}')
            get(f'party_w_{y}_{m}',f'/ElectionParty/LGE/{y}/5/{m}/52103001')
            get(f'map_w_{y}_{m}',f'/ElectionMap/LGE/{y}/5/{m}/52103001')
