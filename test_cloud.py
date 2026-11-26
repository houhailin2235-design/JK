import base64,json,threading,unittest,urllib.request,urllib.error
from unittest.mock import patch
from http.server import ThreadingHTTPServer
from cloud import handler_for

class CloudChecks(unittest.TestCase):
    def test_auth_and_test_origin(self):
        server=ThreadingHTTPServer(('127.0.0.1',0),handler_for('test-password-16-characters'))
        threading.Thread(target=server.serve_forever,daemon=True).start()
        root='http://127.0.0.1:'+str(server.server_port)
        opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
        auth='Basic '+base64.b64encode(b'admin:test-password-16-characters').decode()
        headers={'Authorization':auth,'Content-Type':'application/json','Host':'radar.example','Origin':'https://radar.example'}
        try:
            with self.assertRaises(urllib.error.HTTPError) as denied:
                opener.open(root+'/api/dex-quotes')
            self.assertEqual(denied.exception.code,401)
            denied.exception.close()
            with opener.open(urllib.request.Request(root+'/api/dex-quotes',headers={'Authorization':auth})) as response:
                quote_status=json.load(response)
                self.assertFalse(quote_status['execution_enabled'])
                self.assertEqual(set(quote_status['chains']),{'bsc','base','ethereum'})
            with patch('dex_quotes.manual_quote',return_value={'execution_enabled':False}) as quote:
                with self.assertRaises(urllib.error.HTTPError) as denied_quote:
                    opener.open(urllib.request.Request(root+'/api/arb/quote',data=b'{}',headers={**headers,'Origin':'https://evil.example'}))
                denied_quote.exception.close();self.assertFalse(quote.called)
                with opener.open(urllib.request.Request(root+'/api/arb/quote',data=b'{}',headers=headers)) as response:
                    self.assertFalse(json.load(response)['execution_enabled'])
                quote.assert_called_once()
            with patch('wechat_push.send',return_value={'last_result':'accepted'}) as send:
                with opener.open(urllib.request.Request(root+'/api/push/test',data=b'{}',headers=headers)) as r:
                    self.assertEqual(json.load(r)['last_result'],'accepted')
                self.assertEqual(send.call_count,1)
                for change in [{'Origin':'https://evil.example'},{'Authorization':'Basic invalid'}]:
                    with self.assertRaises(urllib.error.HTTPError):
                        opener.open(urllib.request.Request(root+'/api/push/test',data=b'{}',headers={**headers,**change}))
                with self.assertRaises(urllib.error.HTTPError):
                    opener.open(urllib.request.Request(root+'/api/push/config',data=b'{}',headers=headers))
                self.assertEqual(send.call_count,1)
        finally:server.shutdown();server.server_close()

if __name__=='__main__':unittest.main()
