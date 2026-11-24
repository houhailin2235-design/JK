"""Password-protected, read-only cloud dashboard. Secrets are runtime variables."""
import base64
import hmac
import json
import os
import threading
from http.server import ThreadingHTTPServer
import monitor
import blockbeats
import wechat_push
from cex_market import run as cex_run


def handler_for(password):
    expected='Basic '+base64.b64encode(('admin:'+password).encode()).decode()
    class CloudHandler(monitor.Handler):
        def authenticated(self):
            if hmac.compare_digest(self.headers.get('Authorization','').encode(),expected.encode()):return True
            self.send_response(401)
            self.send_header('WWW-Authenticate','Basic realm="Private token radar", charset="UTF-8"')
            self.send_header('Cache-Control','no-store')
            self.end_headers()
            return False

        def do_GET(self):
            if self.path=='/healthz':
                self.push_response({'ok':True});return
            if not self.authenticated():return
            if self.path=='/':
                page=(monitor.ROOT/'index.html').read_text(encoding='utf-8')
                page=page.replace('<h1>','<style>#news-key,#news-save,#news-enabled,#news-mode,#push-token,#push-save,#push-test,#push-enabled{display:none!important}</style><p>云端只读看板；密钥与推送开关请在平台 Runtime variables 设置。登录用户名：admin。</p><h1>',1)
                body=page.encode()
                self.send_response(200)
                self.send_header('Content-Type','text/html; charset=utf-8')
                self.send_header('Cache-Control','no-store')
                self.end_headers();self.wfile.write(body);return
            super().do_GET()

        def do_POST(self):
            if not self.authenticated():return
            self.push_response({'error':'云端只读，请通过平台 Runtime variables 修改配置'},403)
    return CloudHandler


def main():
    password=os.environ.get('DASHBOARD_PASSWORD','')
    if len(password)<16:raise SystemExit('Set DASHBOARD_PASSWORD with at least 16 characters before deploying')
    config=json.loads((monitor.ROOT/'config.json').read_text(encoding='utf-8'))
    server=ThreadingHTTPServer(('0.0.0.0',int(os.environ.get('PORT','8080'))),handler_for(password))
    for target,args in [(wechat_push.worker,()),(blockbeats.run,(wechat_push.enqueue,)),
                        (cex_run,(monitor.get,monitor.publish,monitor.Detector,config,monitor.STATE,monitor.LOCK)),
                        (monitor.worker,(config,))]:
        threading.Thread(target=target,args=args,daemon=True).start()
    print('Cloud radar started; dashboard requires authentication; health at /healthz',flush=True)
    server.serve_forever()


if __name__=='__main__':main()
