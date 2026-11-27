import unittest
from unittest.mock import patch
import wechat_push
from wechat_push import qualifies
def liquid(a):
    return {**a,'push_liquidity_verified':True,'push_pool_liquidity':100000,'push_pool_observed_at':wechat_push.time.time()}
class PushChecks(unittest.TestCase):
    def test_extreme_candidates_are_explicitly_alerted(self):
        self.assertTrue(qualifies(liquid({'type':'extreme_spread','spread_identity':'candidate','indicative_spread_percent':301})))
        self.assertFalse(qualifies({'type':'extreme_spread','indicative_spread_percent':300}))
    def test_only_verified_over_25(self):
        base=liquid({'type':'indicative_spread','spread_identity':'verified','indicative_spread_percent':25})
        self.assertFalse(qualifies(base))
        self.assertTrue(qualifies(dict(base,indicative_spread_percent=25.01)))
        self.assertFalse(qualifies(dict(base,indicative_spread_percent=99,spread_identity='candidate')))
        self.assertFalse(qualifies(dict(base,type='crash',indicative_spread_percent=99)))
    def test_clawbot_only_spreads_and_feature_switch(self):
        spread=liquid({'type':'indicative_spread','spread_identity':'verified','indicative_spread_percent':26})
        with patch.dict('os.environ',{'CLAWBOT_SPREAD_ENABLED':'true'}):
            self.assertEqual(wechat_push.channels_for(spread),['wechat','clawbot'])
            self.assertEqual(wechat_push.channels_for(dict(spread,indicative_spread_percent=25)),['wechat'])
            self.assertEqual(wechat_push.channels_for({'type':'cex_crash','drop_percent':90}),['wechat'])
            self.assertEqual(wechat_push.channels_for({'type':'newsflash','news_id':'one'}),['wechat'])
        with patch.dict('os.environ',{'CLAWBOT_SPREAD_ENABLED':'false'}):
            self.assertEqual(wechat_push.channels_for(spread),['wechat'])
    def test_channel_failure_does_not_skip_other_channel(self):
        a=liquid({'type':'extreme_spread','indicative_spread_percent':400,'token':'TEST'})
        with patch.dict('os.environ',{'CLAWBOT_SPREAD_ENABLED':'true'}),patch('wechat_push.time.sleep'),patch('wechat_push.send',side_effect=[ValueError('failure'),{}]) as send:
            wechat_push.send_market(a)
            self.assertEqual(send.call_count,2)
            self.assertIn('400.00%',send.call_args_list[1].args[0])
            self.assertEqual(send.call_args_list[1].kwargs['channel'],'clawbot')
if __name__=='__main__':unittest.main()
