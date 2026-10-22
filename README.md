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
- **整本书分章阅读**（≥2 章且 ≥2 万字时自动启用）：章名自动识别（`第X章` 或"空行后的独立短行"，如「三周后（2011年2月）」）并进目录；
  只有当前章在 DOM 里，其余章放在 `<template>` 中惰性挂载（仍是零外部依赖的单文件）；目录可跳到任意章/场景，章末"上一章 / 下一章"，
  进度条与百分比按全书字数计算，"继续阅读"会切到上次的章节与段落

## 整本书模式（长文本规划）

超过 2 章且 ≥1.5 万字（或 >200 段）时，规划改为按章进行：
1. **cast**：每章开头摘样 → 全书人物表（≤10）/ 焦点关系（1 次调用）；
2. 每章 1 次 outline（只切本章场景）+ 本章 detail 批次；**章与章并发**，所有 HTTP 调用共用一个并发闸 `VR_LLM_CONCURRENCY`（默认 3）；
3. 429 / 5xx / 超时：指数退避重试（`VR_LLM_RETRIES`，默认 8 次，尊重 `Retry-After`），不占格式重试次数；
4. 两次不合格（截断 / 坏 JSON / 漏段）的批次对半拆开再试一次；**单章抛异常只让这一章降级**为规则场景 + 规则分镜；
5. **内容过滤拒答**：配置了千问 → 本批改用千问；没有 → 批次对半拆开重试（最多两层），仍被拒的段落用**规则化默认分镜**
   （"人名+说/问/道"判断说话人、场景内出现过的人名算在场、出现脸红类词才给脸红值，情绪取中性值），不再"沿用上一段"；
6. 进度：每章完成输出一条 `chapter+ 第 k/N 章「章名」完成 · m 场景（done/N）`，网页进度页显示到第几章。

## 关键场景专属动画（hero scenes，可选）

`--hero`（网页表单「关键场景动画」）开启后，在分镜之后多一个阶段。**默认是原语模式**：模型不画画，只做编排。

1. **选场景**：每章 1 次 Mercury 调用，依据分镜里的紧张/亲密/脸红曲线和场景表选 1–3 个关键场景（`--hero-max`，默认 2）。
   本章正文按段切成 k 个分区（前/中/后），**每个分区各取 1 个**；模型扎堆在某一段时丢掉多余的，再为空分区补一次调用。
   落在日期行 / 脚注 / 章名 / 首屏段上的位置自动挪到最近的正文段（渲染时也会再兜底一次）。只给截断短摘，不给全文；被内容过滤拒答时去掉短摘再试。
2. **编排（原语模式，默认）**：Mercury 只输出 JSON——选哪几个原语、2–4 个镜头、每个镜头的参数和滚动区间。这一步**只看场景描述，不看原文**。
   画面文字只能是 ≤8 字的意象短词：逐条与原文比对（≥4 字片段出现在原文里即拒收）、拒收台词口吻（你/我/语气词/祈使）和标点；
   标题/图注同样过一遍，不合格就换成编排的小标题。具象原语（门/勺/车）必须在场景描述里真有这个物件；同一章里大多数原语只能用一次，避免千篇一律。
3. **校验**：编排校验（覆盖 0→1、不留空档、同时最多 2 层、暗色镜头 ≤2 且不能全暗）→ `tools/hero_check.js` 在与阅读页相同的沙箱 iframe 里实测：
   JS 报错、CSP 违规与外部请求、桌面与手机横向溢出、明暗主题 p=0/0.5/1 非空白、**全程画面非空白占比**、**终态对比度 ≥3:1**（明暗主题各测）、
   画面随滚动变化、夜间背景不是写死的浅色、进度扫动时的帧间隔与长任务、`prefers-reduced-motion` 下画面静止。
4. **修一次 → 规则编排 → 预制组件**：不合格把错误回灌让 Mercury 修一次；仍不合格 → 按场景描述关键词做规则编排（一定合格，同样实测）；再不行才回退预制组件。

### 原语库（`vibereading/hero_lib/`，Hark 手写，零依赖，约 55KB，全页只放一份）

