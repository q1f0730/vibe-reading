#!/usr/bin/env python3
"""初始化 / 迁移 VibeReading 数据库（幂等，可重复执行）。
用法：python3 scripts/init_db.py            # 数据目录默认 <仓库>/data，可用 VR_DATA_DIR 覆盖
      python3 scripts/init_db.py --recover  # 另外把未完成任务标为失败（服务启动时也会自动做）"""
import argparse, pathlib, sys
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from vibereading.web import db  # noqa: E402

ap = argparse.ArgumentParser(); ap.add_argument("--recover", action="store_true"); a = ap.parse_args()
c = db.connect()
v = db.migrate(c)
msg = f"数据库 {db.db_path()} · schema v{v}"
if a.recover:
    msg += f" · 标记中断任务 {db.recover_interrupted(c)} 个"
print(msg)
