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
import dex_quotes
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
                page=page.replace('<h1>','<style>#news-key,#news-save,#news-enabled,#news-mode,#push-token,#push-save,#push-enabled{display:none!important}</style><p>云端看板；密钥与推送开关请在平台 Runtime variables 设置。登录用户名：admin。</p><h1>',1)
                page=page.replace('Token仅保存在本机D盘配置文件，不返回到页面。','Token通过云平台运行时密钥注入，不返回到页面。')
                page=page.replace('电脑需要保持运行。','监控由云端运行，无需本地电脑开机。')
                page=page.replace('电脑与监控进程需保持运行。','云端监控进程需保持运行，无需本地电脑开机。')
                claw_on=os.environ.get('CLAWBOT_SPREAD_ENABLED','false').lower()=='true'
                page=page.replace('<h1>',f'<p>ClawBot价差提醒：{"已开启" if claw_on else "未开启"}；服务号继续接收原有提醒。</p><h1>',1)
                page=page.replace('<button id="push-test">发送测试消息</button>','<button id="push-test">发送测试消息</button><button id="clawbot-test">测试 ClawBot 价差通道</button>')
                page=page.replace('</script>','''
el('clawbot-test').onclick=()=>pushAction('/api/push/test',{channel:'clawbot'});
</script>''',1)
                body=page.encode()
                self.send_response(200)
                self.send_header('Content-Type','text/html; charset=utf-8')
                self.send_header('Cache-Control','no-store')
                self.end_headers();self.wfile.write(body);return
            super().do_GET()

        def do_POST(self):
            if not self.authenticated():return
            # Only the explicit test action is writable; configuration stays platform-only.
            expected_origin='https://'+self.headers.get('Host','')
            if (self.path!='/api/push/test' or self.headers.get('Origin')!=expected_origin
                    or self.headers.get('Content-Type','').split(';')[0]!='application/json'):
                self.push_response({'error':'云端只读；测试请求必须来自当前看板'},403);return
            try:
                length=int(self.headers.get('Content-Length','0'))
                if not 0<length<=4096:raise ValueError('请求长度无效')
                data=json.loads(self.rfile.read(length))
                if not isinstance(data,dict) or set(data)-{'channel'}:raise ValueError('测试请求格式无效')
                channel=data.get('channel','wechat')
                if channel not in ('wechat','clawbot'):raise ValueError('推送渠道无效')
                title='ClawBot价差通道测试' if channel=='clawbot' else '云端雷达：微信推送测试'
                result=wechat_push.send(title,'此消息由 Northflank 云端发送。ClawBot仅接收达到阈值的价差提醒；服务号继续接收原有提醒。此条仅为通道测试。',test=True,channel=channel)
                self.push_response(result)
            except Exception as e:
                self.push_response({'error':str(e) if isinstance(e,ValueError) else '测试失败，请检查云端推送配置'},400)
    return CloudHandler


def main():
    password=os.environ.get('DASHBOARD_PASSWORD','')
    if len(password)<16:raise SystemExit('Set DASHBOARD_PASSWORD with at least 16 characters before deploying')
    config=json.loads((monitor.ROOT/'config.json').read_text(encoding='utf-8'))
    server=ThreadingHTTPServer(('0.0.0.0',int(os.environ.get('PORT','8080'))),handler_for(password))
    for target,args in [(dex_quotes.run,(monitor.quote_inputs,)),(wechat_push.worker,()),(blockbeats.run,(wechat_push.enqueue,)),
                        (cex_run,(monitor.get,monitor.publish,monitor.Detector,config,monitor.STATE,monitor.LOCK)),
                        (monitor.worker,(config,))]:
        threading.Thread(target=target,args=args,daemon=True).start()
    print('Cloud radar started; dashboard requires authentication; health at /healthz',flush=True)
    server.serve_forever()


if __name__=='__main__':main()
