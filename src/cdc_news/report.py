"""將當日新聞摘要彙整成 Markdown 報告。"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import anthropic

from .fetcher import NewsItem
from .summarizer import summarize_item, write_overview


def load_items_for_day(data_dir: Path, day: date) -> list[NewsItem]:
    day_dir = data_dir / day.isoformat()
    if not day_dir.exists():
        return []
    items = []
    for path in sorted(day_dir.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        items.append(NewsItem(**data))
    return items


def build_report(config: dict, day: date | None = None) -> Path | None:
    """對指定日期（預設今天）的新聞產生摘要報告，回傳報告路徑。"""
    day = day or date.today()
    data_dir = Path(config.get("data_dir", "data"))
    reports_dir = Path(config.get("reports_dir", "reports"))
    reports_dir.mkdir(parents=True, exist_ok=True)

    items = load_items_for_day(data_dir, day)
    if not items:
        print(f"{day} 沒有新聞資料，略過報告產生。")
        return None

    client = anthropic.Anthropic()
    summaries = []
    for item in items:
        summary = summarize_item(client, item, config)
        summary["_item"] = item
        summaries.append(summary)
        print(f"已摘要：{item.title}")

    overview = write_overview(client, [
        {k: v for k, v in s.items() if k != "_item"} for s in summaries
    ], config)

    lines = [f"# 疫情每日摘要 {day.isoformat()}", "", "## 重點速覽", "", overview, "", "## 各則新聞摘要", ""]
    for s in summaries:
        item: NewsItem = s["_item"]
        lines += [
            f"### {item.title}",
            "",
            f"- **疾病**：{s['disease']}",
            f"- **關注程度**：{s['severity']}",
            f"- **地區**：{s['region']}",
            f"- **病例概況**：{s['case_summary'] or '—'}",
            f"- **摘要**：{s['summary']}",
            f"- **防疫建議**：{s['advice'] or '—'}",
            f"- **原文連結**：{item.link}",
            "",
        ]

    report_path = reports_dir / f"{day.isoformat()}.md"
    report_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"報告已產生：{report_path}")
    return report_path
