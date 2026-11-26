"""Public read-only polling monitor; Python standard library only."""
import collections, concurrent.futures, json, math, os, pathlib, threading, time, urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from asset_filter import excluded
from runtime_paths import STATE_ROOT
from spreads import rank_spreads
from quality import assess,Confirmation,cross_check
import wechat_push
import blockbeats
import dex_quotes
import logging
from logging.handlers import RotatingFileHandler

ROOT = pathlib.Path(__file__).resolve().parent
LOCK = threading.Lock()
ALERT_LOG=logging.getLogger('radar.alerts')
ALERT_LOG.setLevel(logging.INFO)
ALERT_LOG.propagate=False
if not ALERT_LOG.handlers:
    ALERT_LOG.addHandler(RotatingFileHandler(STATE_ROOT/'alerts.jsonl',maxBytes=2_000_000,backupCount=3,encoding='utf-8'))
STATE = {'running': False, 'updated': None, 'pairs': [], 'alerts': [], 'errors': [], 'coverage': 'four CEX spot USDT markets plus directory DEX candidates'}

def get(url):
    req = urllib.request.Request(url, headers={'User-Agent':'Mozilla/5.0 (compatible; LocalCrashMonitor/1.0)'})
    with urllib.request.urlopen(req, timeout=12) as r:
        return json.load(r)

class Detector:
    def __init__(self, config):
        self.c = config
        self.history = collections.defaultdict(collections.deque)
        self.last = {}

    def observe(self, key, price, liquidity, now):
        if not math.isfinite(price) or price <= 0 or not math.isfinite(liquidity):
            return []
        h = self.history[key]
        alerts=[]
        for seconds, threshold in self.c['windows']:
            # Compare against a sample near the actual rolling-window boundary.
            candidates=[x for x in h if now-seconds-self.c['poll_seconds']*1.5 <= x[0] <= now-seconds]
            if not candidates: continue
            old=max(candidates, key=lambda x:x[0])
            drop=(1-price/old[1])*100
            ak=(key,seconds)
            if drop >= threshold and now-self.last.get(ak,-1e20) >= self.c['cooldown_seconds']:
                self.last[ak]=now
                alerts.append({'type':'crash','window_seconds':seconds,'drop_percent':round(drop,2),'old_price':old[1],'price':price})
        if h and h[-1][2]>0 and liquidity < h[-1][2]*0.7:
            ak=(key,'liquidity')
            if now-self.last.get(ak,-1e20)>=self.c['cooldown_seconds']:
                self.last[ak]=now
                alerts.append({'type':'liquidity_drop','old_liquidity':h[-1][2],'drop_percent':round((1-liquidity/h[-1][2])*100,2)})
        h.append((now,price,liquidity))
        while h and h[0][0]<now-360: h.popleft()
        return alerts

def publish(alert):
    if excluded(alert):return
    alert={'time':time.time(),**alert}
    with LOCK:
        STATE['alerts'].insert(0,alert)
        del STATE['alerts'][200:]
    ALERT_LOG.info(json.dumps(alert,ensure_ascii=False))
    print('ALERT',json.dumps(alert,ensure_ascii=False),flush=True)
    wechat_push.enqueue(alert)
    should_sound=wechat_push.qualifies(alert)
    if os.name=='nt' and should_sound:
        try:
            import winsound
            winsound.MessageBeep()
        except Exception: pass

def batch_prices(tokens):
    from urllib.parse import quote
    groups=collections.defaultdict(list)
    for t in tokens: groups[t['chain']].append(t)
    jobs=[]
    for chain,items in groups.items():
        for offset in range(0,len(items),30):jobs.append((chain,items[offset:offset+30]))
    output={};errors=[]
    def request(job):
        chain,items=job
        data=get('https://api.dexscreener.com/tokens/v1/'+quote(chain,safe='')+'/'+','.join(quote(t['address'],safe='') for t in items))
        if not isinstance(data,list):raise ValueError('Unexpected DEX response')
        fetched=time.time()
        for p in data:p['_fetched_at']=fetched
        result=[]
        for t in items:
            address=t['address'];case=chain in ('solana','sui','aptos','tron','ton')
            pairs=[p for p in data if (p.get('baseToken',{}).get('address')==address if case else p.get('baseToken',{}).get('address','').lower()==address.lower())]
            result.append((chain+':'+address,pairs))
        return result
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        futures={}
        for job in jobs:
            futures[pool.submit(request,job)]=job
            time.sleep(0.3) # At most 200 starts/minute; below the 300/min endpoint limit.
        for f in concurrent.futures.as_completed(futures):
            try:output.update(f.result())
            except Exception as e:errors.append(futures[f][0]+': '+str(e))
    return output,errors

