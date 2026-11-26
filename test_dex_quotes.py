import unittest
from unittest.mock import patch
import dex_quotes as dq

def abi(amounts):
    return '0x'+''.join(f'{x:064x}' for x in [32,len(amounts),*amounts])

class QuoteTests(unittest.TestCase):
    def test_abi_roundtrip_and_invalid_response(self):
        path=[dq.USDT,dq.WBNB]
        encoded=dq.encode_quote(50*10**18,path)
        self.assertEqual(encoded[:10],'0xd06ca61f')
        self.assertEqual(len(encoded),10+5*64)
        self.assertEqual(dq.decode_quote(abi([50,60]),50,path),60)
        for response in ['0x',abi([51,60]),abi([50,0]),abi([50,60,70])]:
            with self.assertRaises(ValueError):dq.decode_quote(response,50,path)

    def test_costs_can_erase_reference_profit(self):
        row=dq.cost_result(100*10**18,101*10**18,0.1)
        self.assertFalse(row['positive_quote'])
        self.assertFalse(row['executable'])
        self.assertFalse(row['execution_simulated'])
        self.assertTrue(dq.cost_result(100*10**18,105*10**18,0.1)['positive_quote'])
        a=dq.cost_result(100*10**18,105*10**18,0.1)
        b=dq.cost_result(100*10**6,105*10**6,0.1,decimals=6)
        self.assertEqual(a['buffered_net_usdt'],b['buffered_net_usdt'])

    def test_chain_failures_are_isolated(self):
        with patch.dict(dq.STATES['base'],{'error':'RPC失败','updated':dq.time.time()}):
            s=dq.status()
            self.assertTrue(s['chains']['base']['stale'])
            self.assertFalse(s['execution_enabled'])
            self.assertEqual(set(s['chains']),{'base','bsc','ethereum'})

    def test_all_monitored_tokens_are_candidates_by_contract(self):
        for chain in dq.CHAINS:
            tokens=dq.monitored_tokens(chain)
            self.assertGreater(len(tokens),50)
            self.assertEqual(len(tokens),len({x['address'] for x in tokens}))
            self.assertNotIn(dq.CHAINS[chain]['base'],{x['address'] for x in tokens})

    def test_trigger_first_crossing_cooldown_and_stale_filter(self):
        catalog=[{'address':dq.WBNB,'symbol':'WBNB'}]
        pair={'chain':'bsc','contract':dq.WBNB,'eligible':True,'observed_at':1000,'indicative_spread_percent':25}
        self.assertFalse(dq.candidate_signals('bsc',catalog,{'pairs':[pair]},1000))
        pair['indicative_spread_percent']=26
        signals=dq.candidate_signals('bsc',catalog,{'pairs':[pair]},1000)
        gate=dq.SignalGate()
        self.assertEqual(gate.update(signals,1000),{dq.WBNB})
        self.assertEqual(gate.update(signals,1700),set())
        gate.update({},1701)
        self.assertEqual(gate.update(signals,1702),{dq.WBNB})
        gate.update({},1703)
        self.assertEqual(gate.update(signals,1704),set())
        self.assertFalse(dq.candidate_signals('bsc',catalog,{'pairs':[pair]},1100))
        event={'type':'cex_crash','chain':'OKX','token':'WBNB','drop_percent':25,'time':1000}
        self.assertIn(dq.WBNB,dq.candidate_signals('bsc',catalog,{'alerts':[event]},1000))
        event['type']='liquidity_drop'
        self.assertFalse(dq.candidate_signals('bsc',catalog,{'alerts':[event]},1000))

    def test_no_write_rpc_methods(self):
        with self.assertRaises(ValueError):dq.RPC().batch([('eth_sendRawTransaction',['0x'])])

    def test_manual_amount_requotes_without_execution(self):
        token={'address':dq.WBNB,'symbol':'WBNB'}
        with patch.object(dq,'monitored_tokens',return_value=[token]),patch.object(dq,'collect',return_value={'rows':[]}) as collect,patch.object(dq,'MANUAL_LAST',0):
            for amount in ['NaN','-1','0','10001','1.0000000000000000001']:
                with self.assertRaises(ValueError):dq.manual_quote({'chain':'bsc','ca':dq.WBNB,'amount':amount})
            self.assertFalse(collect.called)
            result=dq.manual_quote({'chain':'bsc','ca':dq.WBNB,'amount':'123.45'})
            self.assertFalse(result['execution_enabled'])
            self.assertEqual(str(collect.call_args.args[3][0]),'123.45')

    def test_candidate_push_requires_recheck_and_separate_cooldown(self):
        import wechat_push as push
        row={'type':'arb_candidate','chain':'bsc','contract':dq.WBNB,'positive_quote_rechecked':True,'buffered_net_usdt':1,'observed_at':dq.time.time()}
        self.assertTrue(push.qualifies(row))
        self.assertFalse(push.qualifies({**row,'observed_at':0}))
        self.assertFalse(push.qualifies({**row,'positive_quote_rechecked':False}))
        with patch.object(push,'settings',return_value={'enabled':True}),patch.object(push,'JOBS') as jobs,patch.dict(push.LAST,{'bsc:'+dq.WBNB:dq.time.time()},clear=True):
            push.enqueue(row);push.enqueue(row)
            self.assertEqual(jobs.put_nowait.call_count,1)

    def test_quotes_pinned_and_wrong_chain_rejected(self):
        class FakeRPC:
            def __init__(self,chain='0x38'):self.chain=chain;self.blocks=[]
            def batch(self,calls):
                if calls[0][0]=='eth_chainId':return [self.chain,'0x123','0x3b9aca00']
                if calls[0][0]=='eth_call':return ['0x12']*len(calls)
                return [{'number':'0x123','timestamp':hex(int(dq.time.time())),'hash':'0xabc'}]+['0x1234']*(len(calls)-1)
            def quotes(self,jobs,block):
                self.blocks.append(block)
                return [amount*2 for venue,amount,path in jobs]
        rpc=FakeRPC();sample=dq.collect(rpc)
        self.assertTrue(sample['rows'])
        self.assertTrue(all(block=='0x123' for block in rpc.blocks))
        self.assertTrue(all(r['buy_venue']!=r['sell_venue'] for r in sample['rows']))
        with self.assertRaisesRegex(ValueError,'链不符'):dq.collect(FakeRPC('0x1'))

if __name__=='__main__':unittest.main()
