"""Read-only, amount-specific DEX to CEX review. Never signs wallet transactions."""
import base64
import concurrent.futures
import datetime
from decimal import Decimal, localcontext
import hashlib
import hmac
import json
import math
import os
import pathlib
import re
import threading
import time
from urllib.parse import urlencode
import urllib.request

import dex_quotes
import gate_review
from asset_filter import excluded

VENUES=('Binance','OKX','Gate','KuCoin')
ALIASES={**gate_review.ALIASES,'ethereum':{'ETH','ERC20','ETHEREUM'},'bsc':{'BSC','BEP20','BNB SMART CHAIN'},'base':{'BASE'}}
LOCK=threading.Lock()
REQUEST_LOCK=threading.Lock()
LAST_MANUAL=0
STATE={'updated':None,'rows':[],'history':[],'error':'等待首次暴跌或价差信号','checked':0}
CACHE={}

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):return None

def fetch(url,headers=None):
    # URLs are assembled only from fixed venue endpoints and validated symbols.
    req=urllib.request.Request(url,headers={'Accept':'application/json','User-Agent':'RadarAmountReview/1.0',**(headers or {})})
    with urllib.request.build_opener(NoRedirect()).open(req,timeout=10) as response:
        raw=response.read(4_000_001)
    if len(raw)>4_000_000:raise ValueError('接口响应过大')
    return json.loads(raw)

def cached(key,loader,ttl=60):
    with LOCK:item=CACHE.get(key)
    if item and time.monotonic()-item[0]<ttl:return item[1]
    value=loader()
    with LOCK:
        if len(CACHE)>1000:CACHE.clear()
        CACHE[key]=(time.monotonic(),value)
    return value

def unwrap(data,code):
    if not isinstance(data,dict) or str(data.get('code'))!=code:raise ValueError('接口未返回成功结果')
    return data['data']

def binance_networks(symbol):
    key=os.environ.get('BINANCE_READ_API_KEY','');secret=os.environ.get('BINANCE_READ_API_SECRET','')
    if not key or not secret:raise ValueError('币安充值网络待核实：未配置只读API')
    def load():
        query=urlencode({'timestamp':int(time.time()*1000),'recvWindow':5000})
        signature=hmac.new(secret.encode(),query.encode(),hashlib.sha256).hexdigest()
        return fetch('https://api.binance.com/sapi/v1/capital/config/getall?'+query+'&signature='+signature,{'X-MBX-APIKEY':key})
    data=cached('binance:networks',load)
    row=next((x for x in data if x.get('coin')==symbol),None)
    if not row:raise ValueError('币安未返回该资产网络')
    return [{'name':x.get('network'),'ca':x.get('contractAddress'),'deposit':x.get('depositEnable'),
             'withdraw':x.get('withdrawEnable'),'minimum':x.get('depositDust'),
             'confirmations':x.get('minConfirm'),'deposit_fee':None} for x in row.get('networkList',[])]

def okx_networks(symbol):
    key=os.environ.get('OKX_READ_API_KEY','');secret=os.environ.get('OKX_READ_API_SECRET','')
    phrase=os.environ.get('OKX_READ_API_PASSPHRASE','')
    if not all((key,secret,phrase)):raise ValueError('OKX充值网络待核实：未配置只读API')
    def load():
        path='/api/v5/asset/currencies?'+urlencode({'ccy':symbol})
        stamp=datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='milliseconds').replace('+00:00','Z')
        sign=base64.b64encode(hmac.new(secret.encode(),(stamp+'GET'+path).encode(),hashlib.sha256).digest()).decode()
        return unwrap(fetch('https://www.okx.com'+path,{'OK-ACCESS-KEY':key,'OK-ACCESS-SIGN':sign,
                      'OK-ACCESS-TIMESTAMP':stamp,'OK-ACCESS-PASSPHRASE':phrase}),'0')
    data=cached('okx:networks:'+symbol,load)
    return [{'name':str(x.get('chain','')).removeprefix(symbol+'-'),'ca':x.get('ctAddr'),
             'deposit':x.get('canDep'),'withdraw':x.get('canWd'),'minimum':x.get('minDep'),
             'confirmations':x.get('minDepArrivalConfirm'),'deposit_fee':None} for x in data if x.get('ccy')==symbol]

