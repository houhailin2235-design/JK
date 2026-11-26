/* Explicit wallet deployment only; no approvals, swaps or background transactions. */
(function(root){
  'use strict';
  const validAddress=x=>typeof x==='string'&&/^0x[0-9a-fA-F]{40}$/.test(x)&&!/^0x0{40}$/i.test(x);
  function units(text,decimals){
    if(typeof text!=='string'||!/^\d+(\.\d+)?$/.test(text.trim()))throw Error('请填写正数金额');
    const [whole,fraction='']=text.trim().split('.');
    if(fraction.length>decimals)throw Error('金额小数位超过代币精度');
    const value=BigInt(whole)*10n**BigInt(decimals)+BigInt((fraction+'0'.repeat(decimals)).slice(0,decimals)||'0');
    if(value<=0n||value>10000n*10n**BigInt(decimals))throw Error('合约单笔上限需大于0且不超过10000单位稳定币');
    return value;
  }
  function format(value,decimals){
    const n=BigInt(value),scale=10n**BigInt(decimals);
    const fraction=String(n%scale).padStart(decimals,'0').slice(0,8).replace(/0+$/,'');
    return String(n/scale)+(fraction?'.'+fraction:'');
  }
  function deploymentData(artifact,cfg,owner,cap){
    const addresses=[owner,cfg.asset,cfg.routers[0],cfg.routers[1]];
    if(!addresses.every(validAddress)||!/^0x[0-9a-f]+$/i.test(artifact.bytecode||''))throw Error('合约构建参数无效');
    return artifact.bytecode+addresses.map(a=>a.slice(2).toLowerCase().padStart(64,'0')).join('')+units(cap,cfg.decimals).toString(16).padStart(64,'0');
  }
  const api={units,format,deploymentData,validAddress};
  if(typeof module!=='undefined')module.exports=api;
  root.RadarWallet=api;
  if(typeof document==='undefined')return;
  const el=id=>document.getElementById(id),entries=[];
  let provider=null,account='',chain='',epoch=0,handlers=[],configPromise,preview=null,busy=false;
  const names={'0x38':'BSC','0x2105':'Base','0x1':'Ethereum'};
  const message=text=>{el('wallet-status').textContent=text;};
  const clearEstimate=()=>{preview=null;el('wallet-deploy').disabled=true;el('wallet-estimate-result').textContent='尚未估算；部署状态请查看下方记录，实盘关闭。';};
  const key=(id,owner)=>'radar-deployment-v1:'+id+':'+owner.toLowerCase();
  function record(id,owner){const raw=root.localStorage.getItem(key(id,owner));return raw?JSON.parse(raw):null;}
  function save(id,owner,value){root.localStorage.setItem(key(id,owner),JSON.stringify(value));}
  function records(){
    if(!validAddress(account))return;
    el('wallet-deployment-status').textContent=Object.entries(names).map(([id,name])=>{try{const r=record(id,account);return name+'：'+(r?(r.address||r.hash||'等待钱包处理')+' · '+r.state:'未部署');}catch{return name+'：记录不可用';}}).join('\n');
  }
  function add(p,name){
    if(!p||typeof p.request!=='function'||entries.some(x=>x.provider===p))return;
    entries.push({provider:p,name:String(name||'浏览器钱包').slice(0,80)});
    const option=document.createElement('option');option.value=String(entries.length-1);option.textContent=entries.at(-1).name;el('wallet-provider').append(option);
  }
  function discover(){
    if(root.okxwallet)add(root.okxwallet,'OKX Wallet');
    const injected=root.ethereum;
    if(injected){for(const p of injected.providers||[injected])add(p,p.isOkxWallet?'OKX Wallet':p.isMetaMask?'MetaMask':'浏览器钱包');}
    root.dispatchEvent(new Event('eip6963:requestProvider'));
    if(!entries.length)message('未检测到浏览器钱包。请先打开已安装的钱包扩展，再点检测钱包。');
  }
  root.addEventListener('eip6963:announceProvider',event=>{try{add(event.detail.provider,event.detail.info.name);}catch{}});
  async function settings(){
    if(!configPromise)configPromise=fetch('/api/arb/wallet-config',{cache:'no-store'}).then(r=>{if(!r.ok)throw Error('钱包配置未加载');return r.json();}).catch(e=>{configPromise=null;throw e;});
    return configPromise;
  }
  function detach(){for(const [event,fn] of handlers){try{provider.removeListener(event,fn);}catch{}}handlers=[];}
  function forget(){epoch++;detach();provider=null;account='';chain='';el('wallet-balance').textContent='';clearEstimate();message('本页已断开。钱包扩展中的网站授权可由你自行管理。');}
  async function refresh(){
    if(!provider)return;
    const p=provider,token=++epoch;clearEstimate();
    const [accounts,id,cfg]=await Promise.all([p.request({method:'eth_accounts'}),p.request({method:'eth_chainId'}),settings()]);
    if(token!==epoch||p!==provider)return;
    if(!accounts||!validAddress(accounts[0])){forget();return;}
    account=accounts[0];chain='0x'+BigInt(id).toString(16);
    records();
    const expected=el('wallet-expected').value.trim();
    const mismatch=expected&&account.toLowerCase()!==expected.toLowerCase();
    message('已连接：'+account+' · 网络：'+(names[chain]||chain)+(mismatch?' · 与填写的套利地址不一致，请在钱包中切换账户':'')+' · 实盘关闭');
    if(cfg.chains[chain])el('wallet-network').value=chain;
    const selected=cfg.chains[chain];
    const calls=[p.request({method:'eth_getBalance',params:[account,'latest']})];
    if(selected)calls.push(p.request({method:'eth_call',params:[{to:selected.asset,data:'0x70a08231'+account.slice(2).toLowerCase().padStart(64,'0')},'latest']}));
    const balances=await Promise.allSettled(calls);
    if(token!==epoch||p!==provider)return;
    el('wallet-balance').textContent='Gas余额：'+(balances[0].status==='fulfilled'?format(balances[0].value,18)+' '+(selected?selected.native:'原生币'):'读取失败')+(selected?' · '+selected.symbol+'余额：'+(balances[1].status==='fulfilled'?format(balances[1].value,selected.decimals):'读取失败'):' · 请切换到BSC、Base或Ethereum');
  }
  async function connect(){
    discover();const entry=entries[Number(el('wallet-provider').value)];
    if(!entry)throw Error('请先检测并选择钱包');
    const expected=el('wallet-expected').value.trim();
    if(!validAddress(expected))throw Error('请填写本次专用钱包的完整公开地址');
    epoch++;if(provider)detach();provider=entry.provider;
    try{
      message('请在钱包扩展中确认连接；这里只读取公开地址和余额。');
      await provider.request({method:'eth_requestAccounts'});
      for(const event of ['accountsChanged','chainChanged']){const fn=()=>refresh().catch(e=>message(e.message));provider.on?.(event,fn);handlers.push([event,fn]);}
      const disconnected=()=>forget();provider.on?.('disconnect',disconnected);handlers.push(['disconnect',disconnected]);
      await refresh();
    }catch(e){forget();throw e;}
  }
  async function estimate(){
    clearEstimate();
    if(!provider||!account)throw Error('请先连接专用钱包');
    const token=epoch,p=provider,owner=account,id=chain;
    const expected=el('wallet-expected').value.trim(),cap=el('wallet-cap').value.trim();
    if(!validAddress(expected)||expected.toLowerCase()!==owner.toLowerCase())throw Error('当前账户与套利地址不一致');
    if(el('wallet-network').value!==id)throw Error('请先切换到所选网络，再估算');
    const cfg=await settings(),network=cfg.chains[id];if(!network)throw Error('当前链尚未接入');
    const data=deploymentData(cfg.artifact,network,owner,cap);
    el('wallet-estimate-result').textContent='正在只读模拟合约部署并估算Gas；不会请求签名…';
    const [gasHex,priceHex,balanceHex]=await Promise.all([
      p.request({method:'eth_estimateGas',params:[{from:owner,data,value:'0x0'}]}),
      p.request({method:'eth_gasPrice'}),p.request({method:'eth_getBalance',params:[owner,'latest']})]);
    const [accounts,currentId]=await Promise.all([p.request({method:'eth_accounts'}),p.request({method:'eth_chainId'})]);
    if(token!==epoch||p!==provider||String(accounts[0]).toLowerCase()!==owner.toLowerCase()||BigInt(currentId)!==BigInt(id)||cap!==el('wallet-cap').value.trim()||expected!==el('wallet-expected').value.trim())throw Error('钱包、网络或参数已改变，请重新估算');
    const gas=BigInt(gasHex),bufferedGas=(gas*120n+99n)/100n,price=BigInt(priceHex),budget=bufferedGas*price;
    let extra=0n;
    if(id==='0x2105'){
      // Bytecode size plus a conservative allowance for unsigned RLP envelope.
      const oracle='0x420000000000000000000000000000000000000f';
      const word=n=>n.toString(16).padStart(64,'0');
      const fees=await Promise.all(['0xf1c7a58b'+word(BigInt((data.length-2)/2+256)),'0x275aedd2'+word(bufferedGas)].map(d=>p.request({method:'eth_call',params:[{to:oracle,data:d},'latest']})));
      extra=(BigInt(fees[0])+BigInt(fees[1]))*120n/100n;
    }
    if(token!==epoch||p!==provider)throw Error('钱包或参数已改变，请重新估算');
    const total=budget+extra;
    preview={token,p,owner,id,cap,data,gas:bufferedGas,price,total,time:Date.now(),expected};
    const existing=record(id,owner);
    el('wallet-deploy').disabled=BigInt(balanceHex)<total||!!existing||busy;
    el('wallet-estimate-result').textContent='部署预估：'+names[id]+' · 所有者 '+owner+' · 合约单笔本金上限 '+cap+' '+network.symbol+' · Gas约 '+gas+'（预留20%：'+bufferedGas+'） · 费用预算 '+format(total,18)+' '+network.native+(id==='0x2105'?'（含L1数据及operator费用预留）':'')+(BigInt(balanceHex)<total?' · 当前Gas余额不足':'')+(existing?' · 已有部署或待核验记录，请先核验':'')+'。仅费用预估，不是固定报价；未签名、未部署、未授权资产。合约未经外部审计。';
  }
  async function checkDeployment(){
    if(!provider||!account)throw Error('请先连接钱包');
    const p=provider,owner=account,id=chain,r=record(id,owner);
    if(!r||!/^0x[0-9a-f]{64}$/i.test(r.hash||''))throw Error('没有可查询的交易Hash；若钱包曾发送交易，请勿重复部署，先检查钱包记录');
    const cfg=(await settings()).chains[id];
    const [receipt,tx]=await Promise.all([p.request({method:'eth_getTransactionReceipt',params:[r.hash]}),p.request({method:'eth_getTransactionByHash',params:[r.hash]})]);
    if(p!==provider||owner!==account||id!==chain)throw Error('网络或账户改变，请重新核验');
    if(!receipt){message('部署交易尚未确认：'+r.hash+'；稍后点击核验，勿重复发送。');return;}
    if(BigInt(receipt.status)!==1n){r.state='交易失败（Gas可能已扣）';save(id,owner,r);records();throw Error(r.state+'；请检查钱包交易记录，勿反复部署');}
    const expectedData=deploymentData((await settings()).artifact,cfg,owner,r.cap);
    if(!tx||tx.from.toLowerCase()!==owner.toLowerCase()||tx.to!=null||BigInt(tx.value)!==0n||String(tx.input||tx.data).toLowerCase()!==expectedData.toLowerCase()||!validAddress(receipt.contractAddress))throw Error('部署交易内容不符，未标记成功');
    const address=receipt.contractAddress,block=receipt.blockNumber;
    const selectors=['0x8da5cb5b','0x38d52e0f','0x65434da7','0x46b5f0d6','0xd08b18b5'];
    const [code,meta,height,...values]=await Promise.all([p.request({method:'eth_getCode',params:[address,block]}),p.request({method:'eth_getBlockByNumber',params:[block,false]}),p.request({method:'eth_blockNumber'}),...selectors.map(data=>p.request({method:'eth_call',params:[{to:address,data},block]}))]);
    const wanted=[owner,cfg.asset,...cfg.routers].map(x=>BigInt(x));wanted.push(units(r.cap,cfg.decimals));
    if(code==='0x'||meta?.hash!==receipt.blockHash||values.some((v,i)=>BigInt(v)!==wanted[i]))throw Error('链上代码、所有者或参数核验失败，未标记成功');
    if(BigInt(height)<BigInt(block)+1n){message('已出块，等待下一块确认；稍后再次核验。Hash：'+r.hash);return;}
    if(p!==provider||owner!==account||id!==chain)throw Error('网络或账户改变，请重新核验');
    save(id,owner,{...r,address,state:'部署已核验；交易执行尚未启用'});records();clearEstimate();message(names[id]+'部署已核验：'+address+'；未授权资产，实盘仍关闭。');
  }
  async function deploy(){
    if(busy)throw Error('已有钱包请求，请勿重复操作');
    const v=preview;
    if(!v||Date.now()-v.time>60000)throw Error('请重新估算部署费用（预估有效期60秒）');
    const {p,owner,id,cap,data,token}=v;
    const [accounts,currentId,balance]=await Promise.all([p.request({method:'eth_accounts'}),p.request({method:'eth_chainId'}),p.request({method:'eth_getBalance',params:[owner,'latest']})]);
    if(token!==epoch||p!==provider||String(accounts[0]).toLowerCase()!==owner.toLowerCase()||BigInt(currentId)!==BigInt(id)||cap!==el('wallet-cap').value.trim()||v.expected!==el('wallet-expected').value.trim()||el('wallet-network').value!==id)throw Error('钱包或参数已改变，请重新估算');
    if(BigInt(balance)<v.total)throw Error('Gas余额不足');
    if(record(id,owner))throw Error('已有部署或待核验记录，请先核验，勿重复付费');
    save(id,owner,{cap,state:'等待钱包确认；勿重复发送'});records();
    busy=true;el('wallet-deploy').disabled=true;
    try{
      message('请在钱包核对 '+names[id]+'、所有者 '+owner+'、上限 '+cap+'，自行确认部署签名。这里只部署合约，不授权或买卖。');
      const hash=await p.request({method:'eth_sendTransaction',params:[{from:owner,chainId:id,data,value:'0x0',gas:'0x'+v.gas.toString(16),gasPrice:'0x'+v.price.toString(16)}]});
      if(!/^0x[0-9a-f]{64}$/i.test(hash))throw Error('钱包未返回有效交易Hash，请检查钱包记录');
      message('已发送部署交易：'+hash+'；等待确认后点击核验。');
      save(id,owner,{cap,hash,state:'已发送，等待链上核验'});records();
    }catch(e){
      if(e.code===4001)root.localStorage.removeItem(key(id,owner));
      else message('发送结果不确定，请检查钱包交易记录，勿重复发送。'+String(e.message||'').slice(0,160));
      throw e;
    }finally{busy=false;clearEstimate();records();}
  }
  const action=fn=>async()=>{try{await fn();}catch(e){message(e.code===4001?'你已取消钱包请求。':String(e.message||'钱包操作失败').slice(0,250));}};
  el('wallet-discover').onclick=discover;el('wallet-connect').onclick=action(connect);el('wallet-disconnect').onclick=forget;
  el('wallet-refresh').onclick=action(refresh);el('wallet-estimate').onclick=action(estimate);
  el('wallet-deploy').onclick=action(deploy);el('wallet-check-deployment').onclick=action(checkDeployment);
  el('wallet-copy-deployments').onclick=action(async()=>{if(!validAddress(account))throw Error('请先连接钱包');const all={owner:account,chains:Object.fromEntries(Object.keys(names).map(id=>[id,record(id,account)]))};await root.navigator.clipboard.writeText(JSON.stringify(all,null,2));message('已复制公开部署记录，请保存；不含私钥。');});
  el('wallet-switch').onclick=action(async()=>{if(!provider)throw Error('请先连接钱包');await provider.request({method:'wallet_switchEthereumChain',params:[{chainId:el('wallet-network').value}]});await refresh();});
  for(const id of ['wallet-expected','wallet-cap','wallet-network'])el(id).addEventListener('input',()=>{epoch++;clearEstimate();});
  discover();
})(globalThis);
