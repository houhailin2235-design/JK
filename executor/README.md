# Semi-automatic execution preparation

Status: contract source only; NOT deployed or connected to any wallet. No automatic signing or broadcasts. The cloud radar still only probes quotes.

Flow: rechecked positive quote → service-account candidate reminder → user chooses amount → fresh quote and full atomic-call simulation → show amount, fee cap and minimum net profit → user wallet authorization → execute one order. Approval transactions, if needed, must be shown separately. Changing amount requires a fresh quote and simulation. Nothing should execute from an old notification.

AtomicArbitrage.sol completes both V2 swaps in one transaction, using the owner's own assets. Only that owner can execute; the stablecoin, router pair and input cap are immutable. Paths, deadline, gas price and positive minimum gross token profit are enforced. Exact allowances are cleared and proceeds returned to the owner. Existing balances cannot subsidize a losing route. A revert rolls back swaps but may cost gas. Gas is NOT part of the on-contract profit calculation: the integration must set minimum gross profit above the fee budget plus user minimum net profit.

Before deployment/use: user must prepare a separate wallet, choose per-chain contract input caps, approve deployment gas, and complete independent review. No private keys or seed phrases belong in the dashboard, chat, repository or free cloud service. Real token taxes, malicious tokens, MEV and extra chain fees still require simulation/security checks. Local fixture tests do not establish mainnet safety. This source has not been externally audited.

Dependencies are isolated on drive D under .codex-storage/temp/arb-executor-lab: solc0.8.30, ethers6.15.0, ganache7.9.2, OpenZeppelin5.4.0. TestFixtures.sol is for local tests only.
