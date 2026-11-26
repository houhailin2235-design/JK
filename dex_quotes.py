"""Read-only BSC round-trip router quotes. Never signs or broadcasts transactions."""
import json
import logging
import os
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
SIZES=(50,100,300)
LOCK=threading.Lock()
STATE={'chain':'bsc','mode':'quotes_only','execution_enabled':False,'updated':None,
       'error':'等待首次报价','rows':[],'rounds':0,'positive_quote_samples':0,
       'confirmed_executable_trades':0,'poll_seconds':60,'history':[]}

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
    def __init__(self):
        self.url=os.environ.get('BSC_QUOTE_RPC_URL','https://bsc-rpc.publicnode.com')
        if not self.url.startswith('https://'):raise ValueError('报价RPC必须使用HTTPS')
    def batch(self,calls):
        # Only a fixed set of read methods can leave this component.
        if any(m not in ('eth_chainId','eth_blockNumber','eth_getBlockByNumber','eth_call','eth_getCode','eth_gasPrice') for m,p in calls):
            raise ValueError('read-only RPC')
        answers=[]
        for start in range(0,len(calls),12):
            chunk=calls[start:start+12]
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
        result=self.batch([('eth_call',[{'to':ROUTERS[venue],'data':encode_quote(amount,path)},block]) for venue,amount,path in jobs])
        out=[]
        for value,(_,amount,path) in zip(result,jobs):
            try:out.append(decode_quote(value,amount,path))
            except (ValueError,TypeError):out.append(None)
        return out

def paths(token):
    return [[USDT,token]] if token==WBNB else [[USDT,token],[USDT,WBNB,token]]

def cost_result(amount,out,gas_usdt,slippage_bps=50):
    # Router outputs already include pool fees and price impact. Allow 0.5% on EACH leg.
    conservative=out*(10000-slippage_bps)**2//10000**2
    net=(conservative-amount)/10**18-gas_usdt
    return {'output_usdt':out/10**18,'gross_usdt':(out-amount)/10**18,
            'gas_budget_usdt':gas_usdt,'slippage_buffer_bps_per_leg':slippage_bps,
            'buffered_net_usdt':round(net,8),'positive_quote':net>0,
            'execution_simulated':False,'executable':False}

def collect(rpc):
    chain,block,gas=rpc.batch([('eth_chainId',[]),('eth_blockNumber',[]),('eth_gasPrice',[])])
    if chain!='0x38':raise ValueError('RPC链不符，必须是BSC主网56')
    gas=int(gas,16)
    meta,*codes=rpc.batch([('eth_getBlockByNumber',[block,False])]+[('eth_getCode',[r,block]) for r in ROUTERS.values()])
    if not isinstance(meta,dict) or meta.get('number')!=block:raise ValueError('区块响应不符')
    timestamp=int(meta['timestamp'],16)
    if not -5<=time.time()-timestamp<=30:raise ValueError('RPC区块已过期')
    if any(not x or x=='0x' for x in codes):raise ValueError('路由合约不存在')
    decimals=rpc.batch([('eth_call',[{'to':address,'data':'0x313ce567'},block]) for address in [USDT,*TOKENS.values()]])
    if any(not isinstance(x,str) or int(x,16)!=18 for x in decimals):raise ValueError('报价代币精度不符')
    # Native BNB fee conversion, pinned to the same block as both swap legs.
    price=rpc.quotes([('PancakeSwap V2',10**18,[WBNB,USDT])],block)[0]
    if not price:raise ValueError('无法获取BNB费用换算报价')
    # Budget only: no executor exists yet, so this is deliberately NOT eth_estimateGas.
    gas_budget=gas*900_000/10**18*(price/10**18)*1.5
    buys=[];labels=[]
    for symbol,token in TOKENS.items():
        for size in SIZES:
            for venue in ROUTERS:
                for path in paths(token):
                    buys.append((venue,size*10**18,path));labels.append((symbol,size))
    first=rpc.quotes(buys,block)
    sells=[];details=[]
    for buy,(symbol,size),received in zip(buys,labels,first):
        if not received:continue
        for venue in ROUTERS:
            if venue==buy[0]:continue
            for path in paths(TOKENS[symbol]):
                sells.append((venue,received,list(reversed(path))))
                details.append((symbol,size,buy))
    second=rpc.quotes(sells,block) if sells else []
    best={}
    for sell,(symbol,size,buy),returned in zip(sells,details,second):
        if not returned:continue
        row={'token':symbol,'ca':TOKENS[symbol],'amount_usdt':size,'buy_venue':buy[0],
             'sell_venue':sell[0],'buy_path':buy[2],'sell_path':sell[2],
             **cost_result(size*10**18,returned,gas_budget)}
        key=(symbol,size,buy[0],sell[0])
        if key not in best or row['output_usdt']>best[key]['output_usdt']:best[key]=row
    if time.time()-timestamp>30:raise ValueError('本轮报价耗时过长，结果作废')
    check=rpc.batch([('eth_getBlockByNumber',[block,False])])[0]
    if not isinstance(check,dict) or check.get('hash')!=meta.get('hash'):raise ValueError('区块发生重组，结果作废')
    rows=sorted(best.values(),key=lambda r:r['buffered_net_usdt'],reverse=True)
    return {'block':int(block,16),'block_timestamp':timestamp,'rows':rows,
            'attempted_buy_quotes':len(buys),'successful_buy_quotes':sum(x is not None for x in first),
            'attempted_sell_quotes':len(sells),'successful_sell_quotes':sum(x is not None for x in second)}

def status():
    with LOCK:s=dict(STATE)
    s['stale']=bool(s['error']) or s['updated'] is None or time.time()-s['updated']>90
    return s

def run():
    logger=logging.getLogger('radar.dex_quotes');logger.setLevel(logging.INFO);logger.propagate=False
    if not logger.handlers:
        logger.addHandler(RotatingFileHandler(STATE_ROOT/'dex-quotes.jsonl',maxBytes=2_000_000,backupCount=2,encoding='utf-8'))
    while True:
        start=time.monotonic()
        try:
            sample=collect(RPC());now=time.time();positive=sum(r['positive_quote'] for r in sample['rows'])
            summary={'time':now,'block':sample['block'],'routes':len(sample['rows']),'positive_quotes':positive,
                     'best_buffered_net_usdt':sample['rows'][0]['buffered_net_usdt'] if sample['rows'] else None}
            with LOCK:
                history=(STATE['history']+[summary])[-1440:]
                STATE.update(**sample,updated=now,error='' if sample['rows'] else '路由没有可用双向报价',
                             rounds=STATE['rounds']+1,positive_quote_samples=STATE['positive_quote_samples']+positive,history=history)
            logger.info(json.dumps({'time':now,**sample},ensure_ascii=False))
        except Exception as e:
            # RPC URLs may contain API keys: never publish exception messages.
            with LOCK:STATE['error']=str(e) if isinstance(e,ValueError) and str(e).startswith(('RPC','区块','路由','报价','无法','本轮')) else 'RPC报价失败，保留上轮结果；请检查连接或额度'
        time.sleep(max(1,60-(time.monotonic()-start)))
