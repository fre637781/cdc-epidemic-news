"""使用 Claude API 對疫情新聞產生結構化摘要。"""

from __future__ import annotations

import anthropic

from .fetcher import NewsItem

SYSTEM_PROMPT = """\
你是台灣公共衛生領域的疫情資訊分析師，負責將疾管署（CDC）發布的新聞整理成
給一般民眾與基層醫療人員閱讀的摘要。使用台灣慣用的繁體中文與醫學名詞。
摘要必須忠於原文，不可推測原文沒有的數字或結論。"""

SUMMARY_SCHEMA = {
    "type": "object",
    "properties": {
        "disease": {"type": "string", "description": "主要疾病名稱，如：登革熱、流感、腸病毒；非疾病相關新聞填「一般公告」"},
        "severity": {
            "type": "string",
            "enum": ["高", "中", "低", "不適用"],
            "description": "依病例數變化與疾管署警示用語判斷的關注程度",
        },
        "region": {"type": "string", "description": "影響地區：國內新聞填縣市或「全國」；國際疫情新聞填國家或地區名稱，如「加拿大」"},
        "case_summary": {"type": "string", "description": "病例數與疫情趨勢的一句話摘要"},
        "summary": {"type": "string", "description": "新聞重點摘要，2-4 句"},
        "advice": {"type": "string", "description": "對民眾的防疫建議，1-2 句"},
    },
    "required": ["disease", "severity", "region", "case_summary", "summary", "advice"],
    "additionalProperties": False,
}


def summarize_item(client: anthropic.Anthropic, item: NewsItem, config: dict) -> dict:
    """對單則新聞產生結構化摘要，回傳符合 SUMMARY_SCHEMA 的 dict。"""
    content = f"標題：{item.title}\n發布時間:{item.published}\n\n內文：\n{item.body or '（無法取得內文，請僅依標題摘要）'}"
    response = client.messages.create(
        model=config.get("model", "claude-opus-5"),
        max_tokens=int(config.get("max_tokens", 16000)),
        system=SYSTEM_PROMPT,
        output_config={"format": {"type": "json_schema", "schema": SUMMARY_SCHEMA}},
        messages=[{"role": "user", "content": content}],
    )
    if response.stop_reason == "refusal":
        return {
            "disease": "一般公告", "severity": "不適用", "region": "全國",
            "case_summary": "", "summary": "（此則新聞無法自動摘要）", "advice": "",
        }
    import json

    text = next(b.text for b in response.content if b.type == "text")
    return json.loads(text)


OVERVIEW_SCHEMA = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "disease": {"type": "string", "description": "疾病名稱（同一疾病只出現一次）"},
                    "level": {
                        "type": "string",
                        "enum": ["高", "中", "低"],
                        "description": "關注程度",
                    },
                    "headline": {
                        "type": "string",
                        "description": "一句話標題，30 字以內，最多放一個關鍵數字；"
                                       "說「發生了什麼、往哪個方向」，不要堆砌數據",
                    },
                    "detail": {
                        "type": "string",
                        "description": "補充說明一句（可含建議或背景），沒有可補充時填空字串",
                    },
                },
                "required": ["disease", "level", "headline", "detail"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["items"],
    "additionalProperties": False,
}

LEVEL_EMOJI = {"高": "🔴", "中": "🟠", "低": "🟢"}


def write_overview(client: anthropic.Anthropic, summaries: list[dict], config: dict) -> str:
    """由各則摘要彙整出「重點速覽」段落（Markdown 條列，每點附警示燈號）。"""
    if not summaries:
        return "本週無新增疫情新聞。"
    bullet_input = "\n".join(
        f"- [{s['disease']}/{s['severity']}/{s['region']}] {s['case_summary']} {s['summary']}"
        for s in summaries
    )
    response = client.messages.create(
        model=config.get("model", "claude-opus-5"),
        max_tokens=4096,
        system=SYSTEM_PROMPT,
        output_config={"format": {"type": "json_schema", "schema": OVERVIEW_SCHEMA}},
        messages=[{
            "role": "user",
            "content": "以下是本週各則疾管署新聞的摘要，請挑出 3-6 個最重要的重點做成"
                       "「重點速覽」。原則：\n"
                       "1. 新發事件（首例、國際緊急事件、警示升級）優先於持續中的趨勢\n"
                       "2. 同一疾病合併成一點，關注程度高的排前面\n"
                       "3. headline 是給忙碌讀者掃一眼用的：一句話、最多一個關鍵數字，"
                       "詳細數據留在報告後段即可\n\n" + bullet_input,
        }],
    )
    if response.stop_reason == "refusal":
        return bullet_input
    import json

    data = json.loads(next(b.text for b in response.content if b.type == "text"))
    order = {"高": 0, "中": 1, "低": 2}
    lines = []
    for it in sorted(data["items"], key=lambda x: order.get(x["level"], 9)):
        emoji = LEVEL_EMOJI.get(it["level"], "⚪")
        line = f"- {emoji} **{it['headline'].rstrip('。')}**"
        if it.get("detail"):
            line += f"：{it['detail']}"
        lines.append(line)
    return "\n".join(lines) if lines else bullet_input
