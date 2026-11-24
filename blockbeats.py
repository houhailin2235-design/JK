"""Official BlockBeats RSS integration; API key is kept on the local server."""
import os
from runtime_paths import STATE_ROOT
import datetime, email.utils, hashlib, html, json, pathlib, re, threading, time
import urllib.request, urllib.parse, xml.etree.ElementTree as ET

ROOT=pathlib.Path(__file__).resolve().parent
SECRET=STATE_ROOT/'blockbeats.local.json'
CACHE=STATE_ROOT/'blockbeats-cache.json'
URL='https://api-pro.theblockbeats.info/v1/rss/newsflash?size=50&page=1'
LOCK=threading.Lock()
RISK=re.compile(r'黑客|攻击|被盗|盗取|漏洞|闪电贷|私钥泄露|暂停.{0,8}(充|提|交易)|停止.{0,8}(充|提)|脱锚|hack|exploit|breach|depeg',re.I)
STATUS={'items':[],'updated':None,'error':'请先配置律动 API Key','last_batch':None}

def settings():
    if os.environ.get('BLOCKBEATS_API_KEY'):
        return {'api_key':os.environ['BLOCKBEATS_API_KEY'],'enabled':os.environ.get('NEWS_PUSH_ENABLED','false').lower()=='true','mode':'all'}
    return json.loads(SECRET.read_text(encoding='utf-8')) if SECRET.exists() else {}

def save(key,enabled,mode):
    if mode not in ('risk','all'):raise ValueError('快讯模式无效')
    key=key.strip() or settings().get('api_key','')
    if not key or len(key)>500 or '\n' in key or '\r' in key:raise ValueError('请填写有效的律动 API Key')
    with LOCK:
        tmp=SECRET.with_suffix('.tmp')
        tmp.write_text(json.dumps({'api_key':key,'enabled':bool(enabled),'mode':mode}),encoding='utf-8')
        tmp.replace(SECRET)

def status():
    c=settings()
    with LOCK:return {**STATUS,'configured':bool(c.get('api_key')),'enabled':c.get('enabled',False),'mode':c.get('mode','all'),'poll_seconds':30}

def text(value):
    return html.unescape(re.sub(r'<[^>]*>',' ',value or '')).strip()

def parse(body):
    if body.lstrip().startswith(b'{'):
        # Error responses are JSON; never include response text or credentials in errors.
        raise ValueError('律动接口未返回 RSS，请检查 API Key、接口权限或额度')
    root=ET.fromstring(body)
    if root.tag!='rss':raise ValueError('律动 RSS 格式不符')
    items=[]
    for x in root.findall('./channel/item'):
        title=text(x.findtext('title'));link=(x.findtext('link') or '').strip()
        u=urllib.parse.urlparse(link)
        if not title or u.scheme!='https' or u.hostname not in ('www.theblockbeats.info','m.theblockbeats.info','www.theblockbeats.news','m.theblockbeats.news'):continue
        guid=x.findtext('guid') or link
        date=x.findtext('pubDate') or ''
        try:published=email.utils.parsedate_to_datetime(date).timestamp()
        except (ValueError,TypeError,OverflowError):published=None
        description=text(x.findtext('description'))[:3000]
        items.append({'id':hashlib.sha256(guid.encode()).hexdigest()[:24],'title':title,'summary':description,'url':link,'published':published,'risk':bool(RISK.search(title+' '+description))})
    if not items:raise ValueError('律动 RSS 没有可识别快讯，保留上次数据')
    return sorted(items,key=lambda x:x['published'] or 0,reverse=True)

def fetch(key):
    req=urllib.request.Request(URL,headers={'api-key':key,'language':'cn','User-Agent':'LocalCrashMonitor/1.0','Accept':'*/*'})
    with urllib.request.urlopen(req,timeout=15) as r:body=r.read(2_000_001)
    if len(body)>2_000_000:raise ValueError('律动 RSS 响应过大')
    return parse(body)

def fresh_items(items,seen,initialized,now):
    # Establish a baseline on first connection; don't push historical news.
    return [x for x in items if x['id'] not in seen and x['published'] is not None and 0<=now-x['published']<=3600] if initialized else []

def run(enqueue):
    cache=json.loads(CACHE.read_text(encoding='utf-8')) if CACHE.exists() else {}
    seen=cache.get('seen',[]);initialized=bool(cache.get('initialized'))
    with LOCK:STATUS['items']=cache.get('items',[])
    while True:
        start=time.monotonic();c=settings()
        if c.get('api_key'):
            try:
                items=fetch(c['api_key']);now=time.time()
                fresh=fresh_items(items,set(seen),initialized,now)
                chosen=[x for x in fresh if c.get('mode','all')=='all' or x['risk']]
                # Persist deduplication before enqueueing; restart won't resend the same item.
                seen=list(dict.fromkeys([x['id'] for x in items]+seen))[:5000]
                tmp=CACHE.with_suffix('.tmp')
                tmp.write_text(json.dumps({'initialized':True,'seen':seen,'items':items},ensure_ascii=False),encoding='utf-8');tmp.replace(CACHE)
                initialized=True
                with LOCK:STATUS.update(items=items,updated=now,error='')
                if chosen and c.get('enabled'):
                    for item in reversed(chosen):
                        enqueue({'type':'newsflash','news_id':item['id'],'news_title':'律动快讯 · '+item['title'],
                                 'news_content':item['title']+'\n\n'+item['summary']+'\n\n'+item['url']})
                    with LOCK:STATUS['last_batch']=now
            except Exception as e:
                with LOCK:STATUS['error']=str(e) if isinstance(e,ValueError) else '律动连接失败，请检查网络、API Key 或额度；保留上次数据'
        time.sleep(max(1,30-(time.monotonic()-start)))
