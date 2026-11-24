import math

def cross_check(rows):
    groups={}
    for r in rows:groups.setdefault((r['chain'],r['contract']),[]).append(r)
    for group in groups.values():
        valid=[r for r in group if r.get('quality_ok')]
        anchor=max(valid,key=lambda r:r['liquidity']) if valid else None
        for r in group:
            r['reference_pool']=anchor['pair'] if anchor else None
            r['pool_outlier']=bool(anchor and r['pair']!=anchor['pair'] and abs(r['price']/anchor['price']-1)>.5)
            r['pool_evidence']='单池数据，无法交叉验证' if len(valid)<2 else '同合约多池报价（同一聚合数据源）'
    return rows

def assess(row,config,now):
    reasons=[]
    if not all(math.isfinite(row.get(k,0)) and row.get(k,0)>0 for k in ('price','liquidity')):reasons.append('价格或流动性无效')
    if row.get('liquidity',0)<config['min_liquidity_usd']:reasons.append('流动性不足')
    if row.get('volume_h24',0)<config['min_volume_h24_usd']:reasons.append('24小时成交量不足')
    if row.get('trades_m5',0)<3 or row.get('volume_m5',0)<500:reasons.append('近5分钟成交不足')
    created=row.get('created_at')
    if created is None:reasons.append('缺少建池时间')
    elif now-created<1800:reasons.append('新池不足30分钟')
    row.update(quality_reasons=reasons,quality_ok=not reasons)
    row['eligible']=bool(row.get('eligible') and not reasons)
    return row

class Confirmation:
    def __init__(self):self.pending={};self.last={}
    def check(self,rows,events,config,now):
        bykey={r['key']:r for r in rows};output=[]
        for row,event in events:
            key=(row['key'],event['type'],event.get('window_seconds'))
            if now-self.last.get(key,-1e20)<config['cooldown_seconds']:continue
            self.pending.setdefault(key,{'event':event,'first':now,'last':now,'count':0,'pool':row['pair']})
        for key,pending in list(self.pending.items()):
            row=bykey.get(key[0]);event=pending['event']
            if not row or now-pending['first']>max(180,event.get('window_seconds',0)+90):self.pending.pop(key);continue
            if event['type']=='liquidity_drop':
                valid=row.get('trades_m5',0)>=3 and row.get('volume_h24',0)>=config['min_volume_h24_usd'] and row['liquidity']<=event['old_liquidity']*.7
            elif event['type']=='crash':
                threshold=dict(config['windows'])[event['window_seconds']]
                valid=row.get('quality_ok') and (1-row['price']/event['old_price'])*100>=threshold
            elif event['type']=='extreme_spread':valid=row.get('quality_ok') and row.get('indicative_spread_percent',0)>300
            else:valid=row.get('quality_ok') and not row.get('quarantine_reason') and row.get('spread_identity')=='verified' and row.get('indicative_spread_percent',0)>config['spread_percent']
            if not valid:self.pending.pop(key);continue
            if pending['count']==0 or now-pending['last']>=config['poll_seconds']*.7:
                pending['count']+=1;pending['last']=now
            if pending['count']<2:continue
            enriched=dict(event,confirmation='连续两轮采样确认（非独立数据源）')
            if event['type']=='crash':
                peers=[r for r in rows if r['key']!=row['key'] and r['chain']==row['chain'] and r['contract']==row['contract'] and r.get('quality_ok')]
                supporting=[r for r in peers if r['price']<=event['old_price']*(1-dict(config['windows'])[event['window_seconds']]/100)]
                enriched['type']='multi_pool_price_drop' if supporting else 'single_pool_anomaly'
                enriched['verification']='多池同向低价，尚非独立时间跌幅验证' if supporting else '仅单池，未证实代币整体暴跌'
                enriched['peer_pool_count']=len(peers)
                enriched['drop_percent']=round((1-row['price']/event['old_price'])*100,2)
            output.append((row,enriched));self.last[key]=now;self.pending.pop(key)
        return output
