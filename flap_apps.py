"""Public Flap app-directory polling. Never connects wallets or opens app targets."""
import json
import os
import re
import threading
import time
import urllib.parse
import urllib.request
from runtime_paths import STATE_ROOT

API='https://bnb.taxed.fun/v1/apps'
DIRECTORY='https://flap.sh/apps?tab=all&lang=zh'
CACHE=STATE_ROOT/'flap-apps-cache.json'
LOCK=threading.Lock()
POLL_SECONDS=60
STATUS={'items':[],'updated':None,'error':'等待首次目录检查','initialized':False,'last_new_count':0}
SLUG=re.compile(r'[a-z0-9][a-z0-9_-]{0,127}\Z')

def enabled():return os.environ.get('FLAP_APPS_ENABLED','true').lower()=='true'
def push_enabled():return os.environ.get('FLAP_APPS_PUSH_ENABLED','true').lower()=='true'
def status():
    with LOCK:return {**STATUS,'enabled':enabled(),'push_enabled':push_enabled(),'poll_seconds':POLL_SECONDS,'source':DIRECTORY}

def localized(value,fallback=''):
    if isinstance(value,dict):value=value.get('zh') or value.get('en') or fallback
    return value.strip() if isinstance(value,str) else fallback

def parse_page(data):
    if not isinstance(data,dict) or type(data.get('total')) is not int or not 0<=data['total']<=2400 or not isinstance(data.get('items'),list):
        raise ValueError('Flap目录结构或数量异常')
    cursor=data.get('nextCursor')
    if cursor is not None and (not isinstance(cursor,str) or not 0<len(cursor)<=2048):raise ValueError('Flap目录分页游标异常')
    result=[]
    for raw in data['items']:
        if not isinstance(raw,dict) or not isinstance(raw.get('slug'),str) or not SLUG.fullmatch(raw['slug']):raise ValueError('Flap目录项目标识异常')
        slug=raw['slug']
        app_id=raw.get('appId')
        if app_id is not None and (not isinstance(app_id,str) or not 0<len(app_id)<=200):raise ValueError('Flap目录appId异常')
        result.append({'slug':slug,'app_id':app_id,'name':localized(raw.get('displayTitle'),localized(raw.get('name'),slug))[:200],
                       'description':localized(raw.get('description'))[:1500],'url':'https://flap.sh/apps/'+slug+'?lang=zh',
                       # Match the site's explicitly hidden directory entry.
                       'visible':raw.get('listed') is not False and slug!='stonkspad' and app_id!='vaultui_stonkspad_01M4JCJNZV07N8VH66ZPKQWDFN'})
    return result,data['total'],cursor

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):return None

def get_page(url):
    req=urllib.request.Request(url,headers={'User-Agent':'Mozilla/5.0','Origin':'https://flap.sh','Referer':'https://flap.sh/','Accept':'application/json'})
    with urllib.request.build_opener(NoRedirect()).open(req,timeout=15) as response:body=response.read(1_000_001)
    if len(body)>1_000_000:raise ValueError('Flap目录响应过大')
    return json.loads(body)

def fetch_directory():
    items={};seen_cursors=set();cursor=None;expected=None;start=time.monotonic()
    for page in range(100):
        if time.monotonic()-start>45:raise ValueError('Flap目录扫描超时，保留上次数据')
        query={'tab':'all','limit':'24'}
        if cursor is not None:query['cursor']=cursor
        rows,total,next_cursor=parse_page(get_page(API+'?'+urllib.parse.urlencode(query)))
        if expected is not None and total!=expected:raise ValueError('Flap目录扫描中数量变化，等待下轮')
        expected=total
        for item in rows:items[item['slug']]=item
        if next_cursor is None:
            if len(items)!=expected:raise ValueError('Flap目录分页不完整，保留上次数据')
            return [x for x in items.values() if x['visible']]
        if next_cursor in seen_cursors:raise ValueError('Flap目录分页重复，保留上次数据')
        seen_cursors.add(next_cursor);cursor=next_cursor
    raise ValueError('Flap目录超过分页上限')

def identity_keys(item):
    return ['slug:'+item['slug']]+(['app:'+item['app_id']] if item.get('app_id') else [])

def advance(items,cache,now):
    seen=set(cache.get('seen',[]));first=dict(cache.get('first_seen',{}));initialized=cache.get('initialized') is True
    fresh=[];shown=[]
    for item in items:
        keys=identity_keys(item);new=initialized and not any(k in seen for k in keys)
        first_seen=next((first[k] for k in keys if k in first),now if new else None)
        for k in keys:seen.add(k);first[k]=first_seen
        row={**item,'first_seen':first_seen};shown.append(row)
        if new:fresh.append(row)
    if len(seen)>10000:raise ValueError('Flap目录去重记录已达上限，需维护')
    return {'initialized':True,'seen':sorted(seen),'first_seen':first,'items':shown},fresh

def event(item):
    return {'type':'newsflash','news_id':'flap-app:'+item['slug'],
            'news_title':'Flap 新发现小程序 · '+item['name'],
            'news_content':item['name']+'\n\n'+item['description']+'\n\n'+item['url']+'\n\n目录新增发现提醒；不代表安全审核或收益保证。'}

def load_cache():
    if not CACHE.exists():return {}
    try:
        cache=json.loads(CACHE.read_text(encoding='utf-8'))
        if not isinstance(cache,dict) or not isinstance(cache.get('seen'),list) or len(cache['seen'])>10000 or not all(isinstance(x,str) for x in cache['seen']) or not isinstance(cache.get('first_seen'),dict):return {}
        return cache
    except (OSError,ValueError):return {}

def run(enqueue):
    cache=load_cache()
    while True:
        start=time.monotonic()
        if enabled():
            try:
                items=fetch_directory();now=time.time();next_cache,fresh=advance(items,cache,now)
                temp=CACHE.with_suffix('.tmp');temp.write_text(json.dumps(next_cache,ensure_ascii=False),encoding='utf-8');temp.replace(CACHE)
                cache=next_cache
                with LOCK:STATUS.update(items=cache['items'],updated=now,error='',initialized=True,last_new_count=len(fresh))
                if push_enabled():
                    for item in fresh:enqueue(event(item))
            except Exception as error:
                with LOCK:STATUS['error']=str(error) if isinstance(error,ValueError) and str(error).startswith('Flap目录') else 'Flap目录连接或缓存失败；保留上次数据，等待下轮'
        time.sleep(max(1,POLL_SECONDS-(time.monotonic()-start)))
