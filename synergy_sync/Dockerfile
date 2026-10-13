ARG BUILD_FROM=ghcr.io/home-assistant/amd64-base-debian:bookworm
FROM ${BUILD_FROM}

ENV PYTHONUNBUFFERED=1 \
    DISPLAY=:99 \
    PLAYWRIGHT_BROWSERS_PATH=/opt/ms-playwright \
    NOVNC_DIR=/usr/share/novnc

# Virtual display + VNC server + noVNC web client (served by the panel)
RUN apt-get update \
    && apt-get install -y --no-install-recommends python3 python3-venv xvfb x11vnc novnc fonts-liberation \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /tmp/requirements.txt
RUN python3 -m venv /opt/venv \
    && /opt/venv/bin/pip install --no-cache-dir -r /tmp/requirements.txt \
    && /opt/venv/bin/playwright install --with-deps chromium \
    && rm -rf /var/lib/apt/lists/*

COPY synergy_sync /app/synergy_sync
COPY run.sh /run.sh
RUN chmod a+x /run.sh
WORKDIR /app

CMD ["/run.sh"]
