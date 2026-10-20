# 分镜 JSON（plan.json）格式

`plan.json` 是 VibeReading 的"分镜"：它只描述**怎么呈现**，不包含、也不改写正文。
LLM 规划器输出的就是这份 JSON；你也可以手写，或让任意外部程序 / agent 生成，然后：

```bash
bin/vibereading build novel.txt -o out.html --plan plan.json
```

段号 = 切分后的段落编号（从 0 开始；一个非空行一段，见 `vibereading/segment.py`）。
查看段号：

```bash
python3 -m vibereading.segment novel.txt        # 打印段落数与特殊段（标题/日期行/脚注）
# 或看任意一次生成的 out.segments.json
```

## 顶层字段

| 字段 | 必填 | 说明 |
|---|---|---|
| `meta` | 否 | `title` `author` `work`(书名) `subtitle`(首屏副标题) `hero_motif`(`door`/`window`/`lamp`/`moon`) `hero_segments`(放进首屏的开头日期/地点行段号，最多 2 个，必须从 0 连续) |
| `characters` | 建议 | `[{ "id": "kong", "name": "孔乙己", "aliases": [], "color": "#2b4a6f" }]`，id 为小写字母开头 ≤16 位；颜色可省，渲染器会自动挑选并保证纸面/夜间对比度，且避开"脸红"专用色 |
| `focal_pair` | 否 | 侧栏关系线连接的两个人物 id，如 `["wo","kong"]` |
| `scenes` | **是** | `[{ "start": 0, "end": 2, "name": "柜台温酒", "time": "二十多年前", "place": "咸亨酒店", "tone": "day" }]`，必须首尾相接覆盖全部段落；`tone` ∈ `day`/`dim`/`night`/`memory` |
| `paras` | 建议 | 每段一条，见下 |
| `utterances` | 否 | 段内台词归属：`{ "i": 段号, "q": "原文逐字子串", "sp": 人物id }`，渲染为说话人色下划线 |
| `visuals` | 否 | 组件节点：`{ "at": 段号, "pos": "before"/"after", "type": 组件名, "params": {...} }` |
| `quotes` | 否 | 大字摘句：`{ "at": 段号, "text": "原文逐字子串", "w": 1-5 分量 }` |

### paras（两种写法都接受）

对象：`{ "sp": "kong", "pr": ["wo","kong"], "in": 0.4, "te": 0.7, "bl": 0.9, "bw": "kong" }`
紧凑数组：`[段号, sp, pr, in, te, bl, bw]`

- `sp` 说话人（这一段主要是谁的台词，叙述填 `""`）
- `pr` 在场人物
- `in` 焦点关系亲密度 0–1（侧栏两点距离、连线张力）
- `te` 紧张/压力 0–1（>0.45 出现压迫暗角、连线变虚）
- `bl` 脸红/难堪 0–1，`bw` 脸红的人（>0.3 出现珊瑚色边缘光，侧栏该人物出现光晕 + "脸红"文字）

## 组件（visuals.type）

见 `vibereading/registry.py`，参数不合法的键会被丢弃并使用默认值：

| type | 用途 | params |
|---|---|---|
| `doorbell` | 门铃/铃声 | `caption` |
| `door` | 进门/出门/关门 | `action`: open/close/ajar, `caption` |
| `interior` | 室内线描（墨线逐笔画出） | `room`: kitchen/tavern/living/bedroom/classroom/office/hall, `props`[], `caption` |
| `window` | 窗与天气 | `weather`: clear/rain/snow/wind/fog/night, `view`: garden/street/field/sea/city, `caption` |
| `car_night` | 车内夜行（自动开启暗色带） | `time`: night/dusk, `caption` |
| `stairs_memory` | 楼梯回忆（自动包成回忆色块） | `caption` |
| `stamp_cards` | 盖章卡片（成绩/价格/账目） | `cards`: [{label, value}] ×1–3, `caption` |
| `blur_chart` | 盯着看到模糊的图表/书页 | `kind`: bars/lines/page, `caption` |
| `knock` | 敲门波纹 | `who`: 人物id（波纹用其颜色）, `caption` |
| `silence` | 留白沉默 | `length`: short/long |
| `object` | 物件特写 | `thing`: jar_spoon/bowl/wine_cup/bean_dish/coins/book/letter/phone/cigarette/key/candle/glass, `action`: spin/steam/drop/none, `caption` |
| `timeline` | 时间流逝 | `labels`: ["中秋","年关",…] ×2–6, `caption` |
| `ledger` | 账板/黑板（可擦除） | `entries`: [{text, state: written/erased}], `caption` |
| `sound` | 声音字漂散（哄笑等） | `glyph`: 1–2 字, `count`: 3–12, `caption` |

渲染器还会做"编辑"：同一房间的 interior 只出现一次、同一物件最多两次、留白最多三处、每场景最多三个组件、
相邻组件按优先级取舍；摘句数量按场景数封顶，且与所在段落几乎相同的摘句不重复展示。被跳过的都会写进 `*.report.json`。

## 原文铁律

`quotes.text` 与 `utterances.q` 必须是对应段落的**逐字子串**，否则会被丢弃（会尝试在同批段落里重新定位）。
正文只来自原文文件本身，分镜里写什么都不会改动正文；校验脚本逐段比对页面文本与原文。