| 原语 | 画面 |
|---|---|
| `typeset` | 动态排版：拆字聚合 + 字距呼吸 / 字被拉开 + 尺寸标注 / 遮罩逐行揭示错位 / 竖排；背后一枚描边大字做景深 |
| `develop` | 暗房显影：红色安全灯、药水盘涟漪，相纸从一片灰显影到清晰，胶片边码 |
| `ink` | 墨迹晕染：沿纤维晕开、边缘积墨、飞溅墨点，字在墨团中央留白 |
| `press` | 凸版压印：凹痕 + 高光，墨不均匀地吃进纤维，印刷角线 / 套准标，可选小红印 |
| `window` | 窗格投影：窗形光在墙上移动、拉长、变色（午→暮→夜），尘埃只在光里亮，字只在光里显出 |
| `focus` | 景深对焦：远词 → 近词的对焦转移，失焦光斑，镜头距离刻度 |
| `orbit` | 关系引力：两人两点，张力线束（高张力绷直颤动）、被对方挤压的场线、尺寸标注 |
| `door` | 门缝光：黑暗里门开出一条光，地板上的梯形光、浮尘，字以透视躺在光里 |
| `spoon` | 物件特写：金属勺的渐变 / 高光 / 窗形反光 / 投影，细引线注释 |
| `passing` | 夜行车内：路灯光带斜扫车厢，远处车灯失焦，雨珠随灯亮起 |
| `chart` | 情绪读数：本场景真实的逐段紧张/亲密/脸红曲线（来自分镜数值，不由模型编造），直接标注 |

统一：纸 / 墨 / 暗房红 / 少量冷灰蓝的色板（明暗两套），中文衬线（Noto Serif SC → 思源宋体 → 系统宋体，不发外部请求），
全局胶片颗粒 + 暗角 + 图号与时间码；所有原语都是滚动进度的纯函数（没有 rAF 循环，减弱动效天然静止），转场在相邻镜头的重叠区“先出后进”。
页面里原语库只嵌一次（`#hs-lib`），每个 hero 只带几百字节的编排 JSON，父页面在接近视口时拼出 srcdoc。

**代码模式（`--hero-mode code` / `VR_HERO_MODE=code`，默认关闭）**：旧流程——先写镜头脚本，再让 Mercury 自由写一段动画模块（≤40KB），
静态检查 + 同样的浏览器实测，修一次，仍不合格回退预制组件。

运行方式：模块放在 `<iframe sandbox="allow-scripts" srcdoc>`（不可同源）+ CSP `default-src 'none'`（无网络），接近视口才创建；
父页面按滚动位置通过 `postMessage` 传进度 `p∈[0,1]` 与主题。舞台 sticky，滚动距离按描述的秒数给（约 1–2.7 屏）；减弱动效时直接显示终态、不 sticky。
原文段落的渲染与逐段校验不经过这一阶段。结果写进 `plan.json` 的 `heroes`（复用分镜重新渲染时保留并重新校验；`--no-hero` 去掉），
排查信息在 `<输出>.heroes.json`。开发时可用 `tools/prim_gallery.js` 对任意编排批量截图。

## 模型无关的生成脚手架（用户自己配模型）

模型由用户自己配置（强/弱、贵/便宜、有无 JSON 模式、会不会被内容过滤都可能），所以质量不能押在模型上：
**不管接什么模型，原文逐字、校验和兜底都是同一套；档位只决定“给模型多大的活、约束多严、多早交给规则”**。强模型可以更好，但不会更差。

```
 设置页填 base_url / model / key ──► ① Provider 层 llm.py ──► ② 能力探针 probe.py ──► 档案 + A/B/C 档（存库）
                                                                     │
 生成任务 ──► Engine(client, 档案, 策略 tiers.py) ──► 规划 / hero 都只认 Engine ──► ④ 统一护栏 quality.py ──► 质量报告
```

