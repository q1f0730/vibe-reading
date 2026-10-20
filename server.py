#!/usr/bin/env python3
"""VibeReading Web 服务：账号登录 + SQLite 书架 + 持久化生成任务。
用法：python3 server.py [--host 127.0.0.1] [--port 8765]
数据（数据库 + 生成的阅读页）在 VR_DATA_DIR，默认 ./data/（不进 git）。模型 key 只从环境变量读取。"""
import pathlib, sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from vibereading.web.app import main  # noqa: E402

if __name__ == "__main__":
    main()
