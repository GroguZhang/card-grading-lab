"""Prompts for per-zone defect inspection.

Design rules, all of them learned from what goes wrong when you ask a model to
"grade this card":

1. **One zone per call.** A nine-cell contact sheet makes the model average
   across regions and lose the 2 mm chip you actually care about.
2. **Closed vocabulary.** The model picks from ten defect names; it cannot
   invent "slight surface unevenness" and quietly create a new category.
3. **Centering is out of scope.** Geometry measures it to ±0.2 pp; asking a
   model to eyeball it would replace a measurement with a guess.
4. **Abstention is a valid answer.** "看不清就说空" is stated explicitly,
   because a model asked to find defects will find them.
5. **The JSON example is in the prompt** because DeepSeek's JSON mode requires
   the word "json" plus a shape to copy.
"""

from __future__ import annotations

from .types import DEFECTS, SEVERITIES

#: Bump when the prompt changes — it is part of the cache key, so a new prompt
#: never silently reuses old answers.
PROMPT_VERSION = "2026.11.1"

ZONE_ZH: dict[str, str] = {
    "top_left": "左上角",
    "top_right": "右上角",
    "bottom_left": "左下角",
    "bottom_right": "右下角",
    "top": "上边",
    "bottom": "下边",
    "left": "左边",
    "right": "右边",
    "center": "画面中央",
}

SIDE_ZH = {"front": "正面", "back": "背面"}

_FOCUS: dict[str, str] = {
    "top_left": "角尖的磨损、露白，以及两条边在一个角上交汇处的分层或翘起",
    "top_right": "角尖的磨损、露白，以及两条边在一个角上交汇处的分层或翘起",
    "bottom_left": "角尖的磨损、露白，以及两条边在一个角上交汇处的分层或翘起",
    "bottom_right": "角尖的磨损、露白，以及两条边在一个角上交汇处的分层或翘起",
    "top": "整条上边缘的毛边、缺口、分层，以及边缘白边的连续性",
    "bottom": "整条下边缘的毛边、缺口、分层，以及边缘白边的连续性",
    "left": "整条左边缘的毛边、缺口、分层，以及边缘白边的连续性",
    "right": "整条右边缘的毛边、缺口、分层，以及边缘白边的连续性",
    "center": "画面内部的划痕、凹点、污渍、印刷线，以及反光掩盖下的可疑区域",
}

DEFECT_MENU = "\n".join(f"- `{key}`：{zh}" for key, zh in DEFECTS.items())
SEVERITY_MENU = "\n".join(f"- `{key}`：{zh}" for key, zh in SEVERITIES.items())

SYSTEM_PROMPT = f"""你是一名球星卡品相检验员，正在查看一张卡片某个局部区域的放大照片。

你的任务只有一个：判断这个区域里**是否真的存在**物理缺陷。你不负责评分，也不负责评价居中。

必须遵守：
1. 只报告你在这张图里**能直接看到**的东西。看不清、有反光挡住、或者只是疑似，就返回空的 findings 数组。宁可漏报，也不要编造。
2. 缺陷名称只能从下面的清单里选，不得自创：
{DEFECT_MENU}
3. 严重程度只能从下面三档里选：
{SEVERITY_MENU}
4. 不要评价居中（左右/上下比例由几何测量给出，与你无关）。
5. 不要因为画面是"印刷图案的一部分"就判定为缺陷。图案边缘、印刷渐变、设计留白都不是缺陷。
6. 只输出 JSON，不要输出任何解释性文字。

输出格式（这是一个 json 示例，请严格照此结构）：
{{
  "observations": "一句话说明你在这个区域看到了什么（不超过 40 字）",
  "findings": [
    {{
      "defect": "whitening",
      "severity": "minor",
      "confidence": 0.72,
      "evidence": "角尖处有一条约 2mm 的白色磨损，沿边缘呈弧形"
    }}
  ]
}}

如果这个区域干净，输出：{{"observations": "边缘平整，未见明显缺陷", "findings": []}}
"""


def build_user_prompt(zone: str, side: str) -> str:
    location = ZONE_ZH.get(zone, zone)
    side_zh = SIDE_ZH.get(side, side)
    focus = _FOCUS.get(zone, "该区域内的物理缺陷")
    return (
        f"这是同一张卡片的【{side_zh} · {location}】区域放大图。\n"
        f"请重点看：{focus}。\n"
        "图片四周可能有少量相邻区域的重复内容（裁切时做了外扩），"
        "只有落在本区域内的缺陷才算数；但若缺陷正好压在本区域边界上，也要报告。\n"
        "现在输出 json。"
    )
