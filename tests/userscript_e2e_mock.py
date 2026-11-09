import json, urllib.request, re, time
from playwright.sync_api import sync_playwright
SCRIPT=open('../sm-userscript/tampermonkey_taskbox_sync_tool.user.js',encoding='utf-8').read()
BASE='http://127.0.0.1:8085'; TOK='tok-123'
def api(path,method='GET',body=None,h=None):
    r=urllib.request.Request(BASE+path,data=json.dumps(body).encode() if body is not None else None,method=method,headers={'Content-Type':'application/json',**(h or {})})
    try:
        x=urllib.request.urlopen(r,timeout=30); return x.status,json.loads(x.read() or b'null')
    except urllib.error.HTTPError as e: return e.code,json.loads(e.read() or b'null')
# catalog + two approved boxes
rows=[{"id":f"{9000+i:06d}","name":f"T{i}","vi":f"Đồ {i}","type":"Obj","status":"ACTIVE","avail":5,"qty":5,"loc":"A01","sector":"Hanoi-GPI"} for i in range(150)]
print('sync',api('/api/catalog/sync','POST',{"objects":rows},{'X-Sync-Token':TOK})[0])
ids=[]
for lab in ('Box A1','Box B1'):
    s,b=api('/api/boxes','POST',{"module":"M8","label":lab,"theme":"x"}); ids.append(b['id'])
    for _ in range(60):
        st=api('/api/state')[1]; bx=[x for x in st['boxes'] if x['id']==b['id']][0]
        if bx['status']!='generating': break
        time.sleep(.2)
    print(lab, bx['status'], len(bx['items']), api(f"/api/boxes/{b['id']}/approve",'POST')[0])
def gm(opts):
    req=urllib.request.Request(opts['url'],data=(opts.get('data') or '').encode() or None,headers=opts.get('headers') or {},method=opts.get('method','GET'))
    try:
        r=urllib.request.urlopen(req,timeout=30); return {'status':r.status,'responseText':r.read().decode()}
    except urllib.error.HTTPError as e: return {'status':e.code,'responseText':e.read().decode()}
sm_posts=[]; sm_boxes=[]
def sm_boxes_route(route):
    req=route.request
    if req.method=='GET': return route.fulfill(status=200,content_type='application/json',body=json.dumps({"boxes":sm_boxes}))
    body=json.loads(req.post_data); sm_posts.append((req.url,body))
    if req.url.rstrip('/').endswith('/api/boxes'):
        n=f"SMB-{len(sm_boxes)+1}"; sm_boxes.append({"box_id":n,"title":body['title'],"status":"created","created_at":time.strftime('%Y-%m-%dT%H:%M:%S'),"contents":body['contents']})
        return route.fulfill(status=200,content_type='application/json',body=json.dumps({"box_id":n}))
    route.fulfill(status=200,content_type='application/json',body='{}')
with sync_playwright() as pw:
    b=pw.chromium.launch(); pg=b.new_context(viewport={'width':1500,'height':1000}).new_page()
    errs=[]; pg.on('pageerror',lambda e:errs.append(str(e))); pg.on('dialog',lambda d:(print('dialog:',d.message[:70].replace('\n',' ')),d.accept()))
    pg.route('https://sm.config.inc/api/boxes**',sm_boxes_route)
    pg.route('https://sm.config.inc/',lambda r:r.fulfill(status=200,content_type='text/html',body='<html><body><nav class="navbar"><strong>tester</strong></nav></body></html>'))
    pg.expose_function('__gm',gm); pg.goto('https://sm.config.inc/')
    pg.evaluate(f"localStorage.setItem('sm_bb_url','{BASE}/');localStorage.setItem('sm_bb_token','{TOK}');localStorage.setItem('sm_bb_auto','0')")
    pg.evaluate("""(src)=>{const G=(o)=>{window.__gm({url:o.url,headers:o.headers,data:o.data,method:o.method}).then(r=>o.onload(r))};new Function('GM_xmlhttpRequest','GM_setValue','GM_getValue',src)(G,()=>{},()=>undefined);}""",SCRIPT)
    pg.click('#sm-sync-floating-btn')
    def preview():
        pg.fill('#sm-sheet-url',BASE+'/'); pg.click('#sm-btn-preview'); pg.wait_for_function("!document.getElementById('sm-btn-preview').disabled",timeout=60000); time.sleep(.5)
        print('rows:',pg.inner_text('#sm-preview-tbody, tbody').replace('\n',' | ')[:300])
    preview()
    pg.click('#sm-btn-sync'); pg.wait_for_function("document.getElementById('sm-log-box').innerText.includes('HOÀN TẤT')",timeout=60000)
    print(pg.inner_text('#sm-log-box'))
    print('SM creates:',len([p for p in sm_posts if p[0].rstrip('/').endswith('/api/boxes')]))
    time.sleep(1.5); print('rows after re-scan:',pg.inner_text('tbody').replace('\n',' | ')[:300])
    # second sync must create nothing
    before=len(sm_posts); pg.click('#sm-btn-sync'); time.sleep(1)
    print('extra SM writes on 2nd click:',len(sm_posts)-before)
    print('pageerrors:',errs); b.close()
st=api('/api/state')[1]; print('app pushed:',[(x['label'],x['pushed'] and x['pushed']['sm_box_id']) for x in st['boxes']])
print('pending:',[x['builder']['label'] for x in api('/api/taskboxes?pending=true')[1]['boxes']])
