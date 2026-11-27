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


### Wallet-confirmed contract deployment

The wallet panel defaults to a 100-unit per-order constructor cap. Each chain requires its own fee estimate and explicit wallet transaction. Base estimates include GasPriceOracle L1-size upper bound and operator fee with reserve. Estimate expiry, account/network guards and saved pending records prevent accidental reuse; deployment never approves tokens or trades. After mining and an additional block, verification checks exact creation input, successful receipt/canonical block, deployed code, owner, asset, routers and cap. Public deployment records are kept in this browser and can be copied for backup. A hash alone is not proof of deployment. Live arbitrage remains disabled until its execution integration is completed. The contract has no external audit. Do not repeat a deployment with an uncertain wallet result; inspect wallet history first.

Validation: 37 Python checks (the local HTTP test needs loopback network access); wallet mock tests cover estimate invalidation, account races, zero-value deployment, duplicate-pending rejection, wrong-owner rejection and confirmation depth. `executor/deployment-preflight.py ADDRESS 100` performs read-only public-chain checks; it does not sign or send.


### Quote-route quality filter

The default arbitrage list now contains only fresh positive quotes that passed a same-block, one-tenth-size capacity probe on both legs. Routes losing over 5% of principal, not covering fee budgets, missing complete capacity probes, or showing over 5% deterioration in either leg's output-per-input rate go into failed records. Stale records have their own view; all records remain inspectable. No token-wide blacklist is created. This probe is not reserve verification, token-tax testing or real execution simulation. Quality rejection also prevents candidate push and its follow-up quote. 38 Python checks and the UI category/syntax checks pass.


### Binance Alpha news alerts

The existing selected-news mode (`crash`, retained for environment compatibility) now also includes Binance Alpha / 币安 Alpha token-listing announcement headlines, including announced scheduled listings. It uses headline matching and filters rumors, denials, delistings, historical mentions and feature/campaign updates. Body-only historical mentions do not trigger. Existing 30-second polling, service-account delivery, per-news-ID deduplication and first-start baseline remain. This is a BlockBeats news filter, not a direct Binance announcement feed. News/push tests include positive and negative Alpha examples.


### Flap app-directory monitor

`flap_apps.py` polls the public backend used by https://flap.sh/apps?tab=all&lang=zh every 60 seconds, following all cursors and rejecting partial/inconsistent responses. Existing entries establish a first-success baseline without notifications. New slug/app-ID identities enqueue a namespaced text alert through the existing PushPlus service-account queue. Sorting, title changes and reappearance do not resend; app-ID aliases also avoid duplicate notifications after slug changes when available. App details are displayed in a separate dashboard tab and `/api/flap-apps/status` requires dashboard authentication. No wallets or app execution are involved.

`FLAP_APPS_ENABLED` and `FLAP_APPS_PUSH_ENABLED` default to `true`; actual delivery also requires the existing `PUSH_ENABLED` and PushPlus token. `flap-apps-cache.json` lives in `RADAR_STATE_DIR`, with atomic replacement before enqueueing. Without a persistent volume, container replacement establishes a new baseline and can miss changes during downtime. Notifications are queue submissions, not delivery receipts, and inherit existing queue/quota limits. `/healthz` exposes only the feature marker and readiness boolean for this monitor. Live public-directory read returned 12 items during setup; 44 Python checks plus dashboard script checks passed.


### Gate sell-leg review and tutorial

Authenticated, same-origin `POST /api/gate/review` performs three public GET requests only: currency networks, pair rules and the first 100 bid levels. It requires exact network plus contract match, open deposits/trading, fresh exchange timestamps, adequate quantity and minimum-order rules. Weighted proceeds replace top-bid extrapolation; partial depth never produces a full-size net figure. Cost and trading fee are user-provided, not account-verified. Results expire at snapshot age 15 seconds and always return `execution_enabled=false`/`executable=false`. The dashboard has a dedicated review form, per-Gate-row shortcut, stale-result handling, and an in-page tutorial with authenticated Markdown download. Inputs invalidate old results. No API key or wallet access is used. Cross-platform transfer/arrival risk and unverified DEX acquisition remain explicit; this is not atomic or automated Gate arbitrage. Spread notification titles now explicitly say indicative spread rather than net profit.

Validation: 51 Python checks, including weighted/partial depth, chain/CA mismatch, paused deposits, stale books, lot precision/minimums, account-cost assumptions and same-origin/auth guards; dashboard JS syntax checks; live IZI public-endpoint diagnostic (no order or transfer).


### Focus asset batch and official-route catalog

The 2026-11-28 batch adds 20 explicit token configurations (19 previously uncovered chain/contract identities), covering Ethereum, Base and BSC. It includes stablecoins and staking/synthetic-dollar assets sourced from official protocol deployments and the Uniswap token list. All 20 contracts passed public-RPC code existence and decimals checks during setup. Existing liquidity/volume, stock exclusion, asset mapping and quote quality gates are retained. Source verification does not establish an exchange deposit route, native issuance, redemption availability or execution safety; chain-specific bridged/pegged variants are labeled.

`data/arb-assets.json` documents this batch and catalogs Lido, Rocket Pool and Ethena routes as NOT connected to official conversion quoting yet. The authenticated `/api/arb/assets` endpoint and Assets/Routes dashboard tab show CAs, sources and this explicit implementation status. No new execution adapters, approvals or trades are enabled. Stable funding tokens remain excluded from their own identical-asset round-trip quote scans as before.


### Last-mile liquidity filtering for opportunity alerts

All exchange-crash, DEX spread/extreme/multi-pool-drop and arb-candidate opportunity notifications now require fresh qualified on-chain liquidity evidence (minimum $50,000 plus existing volume/activity/age checks). Pure CEX crash events remain visible on the dashboard but are not pushed without a matching qualified on-chain pool. Gate identities are resolved from its public currency/network/contract endpoint, cached for five minutes; other CEX events use explicit verified mappings. Missing identity or failed metadata reads fail closed. Matching by ticker alone never qualifies. A DEX event's own pool must pass, so a large unrelated peer cannot rescue a thin event pool. This is liquidity evidence, not confirmed executable depth or deposit-route proof.

Evidence is attached before publication and reevaluated by the push worker after queue delay. Stale/unknown evidence suppresses sound/browser/service-account opportunity alerts. The dashboard displays the suppression reason; delivered market text includes reference chain, CA, pool link and sampled liquidity. News notifications are unchanged. No token is permanently blacklisted. Regression coverage includes the reported BSC XNL CA with $335 liquidity, identity mismatch, stale data, a large peer alongside a thin event pool, queue-time deterioration and unaffected news. 57 prior Python checks plus the added worker recheck regression passed.
