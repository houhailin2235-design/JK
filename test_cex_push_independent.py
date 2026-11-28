import time
import unittest
from unittest.mock import patch
import push_quality as quality
import wechat_push as push


class IndependentCrashChecks(unittest.TestCase):
    def setUp(self):
        self.now=time.time()
        self.event={'type':'cex_crash','venue':'OKX','chain':'OKX','token':'WLFI',
                    'pair':'WLFI-USDT','drop_percent':40,'price':0.023,
                    'old_price':0.038333,'observed_at':self.now,'window_seconds':60}

    def test_threshold_and_all_venues_without_pools(self):
        for venue in ('OKX','Binance','Gate','KuCoin'):
            for drop,expected in ((24.99,False),(25,True),(40,True)):
                event={**self.event,'venue':venue,'chain':venue,'drop_percent':drop}
                with patch.object(quality,'gate_identities',side_effect=AssertionError('no identity lookup')):
                    proof=quality.evaluate(event,[],{},self.now)
                self.assertEqual(proof['push_cex_eligible'],expected)
                self.assertEqual(push.qualifies({**event,**proof}),expected)
                self.assertFalse(proof['push_liquidity_required'])

    def test_stale_or_invalid_cex_never_sends(self):
        for change in ({'observed_at':self.now-91},{'observed_at':self.now+6},
                       {'stale':True},{'price':0},{'price':float('nan')},
                       {'drop_percent':float('inf')},{'drop_percent':101},
                       {'observed_at':None},{'drop_percent':'40'}):
            event={**self.event,**change}
            self.assertFalse(push.qualifies(event))
            with patch.object(push,'send') as send:
                push.send_market(event)
                send.assert_not_called()

    def test_message_has_exchange_window_and_no_fake_liquidity(self):
        with patch.object(push,'send') as send:
            push.send_market(self.event)
        title,body=send.call_args.args
        self.assertIn('交易所暴跌 40.00%',title)
        for value in ('OKX','WLFI-USDT','60秒','不代表可套利'):
            self.assertIn(value,body)
        self.assertNotIn('流动性：$0',body)

    def test_existing_rejected_pool_flag_does_not_block_crash(self):
        self.assertTrue(push.qualifies({**self.event,'push_liquidity_verified':False}))
        self.assertFalse(push.qualifies({**self.event,'type':'multi_pool_price_drop',
                                        'push_liquidity_verified':False}))

    def test_queued_crash_rechecked_when_expired(self):
        with patch.object(push,'JOBS') as jobs,patch.object(push,'send') as send,\
                patch.object(push.time,'time',return_value=self.now+91),\
                patch.object(push.time,'sleep',side_effect=StopIteration):
            jobs.get.return_value=self.event
            with self.assertRaises(StopIteration):push.worker()
            jobs.task_done.assert_called_once()
            send.assert_not_called()

    def test_stock_exclusion_still_applies(self):
        with patch.object(push,'excluded',return_value=True):
            self.assertFalse(push.qualifies(self.event))


if __name__=='__main__':unittest.main()
