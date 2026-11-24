"""Indicative USDT bid versus DEX USD price; identity trust is explicit per venue."""
import math
from asset_filter import excluded

def rank_spreads(pools,quotes,mappings,now,max_age=60):
    index={}
    for q in quotes:
        if excluded(q):continue
        age=now-q.get('observed_at',0)
        source=q.get('source_timestamp')
        if q.get('stale') or age < -5 or age>max_age:continue
        if source is not None and not -5<=now-source<=max_age:continue
        bid=q.get('bid',0)
        if not math.isfinite(bid) or bid<=0:continue
        index.setdefault(q['symbol'].upper(),[]).append(q)
    mapping_index={(m['chain'],m['address']):m for m in mappings}
    result=[]
    for original in pools:
        if excluded(original):continue
        p=dict(original)
        for field in ['indicative_spread_percent','cex_bid','best_venue','spread_identity','venue_quotes','quarantine_reason']:
            p.pop(field,None)
        m=mapping_index.get((p['chain'],p['contract']))
        symbol=m['okx_instrument'].split('-')[0] if m else p['token'].upper()
        ratio=float(m.get('underlying_per_token',1)) if m else 1
        p['mapping_status']='没有新鲜买盘'
        if now-p.get('observed_at',0)>max_age:
            p['mapping_status']='链上采样过期';result.append(p);continue
        if not p.get('eligible'):
            p['mapping_status']='流动性/成交量不足，未排名';result.append(p);continue
        matches=[]
        for q in index.get(symbol,[]):
            verified=bool(m and q['venue'] in m.get('verified_venues',['OKX']))
            bid=q['bid']*ratio
            gap=(bid/p['price']-1)*100
            if not math.isfinite(gap):continue
            matches.append({'venue':q['venue'],'pair':q['pair'],'bid':bid,'spread_percent':round(gap,2),'identity':'verified' if verified else 'candidate','source_timestamp_available':q.get('source_timestamp') is not None})
        matches.sort(key=lambda q:q['bid'],reverse=True)
        if matches:
            verified_matches=[q for q in matches if q['identity']=='verified']
            best=(verified_matches or matches)[0]
            p.update(best_venue=best['venue'],cex=best['pair'],cex_bid=best['bid'],indicative_spread_percent=best['spread_percent'],spread_identity=best['identity'],venue_quotes=matches,mapping_status='身份已核实' if best['identity']=='verified' else '候选价差：交易所身份待核实',relationship=m.get('relationship') if m else None)
            if best['spread_percent']>300:
                p['quarantine_reason']='超过300%：疑似面值/资产身份/报价异常，隔离待核查'
            if p.get('pool_outlier'):
                p['quarantine_reason']='偏离同合约流动性最大合格池超过50%，隔离待核查'
        result.append(p)
    return sorted(result,key=lambda p:p.get('indicative_spread_percent',-math.inf),reverse=True)
