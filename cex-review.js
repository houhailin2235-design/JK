/* Amount-specific, read-only cross-venue review. */
let crossEpoch=0,crossTimer,crossAutoPage=0,crossAutoData;
function crossReset(){
  crossEpoch++;clearTimeout(crossTimer);
  el('cross-result').replaceChildren();
  el('cross-status').textContent='参数已变化，请重新查询。';
}
function crossInput(){return {chain:el('cross-chain').value,ca:el('cross-ca').value.trim(),symbol:el('cross-symbol').value.trim().toUpperCase(),amount:el('cross-amount').value.trim()};}
function openCrossReview(p){
  crossReset();
  document.querySelector('[data-tab="cross-review"]').click();
  if(!['bsc','base','ethereum'].includes(p.chain)){el('cross-status').textContent='当前买入报价只支持BSC、Base、Ethereum。';return;}
  el('cross-chain').value=p.chain;el('cross-ca').value=p.contract;
  el('cross-symbol').value=p.token;el('cross-amount').value='50';
  el('cross-status').textContent='已带入链、CA和候选代码，请核对代码及金额后查询。';
}
function crossCategory(r,now=Date.now()/1000){
  if(now>r.valid_until)return '过期';
  if(r.error)return '买入报价未取得';
  if(!r.identity_verified)return '身份待核实';
  if(!r.amount_checks_passed)return '有阻碍 / 待核实';
  return '金额检查通过，完整套利未验证';
}
function drawCrossResult(r,host){
  const p=document.createElement('p');
  p.textContent=r.chain+' / '+r.symbol+' · '+r.amount+' '+((r.buy||{}).stable_symbol||'稳定币')+' · '+new Date(r.observed_at*1000).toLocaleTimeString();
  host.append(p);
  const buy=r.buy||{},note=document.createElement('p');
  note.textContent=buy.error?'买入报价：'+buy.error:
    '买入：'+buy.venue+' · 报价数量 '+buy.quantity+' · 缓冲后数量 '+buy.min_quantity+
    ' · 本金加费用预算 '+buy.cost_budget_usdt+'（名义USDT） · 未模拟实际买入 / 转账税';
  host.append(note);
  if(r.error){const error=document.createElement('p');error.textContent=r.error;host.append(error);}
  const table=document.createElement('table'),head=document.createElement('thead'),hr=document.createElement('tr');
  for(const name of ['卖出平台','可卖数量 / 盘口覆盖','扣预算后条件差额↓','链+CA / 充值','状态及原因']){
    const th=document.createElement('th');th.textContent=name;hr.append(th);
  }
  head.append(hr);table.append(head);
  const body=document.createElement('tbody');
  for(const row of r.rows||[]){
    const tr=document.createElement('tr');
    cell(tr,row.venue+' '+(row.pair||''));
    cell(tr,(row.quantity||'—')+' / '+(row.filled_quantity||'—'));
    cell(tr,row.conditional_net_usdt===null||row.conditional_net_usdt===undefined?'未计算':row.conditional_net_usdt+' USDT（预算）');
    cell(tr,(row.identity_verified?'匹配':'未核实')+' / '+(row.deposit_enabled===true?'开放':row.deposit_enabled===false?'关闭':'未知'));
    const status=cell(tr,crossCategory(row)+'；'+(row.blocked_reasons||[]).join('；'));
    status.style.cssText='white-space:normal;min-width:260px;max-width:500px';
    body.append(tr);
  }
  table.append(body);host.append(table);
  const footer=document.createElement('p');
  footer.textContent='按检查状态分组、组内按条件差额降序。手续费可能为示例预算；Gas、稳定币汇率、代币税、到账与账户条件未完整验证。所有结果不可直接执行。';
  host.append(footer);
}
for(const id of ['cross-chain','cross-ca','cross-symbol','cross-amount'])el(id).addEventListener('input',crossReset);
el('cross-form').onsubmit=async event=>{
  event.preventDefault();crossReset();const epoch=crossEpoch,data=crossInput();
  el('cross-submit').disabled=true;el('cross-status').textContent='正在取得买入报价，并查询四家交易所…';
  try{
    const response=await fetch('/api/cex-review',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});
    const result=await response.json();
    if(epoch!==crossEpoch)return;
    if(!response.ok)throw Error(result.error||'查询失败');
    drawCrossResult(result,el('cross-result'));
    const remaining=result.valid_until*1000-Date.now();
    el('cross-status').textContent=remaining<=0?'结果已过期，请重新查询。':'已取得金额复核结果；不是已确认可套利。';
    crossTimer=setTimeout(()=>{if(epoch===crossEpoch)el('cross-status').textContent='报价已过期，请重新查询；历史数字不代表当前机会。';},Math.max(0,remaining));
  }catch(error){if(epoch===crossEpoch)el('cross-status').textContent=error.message;}
  finally{el('cross-submit').disabled=false;}
};
function drawCrossAuto(){
  const s=crossAutoData;if(!s)return;
  const rows=s.rows||[],total=Math.max(1,Math.ceil(rows.length/8));crossAutoPage=Math.min(crossAutoPage,total-1);
  el('cross-auto-status').textContent='累计复核 '+s.checked+' 次 · 最近 '+(s.updated?new Date(s.updated*1000).toLocaleString():'等待首次信号')+
    ' · 币安网络API'+(s.binance_network_api_configured?'已配置':'未配置')+' · OKX网络API'+(s.okx_network_api_configured?'已配置':'未配置')+(s.error?' · '+s.error:'');
  const host=el('cross-auto-results');host.replaceChildren();
  for(const result of rows.slice(crossAutoPage*8,crossAutoPage*8+8)){
    const details=document.createElement('details'),summary=document.createElement('summary');
    const positive=(result.rows||[]).find(r=>r.amount_checks_passed&&r.conditional_net_usdt!==null&&Number(r.conditional_net_usdt)>0&&Date.now()/1000<=r.valid_until);
    summary.textContent=result.chain+' / '+result.symbol+' · '+result.amount+' · '+(result.stale?'已过期':positive?'金额检查通过，仍需完整验证':'待核实 / 未覆盖成本');
    details.append(summary);drawCrossResult(result,details);host.append(details);
  }
  const pager=document.createElement('p');
  for(const [label,delta] of [['上一页',-1],['下一页',1]]){
    const b=document.createElement('button');b.textContent=label;b.disabled=delta<0?crossAutoPage===0:crossAutoPage===total-1;
    b.onclick=()=>{crossAutoPage+=delta;drawCrossAuto();};pager.append(b);
  }
  pager.append(document.createTextNode(' 第 '+(crossAutoPage+1)+' / '+total+' 页，共 '+rows.length+' 条'));host.append(pager);
}
async function refreshCrossAuto(){
  try{const response=await fetch('/api/cex-review/status',{cache:'no-store'});if(!response.ok)throw Error('自动复核状态不可用');
    crossAutoData=await response.json();drawCrossAuto();
  }catch(error){el('cross-auto-status').textContent=error.message;}
}
refreshCrossAuto();setInterval(refreshCrossAuto,15000);