def networks(venue,symbol):
    if venue=='Binance':return binance_networks(symbol)
    if venue=='OKX':return okx_networks(symbol)
    if venue=='Gate':
        d=cached('gate:networks:'+symbol,lambda:gate_review.fetch('/spot/currencies/'+symbol))
        if d.get('currency')!=symbol:raise ValueError('Gate资产不符')
        return [{'name':n.get('name'),'ca':n.get('addr'),
                 'deposit':n.get('deposit_disabled') is False and d.get('deposit_disabled') is False,
                 'withdraw':n.get('withdraw_disabled') is False and d.get('withdraw_disabled') is False,
                 'minimum':None,'confirmations':None,'deposit_fee':None} for n in d.get('chains',[])]
    d=cached('kucoin:networks:'+symbol,lambda:unwrap(fetch('https://api.kucoin.com/api/v3/currencies/'+symbol),'200000'))
    if d.get('currency')!=symbol:raise ValueError('KuCoin资产不符')
    return [{'name':n.get('chainId') or n.get('chainName'),'ca':n.get('contractAddress'),
             'deposit':n.get('isDepositEnabled'),'withdraw':n.get('isWithdrawEnabled'),
             'minimum':n.get('depositMinSize'),'confirmations':n.get('confirms'),
             'deposit_fee':n.get('depositFeeRate')} for n in d.get('chains',[])]

def market(venue,symbol):
    if venue=='Binance':
        d=cached('binance:market:'+symbol,lambda:fetch('https://api.binance.com/api/v3/exchangeInfo?symbol='+symbol+'USDT'),300)
        p=next((x for x in d.get('symbols',[]) if x.get('symbol')==symbol+'USDT' and x.get('baseAsset')==symbol and x.get('quoteAsset')=='USDT'),None)
        if not p:raise ValueError('币安无对应USDT现货交易对')
        lot=next((x for x in p.get('filters',[]) if x.get('filterType')=='LOT_SIZE'),{})
        notion=next((x for x in p.get('filters',[]) if x.get('filterType') in ('NOTIONAL','MIN_NOTIONAL')), {})
        return {'pair':p['symbol'],'enabled':p.get('status')=='TRADING','step':lot.get('stepSize'),'minimum':lot.get('minQty'),'notional':notion.get('minNotional')}
    if venue=='OKX':
        def load_okx():
            raw=fetch('https://www.okx.com/api/v5/public/instruments?instType=SPOT&instId='+symbol+'-USDT')
            if raw.get('code')=='51001':raise ValueError('OKX无对应USDT现货交易对')
            return unwrap(raw,'0')
        d=cached('okx:market:'+symbol,load_okx,300)
        p=next((x for x in d if x.get('instId')==symbol+'-USDT' and x.get('baseCcy')==symbol and x.get('quoteCcy')=='USDT'),None)
        if not p:raise ValueError('OKX无对应USDT现货交易对')
        return {'pair':p['instId'],'enabled':p.get('state')=='live','step':p.get('lotSz'),'minimum':p.get('minSz'),'notional':None}
    if venue=='Gate':
        p=cached('gate:market:'+symbol,lambda:gate_review.fetch('/spot/currency_pairs/'+symbol+'_USDT'),300)
        if p.get('id')!=symbol+'_USDT' or p.get('base')!=symbol or p.get('quote')!='USDT':raise ValueError('Gate无对应USDT现货交易对')
        precision=p.get('amount_precision')
        if type(precision) is not int or not 0<=precision<=24:raise ValueError('Gate数量精度未知')
        return {'pair':p['id'],'enabled':p.get('trade_status') in ('tradable','sellable'),
                'step':str(Decimal(1).scaleb(-precision)),'minimum':p.get('min_base_amount'),'notional':p.get('min_quote_amount')}
    p=cached('kucoin:market:'+symbol,lambda:unwrap(fetch('https://api.kucoin.com/api/v2/symbols/'+symbol+'-USDT'),'200000'),300)
    if p.get('symbol')!=symbol+'-USDT' or p.get('baseCurrency')!=symbol or p.get('quoteCurrency')!='USDT':raise ValueError('KuCoin无对应USDT现货交易对')
    return {'pair':p['symbol'],'enabled':p.get('enableTrading') is True,'step':p.get('baseIncrement'),
            'minimum':p.get('baseMinSize'),'notional':p.get('minFunds')}

