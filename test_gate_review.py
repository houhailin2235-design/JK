import time
import unittest
from decimal import Decimal
from unittest.mock import patch
import gate_review as g

class GateReviewChecks(unittest.TestCase):
    def setUp(self):
        self.ca='0x'+'1'*40
        self.data={'chain':'ethereum','ca':self.ca,'currency':'IZI','quantity':'100','cost_usdt':'80','fee_percent':'0.2'}
        self.currency={'currency':'IZI','delisted':False,'trade_disabled':False,'deposit_disabled':False,'chains':[{'name':'ETH','addr':self.ca,'deposit_disabled':False}]}
        self.pair={'id':'IZI_USDT','base':'IZI','quote':'USDT','trade_status':'tradable','amount_precision':0,'min_base_amount':'1','min_quote_amount':'3'}
        self.book={'update':100000,'bids':[['1','50'],['0.9','50']]}
    def test_weighted_proceeds_and_never_executable(self):
        r=g.evaluate(self.data,self.currency,self.pair,self.book,100)
        self.assertEqual(Decimal(r['gross_usdt']),95)
        self.assertEqual(Decimal(r['conditional_net_usdt']),Decimal('14.81'))
        self.assertTrue(r['sell_leg_checks_passed'])
        self.assertFalse(r['execution_enabled']);self.assertFalse(r['executable'])
        aged=g.evaluate(self.data,self.currency,self.pair,self.book,110)
        self.assertEqual(aged['valid_until'],115,'expiry must use the exchange snapshot time')
    def test_no_full_profit_for_partial_depth(self):
        r=g.evaluate({**self.data,'quantity':'200'},self.currency,self.pair,self.book,100)
        self.assertEqual(r['unfilled_quantity'],'100');self.assertIsNone(r['conditional_net_usdt'])
        self.assertFalse(r['sell_leg_checks_passed'])
    def test_chain_and_contract_both_required(self):
        for change in [{'chain':'bsc'},{'ca':'0x'+'2'*40}]:
            r=g.evaluate({**self.data,**change},self.currency,self.pair,self.book,100)
            self.assertFalse(r['deposit_network_matched']);self.assertFalse(r['sell_leg_checks_passed'])
    def test_staleness_deposit_precision_and_negative_net(self):
        for data,currency,book,now in [
            (self.data,self.currency,self.book,116),
            (self.data,{**self.currency,'deposit_disabled':True},self.book,100),
            ({**self.data,'quantity':'1.5'},self.currency,self.book,100),
            ({**self.data,'cost_usdt':'200'},self.currency,self.book,100)]:
            self.assertFalse(g.evaluate(data,currency,self.pair,book,now)['sell_leg_checks_passed'])
    def test_validate_before_requests(self):
        with patch.object(g,'fetch') as fetch:
            for change in [{'currency':'IZI/../x'},{'quantity':'NaN'},{'quantity':'0'},{'fee_percent':'6'},{'ca':'bad'}]:
                with self.assertRaises(ValueError):g.review({**self.data,**change})
            fetch.assert_not_called()
    def test_review_only_gets_fixed_public_endpoints(self):
        def response(path):
            if '/order_book?' in path:return {**self.book,'update':int(time.time()*1000)}
            return self.currency if '/currencies/' in path else self.pair
        with patch.object(g,'LAST',0),patch.object(g,'fetch',side_effect=response) as fetch:
            self.assertFalse(g.review(self.data)['execution_enabled'])
            self.assertEqual(fetch.call_count,3)
            self.assertTrue(all(c.args[0].startswith('/spot/') for c in fetch.call_args_list))
    def test_malformed_depth_fails(self):
        for bids in [[],[['0','2']],[['NaN','2']],[['1','2'],['1','3']]]:
            with self.assertRaises(ValueError):g.sell_depth(bids,Decimal(10))

if __name__=='__main__':unittest.main()
