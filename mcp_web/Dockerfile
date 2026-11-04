FROM python:3.12-slim@sha256:12316debbfb86890f6b2d825820612741ba527ad5a44e3902759141d572bfe8f
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 DDG_CACHE_TTL=0 DDG_REF_URL_THRESHOLD=0
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir --require-hashes -r requirements.txt \
    && groupadd --gid 10002 webtools \
    && useradd --uid 10002 --gid webtools --no-create-home webtools
COPY --chown=webtools:webtools __init__.py server.py safe_http.py /app/mcp_web/
USER webtools
HEALTHCHECK --interval=20s --timeout=5s --start-period=30s --retries=3 \
    CMD ["python", "-c", "import socket; socket.create_connection(('127.0.0.1',8000),timeout=3).close()"]
CMD ["python", "-m", "mcp_web.server"]