def book(venue,symbol):
    start=time.time()
    if venue=='Binance':
        d=fetch('https://api.binance.com/api/v3/depth?symbol='+symbol+'USDT&limit=100')
        bids=d.get('bids');stamp=None
    elif venue=='OKX':
        d=unwrap(fetch('https://www.okx.com/api/v5/market/books?instId='+symbol+'-USDT&sz=100'),'0')[0]
        bids=[x[:2] for x in d['bids']];stamp=float(d['ts'])/1000
    elif venue=='Gate':
        d=gate_review.fetch('/spot/order_book?currency_pair='+symbol+'_USDT&limit=100')
        bids=d.get('bids');stamp=float(d['current'])/1000
    else:
        d=unwrap(fetch('https://api.kucoin.com/api/v1/market/orderbook/level2_100?symbol='+symbol+'-USDT'),'200000')
        bids=d.get('bids');stamp=float(d['time'])/1000
    end=time.time()
    if end-start>10 or (stamp is not None and (not math.isfinite(stamp) or not -5<=end-stamp<=15)):raise ValueError('盘口过期或响应过慢')
    return {'bids':bids,'observed_at':end,'source_timestamp':stamp,'valid_until':min(start+15,stamp+15) if stamp else start+15}

def match_network(items,chain,ca,venue,symbol,config):
    aliases=ALIASES[chain]
    for n in items:
        if str(n.get('name','')).upper() not in aliases:continue
        address=str(n.get('ca') or '').lower()
        if address==ca.lower():return n
        # OKX may expose only the final six hex characters. Never verify identity by suffix alone.
        verified=any(m.get('chain')==chain and str(m.get('address','')).lower()==ca.lower()
                     and venue in m.get('verified_venues',['OKX']) and m.get('okx_instrument','').split('-')[0]==symbol
                     for m in config.get('verified_cex_mappings',[]))
        if venue=='OKX' and verified and re.fullmatch('[0-9a-f]{6}',address) and ca.lower().endswith(address):return n
    return None

def fee_setting(venue):
    value=os.environ.get(venue.upper()+'_REVIEW_FEE_PERCENT')
    try:fee=gate_review.decimal(value if value is not None else '0.2',False)
    except ValueError:raise ValueError('复核手续费配置无效') from None
    if fee>5:raise ValueError('复核手续费不得超过5%')
    return fee,value is not None

def review_venue(venue,symbol,chain,ca,buy,config):
    with localcontext() as context:
        context.prec=80
        return _review_venue(venue,symbol,chain,ca,buy,config)

