#!/usr/bin/env bash
# 常驻启动上传服务。key 只从 ~/.config/vibereading.env（chmod 600，可选）读取，不出现在命令行/日志里。
#   INCEPTION_API_KEY=...   # Mercury（快速，默认）
#   DASHSCOPE_API_KEY=...   # 千问（质量）
# 用法：scripts/serve.sh [port]   日志：server_out/server.log
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"; cd "$DIR"
PORT="${1:-8765}"
ENVF="${VIBEREADING_ENV:-$HOME/.config/vibereading.env}"
if [ -f "$ENVF" ]; then set -a; . "$ENVF"; set +a; fi
PY=python3; [ -x .venv/bin/python ] && PY=.venv/bin/python
mkdir -p server_out
pkill -f "server.py --host 0.0.0.0 --port $PORT" 2>/dev/null || true
INSTABOX_HEADLESS=1 nohup "$PY" server.py --host 0.0.0.0 --port "$PORT" >> server_out/server.log 2>&1 &
echo "VibeReading 上传服务 pid=$! port=$PORT"
