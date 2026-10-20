# VibeReading

**把任意小说 txt 变成一页"代码图文"阅读器**：滚动驱动的场景、人物关系侧栏、说话人标记、墨线插画与动效——
全部是 CSS / SVG / Canvas，单个 HTML 文件，零图片、零外部请求。

核心原则：**原文绝不经过 LLM**。LLM 只负责输出"分镜 JSON"（哪里分场景、谁在说话、情绪曲线、在哪插什么组件），
正文由脚本逐字搬进页面，最后用无头浏览器逐段比对，保证与原文 100% 一致。

示例：[`examples/kongyiji/kongyiji.html`](examples/kongyiji/kongyiji.html)（鲁迅《孔乙己》，公版），下载后用浏览器直接打开。

---

## 架构

```
 txt ──► ① 切分 segment.py ──► 编号段落（原文，只读）
                                    │
                                    ├──► ② 规划 planner.py ──► 分镜 JSON（plan.json）
                                    │       LLM adapter：mercury（默认）/ qwen / 外部 --plan
                                    │       JSON Schema + 语义校验，不合格把错误回灌重试一次，再不行规则修复
                                    ▼
                         ③ 渲染 render.py + components.py + template.html ──► 单文件 HTML 阅读器
                                    │
                                    ▼
                         ④ 校验 tools/verify.js（逐段比对原文 / 外链 / JS 报错 / 1280 与 390 横向溢出）
                            截图 tools/shots.js
```

| 层 | 文件 | 说明 |
|---|---|---|
| 切分 | `vibereading/segment.py` | 一个非空行一段；硬换行+空行分段的文本自动按块合并；识别章节标题、日期行、文末脚注及其正文引用；自检"去空白后逐字相同" |
| 规划 | `vibereading/planner.py` `registry.py` | 两步：outline（人物/场景）→ detail（逐段说话人、在场、亲密度/紧张/脸红、句内台词归属、组件节点、摘句），按场景分批并行；provider 可插拔 |
| 渲染 | `vibereading/render.py` `components.py` `template.html` | 组件库 + 阅读器运行时；"编辑"规则去重、限密度、按优先级取舍 |
| 入口 | `bin/vibereading`、`server.py` + `vibereading/web/` | CLI；Web 服务（账号、SQLite 书架、持久化任务） |
| 校验 | `tools/verify.js` `tools/shots.js` | puppeteer-core + 系统 Chromium |

## 阅读器功能

- 首屏（标题、作者/书名、开篇日期行、动画意象：门 / 窗雨 / 吊灯 / 月）
- 场景标签 + 人物关系侧栏（Canvas）：在场人物、焦点关系两点距离随亲密度变化、紧张时连线变虚并出现压迫暗角、
  **脸红用独立的珊瑚色光晕 + 文字"某某 · 脸红"表示，不改人物本身颜色**
- 说话人标记（克制版）：整段台词只在左侧留一个小色点，正在读的那段才显示名字；段内混合台词用细色下划线（悬停显示是谁）；可一键关闭
- 目录抽屉（场景导航 + 当前位置 + 百分比）、阅读进度条、**阅读位置记忆**（localStorage，下次打开提示"继续阅读"）
- 纸与墨（默认）/ 夜间 两套主题，记住选择；暗场景（车内夜行）自动转暗色带，回忆段自动包成回忆色块
- 滚动触发动画、墨线逐笔绘制、脚注双向跳转、手机适配（顶部状态条 + 迷你关系图）、`prefers-reduced-motion`

## 组件库

`interior`（室内线描：kitchen / tavern / living / bedroom / classroom / office / hall）、`window`（窗与天气：晴/雨/雪/风/雾/夜）、
`door`、`doorbell`、`knock`（敲门波纹）、`car_night`（车内夜行）、`stairs_memory`（楼梯回忆）、`stamp_cards`（盖章卡片）、
`blur_chart`（看到模糊的图表）、`object`（物件特写 12 种，可转/冒热气/掉落）、`timeline`（时间流逝）、`ledger`（粉板/黑板，可擦除）、
`sound`（哄笑等声音字漂散）、`silence`（留白）、`pullquote`（大字摘句，必须是原文子串）。参数详见 [`docs/plan-format.md`](docs/plan-format.md)。

---

## 安装