def _review_venue(venue,symbol,chain,ca,buy,config):
    result={'venue':venue,'symbol':symbol,'identity_verified':False,'deposit_enabled':None,
            'conditional_net_usdt':None,'executable':False,'blocked_reasons':[]}
    reasons=result['blocked_reasons']
    try:
        p=market(venue,symbol)
        result['pair']=p['pair']
        if not p['enabled']:reasons.append('卖出交易未开放')
        try:
            ns=networks(venue,symbol);n=match_network(ns,chain,ca,venue,symbol,config)
            result['networks']=ns;result['identity_verified']=n is not None
            result['deposit_enabled']=n.get('deposit') if n else None
            if not n:reasons.append('链和完整CA身份未核实')
            elif n.get('deposit') is not True:reasons.append('匹配网络充值关闭或未知')
            result['deposit_minimum']=n.get('minimum') if n else None
            result['confirmations']=n.get('confirmations') if n else None
            if n and n.get('deposit_fee') not in (None,'',0,'0'):reasons.append('充值费用需要单独核实')
        except Exception as error:
            reasons.append(str(error) if isinstance(error,ValueError) else '充值网络接口失败；身份及充值状态待核实')
        b=book(venue,symbol);result.update(book_observed_at=b['observed_at'],book_source_timestamp=b['source_timestamp'],valid_until=min(b['valid_until'],buy['valid_until']))
        if not buy.get('min_quantity'):raise ValueError(buy.get('error','未取得实际买入报价'))
        quantity=Decimal(buy['min_quantity'])
        step=gate_review.decimal(str(p['step']))
        quantity=(quantity//step)*step
        if quantity<=0:raise ValueError('报价数量不足最小下单单位')
        result['quantity']=str(quantity);result['dust_quantity']=str(Decimal(buy['min_quantity'])-quantity)
        depth=gate_review.sell_depth(b['bids'],quantity);result.update(depth)
        if Decimal(depth['unfilled_quantity'])>0:reasons.append('前100档买盘不足，不能外推全部卖出收入')
        if p['minimum'] is None:reasons.append('最小下单数量未知')
        elif quantity<gate_review.decimal(str(p['minimum']),False):reasons.append('数量不足最小下单门槛')
        gross=Decimal(depth['gross_usdt'])
        if p['notional'] is not None and gross<gate_review.decimal(str(p['notional']),False):reasons.append('成交额不足最小下单门槛')
        fee,provided=fee_setting(venue)
        result.update(fee_percent=str(fee),fee_source='用户配置预算' if provided else '示例预算，非账户实际费率',
                      sell_fee_usdt=str(gross*fee/100))
        if not provided:reasons.append('手续费使用0.2%示例预算，账户费率待核实')
        minimum=result.get('deposit_minimum')
        if minimum is None or minimum=='':reasons.append('最小充值额待核实')
        elif Decimal(buy['min_quantity'])<gate_review.decimal(str(minimum),False):reasons.append('到账数量低于最小充值额')
        if Decimal(depth['unfilled_quantity'])==0:
            result['conditional_net_usdt']=str(gross-gross*fee/100-Decimal(buy['cost_budget_usdt']))
        if result['conditional_net_usdt'] is not None and Decimal(result['conditional_net_usdt'])<=0:reasons.append('未覆盖本金和费用预算')
        if time.time()>result['valid_until']:reasons.append('买入报价或盘口已过期')
        result['amount_checks_passed']=not reasons
    except Exception as error:
        reasons.append(str(error) if isinstance(error,ValueError) else '交易所查询失败或无对应现货市场')
        result['amount_checks_passed']=False
    return result

def buy_quote(chain,ca,amount):
    """Use fixed-block router quotes, not the DEX screener's display price."""
    cfg=dex_quotes.CHAINS[chain];rpc=dex_quotes.RPC(chain);unit=10**cfg['decimals']
    chain_id,block,gas=rpc.batch([('eth_chainId',[]),('eth_blockNumber',[]),('eth_gasPrice',[])])
    if chain_id!=cfg['id']:raise ValueError('RPC链不符')
    meta,precision,code=rpc.batch([('eth_getBlockByNumber',[block,False]),
        ('eth_call',[{'to':ca,'data':'0x313ce567','gas':'0x186a0'},block]),('eth_getCode',[ca,block])])
    if not meta or not precision or not code or code=='0x':raise ValueError('无法核实链上代币代码或精度')
    decimals=int(precision,16)
    if not 0<=decimals<=36:raise ValueError('代币精度无效')
    block_time=int(meta['timestamp'],16)
    if not -5<=time.time()-block_time<=30:raise ValueError('RPC区块过期')
    raw=int(amount*unit)
    jobs=[(venue,raw,path) for venue in cfg['routers'] for path in dex_quotes.paths(ca,cfg['base'],cfg['wrapped'])]
    outputs=rpc.quotes(jobs,block)
    candidates=[(out,job) for out,job in zip(outputs,jobs) if out]
    if not candidates:raise ValueError('现有V2路由无买入报价；未覆盖的DEX不能据此认定无机会')
    output,job=max(candidates,key=lambda x:x[0])
    probe_in=max(1,raw//10)
    probe,native=rpc.quotes([(job[0],probe_in,job[2]),(job[0],10**18,[cfg['wrapped'],cfg['base']])],block)
    if not probe or not native or output*probe_in*100<probe*raw*95:raise ValueError('买入路径容量检查失败或价格冲击超过5%')
    # Explicit budget for buy + ERC20 transfer, not an actual gas estimate or simulation.
    fee=Decimal(int(gas,16))*Decimal(330000)*Decimal('1.5')/Decimal(10**18)*Decimal(native)/unit+Decimal(cfg['extra_fee'])
    minimum=output*9950//10000
    if time.time()-block_time>30:raise ValueError('买入报价耗时过长，已作废')
    return {'venue':job[0],'path':job[2],'block':block,'observed_at':time.time(),
            'valid_until':time.time()+30,'amount':str(amount),'stable_symbol':cfg['symbol'],
            'quantity':str(Decimal(output)/Decimal(10**decimals)),
            'min_quantity':str(Decimal(minimum)/Decimal(10**decimals)),
            'cost_budget_usdt':str(amount+fee),'gas_budget_usdt':str(fee),
            'slippage_buffer_bps':50,'simulation_passed':False,'token_tax_verified':False,
            'stable_usd_parity_assumed':True,
            'limitations':['V2路由报价，未模拟真实买入或代币转账','Gas为预算，代币税未验证',
                           'USDT/USDC按名义1:1比较，汇率未独立核实','充值到账时间、地址及账户限制未验证']}

def validate(data):
    if not isinstance(data,dict) or set(data)!={'chain','ca','symbol','amount'}:raise ValueError('跨平台复核参数无效')
    chain=data['chain'];ca=data['ca'];symbol=data['symbol']
    if chain not in dex_quotes.CHAINS:raise ValueError('自动买入报价暂支持BSC、Base、Ethereum')
    if not isinstance(ca,str) or not re.fullmatch('0x[0-9a-fA-F]{40}',ca):raise ValueError('请填写完整CA')
    if not isinstance(symbol,str) or not re.fullmatch('[A-Z0-9]{1,24}',symbol):raise ValueError('请填写交易所币种代码')
    amount=gate_review.decimal(data['amount'])
    if amount>100:raise ValueError('本次只读复核金额上限100单位稳定币')
    if excluded({'chain':chain,'contract':ca,'token':symbol}):raise ValueError('该资产属于排除名单')
    return chain,ca.lower(),symbol,amount

def sort_rows(rows):
    def key(row):
        net=row.get('conditional_net_usdt')
        return (not row.get('amount_checks_passed',False),not row.get('identity_verified',False),
                -Decimal(net) if net is not None else Decimal('Infinity'))
    return sorted(rows,key=key)

def review(data,config=None):
    chain,ca,symbol,amount=validate(data)
    if config is None:
        config=json.loads((pathlib.Path(__file__).resolve().parent/'config.json').read_text(encoding='utf-8'))
    try:
        with localcontext() as ctx:
            ctx.prec=80
            buy=buy_quote(chain,ca,amount)
    except Exception as error:
        buy={'amount':str(amount),'stable_symbol':dex_quotes.CHAINS[chain]['symbol'],
             'valid_until':time.time()+30,'simulation_passed':False,
             'error':str(error) if isinstance(error,ValueError) else '买入报价接口失败'}
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        rows=list(pool.map(lambda venue:review_venue(venue,symbol,chain,ca,buy,config),VENUES))
    now=time.time()
    for row in rows:
        if row.get('valid_until') is not None and row['valid_until']<now:
            row['amount_checks_passed']=False
            if '买入报价或盘口已过期' not in row['blocked_reasons']:row['blocked_reasons'].append('买入报价或盘口已过期')
    return {'chain':chain,'ca':ca,'symbol':symbol,'amount':str(amount),'buy':buy,'rows':sort_rows(rows),
            'observed_at':now,'valid_until':min([buy['valid_until']]+[r.get('valid_until',0) for r in rows if r.get('valid_until')]),
            'execution_enabled':False,'executable':False,
            'message':'金额报价复核；实际买入、转账、到账及完整交易未模拟，不是确认可套利。'}

def manual_review(data):
    global LAST_MANUAL
    validate(data)
    with REQUEST_LOCK:
        if time.monotonic()-LAST_MANUAL<10:raise ValueError('请间隔10秒再复核')
        LAST_MANUAL=time.monotonic()
    return review(data)

def status():
    now=time.time()
    with LOCK:result={**STATE,'rows':list(STATE['rows']),'history':list(STATE['history'])}
    result['rows']=[{**row,'stale':now>row.get('valid_until',0)} for row in result['rows']]
    result.update(execution_enabled=False,venues=list(VENUES),amounts=[50,100],
                  binance_network_api_configured=bool(os.environ.get('BINANCE_READ_API_KEY') and os.environ.get('BINANCE_READ_API_SECRET')),
                  okx_network_api_configured=all(os.environ.get(x) for x in ('OKX_READ_API_KEY','OKX_READ_API_SECRET','OKX_READ_API_PASSPHRASE')))
    return result

def run(snapshot):
    """Bounded worker: new qualifying signals only, 10 minute per-CA cooldown."""
    gates={chain:dex_quotes.SignalGate() for chain in dex_quotes.CHAINS}
    while True:
        try:
            data=snapshot();now=time.time();jobs=[]
            for chain in dex_quotes.CHAINS:
                catalog=cached('catalog:'+chain,lambda:dex_quotes.monitored_tokens(chain),300)
                signals=dex_quotes.candidate_signals(chain,catalog,data,now)
                fresh=gates[chain].update(signals,now)
                jobs.extend((chain,t) for t in catalog if t['address'] in fresh)
            # Four assets per cycle at most; excess get another chance next cycle.
            selected=jobs[:4]
            for chain,token in jobs[4:]:
                gate=gates[chain];gate.last.pop(token['address'],None)
                gate.active={x for x in gate.active if x[0]!=token['address']}
            for chain,token in selected:
                for amount in (50,100):
                    try:
                        result=review({'chain':chain,'ca':token['address'],'symbol':token['symbol'].upper(),'amount':str(amount)})
                    except Exception as error:
                        result={'chain':chain,'ca':token['address'],'symbol':token['symbol'],'amount':str(amount),
                                'rows':[],'observed_at':time.time(),'valid_until':0,
                                'error':str(error) if isinstance(error,ValueError) else '自动复核接口失败'}
                    with LOCK:
                        STATE['rows']=[r for r in STATE['rows'] if (r['chain'],r['ca'],r['amount'])!=(chain,token['address'],str(amount))]
                        STATE['rows'].insert(0,result);del STATE['rows'][80:]
                        STATE.update(updated=time.time(),checked=STATE['checked']+1,error='')
                        STATE['history'].insert(0,{'chain':chain,'symbol':token['symbol'],'amount':amount,'time':time.time(),'error':result.get('error','')})
                        del STATE['history'][30:]
                    print('CEX_REVIEW',json.dumps(result,ensure_ascii=False),flush=True)
        except Exception:
            with LOCK:STATE['error']='自动复核本轮失败，稍后重查'
        time.sleep(15)

