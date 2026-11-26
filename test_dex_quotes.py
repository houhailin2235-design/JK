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

    def test_no_write_rpc_methods(self):
        with self.assertRaises(ValueError):dq.RPC().batch([('eth_sendRawTransaction',['0x'])])

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
