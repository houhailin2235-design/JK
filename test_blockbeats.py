import unittest
from unittest.mock import patch
import blockbeats
import wechat_push

class NewsChecks(unittest.TestCase):
    @patch.dict('os.environ',{'EQN_ALPHA_ENABLED':'false'})
    def test_binance_alpha_listing_announcements(self):
        for title in ['Binance Alpha上线ABC','币安Alpha将于今日上线ABC（ABC）','ABC将上线币安 Alpha',
                      'Binance Alpha will list ABC tomorrow','ABC is listed on Binance Alpha',
                      '币安Alpha新增ABC，用户可领取空投','Binance Alpha lists ABC, Alpha points required']:
            self.assertTrue(blockbeats.should_push({'title':title},'crash'),title)
        for title in ['Binance现货将上线ABC','Alpha Finance上线新产品','币安Alpha积分规则更新',
                      'Binance Alpha下架ABC','传闻ABC将上线Binance Alpha','币安Alpha不会上线ABC',
                      'ABC might be listed on Binance Alpha','Binance Alpha launches new features',
                      '币安Alpha上线新功能','币安Alpha上线交易竞赛活动','币安钱包上线ABC，之前曾入选Binance Alpha']:
            self.assertFalse(blockbeats.should_push({'title':title,'summary':'Binance Alpha上线ABC'},'crash'),title)
        self.assertFalse(blockbeats.should_push({'title':'市场综述','summary':'Binance Alpha上线ABC'},'crash'))
    def test_theft_news_also_notifies(self):
        for title in ['某协议被盗100万美元','黑客盗走用户资产','某钱包失窃','用户资金遭盗','Tokens stolen from protocol']:
            self.assertTrue(blockbeats.should_push({'title':title},'crash'),title)
        for title in ['协议未被盗','协议并未发生被盗事件','项目修复漏洞','市场行情综述']:
            self.assertFalse(blockbeats.should_push({'title':title,'summary':'上月曾被盗'},'crash'),title)

    def test_crash_only_news_filter(self):
        for title in ['某代币短时暴跌80%','某币24小时跌幅达25%','某币一小时下跌30%','Token price plummeted 40%']:
            self.assertTrue(blockbeats.should_push({'title':title},'crash'),title)
        for title in ['协议遭黑客攻击','比特币上涨30%','某币下跌2.5%','交易所暂停提现','分析师预测比特币或将暴跌','比特币没有暴跌','项目完成融资']:
            self.assertFalse(blockbeats.should_push({'title':title,'risk':True},'crash'),title)
        self.assertFalse(blockbeats.should_push({'title':'市场行情综述','summary':'去年某币暴跌90%'},'crash'))

    def test_parse_and_safe_link(self):
        body='''<rss><channel><item><title>协议遭攻击</title><description><![CDATA[<p>暂停提现</p>]]></description><link>https://www.theblockbeats.info/flash/123</link><guid>123</guid><pubDate>Wed, 25 Nov 2026 10:00:00 +0800</pubDate></item><item><title>bad</title><link>javascript:alert(1)</link></item></channel></rss>'''.encode()
        items=blockbeats.parse(body)
        self.assertEqual(len(items),1)
        self.assertTrue(items[0]['risk'])
        self.assertNotIn('<p>',items[0]['summary'])
        self.assertEqual(blockbeats.fresh_items(items,set(),False,items[0]['published']+10),[])
        self.assertEqual(blockbeats.fresh_items(items,set(),True,items[0]['published']+10),items)
        self.assertEqual(blockbeats.fresh_items(items,{items[0]['id']},True,items[0]['published']+10),[])
        self.assertEqual(blockbeats.fresh_items(items,set(),True,items[0]['published']+4000),[])

    def test_api_errors_dont_expose_credentials(self):
        with self.assertRaisesRegex(ValueError,'API Key'):
            blockbeats.parse(b'{"status":100,"message":"Missing API key"}')

    def test_news_dedup_does_not_share_market_cooldown(self):
        with patch.object(wechat_push,'settings',return_value={'enabled':True}),patch.object(wechat_push,'JOBS') as jobs,patch.dict(wechat_push.LAST,{},clear=True):
            for id in ['one','one','two']:
                wechat_push.enqueue({'type':'newsflash','news_id':id})
            self.assertEqual(jobs.put_nowait.call_count,2)

    def test_status_never_exposes_key(self):
        with patch.object(blockbeats,'settings',return_value={'api_key':'secret','enabled':True,'mode':'all'}):
            status=blockbeats.status()
            self.assertTrue(status['configured'])
            self.assertNotIn('secret',str(status))

if __name__=='__main__':unittest.main()