```bash
# Python ≥3.10
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt     # 或：uv venv && uv pip install -r requirements.txt
# 校验/截图（可选）：需要系统 Chromium（默认 /usr/bin/chromium，可用 CHROMIUM_PATH 指定）
cd tools && npm install && cd ..
```

`bin/vibereading` 会自动使用仓库内的 `.venv`。

## 配置 LLM key

代码只从环境变量读取凭证，**不要把 key 写进文件或命令行参数**：

| provider | 环境变量 | 模型 | 用途 |
|---|---|---|---|
| `mercury`（默认） | `INCEPTION_API_KEY` | `mercury-3`（reasoning_effort 默认 `instant`） | 快速模式 |
| `qwen` | `DASHSCOPE_API_KEY` | `qwen3.8-max`（失败回退 `qwen3.7-plus`），关闭思考 | 质量模式 |

推荐写在只有自己可读的 shell 配置里（如 `~/.config/vibereading.env`，`chmod 600`，再在启动脚本里 `set -a; . ~/.config/vibereading.env; set +a`）。
在凭证由网关自动注入的环境中，设置 `VIBEREADING_GATEWAY=1` 即可。
**没有任何 key 时**：CLI 会提示用 `--plan`；上传页自动切换到"上传 plan.json"模式。

Mercury 的内容过滤偶尔会拒绝文学文本里的敏感情节，此时该批自动改用千问（如果配置了）。

## 用法

```bash
# 默认：Mercury 快速模式
bin/vibereading build novel.txt -o out/novel.html --title 第一章 --author 某某 --work 书名

bin/vibereading build novel.txt -o out/novel.html --quality          # 千问质量模式
bin/vibereading build novel.txt -o out/novel.html --model qwen3.7-plus
bin/vibereading build novel.txt -o out/novel.html --effort low       # mercury 推理强度

# 不用 LLM：直接喂分镜（人工 / 外部程序 / 其他 agent 写的）
bin/vibereading build examples/kongyiji/kongyiji.txt -o out/k.html --plan examples/kongyiji/kongyiji.plan.json

# 只改了渲染器，复用上次的 out/novel.plan.json
bin/vibereading build novel.txt -o out/novel.html --reuse-plan

# 生成后校验 / 截图
bin/vibereading build novel.txt -o out/novel.html --verify --shots
```

每次输出：`novel.html`（阅读页）、`novel.plan.json`（分镜，可手改后 `--plan` 重渲染）、`novel.segments.json`（切分结果）、
`novel.report.json`（耗时、调用次数、JSON 首次合格率、被跳过的组件、校验结果）。

## Web 服务：账号 + 书架

```bash
python3 scripts/init_db.py                          # 建库 / 迁移（幂等；服务启动时也会自动迁移）
python3 server.py --host 127.0.0.1 --port 8765      # 打开 http://127.0.0.1:8765
```

| 页面 | 说明 |
|---|---|
| `/` | 产品一句话 + 公开示例《孔乙己》入口 + 上传（未登录时上传区锁定） |
| `/login` `/register` | 用户名或邮箱 + 密码 |
| `/shelf` | 我的书架：标题 / 字数 / 段数 / 校验结果 / 生成时间 / 状态；打开、重新生成（沿用分镜重渲染 或 重新规划）、分享链接、删除；底部可注销账号 |
| `/jobs/<id>` | 生成进度（暗房风）：切分 → 规划 → 渲染 → 校验 四步 + 实时日志 + 时间码 |
| `/b/<id>` | 阅读页。私密书只有本人能看（别人 403）；开启"链接可见"后拿到链接的人可读 |
| `/sample/kongyiji` | 公开示例，无需登录 |

- **登录**：密码用 scrypt（N=2^15, r=8, p=1, 16 字节随机盐）哈希；会话 token 只在 `HttpOnly; SameSite=Lax` cookie 里，
  库里只存它的 sha256；HTTPS 下（`X-Forwarded-Proto: https` 或 `VR_COOKIE_SECURE=1`）自动加 `Secure`。
  登录失败限速：同一账号 15 分钟 5 次、同一 IP 15 分钟 20 次，超过返回 429；注册同一 IP 每小时 10 个。
  所有写操作只接受 `application/json`（挡跨站表单 CSRF），并拒绝 `Sec-Fetch-Site: cross-site`。