| 层 | 文件 | 说明 |
|---|---|---|
| Provider | `vibereading/llm.py` | OpenAI 兼容（Mercury / 千问 / DeepSeek / OpenRouter / Gemini 兼容端点…）+ Anthropic 原生 + Gemini 原生；超时、429/5xx 指数退避（尊重 Retry-After）、每个配置一个并发闸、流式/非流式（SSE）、网关总时长上限；结构化输出三档（json_schema / Anthropic 强制 tool / Gemini responseJsonSchema → JSON 模式 → 提示词），提供方 400 拒绝某参数时自动降一档并记住；**推理吃光输出**（finish=length 且正文空或推理占大头）时自动降推理档 → 关推理；各家内容过滤信号统一成 `ContentFiltered`；key 只进请求头，`repr` / 报错 / 日志一律 `redact()` |
| 探针 | `vibereading/probe.py` | 保存配置时自动跑约 11 次小调用：连通+延迟、JSON 模式、结构化输出、提示词 JSON、工具调用、最大输出（参数接受度 + 一次 160 项的真实长 JSON）、上下文（2 万字大海捞针）、是否推理模型、中文长文本（《孔乙己》逐段 JSON + 逐字摘句）、内容过滤倾向（《祝福》《孔乙己》含冲突的公版节选） |
| 分档 / 策略 | `vibereading/tiers.py` | 档位 → 批次大小、上下文切块阈值、提示词详细度、few-shot 数、schema/enum 约束、修复轮数、每场景组件上限、hero 每章上限与镜头数、代码模式开关；再按探针实测的可靠输出 / 上下文收紧 |
| 护栏 / 报告 | `vibereading/quality.py` | 见下 |
| Web | `vibereading/web/models.py` `crypto.py` | 设置页 `/settings`（测试 / 保存并探测 / 删除）；key 用 AES-256-GCM 加密存库，主密钥只来自环境变量 `VR_SECRET_KEY`；base_url 默认禁止内网/回环地址且必须 https（防 SSRF） |

### 分档规则（`probe.classify`）

| 档 | 条件 | 打法 |
|---|---|---|
| **C** | 连不上；或 JSON 模式 / 结构化 / 提示词 JSON 都不行；或中文长文本 < 0.6；或可靠输出 < 2000 token；或上下文 < 8k | 每批 ≤8 段 / 1500 字，outline 预算 5000 字（超长文本更早按块规划），提示词附 2 条 few-shot + 【严格要求】（段号清单、人物 id / 组件 type 枚举），2 轮修复，每场景 ≤1 个组件，hero 每章 1 个、只用 2 镜头 |
| **B** | 介于两者之间 | 每批 ≤16 段 / 3000 字，1 条 few-shot + 枚举约束，2 轮修复，每场景 ≤2 个组件，hero 每章 ≤2、≤3 镜头 |
| **A** | 有原生结构化输出（schema 或 JSON 模式）且中文长文本 ≥ 0.9、可靠输出 ≥ 6000、上下文 ≥ 16k、内容过滤 0 误伤 | 每批 ≤28 段 / 5000 字，不加示例，1 轮修复，每场景 ≤3 个组件，hero 每章 ≤3、≤4 镜头；**代码模式**只对 A 档且可靠输出 ≥12000 开放，且仍默认关闭 |

所有档：结构化约束取探针实测的最强一档；没有 JSON 模式就靠提示词 + 宽松解析（去代码块 / 拼接多段 JSON / 0→"" 等无歧义修正）。
用户模型被内容过滤时**不会偷偷改用平台模型**：对半拆批重试，仍被拒的段落用规则分镜。平台 Mercury 被过滤时沿用旧行为改用平台千问。

### 统一质量护栏（所有模型同一套）

1. schema + 语义校验（段号完整、场景首尾相接）→ 带错误回灌修复（轮数按档）→ 对半拆批 → 规则分镜；
2. **组件与原文一致性**（启发式）：`door` 附近 ±3 段要有“门”，`car_night` 要有“车”，`object` 的物件词、`interior` 的房间词、
   `timeline` 标签必须出自本场景原文…；不一致先回灌让模型改，改不好由 `guard_plan` 直接去掉；同时按档限制每场景组件数、去掉夹带 ≥8 字原文的图注；
3. hero：编排校验 + 镜头数上限 + **画面意象词与场景原文至少有字面关联** → 修一次 → 规则编排 → 预制组件；浏览器实测不变；
4. 渲染后静态解析 HTML 的 `[data-src]` 逐段比对原文（不开浏览器也能确认逐字一致 / 漏段），开了 `--verify` 再用 Chromium 测外链 / JS 报错 / 溢出。

每次生成附一张**质量报告**（`report.json` 的 `quality`，并存进 `books.quality_json`）：各项得分（schema 合格率、首次合格率、逐字一致、
模型分镜覆盖率、场景分布、组件一致、组件/原语多样性、hero）、总分、**下限检查**（逐字 100%、无漏段、分镜完整、页面组件全部与原文一致、
hero 全部通过实测或已回退、浏览器校验）、降级段落比例、用的模型 / 档位 / 自动适配事件、调用数 / token / 耗时。
书架卡片上有一行“质量 · 档 · 降级 · 下限”，书的主人打开阅读页右下角有“作者视图”徽标，报告页 `/b/<id>/quality` 只有主人能看；分享链接的读者拿到的是原文件。

### 评测套件 `evals/`

