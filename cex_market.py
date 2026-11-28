import concurrent.futures,time,json,pathlib,math
from asset_filter import excluded,update_okx
from stablecoins import is_stablecoin,detector_config
URLS={
 'Binance':'https://data-api.binance.vision/api/v3/ticker/24hr',
 'OKX':'https://www.okx.com/api/v5/market/tickers?instType=SPOT',
 'Gate':'https://api.gateio.ws/api/v4/spot/tickers',
 'KuCoin':'https://api.kucoin.com/api/v1/market/allTickers'}

def normalize(venue,data,now):
    active=None
    registry=pathlib.Path(__file__).resolve().parent/'data'/({'Binance':'binance','Gate':'gate','KuCoin':'kucoin'}.get(venue,'none')+'.json')
    if registry.exists():
        listed=json.loads(registry.read_text())
        if venue=='Binance':active={x['symbol'] for x in listed['symbols'] if x['status']=='TRADING'}
        elif venue=='Gate':active={x['id'] for x in listed if x['trade_status']=='tradable'}
        elif venue=='KuCoin':active={x['symbol'] for x in listed['data'] if x['enableTrading']}
    if venue=='OKX':
        if data.get('code')!='0':raise ValueError(str(data.get('msg','OKX error')))
        data=data['data']
    if venue=='KuCoin':
        if data.get('code')!='200000':raise ValueError(str(data))
        timestamp=float(data['data']['time'])/1000
        data=data['data']['ticker']
    rows=[]
    for x in data:
        if venue=='Binance':
            pair=x['symbol'];quote=next((q for q in ('FDUSD','USDT','USDC') if pair.endswith(q)),None)
            if quote is None:continue
            symbol=pair[:-len(quote)];price=x['lastPrice'];volume=x['quoteVolume'];bid=x.get('bidPrice');ask=x.get('askPrice');ts=float(x['closeTime'])/1000
        elif venue=='OKX':
            pair=x['instId']
            if '-' not in pair:continue
            symbol,quote=pair.rsplit('-',1);price=x.get('last');volume=x.get('volCcy24h',0);bid=x.get('bidPx');ask=x.get('askPx');ts=float(x.get('ts',0))/1000
        elif venue=='Gate':
            pair=x['currency_pair']
            if '_' not in pair:continue
            symbol,quote=pair.rsplit('_',1);price=x.get('last');volume=x.get('quote_volume',0);bid=x.get('highest_bid');ask=x.get('lowest_ask');ts=None
        else:
            pair=x['symbol']
            if '-' not in pair:continue
            symbol,quote=pair.rsplit('-',1);price=x.get('last');volume=x.get('volValue',0);bid=x.get('buy');ask=x.get('sell');ts=timestamp
        stable=is_stablecoin(symbol)
        if quote!='USDT' and not (stable and quote in ('USDC','FDUSD')):continue
        try:
            if active is not None and pair not in active:continue
            if excluded({'venue':venue,'symbol':symbol}):continue
            price=float(price or 0);volume=float(volume or 0)
            if not math.isfinite(price) or price<=0 or not math.isfinite(volume) or volume<0:continue
            common={'venue':venue,'pair':pair,'volume_h24':volume,'observed_at':now,'source_timestamp':ts,'stale':ts is not None and (now-ts>60 or ts-now>5)}
            rows.append({**common,'key':venue+':'+pair,'symbol':symbol,'price':price,'bid':float(bid or 0),'quote_symbol':quote,'stablecoin':stable,'inverted':False})
            # USDT/USDT does not exist: invert USDC or FDUSD markets to monitor USDT too.
            if quote=='USDT' and symbol in ('USDC','FDUSD'):
                ask=float(ask or 0)
                rows.append({**common,'key':venue+':'+pair+':inverse','symbol':'USDT','price':1/price,'bid':1/ask if ask>0 else 0,'quote_symbol':symbol,'stablecoin':True,'inverted':True})
        except (ValueError,TypeError,ZeroDivisionError):continue
    return rows


def observe_crash(row,detector,stable_detector):
    selected=stable_detector if is_stablecoin(row['symbol']) else detector
    return selected.observe('cex:'+row['key'],row['price'],0,row['observed_at'])

def run(get,publish,Detector,config,state,lock):
    detector=Detector(config)
    stable_detector=Detector(detector_config(config))
    next_classification=0
    while True:
        start=time.monotonic();rows=[];errors=[];counts={}
        if start>=next_classification:
            try:
                update_okx(get('https://www.okx.com/api/v5/public/instruments?instType=SPOT'))
                next_classification=start+3600
            except Exception as e:
                errors.append('OKX资产分类更新失败，使用本地缓存: '+str(e))
                next_classification=start+300
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as ex:
            jobs={ex.submit(get,url):venue for venue,url in URLS.items()}
            for f in concurrent.futures.as_completed(jobs):
                venue=jobs[f]
                try:
                    data=normalize(venue,f.result(),time.time());counts[venue]=len(data)
                    for r in data:
                        if not r['stale'] and r['volume_h24']>=config['min_volume_h24_usd']:
                            for event in observe_crash(r,detector,stable_detector):
                                publish({**r,**event,'chain':venue,'token':r['symbol'],'type':'cex_crash','data_note':'Stablecoin cross-quote spot; inverse quotes explicitly labelled; Gate source timestamp unavailable' if r['stablecoin'] else 'USDT spot; Gate source timestamp unavailable'})
                    rows.extend(data)
                except Exception as e:errors.append(venue+': '+str(e));counts[venue]=0
        with lock:state.update(cex_rows=rows,cex_errors=errors,cex_counts=counts,cex_updated=time.time())
        time.sleep(max(1,config['poll_seconds']-(time.monotonic()-start)))
