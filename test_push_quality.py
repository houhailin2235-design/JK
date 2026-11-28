import unittest
import time
from unittest.mock import patch
import push_quality as q
import wechat_push as push

class LiquidityPushChecks(unittest.TestCase):
    def setUp(self):
        self.config={'min_liquidity_usd':50000,'min_volume_h24_usd':10000}
        self.ca='0xdfeb13936a8975f9f788e73fb93861ce461541f9'
        self.now=time.time()
        self.pool={'chain':'bsc','contract':self.ca,'liquidity':100000,'volume_h24':100000,'quality_ok':True,'eligible':True,'observed_at':self.now,'pair':'p'}
        self.event={**self.pool,'type':'multi_pool_price_drop','token':'XNL','drop_percent':30}
    def check(self,event,pools,identities=None):
        return q.evaluate(event,pools,self.config,self.now,lambda _:identities if identities is not None else [('bsc',self.ca)])
    def test_xnl_low_liquidity_suppresses_dex_push(self):
        p={**self.pool,'liquidity':335,'quality_ok':False,'eligible':False}
        event={**self.event,**p}
        result=self.check(event,[p]);self.assertFalse(result['push_liquidity_verified'])
        self.assertEqual(result['observed_max_liquidity_usd'],335)
        self.assertFalse(push.qualifies({**event,**result}))
    def test_matching_identity_and_fresh_pool_required(self):
        good=self.check(self.event,[self.pool]);self.assertTrue(good['push_liquidity_verified'])
        self.assertTrue(push.qualifies({**self.event,**good}))
        self.assertFalse(self.check({**self.event,'observed_at':self.now-61},[self.pool])['push_liquidity_verified'])
    def test_large_peer_does_not_rescue_thin_event_pool(self):
        event={**self.pool,'type':'extreme_spread','indicative_spread_percent':400,'liquidity':335,'quality_ok':False}
        self.assertFalse(self.check(event,[self.pool])['push_liquidity_verified'])
        event={**self.pool,'type':'extreme_spread','indicative_spread_percent':400,'liquidity':50000}
        r=self.check(event,[self.pool]);self.assertEqual(r['push_pool_liquidity'],50000)
    def test_unknown_or_old_evidence_never_sends(self):
        self.assertFalse(push.qualifies(self.event))
        proof=self.check(self.event,[self.pool]);proof['push_pool_observed_at']=self.now-91
        self.assertFalse(push.qualifies({**self.event,**proof}))
        with patch.object(push,'send') as send:push.send_market({**self.event,**proof});send.assert_not_called()
    def test_small_cex_drop_does_not_call_identity_endpoint(self):
        with patch('push_quality.gate_identities') as resolver:
            r=q.evaluate({**self.event,'type':'cex_crash','drop_percent':10},[],self.config,self.now,resolver)
            resolver.assert_not_called();self.assertFalse(r['push_cex_eligible'])
    def test_news_unaffected(self):
        self.assertEqual(self.check({'type':'newsflash'},[]),{})
        self.assertTrue(push.qualifies({'type':'newsflash','news_id':'one'}))
    def test_worker_rechecks_liquidity_after_queue_delay(self):
        alert={**self.event,**self.check(self.event,[self.pool])}
        def deteriorated(a):a.update(push_liquidity_verified=False)
        with patch.object(push,'JOBS') as jobs,patch.object(push,'prepare',side_effect=deteriorated),patch.object(push,'send') as send,patch.object(push.time,'sleep',side_effect=StopIteration):
            jobs.get.return_value=alert
            with self.assertRaises(StopIteration):push.worker()
            jobs.task_done.assert_called_once();send.assert_not_called()

if __name__=='__main__':unittest.main()
