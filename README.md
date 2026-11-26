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


## BSC round-trip quote study
Read-only `/api/dex-quotes`, authenticated by the cloud dashboard. Every 60 seconds quotes 50/100/300 USDT via PancakeSwap V2 and Biswap V2 for WBNB/CAKE/ETH/BTCB, using direct or WBNB paths. Both legs are pinned to the same recent block; chain ID, contract code, decimals and block hash are checked. Router quotes include pool fees and price impact; an additional 0.5% per leg and 900,000 gas times 1.5 at current gas price are budgeted. This is NOT an executor simulation, token-tax check, MEV guarantee or executable profit. No wallet, signing, broadcasts, flash loans or quote notifications. `BSC_QUOTE_RPC_URL` optionally selects an HTTPS BSC RPC. Aggregate in-memory history holds 1440 rounds; bounded `dex-quotes.jsonl` logs rotate at 2MB with two backups. Without persistent storage these reset on redeploy. Source addresses: verified router code on BscScan; token addresses from the PancakeSwap maintained BSC token list. Validation: 32 tests passed and a live read-only round returned 24 routes; all sampled net quotes were negative.
