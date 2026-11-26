"""Read-only multi-chain round-trip router quotes. Never signs or broadcasts transactions."""
import json
from decimal import Decimal, InvalidOperation
import logging
import os
import pathlib
from asset_filter import excluded
import re
import threading
import time
import urllib.request
from logging.handlers import RotatingFileHandler
from runtime_paths import STATE_ROOT

USDT='0x55d398326f99059ff775485246999027b3197955'
WBNB='0xbb4cdb9cbd36b01bd1cbaebf2de08d9173bc095c'
TOKENS={'WBNB':WBNB,'CAKE':'0x0e09fabb73bd3ade0a17ecc321fd13a19e81ce82',
        'ETH':'0x2170ed0880ac9a755fd29b2688956bd959f933f8',
        'BTCB':'0x7130d2a12b9bcbfae4f2634d864a1ee1ce3ead9c'}
ROUTERS={'PancakeSwap V2':'0x10ed43c718714eb63d5aa57b78b54704e256024e',
         'Biswap V2':'0x3a6d8ca21d1cf76f653a67577fa0d27453350dd8'}
CHAINS={
 'bsc':{'id':'0x38','rpc':'https://bsc-rpc.publicnode.com','symbol':'USDT','base':USDT,'decimals':18,'wrapped':WBNB,'routers':ROUTERS,'tokens':TOKENS,'extra_fee':0},
 'base':{'id':'0x2105','rpc':'https://base-rpc.publicnode.com','symbol':'USDC','base':'0x833589fcd6edb6e08f4c7c32d4f71b54bda02913','decimals':6,'wrapped':'0x4200000000000000000000000000000000000006',
 'routers':{'Uniswap V2':'0x4752ba5dbc23f44d87826276bf6fd6b1c372ad24','SushiSwap V2':'0x6bded42c6da8fbf0d2ba55b2fa120c5e0c8d7891'},
 'tokens':{'WETH':'0x4200000000000000000000000000000000000006','cbBTC':'0xcbb7c0000ab88b473b1f5afd9ef808440eed33bf','AERO':'0x940181a94a35a4569e4529a3cdfb74e38fd98631'},'extra_fee':1},
 'ethereum':{'id':'0x1','rpc':'https://ethereum-rpc.publicnode.com','symbol':'USDC','base':'0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48','decimals':6,'wrapped':'0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2',
 'routers':{'Uniswap V2':'0x7a250d5630b4cf539739df2c5dacb4c659f2488d','SushiSwap V2':'0xd9e1ce17f2641f24ae83637ab66a2cca9c378b9f'},
 'tokens':{'WETH':'0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2','WBTC':'0x2260fac5e5542a773aa44fbcfedf7c193bc2c599','LINK':'0x514910771af9ca656af840dff83e8264ecf986ca','USDT':'0xdac17f958d2ee523a2206206994597c13d831ec7'},'extra_fee':0}
}
SIZES=(50,100,300)
LOCK=threading.Lock()
INITIAL={'chain':'bsc','mode':'quotes_only','execution_enabled':False,'updated':None,
       'error':'等待首次符合条件的信号','rows':[],'rounds':0,'positive_quote_samples':0,
       'confirmed_executable_trades':0,'poll_seconds':60,'history':[]}

STATES={chain:{**INITIAL,'chain':chain,'stable_symbol':cfg['symbol'],'rows':[],'history':[]} for chain,cfg in CHAINS.items()}
STATE=STATES['bsc']

def encode_quote(amount,path):
    if not 0<amount<2**256 or not 2<=len(path)<=3:raise ValueError('invalid quote')
    if any(not re.fullmatch(r'0x[0-9a-fA-F]{40}',x) for x in path):raise ValueError('invalid address')
    words=[amount,64,len(path)]+[int(x,16) for x in path]
    return '0xd06ca61f'+''.join(f'{x:064x}' for x in words)

