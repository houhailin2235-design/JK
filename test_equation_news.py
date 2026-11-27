import datetime,json,pathlib,tempfile,time,unittest,urllib.error
from unittest.mock import patch
import equation_news as q
import blockbeats

def raw(no=1,zh='币安 Alpha 上线新代币：\nExample (XYZ)\n合约地址：0x'+'1'*40,source='Binance Alpha'):
    return {'no':no,'time':datetime.datetime.now(datetime.timezone.utc).isoformat(),'channels':['alpha'],'source':source,'zh':zh,'en':'','url':'https://eqn.news/monitor/'+str(no)}

class EquationChecks(unittest.TestCase):
    def test_listing_classification(self):
        for title,source in [('币安 Alpha 上线新代币：','Binance Alpha'),('ABC将上线币安Alpha','Binance Alpha')]:
            self.assertTrue(q.is_listing(title,source),title)
        for title,source in [('Alpha积分规则更新','Binance Alpha'),('币安Alpha第三波空投奖励现已开启','Binance Wallet Pre-TGE'),('请准备好领取币安Alpha空投','Binance Wallet Pre-TGE'),('Aster下架ABCUSDT','Aster'),('Aster交易竞赛活动上线','Aster'),('传闻币安Alpha上线ABC','Binance Alpha'),('币安Alpha上线代币化股票','Binance Alpha')]:
            self.assertFalse(q.is_listing(title,source),title)
        self.assertFalse(q.is_listing('Aster Spot Listing: NVDABUSDT','Aster'))
        for title in ['Aster永续合约上线：ABCUSDT','Aster Perp Listing: ABCUSDT','Aster现货上线：XYZUSDT']:
            self.assertFalse(q.is_listing(title,'Aster合约上新'))
    def test_old_aster_cache_is_removed_but_seen_ids_preserved(self):
        with tempfile.TemporaryDirectory(dir=pathlib.Path(__file__).parent) as folder:
            path=pathlib.Path(folder)/'cache.json'
            path.write_text(json.dumps({'seen':['1','2'],'items':[{'title':'Aster永续合约上线ABC','source':'Aster'},{'title':'币安Alpha上线XYZ','source':'Binance Alpha'}]}))
            with patch.object(q,'CACHE',path):cache=q.load_cache()
            self.assertEqual(len(cache['items']),1);self.assertEqual(cache['seen'],['1','2'])
    def test_parse_links_timezone_and_language(self):
        items,cursor=q.parse({'items':[raw()],'next_cursor':None})
        self.assertIn('Example',items[0]['title']);self.assertTrue(items[0]['listing'])
        self.assertTrue(q.event(items[0])['news_id'].startswith('eqn-alpha:'))
        for change in [{'url':'https://evil.example/monitor/1'},{'time':'2026-11-28T10:00:00'},{'no':'1'}]:
            with self.assertRaises(ValueError):q.parse({'items':[{**raw(),**change}]})
    def test_baseline_no_history_replay_and_update_dedup(self):
        items,_=q.parse({'items':[raw()]});now=time.time()
        self.assertFalse(q.fresh_items(items,{},now))
        self.assertEqual(q.fresh_items(items,{'initialized':True,'seen':[]},now),items)
        self.assertFalse(q.fresh_items(items,{'initialized':True,'seen':['1']},now))
        self.assertFalse(q.fresh_items(items,{'initialized':True,'seen':[]},now+3601))
    def test_etag_not_modified(self):
        error=urllib.error.HTTPError(q.URL,304,'Not Modified',{},None)
        with patch.object(q.urllib.request,'build_opener') as opener:
            opener.return_value.open.side_effect=error
            self.assertEqual(q.fetch_page(etag='test'),(None,'test'))
            self.assertEqual(opener.return_value.open.call_args.args[0].get_header('If-none-match'),'test')
    def test_blockbeats_alpha_primary_switch_risk_retained(self):
        with patch.dict('os.environ',{'EQN_ALPHA_ENABLED':'true'}):
            self.assertFalse(blockbeats.should_push({'title':'币安Alpha上线ABC'},'crash'))
            self.assertTrue(blockbeats.should_push({'title':'某协议被盗100万美元'},'crash'))
            self.assertTrue(blockbeats.should_push({'title':'ABC短时暴跌30%'},'crash'))
    def test_restart_dedup_and_persist_before_enqueue(self):
        with tempfile.TemporaryDirectory(dir=pathlib.Path(__file__).parent) as folder:
            path=pathlib.Path(folder)/'cache.json';path.write_text(json.dumps({'initialized':True,'seen':['1'],'items':[]}))
            calls=[]
            def enqueue(event):
                self.assertIn('2',json.loads(path.read_text())['seen']);calls.append(event)
            with patch.object(q,'CACHE',path),patch.object(q,'enabled',return_value=True),patch.object(q,'push_enabled',return_value=True),patch.object(q,'fetch_page',return_value=({'items':[raw(2),raw(1)],'next_cursor':None},'tag')),patch.object(q.time,'sleep',side_effect=StopIteration):
                with self.assertRaises(StopIteration):q.run(enqueue)
                with self.assertRaises(StopIteration):q.run(enqueue)
            self.assertEqual(len(calls),1)

if __name__=='__main__':unittest.main()
