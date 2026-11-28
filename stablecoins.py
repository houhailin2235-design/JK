"""Explicit USD stablecoin symbols for CEX alerts; never classify by ticker prefix."""
STABLECOINS=frozenset('USDT USD1 USDC FDUSD DAI USDS USDD TUSD USDP PYUSD USDE FRAX GUSD RLUSD'.split())
STABLE_DROP_PERCENT=5

def is_stablecoin(symbol):
    return isinstance(symbol,str) and symbol.upper() in STABLECOINS

def alert_threshold(alert):
    return STABLE_DROP_PERCENT if is_stablecoin(alert.get('symbol') or alert.get('token')) else 25

def detector_config(config):
    return {**config,'windows':[[seconds,STABLE_DROP_PERCENT] for seconds,_ in config['windows']]}
