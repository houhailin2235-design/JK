"""Last-mile on-chain liquidity evidence for opportunity notifications."""
import math
import re
import threading
import time
import gate_review

TYPES={'cex_crash','indicative_spread','extreme_spread','multi_pool_price_drop','arb_candidate'}
META={}
LOCK=threading.Lock()

def gate_identities(symbol):
    if not isinstance(symbol,str) or not re.fullmatch('[A-Z0-9]{1,24}',symbol):return []
    with LOCK:cached=META.get(symbol)
    if cached and time.monotonic()-cached[0]<300:return cached[1]
    data=gate_review.fetch('/spot/currencies/'+symbol)
    if not isinstance(data,dict) or data.get('currency')!=symbol:raise ValueError('Gate identity unavailable')
    identities=[]
    for network in data.get('chains',[]):
        if not isinstance(network,dict):continue
        address=network.get('addr','')
        if not isinstance(address,str) or not re.fullmatch('0x[0-9a-fA-F]{40}',address):continue
        for chain,aliases in gate_review.ALIASES.items():
            if str(network.get('name','')).upper() in aliases:identities.append((chain,address.lower()))
    with LOCK:
        if len(META)>=2000:META.clear()
        META[symbol]=(time.monotonic(),identities)
    return identities

def good_pool(row,config,now):
    for field,minimum in [('liquidity',max(50000,config['min_liquidity_usd'])),('volume_h24',config['min_volume_h24_usd'])]:
        value=row.get(field)
        if not isinstance(value,(int,float)) or not math.isfinite(value) or value<minimum:return False
    return bool(row.get('quality_ok') and row.get('eligible') and not row.get('pool_outlier')
                and -5<=now-row.get('observed_at',0)<=60)

def evaluate(alert,pools,config,now,resolve_gate=gate_identities):
    if alert.get('type') not in TYPES:return {}
    if alert.get('type')=='cex_crash' and alert.get('drop_percent',0)<25:
        return {'push_liquidity_verified':False,'push_liquidity_reason':'交易所跌幅未达到25%推送门槛'}
    identities=[];basis='same_contract_pool'
    if alert.get('type')=='cex_crash':
        venue=alert.get('venue') or alert.get('chain');symbol=alert.get('symbol') or alert.get('token')
        basis='exchange_identity_and_pool'
        if venue=='Gate':
            try:identities=resolve_gate(symbol)
            except Exception:pass  # Identity failure cannot qualify an opportunity.
        else:
            for mapping in config.get('verified_cex_mappings',[]):
                if (venue in mapping.get('verified_venues',['OKX']) and mapping.get('okx_instrument','').split('-')[0]==symbol):
                    identities.append((mapping['chain'],mapping['address'].lower()))
    else:
        contract=alert.get('contract')
        if isinstance(contract,str):identities=[(alert.get('chain'),contract.lower())]
    matched=[p for p in pools if (p.get('chain'),str(p.get('contract','')).lower()) in identities]
    # DEX events already carry their own observed pool, but it must pass all gates.
    if alert.get('type')!='cex_crash' and not matched and alert.get('liquidity') is not None:matched=[alert]
    qualified=[p for p in matched if good_pool(p,config,now)]
    chosen=max(qualified,key=lambda x:x['liquidity']) if qualified else None
    # For a DEX price event the reported pool itself must be qualified, not a larger peer.
    if chosen and alert.get('type') in ('indicative_spread','extreme_spread','multi_pool_price_drop'):
        chosen=alert if good_pool(alert,config,now) else None
    maximum=max((p.get('liquidity',0) for p in matched if isinstance(p.get('liquidity'),(int,float)) and math.isfinite(p['liquidity'])),default=0)
    result={'push_liquidity_verified':bool(chosen),'push_liquidity_basis':basis,
            'push_liquidity_reason':'有新鲜合格链上池，仅流动性检查通过，非利润确认' if chosen else '链上池流动性/活跃度不足或数据过期' if matched else '未取得交易所身份匹配的链上合格池',
            'observed_max_liquidity_usd':maximum}
    if chosen:result.update(push_pool_liquidity=chosen['liquidity'],push_pool_observed_at=chosen['observed_at'],push_pool_chain=chosen['chain'],push_pool_ca=chosen['contract'],push_pool_url=chosen.get('url',''))
    return result
