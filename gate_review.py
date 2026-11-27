"""Read-only Gate sell-leg review. No credentials, orders, transfers or signing."""
import concurrent.futures
from decimal import Decimal,InvalidOperation
import json
import re
import threading
import time
import urllib.request

ALIASES={'ethereum':{'ETH','ERC20'},'bsc':{'BSC','BEP20'},'base':{'BASE'},'arbitrum':{'ARBEVM','ARBITRUM','ARB'},'polygon':{'MATIC','POLYGON'},'linea':{'LINEA'},'mantle':{'MANTLE'},'scroll':{'SCROLL'},'zksync':{'ZKSYNC','ZKSYNCERA'}}
LOCK=threading.Lock()
LAST=0

def decimal(value,positive=True):
    if not isinstance(value,(str,int)) or not re.fullmatch(r'\d+(?:\.\d{1,24})?',str(value)):raise ValueError('金额格式无效，请使用普通正数')
    try:n=Decimal(value)
    except InvalidOperation:raise ValueError('金额格式无效') from None
    if not n.is_finite() or n<0 or (positive and n==0) or n>Decimal('1000000000000000'):raise ValueError('金额超出范围')
    return n

def fetch(path):
    req=urllib.request.Request('https://api.gateio.ws/api/v4'+path,headers={'Accept':'application/json','User-Agent':'RadarGateReview/1.0'})
    with urllib.request.urlopen(req,timeout=12) as response:body=response.read(1_000_001)
    if len(body)>1_000_000:raise ValueError('Gate响应过大')
    return json.loads(body)

def sell_depth(bids,quantity):
    if not isinstance(bids,list) or not bids:raise ValueError('Gate没有可用买盘')
    levels=[];seen=set()
    for level in bids:
        if not isinstance(level,list) or len(level)!=2:raise ValueError('Gate盘口格式异常')
        price,size=map(decimal,level)
        if price in seen:raise ValueError('Gate盘口价格重复，结果作废')
        seen.add(price);levels.append((price,size))
    levels.sort(reverse=True)
    left=quantity;gross=Decimal(0)
    for price,size in levels:
        filled=min(left,size);gross+=filled*price;left-=filled
        if left==0:break
    return {'filled_quantity':str(quantity-left),'unfilled_quantity':str(left),'gross_usdt':str(gross),
            'average_sell_price':str(gross/(quantity-left)) if quantity>left else '0',
            'best_bid':str(levels[0][0]),'best_bid_capacity_usdt':str(levels[0][0]*levels[0][1])}

def evaluate(data,currency,pair,book,now):
    quantity=decimal(data['quantity']);cost=decimal(data['cost_usdt']);fee=decimal(data['fee_percent'],False)
    if fee>5:raise ValueError('卖出手续费率请填写0至5之间的百分数')
    reasons=[]
    if not isinstance(currency,dict) or not isinstance(pair,dict) or not isinstance(book,dict):raise ValueError('Gate接口响应异常')
    code=data['currency']
    if currency.get('currency')!=code or pair.get('id')!=code+'_USDT' or pair.get('base')!=code or pair.get('quote')!='USDT':raise ValueError('Gate资产或交易对不符')
    networks=currency.get('chains',[])
    if not isinstance(networks,list) or any(not isinstance(x,dict) for x in networks):raise ValueError('Gate网络字段异常')
    selected=[x for x in networks if str(x.get('name','')).upper() in ALIASES[data['chain']] and str(x.get('addr','')).lower()==data['ca'].lower()]
    if not selected:reasons.append('Gate未返回与所选链及CA同时匹配的充值网络；不可直接转入')
    deposit_ok=bool(selected) and currency.get('deposit_disabled') is False and any(x.get('deposit_disabled') is False for x in selected)
    if selected and not deposit_ok:reasons.append('匹配网络充值关闭或状态不明')
    if currency.get('delisted') is not False or currency.get('trade_disabled') is not False or pair.get('trade_status') not in ('tradable','sellable'):reasons.append('Gate卖出交易关闭或状态不明')
    age=now-float(book.get('update',0))/1000
    if not -5<=age<=15:reasons.append('Gate盘口超过15秒或时间异常，请重新复核')
    depth=sell_depth(book.get('bids'),quantity)
    if Decimal(depth['unfilled_quantity'])>0:reasons.append('前100档买盘不足以卖完指定数量；不能外推全部收益')
    precision=pair.get('amount_precision')
    if type(precision) is not int or not 0<=precision<=24:raise ValueError('Gate数量精度未知')
    if quantity!=quantity.quantize(Decimal(1).scaleb(-precision)):reasons.append('数量不符合Gate下单精度，需调整数量后复核')
    min_base=decimal(str(pair.get('min_base_amount','0')),False)
    min_quote=decimal(str(pair.get('min_quote_amount','0')),False)
    gross=Decimal(depth['gross_usdt']);fee_amount=gross*fee/100;net=gross-fee_amount-cost
    if quantity<min_base or gross<min_quote:reasons.append('数量或预计成交额未达到Gate最小下单要求')
    if net<=0:reasons.append('按所填成本及手续费估算，没有正差额')
    return {**depth,'currency':code,'chain':data['chain'],'ca':data['ca'],'quantity':str(quantity),'cost_usdt':str(cost),
            'fee_percent':str(fee),'sell_fee_usdt':str(fee_amount),'conditional_net_usdt':str(net) if Decimal(depth['unfilled_quantity'])==0 else None,
            'observed_at':now,'valid_until':min(now+15,float(book.get('update',0))/1000+15),'book_age_seconds':round(age,3),'deposit_network_matched':bool(selected),'deposit_enabled':deposit_ok,
            'networks':[{'name':x.get('name'),'ca':x.get('addr'),'deposit_disabled':x.get('deposit_disabled'),'withdraw_disabled':x.get('withdraw_disabled')} for x in networks],
            'blocked_reasons':reasons,'sell_leg_checks_passed':not reasons,'executable':False,'execution_enabled':False,
            'trade_url':'https://www.gate.com/trade/'+code+'_USDT',
            'message':'仅Gate卖出侧复核；成本和费率由你填写，未验证链上实际买入、代币税、最小充值额及到账时间。盘口不锁价，跨平台转账期间价差可能消失。未下单、未转账。'}

def review(data):
    global LAST
    if not isinstance(data,dict) or set(data)!={'chain','ca','currency','quantity','cost_usdt','fee_percent'}:raise ValueError('Gate复核参数无效')
    if data['chain'] not in ALIASES or not isinstance(data['ca'],str) or not re.fullmatch(r'0x[0-9a-fA-F]{40}',data['ca']):raise ValueError('请填写支持的EVM链及完整CA')
    if not isinstance(data['currency'],str) or not re.fullmatch(r'[A-Z0-9]{1,24}',data['currency']):raise ValueError('Gate币种代码无效')
    decimal(data['quantity']);decimal(data['cost_usdt'])
    if decimal(data['fee_percent'],False)>5:raise ValueError('手续费率不得超过5%')
    with LOCK:
        if time.monotonic()-LAST<10:raise ValueError('请间隔10秒再复核')
        LAST=time.monotonic()
    paths=['/spot/currencies/'+data['currency'],'/spot/currency_pairs/'+data['currency']+'_USDT','/spot/order_book?currency_pair='+data['currency']+'_USDT&limit=100']
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:values=list(pool.map(fetch,paths))
        return evaluate(data,*values,time.time())
    except Exception as error:
        if isinstance(error,ValueError) and str(error).startswith(('Gate','金额','卖出')):raise
        raise ValueError('Gate复核失败或超时，未取得有效结果；未下单、未转账') from None