def cycle(config, detector):
    rows=[]; errors=[]; events_pending=[]; tickers={}
    tokens=list(config['tokens'])
    if (ROOT/'universe.json').exists():
        universe=json.loads((ROOT/'universe.json').read_text(encoding='utf-8'))
        known={(t['chain'],t['address']) for t in tokens}
        tokens += [t for t in universe['candidates'] if (t['chain'],t['address']) not in known]
        with LOCK: STATE['directory']={k:v for k,v in universe.items() if k!='candidates'}
    blocked={(b['chain'],b['address']) for b in config.get('blocked_tokens',[])}
    tokens=[t for t in tokens if (t['chain'],t['address']) not in blocked and not excluded(t)]
    prices,batch_errors=batch_prices(tokens)
    errors.extend(batch_errors)
    with LOCK: STATE.update(dex_configured=len(tokens),dex_no_pairs=sum(not prices.get(t['chain']+':'+t['address']) for t in tokens),dex_batch_errors=len(batch_errors))
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        futures={pool.submit(lambda t:(t,prices.get(t['chain']+':'+t['address'],[])),t):t for t in tokens}
        for future in concurrent.futures.as_completed(futures):
            token=futures[future]
            try:
                _,pairs=future.result()
                if not isinstance(pairs,list): raise ValueError('Unexpected DEX response')
                for p in pairs:
                    # Endpoint may return pairs where the queried asset is the quote.
                    base=p.get('baseToken',{}).get('address','')
                    match=base==token['address'] if token['chain']=='solana' else base.lower()==token['address'].lower()
                    if not match: continue
                    price=float(p.get('priceUsd') or 0)
                    liq=float((p.get('liquidity') or {}).get('usd') or 0)
                    volume=float((p.get('volume') or {}).get('h24') or 0)
                    if price<=0: continue
                    key=token['chain']+':'+p['pairAddress']
                    now=time.time()
                    mapping=next((m for m in config.get('verified_cex_mappings',[]) if m['chain']==token['chain'] and m['address']==token['address']),None)
                    row={'key':key,'chain':token['chain'],'token':token['label'],'contract':token['address'],'pair':p['pairAddress'],'price':price,'liquidity':liq,'volume_h24':volume,'url':p.get('url'),'observed_at':p.get('_fetched_at',now),'dex':p.get('dexId','未知DEX'),'volume_m5':float((p.get('volume') or {}).get('m5') or 0),'trades_m5':sum((p.get('txns',{}).get('m5') or {}).get(k,0) for k in ('buys','sells')),'created_at':float(p['pairCreatedAt'])/1000 if p.get('pairCreatedAt') else None,'cex':'unmapped','eligible':liq>=config['min_liquidity_usd'] and volume>=config['min_volume_h24_usd']}
                    row['identity_status']=token.get('identity_status','verified'); row['mapping_status']='行情暂不可用' if mapping else '目录候选，未核实交易所身份'
                    if mapping: row.update(cex=mapping['okx_instrument'],relationship=mapping.get('relationship','same_token'))
                    assess(row,config,now)
                    events=detector.observe(key,price,liq,now)
                    for event in events:
                        if event['type']=='liquidity_drop' or row['eligible']:
                            events_pending.append((row,event))
                    rows.append(row)
                # No returned pool is tracked as coverage, not a transport error.
            except Exception as e: errors.append(token['chain']+'/'+token['label']+': '+str(e))
    with LOCK: quotes=list(STATE.get('cex_rows',[]))
    rows=rank_spreads(cross_check(rows),quotes,config.get('verified_cex_mappings',[]),time.time())
    for row in rows:
        gap=row.get('indicative_spread_percent',-1e9)
        ak=(row['key'],'spread')
        if row.get('quality_ok') and gap>300:
            events_pending.append((row,{'type':'extreme_spread','indicative_spread_percent':gap,'verification':'极端价差，待核实；可能假池、同名或面值不符，不代表可套利'}))
        if not row.get('quarantine_reason') and row.get('spread_identity')=='verified' and gap>config['spread_percent'] and time.time()-detector.last.get(ak,-1e20)>=config['cooldown_seconds']:
            detector.last[ak]=time.time()
            events_pending.append((row,{'type':'indicative_spread'}))
    if not hasattr(detector,'confirmation'):detector.confirmation=Confirmation()
    confirmed=detector.confirmation.check(rows,events_pending,config,time.time())
    for row,event in confirmed:publish({**row,**event})
    with LOCK:
        STATE['quality']={'excluded_pools':sum(not r.get('quality_ok') for r in rows),'pending':len(detector.confirmation.pending),'confirmation_rounds':2}
        STATE.update(updated=time.time(),pairs=rows,errors=errors,running=True,dex_covered=len({(r['chain'],r['contract']) for r in rows}),dex_without_usable_price=len(tokens)-len({(r['chain'],r['contract']) for r in rows}))

