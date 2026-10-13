#!/usr/bin/env bash
set -e

# Virtual display Chromium draws on, and a VNC server the panel's noVNC viewer
# reaches through the add-on's own /websockify bridge (localhost only).
rm -f /tmp/.X99-lock
Xvfb :99 -screen 0 1280x800x24 -nolisten tcp &
for _ in $(seq 50); do [ -e /tmp/.X11-unix/X99 ] && break; sleep 0.1; done
x11vnc -display :99 -localhost -rfbport 5900 -nopw -forever -shared -quiet -noxdamage &

exec /opt/venv/bin/python -m synergy_sync
