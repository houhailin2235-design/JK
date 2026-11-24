from asset_filter import excluded
"""PushPlus transport. Token remains server-side; no token is included in status/logs."""
import collections,datetime,json,os,pathlib,queue,re,threading,time,urllib.request
from runtime_paths import STATE_ROOT
ROOT=pathlib.Path(__file__).resolve().parent
SECRET=STATE_ROOT/'pushplus.local.json'
USAGE=STATE_ROOT/'pushplus-usage.json'
LOCK=threading.Lock()
DAILY_LIMIT=2000
REQUEST_INTERVAL=3
JOBS=queue.Queue(maxsize=200)
LAST={}
STATUS={'last_result':'尚未测试'}

def settings():
    if os.environ.get('PUSHPLUS_TOKEN'):
        return {'token':os.environ['PUSHPLUS_TOKEN'],'enabled':os.environ.get('PUSH_ENABLED','false').lower()=='true'}
    if not SECRET.exists():return {}
    return json.loads(SECRET.read_text(encoding='utf-8'))

def save(token,enabled):
    current=settings()
    token=token.strip() or current.get('token','')
    if not re.fullmatch(r'[A-Za-z0-9_-]{16,200}',token):raise ValueError('Token格式无效，请复制一对一消息页的Token')
    with LOCK:
        temp=SECRET.with_suffix('.tmp');temp.write_text(json.dumps({'token':token,'enabled':bool(enabled)}),encoding='utf-8');temp.replace(SECRET)

def status():
    config=settings()
    with LOCK:return {'configured':bool(config.get('token')),'enabled':config.get('enabled',False),**STATUS,'daily_limit':DAILY_LIMIT,'cooldown_minutes':10}

def qualifies(a):
    if a.get("type")=="newsflash":return bool(a.get("news_id"))
    if excluded(a):return False
    spread=not a.get('quarantine_reason') and a.get('type')=='indicative_spread' and a.get('spread_identity')=='verified' and a.get('indicative_spread_percent',0)>25
    crash=a.get('type') in ('cex_crash','multi_pool_price_drop') and a.get('drop_percent',0)>=25
    extreme=a.get('type')=='extreme_spread' and a.get('indicative_spread_percent',0)>300
    return spread or crash or extreme

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):return None

def send(title,content,test=False):
    config=settings()
    if not config.get('token'):raise ValueError('请先保存Token')
    if not test and not config.get('enabled'):return
    with LOCK:
        today=datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8))).date().isoformat()
        usage=json.loads(USAGE.read_text()) if USAGE.exists() else {}
        if usage.get('date')!=today:usage={'date':today,'count':0,'last':0}
        if usage['count']>=DAILY_LIMIT:raise ValueError('已到本地每日2000次会员上限')
        if time.time()-usage.get('last',0)<REQUEST_INTERVAL:raise ValueError('发送过快，请3秒后再试')
        usage.update(count=usage['count']+1,last=time.time())
        USAGE.write_text(json.dumps(usage),encoding='utf-8')
    body=json.dumps({'token':config['token'],'title':title,'content':content,'template':'txt','channel':'wechat'}).encode()
    req=urllib.request.Request('https://www.pushplus.plus/send',data=body,headers={'Content-Type':'application/json','User-Agent':'LocalCrashMonitor/1.0'})
    try:
        with urllib.request.build_opener(NoRedirect()).open(req,timeout=12) as response:result=json.load(response)
    except Exception:
        with LOCK:STATUS['last_result']='发送请求失败；未自动重试，请检查网络或服务状态'
        raise ValueError('发送请求失败；未自动重试') from None
    ok=result.get('code')==200
    with LOCK:STATUS['last_result']='服务已受理，等待微信送达（不是送达确认）' if ok else '服务拒绝，错误码 '+str(result.get('code'))
    if not ok:raise ValueError(STATUS['last_result'])
    return status()

def enqueue(alert):
    if not qualifies(alert) or not settings().get('enabled'):return
    key=('news:'+alert['news_id']) if alert.get('type')=='newsflash' else alert.get('chain','')+':'+(alert.get('contract') or alert.get('pair') or alert.get('token',''))
    if time.time()-LAST.get(key,0)<600:return
    try:JOBS.put_nowait(dict(alert));LAST[key]=time.time()
    except queue.Full:
        with LOCK:STATUS['last_result']='推送队列已满，本条未发送'

def worker():
    while True:
        a=JOBS.get()
        try:
            if a.get('type')=='newsflash':
                send(a['news_title'],a['news_content'])
            else:
                send_market(a)
        except Exception as e:
            with LOCK:STATUS['last_result']=str(e) if isinstance(e,ValueError) else '推送失败'
        finally:JOBS.task_done()
        time.sleep(REQUEST_INTERVAL)

def send_market(a):
    is_spread=not a.get('quarantine_reason') and a.get('type')=='indicative_spread'
    amount=a.get('indicative_spread_percent',0) if is_spread else a.get('drop_percent',0)
    title=f"{'极端价差⚠待核实' if a.get('type')=='extreme_spread' else '价差' if is_spread else '下跌'} {amount:.2f}% · {a.get('token','')}"
    content='\n'.join([f"链：{a.get('chain','')}",f"平台：{a.get('best_venue','')} {a.get('cex','')}",f"{'参考价差' if is_spread else '监测跌幅'}：{amount}%",f"类型：{a.get('verification',a.get('type',''))}",f"CA：{a.get('contract','')}",f"池子：{a.get('url','')}",'仅参考价差，未扣成本及验证充提路线。'])
    send(title,content)
