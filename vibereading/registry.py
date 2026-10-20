"""组件注册表：规划器提示词与渲染器共用。每个组件 = 类型名 + 何时用 + 参数 schema。
所有组件都是装饰层（aria-hidden），不承载原文；caption 等文字由 LLM 生成，仅作图注。"""

CAP = {"type": "string", "maxLength": 24}

ROOM_PROPS = ["window", "counter", "sink", "fridge", "table", "chair", "jar", "bowl", "wine_jar",
              "stove", "bookshelf", "bed", "lamp", "door", "desk", "blackboard", "sign"]
ROOMS = ["kitchen", "tavern", "living", "bedroom", "classroom", "office", "hall"]
THINGS = ["jar_spoon", "bowl", "wine_cup", "bean_dish", "coins", "book", "letter", "phone",
          "cigarette", "key", "candle", "glass"]

COMPONENTS = {
    "doorbell": {"use": "门铃/电话铃/钟声等'响起来'的瞬间，铃按钮按下+声波扩散",
                 "params": {"caption": CAP}},
    "door": {"use": "有人进门、出门、关门；门作为意象",
             "params": {"action": {"enum": ["open", "close", "ajar"]}, "caption": CAP}},
    "interior": {"use": "新场景开头交代空间：线描室内图，墨线逐笔画出",
                 "params": {"room": {"enum": ROOMS},
                            "props": {"type": "array", "items": {"enum": ROOM_PROPS}, "maxItems": 8},
                            "caption": CAP}},
    "window": {"use": "人物看向窗外/天气与季节变化/户外景物",
               "params": {"weather": {"enum": ["clear", "rain", "snow", "wind", "fog", "night"]},
                          "view": {"enum": ["garden", "street", "field", "sea", "city"]}, "caption": CAP}},
    "car_night": {"use": "车内、夜路、离开、沉默同行",
                  "params": {"time": {"enum": ["night", "dusk"]}, "caption": CAP}},
    "stairs_memory": {"use": "回忆/闪回片段（楼梯、旧日画面），配合 tone=memory 的场景",
                      "params": {"caption": CAP}},
    "stamp_cards": {"use": "成绩、分数、价格、账目、标签等'被盖章确认'的信息，1-3 张卡片",
                    "params": {"cards": {"type": "array", "minItems": 1, "maxItems": 3,
                                         "items": {"type": "object", "required": ["label", "value"],
                                                   "properties": {"label": {"type": "string", "maxLength": 14},
                                                                  "value": {"type": "string", "maxLength": 8}}}},
                               "caption": CAP}},
    "blur_chart": {"use": "人物盯着书本/图表/文字走神，画面逐渐模糊",
                   "params": {"kind": {"enum": ["bars", "lines", "page"]}, "caption": CAP}},
    "knock": {"use": "敲门、叩击、心跳等短促节奏，波纹",
              "params": {"who": {"type": "string"}, "caption": CAP}},
    "silence": {"use": "沉默、停顿、时间凝固——用留白和一个淡出的点表现",
                "params": {"length": {"enum": ["short", "long"]}}},
    "object": {"use": "情节里被反复提到的小物件特写（勺子、酒碗、茴香豆、铜钱、信…）",
               "params": {"thing": {"enum": THINGS}, "action": {"enum": ["spin", "steam", "drop", "none"]},
                          "caption": CAP}},
    "timeline": {"use": "时间流逝/季节更替/多次重复（如 中秋→年关→端午），标签取原文里的时间词",
                 "params": {"labels": {"type": "array", "minItems": 2, "maxItems": 6,
                                       "items": {"type": "string", "maxLength": 6}}, "caption": CAP}},
    "ledger": {"use": "账本/黑板/名单：写上去、被擦掉、被划掉",
               "params": {"entries": {"type": "array", "minItems": 1, "maxItems": 4,
                                      "items": {"type": "object", "required": ["text", "state"],
                                                "properties": {"text": {"type": "string", "maxLength": 16},
                                                               "state": {"enum": ["written", "erased"]}}}},
                          "caption": CAP}},
    "sound": {"use": "哄笑、喧哗、叹气等声音在空间里散开：一个字在画面里漂浮扩散",
              "params": {"glyph": {"type": "string", "maxLength": 2}, "count": {"type": "integer", "minimum": 3, "maximum": 12},
                         "caption": CAP}},
}
# pullquote is produced from plan.quotes (text must be a verbatim substring)

HERO_MOTIFS = ["door", "window", "lamp", "moon"]
TONES = ["day", "dim", "night", "memory"]


def prompt_catalog():
    out = []
    for k, v in COMPONENTS.items():
        ps = []
        for pk, pv in v["params"].items():
            if "enum" in pv:
                ps.append(f"{pk}: {'|'.join(pv['enum'])}")
            elif pv.get("type") == "array":
                it = pv["items"]
                if "enum" in it:
                    ps.append(f"{pk}: [{'|'.join(it['enum'])}…]")
                elif it.get("type") == "object":
                    ps.append(f"{pk}: [{{{', '.join(it['properties'])}}}…]")
                else:
                    ps.append(f"{pk}: [短词…]")
            else:
                ps.append(f"{pk}: {pv.get('type', 'string')}")
        out.append(f"- {k}：{v['use']}。参数 {{{'; '.join(ps)}}}")
    return "\n".join(out)
