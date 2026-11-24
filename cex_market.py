import concurrent.futures,time,json,pathlib
from asset_filter import excluded,update_okx
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
            pair=x['symbol']
            if not pair.endswith('USDT'):continue
            symbol=pair[:-4];price=x['lastPrice'];volume=x['quoteVolume'];bid=x.get('bidPrice');ts=float(x['closeTime'])/1000
        elif venue=='OKX':
            pair=x['instId']
            if not pair.endswith('-USDT'):continue
            symbol=pair[:-5];price=x['last'];volume=x['volCcy24h'];bid=x.get('bidPx');ts=float(x['ts'])/1000
        elif venue=='Gate':
            pair=x['currency_pair']
            if not pair.endswith('_USDT'):continue
            symbol=pair[:-5];price=x['last'];volume=x.get('quote_volume',0);bid=x.get('highest_bid');ts=None
        else:
            pair=x['symbol']
            if not pair.endswith('-USDT'):continue
            symbol=pair[:-5];price=x['last'];volume=x.get('volValue',0);bid=x.get('buy');ts=timestamp
        try:
            if active is not None and pair not in active:continue
            if excluded({'venue':venue,'symbol':symbol}):continue
            price=float(price or 0);volume=float(volume or 0)
            if price<=0:continue
            rows.append({'key':venue+':'+pair,'venue':venue,'symbol':symbol,'pair':pair,'price':price,'volume_h24':volume,'bid':float(bid or 0),'observed_at':now,'source_timestamp':ts,'stale':ts is not None and (now-ts>60 or ts-now>5)})
        except (ValueError,TypeError):continue
    return rows

def run(get,publish,Detector,config,state,lock):
    detector=Detector(config)
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
                            for event in detector.observe('cex:'+r['key'],r['price'],0,r['observed_at']):
                                publish({**r,**event,'chain':venue,'token':r['symbol'],'type':'cex_crash','data_note':'USDT spot; Gate source timestamp unavailable'})
                    rows.extend(data)
                except Exception as e:errors.append(venue+': '+str(e));counts[venue]=0
        with lock:state.update(cex_rows=rows,cex_errors=errors,cex_counts=counts,cex_updated=time.time())
        time.sleep(max(1,config['poll_seconds']-(time.monotonic()-start)))
