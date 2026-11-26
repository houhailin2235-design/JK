# 多链行情与律动快讯监控

用于用户本人查看行情、暴跌和参考价差，全部采用公开行情接口。参考价差不代表可套利，需自行核实资产身份、兑换报价及充提条件。

## Northflank 部署

选择 Combined → 此仓库 → main，Dockerfile 构建；容器端口 8080（HTTP），健康检查路径 /healthz；只开一个实例，选择页面明确标注免费的计算套餐，不升级付费计划。控制台可能要求银行卡验证，未通过验证不能视为部署完成。

Runtime variables：

- DASHBOARD_PASSWORD：至少16位的独立看板密码，访问用户名 admin。
- PUSHPLUS_TOKEN：通过平台密钥变量填写，禁止提交到 Git。
- BLOCKBEATS_API_KEY：通过平台密钥变量填写，禁止提交到 Git。
- PUSH_ENABLED=false：首次部署关闭微信推送，验证后改 true。
- NEWS_PUSH_ENABLED=false：首次部署关闭快讯推送，验证后改 true。
- PORT=8080。
- RADAR_STATE_DIR=/app/state。

在平台支持且费用确认仍在免费范围内时，为 /app/state 挂载持久卷并允许 UID 10001 读写。没有持久卷时，重建会丢失快讯去重记录、推送用量和日志；重建后的首次抓取仅建基线，不补推旧消息。额度计数会重置，因此无持久存储的部署不能依赖本地日额度保护。

云端看板通过 HTTPS 与 Basic Authentication 访问，仅开放只读 API。健康检查 /healthz 仅表示进程提供服务，不保证各行情源正常。部署后检查 /api/state 的各来源时间和 errors，检查律动最新更新时间；云端 IP 的接口可达性可能与本地不同，须实测。

本仓库不包含 API Key、Token、钱包、个人日志或本地运行记录。名单为快照，不能保证涵盖全部新上市资产。

云端两轮采样及微信送达验证完成后，再停止本地监控并取消本地自动启动，避免两端重复推送。


## Triggered three-chain quote study
Read-only authenticated /api/dex-quotes; no wallet, signing, broadcasts or quote push notifications. BSC (PancakeSwap V2 / Biswap V2, USDT), Base and Ethereum (Uniswap V2 / SushiSwap V2, USDC) are independent. Every monitored contract on these three chains is in the candidate catalog, respecting stock and manual exclusions; base stablecoins are funding assets rather than self-swap candidates. Current catalog: BSC 375, Base 179, Ethereum 448, subject to directory changes.

The existing radar nominates fresh quality-eligible pools with absolute reference spread >25%, or fresh CEX/multi-pool price-drop alerts >=25%. CEX symbol matching only nominates read-only probes, never verifies identity. Signal rising edges trigger a 50/100/300 stablecoin round-trip quote; continuing signals are deduplicated, with a 10-minute per-contract cooldown and clearing required before a new trigger. Queue entries expire with signals, max age90s, max12 contracts/batch and 15s minimum batch interval. Positive budgeted quotes get ONE follow-up quote; recheck requires a later block and same positive route. No result is marked executable.

Quotes use the same recent block on both legs; chain ID, router code, base token decimals and final block hash are checked. Other token decimals failures are isolated. Returned router amounts already include pool fees/impact; 0.5% per leg and 900,000 gas times1.5 are budgeted; Base adds a fixed1 USDC extra-fee allowance. Gas is a budget, not an executor estimate. Token taxes, actual swaps, extra fees and MEV are not verified. Rows older than90s or after a failed batch are labeled stale. No claim of full DEX coverage, realtime arbitrage, profit or execution simulation.

The UI filters by chain, paginates results and coverage, and shows waiting/failed/no-route outcomes. Missing V2 routes do not imply the token is globally untradeable. Logs rotate per chain at2MB with2 backups;1440 round summaries are kept per chain in memory. Without a volume these reset on redeploy. RPC env overrides: BSC_QUOTE_RPC_URL, BASE_QUOTE_RPC_URL, ETHEREUM_QUOTE_RPC_URL. Default endpoints are PublicNode HTTPS. Whole-directory polling is no longer enabled: no signal means no expensive router query.

Validation: 35 tests passed (34 core tests plus cloud authentication test), JS syntax check passed; live read-only first12 catalog entries per chain completed in15–17seconds, with0/6/12 returned routes; separate major-token tests returned24/18/24 routes. Router source: https://developers.uniswap.org/docs/protocols/v2/deployments ; verified Sushi contracts on BaseScan/Etherscan and PancakeSwap/Biswap contracts on BscScan. USDC source: https://developers.circle.com/stablecoins/usdc-contract-addresses .


## Semi-automatic preparation (execution remains OFF)
Supersedes the no-quote-push sentence above: a positive budgeted quote rechecked in a later block on the same route now sends a service-account candidate reminder, with its own per-contract10-minute cooldown. It is NOT an executable-profit assertion. Stale queued candidate messages (>90s) are skipped. User may select an amount via the dashboard's authenticated, same-origin POST /api/arb/quote; this only requotes a currently monitored CA (1–10000 stablecoin units with correct precision,10s request cooldown), with no signing/broadcast path. A changed amount always needs new quotes and later full simulation. The user chose semi-automatic single-order execution and has not prepared a dedicated wallet.

executor/ contains an undeployed owner-only atomic V2 contract and local test fixtures. It enforces principal+positive gross-token-profit in one transaction, immutable stablecoin/router pair/input cap, short deadline and gas-price ceiling, clearing exact router allowances. Gross contract profit excludes gas: integration must require minimum gross above total gas/extra fees plus desired net profit. It has not been independently audited. No contract deployment, wallet connection, asset approvals or mainnet transaction occurred. Keys/seed phrases must stay in the user's wallet, never the free cloud service. Remaining: dedicated wallet/public address, deployment limits and fee review, deployment and full-chain execution simulation, then a one-order user authorization/signature. Validation:37 Python tests, JS syntax check, solc0.8.30 compile and local Ganache profit/revert/ownership/cap/deadline/router/pause/baseline/allowance tests passed.


## Browser-wallet connection and deployment estimate
The dashboard now discovers EIP-6963/EIP-1193 browser providers (with OKX/legacy fallback) and provides an explicit connect action. The user enters an expected public address locally; it is not baked into public source or sent to the radar server. Account/chain changes invalidate estimates; mismatched accounts cannot estimate deployment. Native/stablecoin balances are read via the user's selected wallet. Selecting a network alone does not switch it: the user presses a separate network button. No private keys are accepted, stored or requested.

Authenticated /api/arb/wallet-config serves the compiled ABI/bytecode and public three-chain constructor parameters, without RPC URLs or secrets. User selects a single-order input cap for a read-only eth_estimateGas deployment preview. The preview does NOT deploy, approve assets, sign, broadcast, or enable trading. Current fee price plus20% gas-unit allowance is only an estimate; Base L1/other extra fees are explicitly excluded. An account/network/parameter change invalidates results. Wallet address/connection still requires the user's extension confirmation. Mainnet deployment, complete executor simulation and order signing remain future separate steps. Validation:37 Python tests, browser-wallet mock tests (no account access on load, expected-address guard, account-change invalidation, local disconnect, read-only RPC calls), and JavaScript syntax check passed. EIP sources: https://eips.ethereum.org/EIPS/eip-1193 and https://eips.ethereum.org/EIPS/eip-6963 .
