"""Exclude tokenized equities/ETFs using directory identity and venue metadata."""
import json, pathlib, re
from collections import defaultdict
ROOT=pathlib.Path(__file__).resolve().parent
PATTERN=re.compile(r"xstock|tokenized[- ]stocks?|tokenized[- ]etf|ondo[- ]tokenized|\betf\b",re.I)
# Gate stock tickers whose display names omit the tokenization classification.
GATE_STOCKS=set("SNDKG GLWG ARMG MUUG BACG PGG KORUG GOOGLG WDCG QNTG SKHYNIXG COING GEG TQQQG SPCXG IBMG FLNCG ABBVG CVXG SOXSG NFLXG HDG LRCXG MUG CSCOG PLTRG METAG HOODG TSLAG AVGOG AXTIG JNJG TENCENTG ANTG VG CATG BRKBG KOG AMZNG CRCLG AAPLG SAMSUNGG UNHG NBISG WMTG SKHYG JPMG MSFTG XOMG NVDAG ZHIPUG CBRSG GSG LLYG MINIMAXG BEG DELLG COSTG AMDG HSBCG MSTRG QCOMG AMATG LITEG INTCG ORCLG MRVLG CRWVG".split())

def load():
    catalog=ROOT/'data/asset-exclusions.json'
    if catalog.exists():
        c=json.loads(catalog.read_text(encoding='utf-8'))
        return set(c['ids']),{tuple(x) for x in c['contracts']},set(c['symbols']),{k:set(v) for k,v in c['venues'].items()}
    coins=json.loads((ROOT/'data/coins.json').read_text(encoding='utf-8'))
    grouped=defaultdict(list)
    ids=set(); contracts=set()
    platform={'binance-smart-chain':'bsc','polygon-pos':'polygon','arbitrum-one':'arbitrum','optimistic-ethereum':'optimism'}
    for c in coins:
        stock=bool(PATTERN.search(c['name']+' '+c['id']))
        grouped[c['symbol'].upper()].append(stock)
        if stock:
            ids.add(c['id'])
            for chain,address in c.get('platforms',{}).items():
                chain=platform.get(chain,chain)
                if address:contracts.add((chain,canonical(chain,address)))
    symbols={s for s,flags in grouped.items() if all(flags)}
    gate=json.loads((ROOT/'data/gate.json').read_text(encoding='utf-8'))
    venue={'Gate':GATE_STOCKS|{c['base'] for c in gate if PATTERN.search(c.get('base_name',''))}}
    okx=ROOT/'data/okx.json'
    if okx.exists():
        venue['OKX']={x['baseCcy'] for x in json.loads(okx.read_text(encoding='utf-8')).get('data',[]) if x.get('instCategory')=='3'}
    return ids,contracts,symbols,venue

def canonical(chain,address):
    return address if chain in ('solana','sui','aptos','tron','ton') else address.lower()

IDS,CONTRACTS,SYMBOLS,VENUES=load()

def excluded(asset):
    chain=asset.get('chain','')
    address=asset.get('contract') or asset.get('address','')
    symbol=(asset.get('symbol') or asset.get('token') or asset.get('label') or '').upper()
    venue=asset.get('venue') or chain
    return (asset.get('coin_id') in IDS or (chain,canonical(chain,address)) in CONTRACTS
            or symbol in SYMBOLS or symbol in VENUES.get(venue,set()))


def update_okx(data):
    """Use the venue's explicit stock category, never ticker prefixes."""
    if data.get('code')!='0' or not data.get('data'):
        raise ValueError('OKX instrument classification unavailable')
    stocks={x['baseCcy'] for x in data['data'] if x.get('instCategory')=='3'}
    path=ROOT/'data/okx.json'
    temp=path.with_suffix('.tmp')
    temp.write_text(json.dumps(data,ensure_ascii=False),encoding='utf-8')
    temp.replace(path)
    VENUES['OKX']=stocks