- **生成需要登录**（防止别人消耗服务器上的模型 key）；每个用户同时只能有 1 个任务，全站并发 `VR_MAX_JOBS`（默认 2）。
- **没有 key 时**：上传页自动切到"上传 plan.json"模式；书架里"沿用分镜，重新渲染"也不需要 key。
- 阅读页带 CSP `connect-src 'none'`：生成的页面不能发起任何网络请求。
- 前端零外部请求：系统中文字体栈、内联 SVG 噪点，无 CDN。

### 数据库

SQLite 单文件，默认在 `<仓库>/data/vibereading.db`（`VR_DATA_DIR` 可改；`data/` 与 `*.db` 已在 `.gitignore`）。
生成的阅读页 / 分镜 / 报告在 `data/books/<book_id>/`。迁移在 `vibereading/web/db.py` 的 `MIGRATIONS`（`PRAGMA user_version` 记版本，只追加）。

| 表 | 内容 |
|---|---|
| `users` | 用户名（大小写不敏感唯一）、可选邮箱、scrypt 哈希 |
| `sessions` | sha256(token)、用户、过期时间（30 天）、IP / UA |
| `books` | 标题、作者、原文、字数、段数、分镜 JSON、HTML 路径、模型、主题、状态 queued/running/done/failed、可见性 private/unlisted、校验结果、耗时、时间 |
| `jobs` | 任务类型 build/replan/rerender、状态、阶段、进度、日志（JSON 数组）、错误、时间。**服务重启时未完成的任务标为失败** |
| `login_attempts` | 限速计数（24 小时后清理） |

### 测试

```bash
python3 -m unittest tests.test_web -v
```

真实起服务（临时数据目录、去掉模型 key）跑：注册/登录/退出、密码哈希、登录限速 429、CSRF、未登录不能生成、无 key 必须 plan.json、
plan.json 生成 + 原文校验 14/14、别人访问/删除/重生成/改可见性 403、重启后书架与会话仍在且中断任务标为失败、注销账号连带删除书。
界面截图：`node tools/ui_shots.js http://127.0.0.1:8765 out/ui-shots`（首页 / 登录 / 注册 / 书架 / 进度页，桌面 1440 + 手机 390）。

## 部署（systemd --user）

```bash
git clone https://github.com/q1f0730/vibe-reading.git ~/vibe-reading && cd ~/vibe-reading
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt && (cd tools && npm install)   # 校验需要系统 Chromium
mkdir -p ~/.config/systemd/user && cp scripts/vibereading.service ~/.config/systemd/user/
systemctl --user daemon-reload && systemctl --user enable --now vibereading      # 监听 0.0.0.0:3000
```

- key（可选）写在 `~/.config/vibereading.env`（`chmod 600`），单元文件用 `EnvironmentFile` 读取，不会出现在命令行或日志里。
- 更新：`git pull && systemctl --user restart vibereading`（`ExecStartPre` 会自动跑数据库迁移）。
- 放在反代 / HTTPS 后面时保持 `X-Forwarded-Proto` 与 `X-Forwarded-For` 头，cookie 会自动加 `Secure`，限速按真实 IP 计。
- 也可以不用 systemd：`scripts/serve.sh 3000`。

## 分镜 JSON

格式见 [`docs/plan-format.md`](docs/plan-format.md)。`examples/kongyiji/` 里有两份真实分镜（千问 / Mercury 各一份）。

## 已知限制

- 段落是最小单位：很长的段落（如《孔乙己》一段 400 字）只能有一组情绪值，组件只能插在段与段之间。
- 组件是预制的参数化插画，不会"画"出文本里独有的物件；LLM 选错组件时靠渲染器的编辑规则兜底，但无法完全避免牵强的配图。
- 分镜质量随模型波动：Mercury 快、偶有类型/字段格式偏差（已做无歧义的自动修正）；千问更稳但每次 30–50 秒。
- 超长文本（整本书）尚未做分章节懒加载，单文件体积会随之增长。

## 致谢

页面视觉与阅读器结构借鉴了 [wang1212/storytelling-web](https://github.com/wang1212/storytelling-web)（MIT License, © 2026 wang1212）
的 Scrolling 模式与 "Paper & Ink" 预设（配色、纸张噪点、滚动淡入、墨线描绘思路）。本项目的组件库、规划管线与阅读器运行时为重新实现。

## License

MIT © 2026 Q1f730
