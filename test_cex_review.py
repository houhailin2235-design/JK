import json
import os
import time
import unittest
from decimal import Decimal
from unittest.mock import patch
import cex_review as c


class CrossVenueReviewChecks(unittest.TestCase):
    def setUp(self):
        self.now=time.time()
        self.ca='0x'+'1'*40
        self.data={'chain':'ethereum','ca':self.ca,'symbol':'QNT','amount':'50'}
        self.buy={'amount':'50','min_quantity':'10.123','quantity':'10.2','cost_budget_usdt':'51',
                  'valid_until':self.now+30,'stable_symbol':'USDC'}
        self.market={'pair':'QNTUSDT','enabled':True,'step':'0.01','minimum':'0.01','notional':'5'}
        self.net={'name':'ETH','ca':self.ca,'deposit':True,'minimum':'0.01'}
        self.book={'bids':[['6','5'],['5','20']],'observed_at':self.now,
                   'source_timestamp':self.now,'valid_until':self.now+15}
        c.CACHE.clear()

    def row(self,venue='Binance',buy=None,net=None,book=None):
        with patch.object(c,'market',return_value=self.market),patch.object(c,'networks',return_value=[net or self.net]),\
             patch.object(c,'book',return_value=book or self.book),patch.object(c,'fee_setting',return_value=(Decimal('0.2'),True)):
            return c.review_venue(venue,'QNT','ethereum',self.ca,buy or self.buy,{})

    def test_weighted_depth_floors_dust_and_accounts_for_cost(self):
        row=self.row()
        self.assertEqual(row['quantity'],'10.12')
        self.assertEqual(Decimal(row['gross_usdt']),Decimal('55.60'))
        self.assertEqual(Decimal(row['conditional_net_usdt']),Decimal('4.4888'))
        self.assertEqual(Decimal(row['dust_quantity']),Decimal('0.003'))
        self.assertTrue(row['amount_checks_passed'])
        self.assertFalse(row['executable'])

    def test_no_identity_by_symbol_or_suffix(self):
        row=self.row(net={**self.net,'ca':'0x'+'2'*40})
        self.assertFalse(row['identity_verified'])
        self.assertFalse(row['amount_checks_passed'])
        suffix={**self.net,'name':'ERC20','ca':self.ca[-6:]}
        self.assertIsNone(c.match_network([suffix],'ethereum',self.ca,'OKX','QNT',{}))
        config={'verified_cex_mappings':[{'chain':'ethereum','address':self.ca,'okx_instrument':'QNT-USDT','verified_venues':['OKX']}]}
        self.assertEqual(c.match_network([suffix],'ethereum',self.ca,'OKX','QNT',config),suffix)
        self.assertIsNone(c.match_network([suffix],'bsc',self.ca,'OKX','QNT',config))

    def test_closed_deposit_and_minimum_block(self):
        for net in ({**self.net,'deposit':False},{**self.net,'minimum':None},{**self.net,'minimum':'11'}):
            self.assertFalse(self.row(net=net)['amount_checks_passed'])

    def test_partial_depth_never_extrapolates(self):
        row=self.row(book={**self.book,'bids':[['6','1']]})
        self.assertIsNone(row['conditional_net_usdt'])
        self.assertFalse(row['amount_checks_passed'])

    def test_missing_buy_still_queries_all_four_venues(self):
        with patch.object(c,'buy_quote',side_effect=ValueError('无买入报价')),\
             patch.object(c,'market',return_value=self.market),patch.object(c,'networks',return_value=[self.net]),\
             patch.object(c,'book',return_value=self.book) as book:
            result=c.review(self.data,{})
        self.assertEqual({r['venue'] for r in result['rows']},set(c.VENUES))
        self.assertEqual(book.call_count,4)
        self.assertTrue(all(r['conditional_net_usdt'] is None for r in result['rows']))
        self.assertFalse(result['executable'])

    def test_unknown_network_does_not_skip_public_book(self):
        with patch.object(c,'market',return_value=self.market),patch.object(c,'networks',side_effect=ValueError('未配置只读API')),\
             patch.object(c,'book',return_value=self.book) as book,patch.object(c,'fee_setting',return_value=(Decimal('0.2'),False)):
            row=c.review_venue('Binance','QNT','ethereum',self.ca,self.buy,{})
        book.assert_called_once()
        self.assertFalse(row['amount_checks_passed'])
        self.assertIn('未配置只读API',row['blocked_reasons'])
        self.assertIsNotNone(row['conditional_net_usdt'])

    def test_expired_quote_cannot_pass(self):
        row=self.row(buy={**self.buy,'valid_until':self.now-1})
        self.assertFalse(row['amount_checks_passed'])

    def test_missing_keys_never_call_signed_endpoints(self):
        with patch.dict(os.environ,{},clear=True),patch.object(c,'fetch') as fetch:
            for loader in (c.binance_networks,c.okx_networks):
                with self.assertRaises(ValueError):loader('QNT')
            fetch.assert_not_called()

    def test_network_adapters_keep_identity_and_deposit_fields(self):
        with patch.object(c.gate_review,'fetch',return_value={'currency':'QNT','deposit_disabled':False,'withdraw_disabled':False,
            'chains':[{'name':'ETH','addr':self.ca,'deposit_disabled':False,'withdraw_disabled':False}]}):
            self.assertEqual(c.networks('Gate','QNT')[0]['ca'],self.ca)
            self.assertTrue(c.networks('Gate','QNT')[0]['deposit'])
        data={'code':'200000','data':{'currency':'QNT','chains':[{'chainId':'eth','contractAddress':self.ca,
             'isDepositEnabled':False,'isWithdrawEnabled':True,'depositMinSize':'1','confirms':12}]}}
        with patch.object(c,'fetch',return_value=data):
            net=c.networks('KuCoin','QNT')[0]
            self.assertEqual(net['ca'],self.ca);self.assertFalse(net['deposit'])
            self.assertEqual(net['confirmations'],12)

    def test_depth_adapters_and_source_expiry(self):
        fixtures={
            'Binance':{'bids':[['6','100']]},
            'OKX':{'code':'0','data':[{'ts':str(int(self.now*1000)),'bids':[['6','100','0','2']]}]},
            'KuCoin':{'code':'200000','data':{'time':int(self.now*1000),'bids':[['6','100']]}}}
        for venue,data in fixtures.items():
            with patch.object(c,'fetch',return_value=data):
                b=c.book(venue,'QNT')
                self.assertEqual(b['bids'],[['6','100']])
                self.assertLessEqual(b['valid_until'],time.time()+15)
        with patch.object(c.gate_review,'fetch',return_value={'current':int(self.now*1000),'update':0,'bids':[['6','100']]}):
            self.assertEqual(c.book('Gate','QNT')['bids'],[['6','100']])
        with patch.object(c,'fetch',return_value={'code':'200000','data':{'time':int((self.now-20)*1000),'bids':[['6','100']]}}):
            with self.assertRaises(ValueError):c.book('KuCoin','QNT')

    def test_all_market_adapters_reject_wrong_asset(self):
        wrong={'symbols':[{'symbol':'FAKEUSDT','baseAsset':'FAKE','quoteAsset':'USDT'}]}
        with patch.object(c,'fetch',return_value=wrong):
            with self.assertRaises(ValueError):c.market('Binance','QNT')

    def test_validation_precedes_network(self):
        with patch.object(c,'buy_quote') as buy,patch.object(c,'market') as market:
            for change in ({'amount':'101'},{'amount':'NaN'},{'amount':'0'},{'symbol':'QNT/../x'},{'ca':'bad'},{'chain':'solana'}):
                with self.assertRaises(ValueError):c.review({**self.data,**change},{})
            buy.assert_not_called();market.assert_not_called()

    def test_ordering_separates_unverified_high_numbers(self):
        rows=[{'venue':'fake','conditional_net_usdt':'700','identity_verified':False},
              {'venue':'verified','conditional_net_usdt':'2','identity_verified':True,'amount_checks_passed':True},
              {'venue':'verified_high','conditional_net_usdt':'3','identity_verified':True,'amount_checks_passed':True}]
        self.assertEqual([r['venue'] for r in c.sort_rows(rows)],['verified_high','verified','fake'])

    def test_buy_quote_fixed_block_and_capacity(self):
        class RPC:
            def batch(_,calls):
                if calls[0][0]=='eth_chainId':return ['0x1','0x123','0x3b9aca00']
                return [{'timestamp':hex(int(time.time()))},'0x12','0x1234']
            def quotes(_,jobs,block):
                self.assertEqual(block,'0x123')
                if len(jobs)==4:return [10**18,10**18,9*10**17,None]
                return [10**17,2000*10**6]
        with patch.object(c.dex_quotes,'RPC',return_value=RPC()):
            buy=c.buy_quote('ethereum',self.ca,Decimal(50))
        self.assertEqual(buy['min_quantity'],'0.995')
        self.assertEqual(buy['block'],'0x123')
        self.assertGreater(Decimal(buy['cost_budget_usdt']),50)
        self.assertFalse(buy['simulation_passed'])
        self.assertFalse(buy['token_tax_verified'])

    def test_status_contains_no_secrets(self):
        with patch.dict(os.environ,{'BINANCE_READ_API_KEY':'private-value','BINANCE_READ_API_SECRET':'secret-value'}):
            text=json.dumps(c.status())
        self.assertNotIn('private-value',text);self.assertNotIn('secret-value',text)

    def test_worker_triggers_both_amounts_once_per_signal(self):
        snapshot={'pairs':[{'chain':'ethereum','contract':self.ca,'eligible':True,
                           'observed_at':self.now,'indicative_spread_percent':30}],'alerts':[]}
        state={'rows':[],'history':[],'checked':0}
        def fake_review(data):
            return {**data,'observed_at':self.now,'valid_until':self.now+15,'rows':[]}
        with patch.object(c,'STATE',state),patch.object(c.dex_quotes,'monitored_tokens',return_value=[{'address':self.ca,'symbol':'QNT'}]),\
             patch.object(c,'review',side_effect=fake_review) as review,patch('builtins.print'),\
             patch.object(c.time,'sleep',side_effect=[None,StopIteration]):
            with self.assertRaises(StopIteration):c.run(lambda:snapshot)
        self.assertEqual(review.call_count,2)
        self.assertEqual({call.args[0]['amount'] for call in review.call_args_list},{'50','100'})
        self.assertEqual(state['checked'],2)

if __name__=='__main__':unittest.main()