def worker(config):
    d=Detector(config)
    while True:
        start=time.monotonic()
        try:
            config=json.loads((ROOT/'config.json').read_text(encoding='utf-8'))
            d.c=config
            cycle(config,d)
        except Exception as e:
            with LOCK: STATE['errors']=[str(e)]
        time.sleep(max(1,config['poll_seconds']-(time.monotonic()-start)))

def quote_inputs():
    with LOCK:return {'pairs':list(STATE.get('pairs',[])),'alerts':list(STATE.get('alerts',[]))}

class Handler(BaseHTTPRequestHandler):
    def push_response(self,value,code=200):
        body=json.dumps(value,ensure_ascii=False).encode()
        self.send_response(code);self.send_header('Content-Type','application/json; charset=utf-8');self.send_header('Cache-Control','no-store');self.end_headers();self.wfile.write(body)
    def do_POST(self):
        expected='127.0.0.1:'+str(self.server.server_port)
        if self.headers.get('Host')!=expected or self.headers.get('Origin')!='http://'+expected or self.headers.get('Content-Type','').split(';')[0]!='application/json':
            self.push_response({'error':'仅接受本地看板请求'},403);return
        try:
            length=int(self.headers.get('Content-Length','0'))
            if not 0<length<=4096:raise ValueError('请求长度无效')
            data=json.loads(self.rfile.read(length))
            if self.path=='/api/push/config':wechat_push.save(data.get('token',''),data.get('enabled',False));result=wechat_push.status()
            elif self.path=='/api/news/config':
                blockbeats.save(data.get('api_key',''),data.get('enabled',False),data.get('mode','all'));result=blockbeats.status()
            elif self.path=='/api/push/test':result=wechat_push.send('多链雷达：微信推送测试','这是一条测试消息。正式提醒仅限已核实参考价差严格大于25%。',test=True)
            else:self.send_error(404);return
            self.push_response(result)
        except Exception as e:self.push_response({'error':str(e) if isinstance(e,ValueError) else '操作失败，请检查本地配置'},400)
    def do_GET(self):
        if self.path=='/api/dex-quotes':self.push_response(dex_quotes.status());return
        if self.path=='/api/news/status':self.push_response(blockbeats.status());return
        if self.path=='/api/push/status':self.push_response(wechat_push.status());return
        if self.path=='/api/state':
            with LOCK: snapshot=dict(STATE)
            snapshot['news']=blockbeats.status()
            config=json.loads((ROOT/'config.json').read_text(encoding='utf-8'))
            blocked={(b['chain'],b['address']) for b in config.get('blocked_tokens',[])}
            snapshot['pairs']=[p for p in snapshot['pairs'] if (p['chain'],p['contract']) not in blocked and not excluded(p)]
            snapshot['alerts']=[a for a in snapshot.get('alerts',[]) if (a.get('chain'),a.get('contract')) not in blocked and not excluded(a)]
            snapshot['pairs']=rank_spreads(snapshot['pairs'],snapshot.get('cex_rows',[]),config.get('verified_cex_mappings',[]),time.time())
            body=json.dumps(snapshot,ensure_ascii=False).encode()
            kind='application/json; charset=utf-8'
        elif self.path=='/': body=(ROOT/'index.html').read_bytes();kind='text/html; charset=utf-8'
        else: self.send_error(404);return
        self.send_response(200);self.send_header('Content-Type',kind);self.send_header('Cache-Control','no-store');self.end_headers();self.wfile.write(body)
    def log_message(self,*args): pass

if __name__=='__main__':
    import argparse
    ap=argparse.ArgumentParser();ap.add_argument('--once',action='store_true');ap.add_argument('--port',type=int,default=8766);args=ap.parse_args()
    config=json.loads((ROOT/'config.json').read_text(encoding='utf-8'))
    if args.once:
        cycle(config,Detector(config));print(json.dumps(STATE,ensure_ascii=False,indent=2))
    else:
        server=ThreadingHTTPServer(('127.0.0.1',args.port),Handler)
        threading.Thread(target=wechat_push.worker,daemon=True).start()
        threading.Thread(target=blockbeats.run,args=(wechat_push.enqueue,),daemon=True).start()
        from cex_market import run as cex_run
        threading.Thread(target=cex_run,args=(get,publish,Detector,config,STATE,LOCK),daemon=True).start()
        threading.Thread(target=worker,args=(config,),daemon=True).start()
        threading.Thread(target=dex_quotes.run,args=(quote_inputs,),daemon=True).start()
        print(f'Local dashboard: http://127.0.0.1:{args.port}',flush=True)
        server.serve_forever()
