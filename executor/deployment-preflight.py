"""Read-only three-chain deployment checks. No keys, signing or broadcast."""
import concurrent.futures
import json
import pathlib
import sys
import urllib.request

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from dex_quotes import CHAINS

owner = sys.argv[1]
cap = int(sys.argv[2])
artifact = json.loads((pathlib.Path(__file__).resolve().parents[1] / 'executor-artifact.json').read_text())

def check(item):
    name, cfg = item
    def rpc(method, params):
        req = urllib.request.Request(cfg['rpc'], data=json.dumps({'jsonrpc':'2.0','id':1,'method':method,'params':params}).encode(), headers={'Content-Type':'application/json'})
        with urllib.request.urlopen(req, timeout=25) as response:
            value = json.load(response)
        if 'error' in value:
            raise ValueError(str(value['error']))
        return value['result']
    result = {'chain':name,'owner':owner,'cap':cap,'symbol':cfg['symbol']}
    try:
        if int(rpc('eth_chainId', []),16) != int(cfg['id'],16):
            raise ValueError('RPC chain mismatch')
        result['native_balance'] = str(int(rpc('eth_getBalance',[owner,'latest']),16)/10**18)
        result['stable_balance'] = str(int(rpc('eth_call',[{'to':cfg['base'],'data':'0x70a08231'+owner[2:].lower().zfill(64)},'latest']),16)/10**cfg['decimals'])
        result['dependency_code'] = [rpc('eth_getCode',[address,'latest']) != '0x' for address in [cfg['base'],*cfg['routers'].values()]]
        data = artifact['bytecode'] + ''.join(a[2:].lower().zfill(64) for a in [owner,cfg['base'],*cfg['routers'].values()]) + hex(cap*10**cfg['decimals'])[2:].zfill(64)
        result['gas_price_wei'] = str(int(rpc('eth_gasPrice',[]),16))
        try:
            result['gas_estimate'] = int(rpc('eth_estimateGas',[{'from':owner,'data':data,'value':'0x0'}]),16)
            result['execution_fee_with_20pct_gas_reserve'] = str((result['gas_estimate']*120//100)*int(result['gas_price_wei'])/10**18)
            if name == 'base':
                oracle='0x420000000000000000000000000000000000000f'
                size=(len(data)-2)//2+256
                gas=(result['gas_estimate']*120+99)//100
                extra=sum(int(rpc('eth_call',[{'to':oracle,'data':selector+f'{value:064x}'},'latest']),16) for selector,value in [('0xf1c7a58b',size),('0x275aedd2',gas)])*120//100
                result['extra_base_fee_budget'] = str(extra/10**18)
                result['combined_budget'] = str((gas*int(result['gas_price_wei'])+extra)/10**18)
        except Exception as error:
            result['estimate_error'] = str(error)
        result['extra_fee_note'] = 'Base L1/operator fees included when combined_budget exists; estimate, not a guaranteed cap' if name == 'base' else ''
    except Exception as error:
        result['error'] = str(error)
    return result

with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
    print(json.dumps(list(pool.map(check, CHAINS.items())), ensure_ascii=False, indent=2))
