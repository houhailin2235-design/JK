"""EquationNews alpha-channel listings; official public REST, no Telegram login."""
import datetime
import json
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import blockbeats
from asset_filter import excluded
from runtime_paths import STATE_ROOT

URL='https://api.eqn.news/public/v1/news?channel=alpha&limit=50'
CACHE=STATE_ROOT/'equation-alpha-cache.json'
LOCK=threading.Lock()
POLL_SECONDS=10
STATUS={'items':[],'updated':None,'error':'等待首次方程式频道检查','last_batch':None,'initialized':False}

def enabled():return os.environ.get('EQN_ALPHA_ENABLED','true').lower()=='true'
def push_enabled():return os.environ.get('EQN_ALPHA_PUSH_ENABLED','true').lower()=='true'
def status():
    with LOCK:return {**STATUS,'enabled':enabled(),'push_enabled':push_enabled(),'poll_seconds':POLL_SECONDS,'channel':'alpha','source':'https://t.me/eqn_alpha_aster'}

def is_listing(title,source):
    if re.search(r'xstocks?|bstocks?|代币化股票|\bETF\b',title,re.I):return False
    if any(excluded({'token':symbol}) for symbol in re.findall(r'(?<![A-Za-z0-9])([A-Z0-9]{2,24})USDT(?![A-Za-z0-9])',title)):return False
    return blockbeats.is_alpha_listing_news(source+' '+title)

def parse(data):
    if not isinstance(data,dict) or not isinstance(data.get('items'),list):raise ValueError('方程式接口结构异常')
    cursor=data.get('next_cursor')
    if cursor is not None and (not isinstance(cursor,str) or len(cursor)>300):raise ValueError('方程式分页异常')
    result=[]
    for raw in data['items']:
        if not isinstance(raw,dict) or type(raw.get('no')) is not int or raw['no']<=0:raise ValueError('方程式快讯编号异常')
        if 'alpha' not in raw.get('channels',[]):continue
        body=raw.get('zh') or raw.get('en')
        if not isinstance(body,str) or not body.strip():raise ValueError('方程式快讯内容异常')
        link=raw.get('url','')
        if not isinstance(link,str) or not re.fullmatch(r'https://eqn\.news/(?:monitor|main|tradfi)/'+str(raw['no']),link):raise ValueError('方程式快讯链接异常')
        try:
            parsed_time=datetime.datetime.fromisoformat(raw['time'].replace('Z','+00:00'))
            if parsed_time.tzinfo is None:raise ValueError('timezone missing')
            published=parsed_time.timestamp()
        except (ValueError,TypeError,KeyError,AttributeError):raise ValueError('方程式快讯时间异常') from None
        lines=[x.strip() for x in body.splitlines() if x.strip()];heading=lines[0]
        title=heading+(' '+lines[1] if len(lines)>1 and heading.endswith((':','：')) else '')
        source=raw.get('source','')
        if not isinstance(source,str):raise ValueError('方程式来源字段异常')
        coins=raw.get('coins',[])
        stock=any(excluded({'token':coin}) for coin in coins if isinstance(coin,str)) if isinstance(coins,list) else False
        result.append({'id':str(raw['no']),'title':title[:240],'body':body[:6000],'source':source[:120],'url':link,'published':published,'listing':not stock and is_listing(heading,source)})
    return result,cursor

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):return None

def fetch_page(cursor=None,etag=None):
    url=URL+('&before='+urllib.parse.quote(cursor,safe='') if cursor else '')
    headers={'User-Agent':'RadarEquationNews/1.0','Accept':'application/json'}
    if etag and not cursor:headers['If-None-Match']=etag
    try:
        with urllib.request.build_opener(NoRedirect()).open(urllib.request.Request(url,headers=headers),timeout=12) as response:
            body=response.read(2_000_001)
            if len(body)>2_000_000:raise ValueError('方程式接口响应过大')
            return json.loads(body),response.headers.get('ETag')
    except urllib.error.HTTPError as error:
        error.close()
        if error.code==304:return None,etag
        raise

def fresh_items(items,cache,now):
    seen=set(cache.get('seen',[]))
    return [x for x in items if x['id'] not in seen and x['listing'] and 0<=now-x['published']<=3600] if cache.get('initialized') else []

def event(item):
    return {'type':'newsflash','news_id':'eqn-alpha:'+item['id'],'news_title':'方程式上新 · '+item['title'],
            'news_content':item['body']+'\n\n来源：'+item['source']+'\n'+item['url']+'\n\n频道上新信息，不代表可套利或项目安全审核。'}

def load_cache():
    try:
        cache=json.loads(CACHE.read_text(encoding='utf-8'))
        if not isinstance(cache,dict) or not isinstance(cache.get('seen'),list) or not all(isinstance(x,str) for x in cache['seen']):return {}
        cache['items']=[x for x in cache.get('items',[]) if isinstance(x,dict) and isinstance(x.get('title'),str) and isinstance(x.get('source'),str) and is_listing(x['title'],x['source'])]
        return cache
    except (OSError,ValueError):return {}

def run(enqueue):
    cache=load_cache();etag=cache.get('etag')
    with LOCK:STATUS.update(items=cache.get('items',[]),initialized=bool(cache.get('initialized')))
    while True:
        start=time.monotonic();delay=POLL_SECONDS
        if enabled():
            try:
                data,new_etag=fetch_page(etag=etag)
                if data is None:
                    with LOCK:STATUS.update(updated=time.time(),error='')
                else:
                    items,cursor=parse(data);seen=set(cache.get('seen',[]));cursors=set()
                    for _ in range(4):
                        if not cache.get('initialized') or not cursor or any(x['id'] in seen for x in items) or not items or min(x['published'] for x in items)<time.time()-3600:break
                        if cursor in cursors or time.monotonic()-start>30:raise ValueError('方程式补查分页异常或超时')
                        cursors.add(cursor);older,_=fetch_page(cursor=cursor);more,cursor=parse(older);items.extend(more)
                    items=list({x['id']:x for x in items}.values());now=time.time();fresh=fresh_items(items,cache,now)
                    next_cache={'initialized':True,'seen':list(dict.fromkeys([x['id'] for x in items]+cache.get('seen',[])))[:5000],
                                'items':sorted((x for x in items if x['listing']),key=lambda x:x['published'],reverse=True)[:30],'etag':new_etag}
                    temp=CACHE.with_suffix('.tmp');temp.write_text(json.dumps(next_cache,ensure_ascii=False),encoding='utf-8');temp.replace(CACHE)
                    cache=next_cache;etag=new_etag
                    with LOCK:STATUS.update(items=cache['items'],updated=now,error='',initialized=True,fetched_count=len(items))
                    if push_enabled():
                        for item in sorted(fresh,key=lambda x:x['published']):enqueue(event(item))
                        if fresh:
                            with LOCK:STATUS['last_batch']=now
            except Exception as error:
                if isinstance(error,urllib.error.HTTPError) and error.code==429:
                    try:delay=max(POLL_SECONDS,min(300,int(error.headers.get('Retry-After','30'))))
                    except (TypeError,ValueError):delay=30
                with LOCK:STATUS['error']=str(error) if isinstance(error,ValueError) and str(error).startswith('方程式') else '方程式接口连接失败；保留上次数据并稍后重试'
        time.sleep(max(1,delay-(time.monotonic()-start)))
