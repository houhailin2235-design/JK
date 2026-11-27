import tempfile
import pathlib
import unittest
from unittest.mock import patch
import flap_apps as f

def item(slug,name=None,app_id=None):
    return {'slug':slug,'name':name or slug,'description':'description','url':'https://flap.sh/apps/'+slug,'app_id':app_id}

class FlapChecks(unittest.TestCase):
    def test_baseline_new_rename_reorder_and_reappearance(self):
        cache,fresh=f.advance([item('one'),item('two')],{},100)
        self.assertEqual(fresh,[])
        cache,fresh=f.advance([item('two','Renamed'),item('one'),item('three')],cache,200)
        self.assertEqual([x['slug'] for x in fresh],['three'])
        self.assertEqual(fresh[0]['first_seen'],200)
        cache,_=f.advance([],cache,300)
        cache,fresh=f.advance([item('three')],cache,400)
        self.assertFalse(fresh)
        self.assertEqual(cache['items'][0]['first_seen'],200)
        cache,_=f.advance([item('old',app_id='stable')],{},100)
        cache,fresh=f.advance([item('new',app_id='stable')],cache,200)
        self.assertFalse(fresh)

    def test_full_pagination_and_hidden_entries(self):
        pages=[{'total':2,'nextCursor':'opaque & next','items':[{'slug':'one'}]},
               {'total':2,'nextCursor':None,'items':[{'slug':'stonkspad'}]}]
        with patch.object(f,'get_page',side_effect=pages) as get:
            rows=f.fetch_directory()
        self.assertEqual([x['slug'] for x in rows],['one'])
        self.assertIn('cursor=opaque+%26+next',get.call_args.args[0])

    def test_incomplete_cursor_loop_and_malformed_do_not_commit(self):
        for pages in [[{'total':2,'nextCursor':None,'items':[{'slug':'one'}]}],
                      [{'total':2,'nextCursor':'x','items':[{'slug':'one'}]}]*2]:
            with patch.object(f,'get_page',side_effect=pages):
                with self.assertRaises(ValueError):f.fetch_directory()
        for data in [{'total':1,'nextCursor':None,'items':[{'slug':'../../private'}]},
                     {'total':True,'nextCursor':None,'items':[]},
                     {'total':1,'nextCursor':None,'items':[{'slug':'bad','appId':[]}]}]:
            with self.assertRaises(ValueError):f.parse_page(data)

    def test_public_links_and_chinese_description(self):
        rows,_,_=f.parse_page({'total':1,'nextCursor':None,'items':[{'slug':'test-app','displayTitle':{'zh':'中文名字'},'description':{'en':'English'},'url':'javascript:bad()'}]})
        self.assertEqual(rows[0]['name'],'中文名字')
        self.assertEqual(rows[0]['description'],'English')
        self.assertTrue(rows[0]['url'].startswith('https://flap.sh/apps/'))
        self.assertEqual(f.event(rows[0])['news_id'],'flap-app:test-app')

    def test_persist_before_enqueue_and_no_repeat_on_restart(self):
        with tempfile.TemporaryDirectory(dir=pathlib.Path(__file__).parent) as folder:
            cache=pathlib.Path(folder)/'cache.json'
            baseline,_=f.advance([item('one')],{},100)
            import json
            cache.write_text(json.dumps(baseline))
            received=[]
            with patch.object(f,'CACHE',cache),patch.object(f,'fetch_directory',return_value=[item('one'),item('two')]),patch.object(f,'enabled',return_value=True),patch.object(f,'push_enabled',return_value=True),patch.object(f.time,'sleep',side_effect=StopIteration):
                def enqueue(event):
                    self.assertIn('slug:two',f.load_cache()['seen'])
                    received.append(event)
                with self.assertRaises(StopIteration):f.run(enqueue)
                with self.assertRaises(StopIteration):f.run(enqueue)
            self.assertEqual(len(received),1)

if __name__=='__main__':unittest.main()