def decode_quote(value,amount,path):
    if not isinstance(value,str) or not re.fullmatch(r'0x[0-9a-fA-F]+',value):raise ValueError('bad ABI')
    raw=value[2:]
    if len(raw)!=(len(path)+2)*64:raise ValueError('bad ABI size')
    words=[int(raw[i:i+64],16) for i in range(0,len(raw),64)]
    if words[:2]!=[32,len(path)] or words[2]!=amount or min(words[2:])<=0:raise ValueError('bad amounts')
    return words[-1]

class RPC:
    def __init__(self,chain='bsc'):
        self.chain=chain
        self.routers=CHAINS[chain]['routers']
        self.url=os.environ.get(chain.upper()+'_QUOTE_RPC_URL',CHAINS[chain]['rpc'])
        if not self.url.startswith('https://'):raise ValueError('报价RPC必须使用HTTPS')
    def batch(self,calls):
        # Only a fixed set of read methods can leave this component.
        if any(m not in ('eth_chainId','eth_blockNumber','eth_getBlockByNumber','eth_call','eth_getCode','eth_gasPrice') for m,p in calls):
            raise ValueError('read-only RPC')
        answers=[]
        for start in range(0,len(calls),50):
            chunk=calls[start:start+50]
            payload=[{'jsonrpc':'2.0','id':i,'method':m,'params':p} for i,(m,p) in enumerate(chunk)]
            req=urllib.request.Request(self.url,data=json.dumps(payload).encode(),headers={'Content-Type':'application/json','User-Agent':'RadarQuoteStudy/1.0'})
            with urllib.request.urlopen(req,timeout=12) as r:raw=r.read(2_000_001)
            if len(raw)>2_000_000:raise ValueError('RPC响应过大')
            result=json.loads(raw)
            if not isinstance(result,list):raise ValueError('RPC不支持批量报价')
            by_id={x.get('id'):x for x in result if isinstance(x,dict)}
            if len(result)!=len(chunk) or set(by_id)!=set(range(len(chunk))):raise ValueError('RPC响应不完整')
            answers.extend(by_id[i].get('result') if not by_id[i].get('error') else None for i in range(len(chunk)))
        return answers
    def quotes(self,jobs,block):
        result=self.batch([('eth_call',[{'to':self.routers[venue],'data':encode_quote(amount,path),'gas':'0x61a80'},block]) for venue,amount,path in jobs])
        out=[]
        for value,(_,amount,path) in zip(result,jobs):
            try:out.append(decode_quote(value,amount,path))
            except (ValueError,TypeError):out.append(None)
        return out

def paths(token,base=USDT,wrapped=WBNB):
    return [[base,token]] if token==wrapped else [[base,token],[base,wrapped,token]]

def cost_result(amount,out,gas_usdt,slippage_bps=50,decimals=18):
    # Router outputs already include pool fees and price impact. Allow 0.5% on EACH leg.
    conservative=out*(10000-slippage_bps)**2//10000**2
    net=(conservative-amount)/10**decimals-gas_usdt
    return {'output_usdt':out/10**decimals,'gross_usdt':(out-amount)/10**decimals,
            'gas_budget_usdt':gas_usdt,'slippage_buffer_bps_per_leg':slippage_bps,
            'buffered_net_usdt':round(net,8),'positive_quote':net>0,
            'execution_simulated':False,'executable':False,'token_tax_verified':False}

