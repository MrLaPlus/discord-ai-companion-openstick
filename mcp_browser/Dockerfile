FROM python:3.12-slim@sha256:12316debbfb86890f6b2d825820612741ba527ad5a44e3902759141d572bfe8f
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir --require-hashes -r requirements.txt \
    && playwright install --with-deps chromium \
    && groupadd --gid 10003 browser \
    && useradd --uid 10003 --gid browser --no-create-home browser \
    && chmod -R a+rX /ms-playwright
COPY --chown=browser:browser __init__.py server.py /app/mcp_browser/
USER browser
HEALTHCHECK --interval=20s --timeout=5s --start-period=30s --retries=3 \
    CMD ["python", "-c", "import socket; socket.create_connection(('127.0.0.1',8000),timeout=3).close()"]
CMD ["python", "-m", "mcp_browser.server"]