```bash
python3 evals/run.py evals/configs/mercury.json evals/configs/qwen.json evals/configs/weak-mercury.json   # 每个配置：探针 → 完整管线 → 分数卡
python3 evals/run.py --scorecard                                                                       # 汇总成 evals/results/SCORECARD.md
```

固定文本 `evals/texts/zhufu.txt`（鲁迅《祝福》，公有领域，来源见 `evals/texts/SOURCE.md`）。配置里不写 key，只写环境变量名 `api_key_env`；
`simulate`（仅评测）可以把强模型压成弱模型（不给 JSON 模式 / schema、限制输出 token），`force_tier` 做“不自适应”对照。
分数卡列：档位、总分、下限、schema 合格率、首次合格、逐字一致、降级段落、场景分布、场景数、漏原文段、组件一致（护栏前）、原语多样性、hero、耗时、调用数、输出 token。
最近一次结果见 [`evals/results/SCORECARD.md`](evals/results/SCORECARD.md)。

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
在凭证由网关自动注入的环境中，设置 `VIBEREADING_GATEWAY=1`（所有 provider）或 `VIBEREADING_GATEWAY=mercury`（只有列出的 provider）。
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
| `/settings` | 模型设置：填自己的模型（OpenAI 兼容 / Anthropic / Gemini），「测试」连通，「保存并探测」后显示能力档案与 A/B/C 档；删除即回到平台默认 |
| `/b/<id>/quality` | 质量报告（只有书的主人能看） |
| `/shelf` | 我的书架：标题 / 字数 / 段数 / 校验结果 / 质量 · 档位 / 生成时间 / 状态；打开、重新生成（沿用分镜重渲染 或 重新规划）、分享链接、删除；底部可注销账号 |
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
python3 -m pytest -q tests        # 或 python3 -m unittest discover tests
```

`tests/test_longtext.py`（全部是合成文本，不含版权作品）：章名识别 / 硬换行判定；整本书规划在模拟的 429、内容过滤、单章异常下仍完成且只局部降级；
分章渲染；≥1 MB txt 上传生成；分章阅读页全部挂载后逐段校验。大页面性能：`node tools/perf.js page.html [--mobile]`。

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
- **用户自带模型需要 `VR_SECRET_KEY`**（≥32 字符随机串，`python3 -c "import secrets;print(secrets.token_urlsafe(48))"`），同样写进 `~/.config/vibereading.env`；
  丢了它，已保存的用户 key 都解不开（用户重新填一次即可）。本地自建模型（内网地址 / http）需要额外设 `VR_ALLOW_PRIVATE_LLM=1` / `VR_ALLOW_HTTP_LLM=1`。
- 更新：`git pull && systemctl --user restart vibereading`（`ExecStartPre` 会自动跑数据库迁移）。
- 放在反代 / HTTPS 后面时保持 `X-Forwarded-Proto` 与 `X-Forwarded-For` 头，cookie 会自动加 `Secure`，限速按真实 IP 计。
- 也可以不用 systemd：`scripts/serve.sh 3000`。

## 分镜 JSON

格式见 [`docs/plan-format.md`](docs/plan-format.md)。`examples/kongyiji/` 里有两份真实分镜（千问 / Mercury 各一份）。

## 已知限制

- 段落是最小单位：很长的段落（如《孔乙己》一段 400 字）只能有一组情绪值，组件只能插在段与段之间。
- 组件是预制的参数化插画，不会"画"出文本里独有的物件；LLM 选错组件时靠渲染器的编辑规则兜底，但无法完全避免牵强的配图。
- 分镜质量随模型波动：Mercury 快、偶有类型/字段格式偏差（已做无歧义的自动修正）；千问更稳但每次 30–50 秒。
- 整本书单文件约 1 MB（12 万字）：首屏只解析/布局第一章，但 HTML 下载体积仍随全文增长。
- 书中间的译注（如段首 "(1) …"）只有出现在文末时才会被识别为脚注并双向链接。
- Mercury 免费档并发能力有限：整本书会遇到大量 429，靠退避重试吸收，耗时主要花在排队上。

## 致谢

页面视觉与阅读器结构借鉴了 [wang1212/storytelling-web](https://github.com/wang1212/storytelling-web)（MIT License, © 2026 wang1212）
的 Scrolling 模式与 "Paper & Ink" 预设（配色、纸张噪点、滚动淡入、墨线描绘思路）。本项目的组件库、规划管线与阅读器运行时为重新实现。

## License

MIT © 2026 Q1f730
