FROM python:3.12-slim@sha256:12316debbfb86890f6b2d825820612741ba527ad5a44e3902759141d572bfe8f

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 DATABASE_PATH=/app/data/bot.sqlite3
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir --require-hashes -r requirements.txt \
    && groupadd --gid 10001 companion \
    && useradd --uid 10001 --gid companion --no-create-home companion \
    && mkdir -p /app/data \
    && chown companion:companion /app/data
COPY --chown=companion:companion bot ./bot
USER companion
VOLUME ["/app/data"]
HEALTHCHECK --interval=20s --timeout=5s --start-period=60s --retries=3 \
    CMD ["python", "-m", "bot.health"]
CMD ["python", "-m", "bot"]
