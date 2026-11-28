import unittest
from cex_market import normalize
class CexChecks(unittest.TestCase):
    def test_okx_stale_and_quote_filter(self):
        rows=normalize('OKX',{'code':'0','data':[{'instId':'ETH-USDT','last':'100','volCcy24h':'20000','bidPx':'99','ts':'100000'},{'instId':'ETH-BTC'}]},200)
        self.assertEqual(len(rows),1)
        self.assertTrue(rows[0]['stale'])
    def test_gate_does_not_invent_source_timestamp(self):
        rows=normalize('Gate',[{'currency_pair':'BTC_USDT','last':'100','quote_volume':'20000','highest_bid':'99'}],200)
        self.assertIsNone(rows[0]['source_timestamp'])
    def test_api_error(self):
        with self.assertRaises(ValueError):normalize('OKX',{'code':'500','msg':'failed'},0)
if __name__=='__main__':unittest.main()


class StockIdentityChecks(unittest.TestCase):
    def test_same_symbol_different_assets(self):
        from asset_filter import excluded
        self.assertTrue(excluded({'venue':'OKX','symbol':'XTER'}))
        self.assertFalse(excluded({'venue':'Gate','symbol':'XTER'}))
        self.assertFalse(excluded({'chain':'bsc','token':'XTER','contract':'0x103071da56e7cd95b415320760d6a0ddc4da1ca5'}))
        self.assertFalse(excluded({'venue':'OKX','symbol':'XRP'}))
        self.assertFalse(excluded({'venue':'OKX','symbol':'XLM'}))
