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
            for path in ['/api/arb/wallet-config','/wallet-connect.js','/api/flap-apps/status','/arbitrage-guide.md']:
                with self.assertRaises(urllib.error.HTTPError) as private:
                    opener.open(root+path)
                self.assertEqual(private.exception.code,401);private.exception.close()
            with opener.open(root+'/healthz') as response:
                health=json.load(response)
                self.assertIn('flap_apps_v1',health['features'])
                self.assertNotIn('items',health)
            with opener.open(urllib.request.Request(root+'/api/flap-apps/status',headers={'Authorization':auth})) as response:
                self.assertEqual(json.load(response)['poll_seconds'],60)
            with patch('gate_review.review',return_value={'execution_enabled':False}) as review:
                with self.assertRaises(urllib.error.HTTPError) as bad_origin:
                    opener.open(urllib.request.Request(root+'/api/gate/review',data=b'{}',headers={**headers,'Origin':'https://evil.example'}))
                bad_origin.exception.close();review.assert_not_called()
                with opener.open(urllib.request.Request(root+'/api/gate/review',data=b'{}',headers=headers)) as response:
                    self.assertFalse(json.load(response)['execution_enabled'])
                review.assert_called_once()
            with opener.open(urllib.request.Request(root+'/api/arb/wallet-config',headers={'Authorization':auth})) as response:
                wallet_config=json.load(response)
                self.assertEqual(set(wallet_config['chains']),{'0x38','0x2105','0x1'})
                self.assertTrue(wallet_config['artifact']['bytecode'].startswith('0x'))
                self.assertFalse(wallet_config['execution_enabled'])
                self.assertNotIn('rpc',json.dumps(wallet_config))
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
