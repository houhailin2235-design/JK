import time,unittest
from unittest.mock import patch
from cex_market import normalize,observe_crash
from stablecoins import detector_config,is_stablecoin,alert_threshold
from monitor import Detector
import wechat_push,push_quality

class StableCexTests(unittest.TestCase):
    def setUp(self):
        self.now=time.time();self.config={'windows':[[30,10],[60,20],[300,40]],'poll_seconds':30,'cooldown_seconds':180}
    def normalize(self,venue,data):
        with patch('cex_market.pathlib.Path.exists',return_value=False):return normalize(venue,data,self.now)
    def test_usd1_and_nonstable_quotes(self):
        rows=self.normalize('Binance',[{'symbol':'USD1USDT','lastPrice':'.95','quoteVolume':'20000','closeTime':str(self.now*1000)}, {'symbol':'BTCUSDC','lastPrice':'50000','quoteVolume':'30000','closeTime':str(self.now*1000)}])
        self.assertEqual(len(rows),1);self.assertTrue(rows[0]['stablecoin']);self.assertEqual(rows[0]['quote_symbol'],'USDT')
    def test_usdt_inverse_direction_and_bid(self):
        rows=self.normalize('OKX',{'code':'0','data':[{'instId':'USDC-USDT','last':str(1/.94),'volCcy24h':'20000','bidPx':'1.06','askPx':'1.07','ts':str(self.now*1000)}]})
        inverse=next(r for r in rows if r['symbol']=='USDT');self.assertAlmostEqual(inverse['price'],.94);self.assertAlmostEqual(inverse['bid'],1/1.07);self.assertTrue(inverse['inverted']);self.assertEqual(inverse['quote_symbol'],'USDC')
    def test_normal_stable_market_does_not_drop_usdt(self):
        rows=self.normalize('Gate',[{'currency_pair':'USDC_USDT','last':'1','quote_volume':'20000'}]);self.assertEqual(len(rows),2);self.assertEqual(rows[1]['price'],1);self.assertIsNone(rows[1]['source_timestamp'])
    def test_bad_prices_never_produce_inverse(self):
        for value in ['0','-1','nan','inf']:
            self.assertFalse(self.normalize('Gate',[{'currency_pair':'USDC_USDT','last':value,'quote_volume':'20000'}]))
    def test_exact_symbols_only(self):
        self.assertTrue(is_stablecoin('USD1'));self.assertTrue(is_stablecoin('usdt'));self.assertFalse(is_stablecoin('USDTX'));self.assertEqual(alert_threshold({'token':'BTC'}),25)
    def test_five_percent_reaches_notification_end_to_end(self):
        normal=Detector(self.config);stable=Detector(detector_config(self.config));r={'key':'Gate:USD1_USDT','symbol':'USD1','price':1.,'observed_at':self.now}
        self.assertFalse(observe_crash(r,normal,stable));r={**r,'price':.95,'observed_at':self.now+30};events=observe_crash(r,normal,stable);self.assertTrue(events)
        a={**r,**events[0],'type':'cex_crash','token':'USD1','pair':'USD1_USDT','venue':'Gate','chain':'Gate','quote_symbol':'USDT'}
        with patch('wechat_push.time.time',return_value=self.now+30):
            self.assertTrue(wechat_push.qualifies(a))
            with patch.object(wechat_push,'send') as send:wechat_push.send_market(a)
        self.assertIn('稳定币下跌 5.00%',send.call_args.args[0])
    def test_five_percent_does_not_lower_other_asset_policy(self):
        for token,expected in [('USDT',True),('USD1',True),('USDC',True),('BTC',False),('USD1X',False)]:
            a={'type':'cex_crash','token':token,'drop_percent':5,'price':.95,'observed_at':self.now}
            self.assertEqual(push_quality.cex_qualifies(a,self.now),expected)
        self.assertFalse(push_quality.cex_qualifies({'type':'cex_crash','token':'USD1','drop_percent':4.99,'price':.95,'observed_at':self.now},self.now))
    def test_stable_stale_quotes_blocked(self):
        a={'type':'cex_crash','token':'USDT','drop_percent':10,'price':.9,'observed_at':self.now-91};self.assertFalse(push_quality.cex_qualifies(a,self.now))
    def test_message_labels_inverse_reference(self):
        a={'type':'cex_crash','token':'USDT','drop_percent':6,'price':.94,'observed_at':self.now,'quote_symbol':'USDC','inverted':True,'window_seconds':30}
        with patch.object(wechat_push,'send') as send:wechat_push.send_market(a)
        self.assertIn('USDC',send.call_args.args[1]);self.assertIn('反向换算',send.call_args.args[1])
if __name__=='__main__':unittest.main()
