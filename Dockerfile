FROM python:3.13-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PORT=8080 RADAR_STATE_DIR=/app/state
WORKDIR /app
COPY cex_review.py monitor.py cex_market.py spreads.py quality.py asset_filter.py blockbeats.py equation_news.py flap_apps.py gate_review.py push_quality.py wechat_push.py runtime_paths.py cloud.py dex_quotes.py ./
COPY config.json universe.json index.html cex-review.js wallet-connect.js executor-artifact.json ARBITRAGE_GUIDE.md ./
COPY data/ ./data/
RUN groupadd --gid 10001 radar && useradd --uid 10001 --gid radar --no-create-home radar && mkdir -p /app/state && chown -R radar:radar /app
USER radar
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/healthz',timeout=4).read()"
CMD ["python", "cloud.py"]