def screen_routes(rpc,rows,block):
    """Fail closed on missing capacity probes; never blacklist an entire token.

    Compare each actual leg with a 1/10-size quote at the same block. A >5%
    deterioration in output per input rejects the route for this amount.
    This is a quote-based depth check, not a reserve audit or swap simulation.
    """
    jobs=[];pending=[]
    for row in rows:
        row['route_quality_passed']=False
        row['depth_check']='not_run'
        if row['output_usdt']<row['amount_usdt']*.95:
            row['rejection_reason']='往返本金损失超过5%；可能存在浅池或价格异常'
        elif row['buffered_net_usdt']<=0:
            row['rejection_reason']='往返报价未覆盖费用预算'
        else:
            pending.append(row)
            for venue,amount,path in [(row['buy_venue'],int(row['buy_input_raw']),row['buy_path']),
                                      (row['sell_venue'],int(row['buy_output_raw']),row['sell_path'])]:
                jobs.append((venue,max(1,amount//10),path))
            continue
        row['positive_quote']=False
    try:probes=rpc.quotes(jobs,block) if jobs else []
    except Exception:probes=[]  # Missing depth data cannot qualify a candidate.
    for i,row in enumerate(pending):
        small=probes[i*2:i*2+2]
        actual=[(int(row['buy_input_raw']),int(row['buy_output_raw'])),
                (int(row['buy_output_raw']),int(row['sell_output_raw']))]
        impacts=[];exceeded=[]
        for j,(amount,out) in enumerate(actual):
            probe=small[j] if len(small)>j else None
            probe_in=jobs[i*2+j][1]
            if not probe or amount<=probe_in:
                impacts.append(None);exceeded.append(False)
            else:
                impacts.append(round(max(0.,100*(1-out*probe_in/(probe*amount))),6))
                exceeded.append(out*probe_in*100<probe*amount*95)
        row['leg_price_impact_percent']=impacts
        if any(x is None for x in impacts):
            row['depth_check']='unavailable';row['rejection_reason']='路径容量检查未取得完整报价'
        elif any(exceeded):
            row['depth_check']='rejected';row['rejection_reason']='单腿报价价格冲击超过5%，当前金额不适用'
        else:
            row['depth_check']='passed';row['route_quality_passed']=True;row['rejection_reason']=''
        row['positive_quote']=row['positive_quote'] and row['route_quality_passed']
    return rows

def monitored_tokens(chain):
    root=pathlib.Path(__file__).resolve().parent
    cfg=json.loads((root/'config.json').read_text(encoding='utf-8'))
    universe=json.loads((root/'universe.json').read_text(encoding='utf-8'))
    blocked={(x['chain'],x['address'].lower()) for x in cfg.get('blocked_tokens',[])}
    found={}
    for item in cfg['tokens']+universe['candidates']:
        address=item.get('address','').lower()
        if item.get('chain')!=chain or not re.fullmatch(r'0x[0-9a-f]{40}',address):continue
        if (chain,address) in blocked or excluded(item):continue
        found[address]=item.get('label',address[:10])
    return [{'address':address,'symbol':symbol} for address,symbol in sorted(found.items()) if address!=CHAINS[chain]['base']]

def collect(rpc,chain_name='bsc',selected=None,amounts=SIZES):
    cfg=CHAINS[chain_name];routers=cfg['routers'];tokens=cfg['tokens']
    base=cfg['base'];wrapped=cfg['wrapped'];unit=10**cfg['decimals']
    if selected is not None:tokens={x['address']:x['address'] for x in selected}
    labels_by_address={x['address']:x['symbol'] for x in selected} if selected is not None else {v:k for k,v in tokens.items()}
    chain,block,gas=rpc.batch([('eth_chainId',[]),('eth_blockNumber',[]),('eth_gasPrice',[])])
    if chain!=cfg['id']:raise ValueError('RPC链不符：'+chain_name)
    gas=int(gas,16)
    meta,*codes=rpc.batch([('eth_getBlockByNumber',[block,False])]+[('eth_getCode',[r,block]) for r in routers.values()])
    if not isinstance(meta,dict) or meta.get('number')!=block:raise ValueError('区块响应不符')
    timestamp=int(meta['timestamp'],16)
    if not -5<=time.time()-timestamp<=30:raise ValueError('RPC区块已过期')
    if any(not x or x=='0x' for x in codes):raise ValueError('路由合约不存在')
    decimals=rpc.batch([('eth_call',[{'to':address,'data':'0x313ce567','gas':'0x186a0'},block]) for address in [base,*tokens.values()]])
    if not isinstance(decimals[0],str) or int(decimals[0],16)!=cfg['decimals']:raise ValueError('报价稳定币精度不符')
    invalid={address for address,value in zip(tokens.values(),decimals[1:]) if not isinstance(value,str) or not 0<=int(value,16)<=36}
    tokens={symbol:address for symbol,address in tokens.items() if address not in invalid}
    # Native coin fee conversion, pinned to the same block as both swap legs.
    price=rpc.quotes([(next(iter(routers)),10**18,[wrapped,base])],block)[0]
    if not price:raise ValueError('无法获取原生币费用换算报价')
    # Budget only: no executor exists yet, so this is deliberately NOT eth_estimateGas.
    gas_budget=gas*900_000/10**18*(price/unit)*1.5+cfg['extra_fee']
    buys=[];labels=[]
    for symbol,token in tokens.items():
        for size in amounts:
            for venue in routers:
                for path in paths(token,base,wrapped):
                    buys.append((venue,int(size*unit),path));labels.append((symbol,size))
    first=rpc.quotes(buys,block)
    sells=[];details=[]
    for buy,(symbol,size),received in zip(buys,labels,first):
        if not received:continue
        for venue in routers:
            if venue==buy[0]:continue
            for path in paths(tokens[symbol],base,wrapped):
                sells.append((venue,received,list(reversed(path))))
                details.append((symbol,size,buy,received))
    second=rpc.quotes(sells,block) if sells else []
    best={}
    for sell,(symbol,size,buy,received),returned in zip(sells,details,second):
        if not returned:continue
        row={'chain':chain_name,'stable_symbol':cfg['symbol'],'extra_fee_budget':cfg['extra_fee'],'token':labels_by_address[tokens[symbol]],'ca':tokens[symbol],'amount_usdt':float(size),'buy_venue':buy[0],
             'sell_venue':sell[0],'buy_path':buy[2],'sell_path':sell[2],
             'buy_input_raw':str(buy[1]),'buy_output_raw':str(received),'sell_output_raw':str(returned),
             **cost_result(int(size*unit),returned,gas_budget,decimals=cfg['decimals'])}
        key=(symbol,size,buy[0],sell[0])
        if key not in best or row['output_usdt']>best[key]['output_usdt']:best[key]=row
    screen_routes(rpc,list(best.values()),block)
    if time.time()-timestamp>30:raise ValueError('本轮报价耗时过长，结果作废')
    check=rpc.batch([('eth_getBlockByNumber',[block,False])])[0]
    if not isinstance(check,dict) or check.get('hash')!=meta.get('hash'):raise ValueError('区块发生重组，结果作废')
    rows=sorted(best.values(),key=lambda r:r['buffered_net_usdt'],reverse=True)
    checked_at=time.time()
    for row in rows:row['observed_at']=checked_at;row['block']=int(block,16)
    covered={r['ca'] for r in rows}
    coverage=[{'chain':chain_name,'ca':address,'token':label,'checked_at':checked_at,'outcome':'代币精度调用失败' if address in invalid else '有双向报价；未验证真实兑换' if address in covered else '本批V2路径无双向报价'} for address,label in labels_by_address.items()]
    return {'block':int(block,16),'block_timestamp':timestamp,'rows':rows,'coverage':coverage,
            'attempted_buy_quotes':len(buys),'successful_buy_quotes':sum(x is not None for x in first),
            'attempted_sell_quotes':len(sells),'successful_sell_quotes':sum(x is not None for x in second)}

def status():
    with LOCK:chains={c:dict(s) for c,s in STATES.items()}
    rows=[]
    for chain,s in chains.items():
        s['stale']=bool(s['error']) or s['updated'] is None or time.time()-s['updated']>90
        rows.extend({**r,'stale':s['stale'] or time.time()-r.get('observed_at',0)>90} for r in s['rows'])
    return {'mode':'quotes_only','execution_enabled':False,'execution_stage':'deployment_and_simulation_required','chains':chains,
            'rows':sorted(rows,key=lambda r:r['buffered_net_usdt'],reverse=True)}

MANUAL_LAST=0
def manual_quote(data):
    global MANUAL_LAST
    if not isinstance(data,dict) or set(data)!={'chain','ca','amount'}:raise ValueError('金额复核参数无效')
    chain=data['chain']
    if chain not in CHAINS or not isinstance(data['ca'],str):raise ValueError('不支持的链或CA')
    try:amount=Decimal(str(data['amount']))
    except InvalidOperation:raise ValueError('金额格式无效') from None
    unit=10**CHAINS[chain]['decimals']
    if not amount.is_finite() or not 1<=amount<=10000 or amount*unit!=(amount*unit).to_integral_value():raise ValueError('测算金额需为1至10000，且符合该稳定币精度')
    selected=[x for x in monitored_tokens(chain) if x['address']==data['ca'].lower()]
    if not selected:raise ValueError('CA不在当前监控候选目录')
    with LOCK:
        if time.time()-MANUAL_LAST<10:raise ValueError('复核过于频繁，请10秒后再试')
        MANUAL_LAST=time.time()
    try:sample=collect(RPC(chain),chain,selected,(amount,))
    except Exception:raise ValueError('本次复核未取得有效报价，请稍后重试；未发送交易') from None
    return {**sample,'stable_symbol':CHAINS[chain]['symbol'],'execution_enabled':False,
            'message':'仅完成指定金额报价；执行合约尚未部署，此步不执行交易'}

def notify_candidate(row):
    import wechat_push
    if not row.get('route_quality_passed') or not row.get('positive_quote_rechecked') or row.get('buffered_net_usdt',0)<=0:return
    wechat_push.enqueue({'type':'arb_candidate','chain':row['chain'],'contract':row['ca'],'token':row['token'],
        'observed_at':row['observed_at'],'positive_quote_rechecked':True,'buffered_net_usdt':row['buffered_net_usdt'],
        'news_title':'套利报价候选 · 请确认金额',
        'news_content':f"链：{row['chain']}\n代币：{row['token']}\nCA：{row['ca']}\n参考金额：{row['amount_usdt']} {row['stable_symbol']}\n买入：{row['buy_venue']} → 卖出：{row['sell_venue']}\n扣预算后参考差额：{row['buffered_net_usdt']} {row['stable_symbol']}\n已复查报价，尚未验证真实兑换、代币税和最终费用，不代表可执行利润。\n请打开看板的多链套利测算，填写本次金额重新复核。\nhttps://p01--xlin--2wwbcbclk59q.code.run/"})

def candidate_signals(chain,catalog,snapshot,now):
    known={x['address'] for x in catalog};signals={}
    def add(address,reason):
        if address in known:signals.setdefault(address,set()).add(reason)
    for p in snapshot.get('pairs',[]):
        if p.get('chain')!=chain or not p.get('eligible') or now-p.get('observed_at',0)>60:continue
        gap=p.get('indicative_spread_percent')
        if isinstance(gap,(int,float)) and abs(gap)>25:add(p.get('contract','').lower(),'spread')
    for alert in snapshot.get('alerts',[]):
        if not 0<=now-alert.get('time',0)<=90 or alert.get('drop_percent',0)<25:continue
        if alert.get('type') not in ('cex_crash','multi_pool_price_drop'):continue
        if alert.get('chain')==chain:add(alert.get('contract','').lower(),'crash')
        elif alert.get('type')=='cex_crash':
            # Symbol matching only nominates a read-only probe; it never verifies asset identity.
            for token in catalog:
                if token['symbol'].upper()==alert.get('token','').upper():add(token['address'],'crash')
    return signals

class SignalGate:
    def __init__(self):self.active=set();self.last={}
    def update(self,signals,now):
        current={(address,reason) for address,reasons in signals.items() for reason in reasons}
        fresh={address for address,reason in current-self.active if now-self.last.get(address,-1e20)>=600}
        for address in fresh:self.last[address]=now
        self.active=current
        return fresh

def run_chain(chain,source):
    state=STATES[chain];latest={};coverage={};pending={};gate=SignalGate();catalog=[];loaded=0;last_query=0;confirm={}
    logger=logging.getLogger('radar.dex_quotes.'+chain);logger.setLevel(logging.INFO);logger.propagate=False
    if not logger.handlers:
        logger.addHandler(RotatingFileHandler(STATE_ROOT/('dex-quotes-'+chain+'.jsonl'),maxBytes=2_000_000,backupCount=2,encoding='utf-8'))
    while True:
        batch=[]
        try:
            now=time.time()
            if now-loaded>=60:
                catalog=monitored_tokens(chain);loaded=now
                active={x['address'] for x in catalog}
                latest={k:r for k,r in latest.items() if r['ca'] in active}
                coverage={k:r for k,r in coverage.items() if k in active}
                pending={k:v for k,v in pending.items() if k in active}
            signals=candidate_signals(chain,catalog,source(),now)
            by_address={x['address']:x for x in catalog}
            for address in gate.update(signals,now):
                pending[address]=now
                confirm.pop(address,None)
            # Expired signals must not cause delayed network requests.
            pending={a:t for a,t in pending.items() if a in signals and now-t<=90}
            with LOCK:
                state.update(total_tokens=len(catalog),checked_tokens=len(coverage),pending_tokens=len(pending),
                    coverage=[coverage.get(x['address'],{'chain':chain,'ca':x['address'],'token':x['symbol'],'checked_at':None,'outcome':'等待暴跌≥25%或参考价差>25%'}) for x in catalog])
            if not pending or now-last_query<15:
                time.sleep(5);continue
            batch=[by_address[a] for a in list(pending)[:12]]
            for item in batch:pending.pop(item['address'],None)
            last_query=now
            sample=collect(RPC(chain),chain,batch);now=time.time();batch_rows=sample['rows']
            for row in batch_rows:
                prior=confirm.get(row['ca'])
                row['positive_quote_rechecked']=bool(row['positive_quote'] and prior and row['block']>prior['block'] and (row['amount_usdt'],row['buy_venue'],row['sell_venue']) in prior['routes'])
            for row in sorted(batch_rows,key=lambda r:r['buffered_net_usdt'],reverse=True):notify_candidate(row)
            for address in {r['ca'] for r in batch_rows if r['positive_quote']}:
                if address not in confirm:
                    confirm[address]={'block':sample['block'],'routes':{(r['amount_usdt'],r['buy_venue'],r['sell_venue']) for r in batch_rows if r['ca']==address and r['positive_quote']}}
                    pending[address]=now  # One follow-up quote only; still never executes.
            addresses={x['address'] for x in batch}
            latest={k:r for k,r in latest.items() if r['ca'] not in addresses}
            for row in batch_rows:
                row['trigger']=' / '.join(sorted(signals.get(row['ca'],[])))
                latest[(row['ca'],row['amount_usdt'],row['buy_venue'],row['sell_venue'])]=row
            for item in sample['coverage']:coverage[item['ca']]=item
            positive=sum(r['positive_quote'] for r in batch_rows)
            summary={'time':now,'block':sample['block'],'routes':len(batch_rows),'positive_quotes':positive,
                     'best_buffered_net_usdt':max(r['buffered_net_usdt'] for r in batch_rows) if batch_rows else None}
            sample['rows']=list(latest.values())
            sample['coverage']=[coverage.get(x['address'],{'chain':chain,'ca':x['address'],'token':x['symbol'],'checked_at':None,'outcome':'等待初筛信号'}) for x in catalog]
            with LOCK:
                state.update(**sample,updated=now,error='',checked_tokens=len(coverage),batch_tokens=len(batch),
                             rounds=state['rounds']+1,positive_quote_samples=state['positive_quote_samples']+positive,
                             history=(state['history']+[summary])[-1440:])
            logger.info(json.dumps({'time':now,'chain':chain,'block':sample['block'],'rows':batch_rows,'checked_addresses':[x['address'] for x in batch]},ensure_ascii=False))
        except Exception as e:
            now=time.time()
            for item in batch:coverage[item['address']]={'chain':chain,'ca':item['address'],'token':item['symbol'],'checked_at':now,'outcome':'本次RPC失败 / 超时，未取得有效报价'}
            with LOCK:state['error']=str(e) if isinstance(e,ValueError) and str(e).startswith(('RPC','区块','路由','报价','无法','本轮')) else 'RPC报价失败；本信号已去重，等待下一次新信号'
        time.sleep(5)

def run(source=None):
    if source is None:source=lambda:{'pairs':[],'alerts':[]}
    workers=[threading.Thread(target=run_chain,args=(chain,source),daemon=True) for chain in CHAINS]
    for worker in workers:worker.start()
    for worker in workers:worker.join()
