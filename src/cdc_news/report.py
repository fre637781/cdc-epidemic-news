"""將一週的新聞摘要與病例統計彙整成 Markdown 週報。"""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import anthropic

from .fetcher import NewsItem
from .stats import build_stats_lines
from .summarizer import summarize_item, write_overview


def load_items_between(data_dir: Path, start: date, end: date) -> list[NewsItem]:
    """讀取 data/ 下抓取日期在 [start, end] 之間的新聞（含端點）。"""
    items = []
    d = start
    while d <= end:
        day_dir = data_dir / d.isoformat()
        if day_dir.exists():
            for path in sorted(day_dir.glob("*.json")):
                data = json.loads(path.read_text(encoding="utf-8"))
                items.append(NewsItem(**data))
        d += timedelta(days=1)
    return items


def _week_label(day: date) -> str:
    year, week, _ = day.isocalendar()
    return f"{year}-W{week:02d}"


def build_report(config: dict, day: date | None = None) -> Path | None:
    """產生上一完整週（週一至週日）的疫情週報，回傳報告路徑。

    預設以今天推算：報告涵蓋「上週一 ~ 上週日」抓到的新聞。
    """
    day = day or date.today()
    this_monday = day - timedelta(days=day.weekday())
    week_start = this_monday - timedelta(days=7)
    week_end = this_monday - timedelta(days=1)
    week_label = _week_label(week_start)

    data_dir = Path(config.get("data_dir", "data"))
    reports_dir = Path(config.get("reports_dir", "reports"))
    reports_dir.mkdir(parents=True, exist_ok=True)
    watch = config.get("watch_diseases", [])

    lines = [
        f"# 疫情週報 {week_label}",
        "",
        f"涵蓋期間：{week_start.isoformat()} ~ {week_end.isoformat()}",
        "",
        "> 本報告由 AI 自動產生，內容以疾管署原文為準。",
        "",
        "## 本週疫情數據",
        "",
    ]

    stats_lines = build_stats_lines(config, day)
    lines += stats_lines or ["（未設定統計資料來源）"]
    lines.append("")

    items = load_items_between(data_dir, week_start, week_end)
    if not items:
        lines += ["## 本週公告摘要", "", "本週無新增疫情新聞。", ""]
        report_path = reports_dir / f"{week_label}.md"
        report_path.write_text("\n".join(lines), encoding="utf-8")
        print(f"報告已產生（無新聞）：{report_path}")
        return report_path

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
    lines += ["## 重點速覽", "", overview, ""]

    # 依疾病分組：關注疾病（依設定順序）在前，其餘依名稱排序
    groups: dict[str, list[dict]] = {}
    for s in summaries:
        groups.setdefault(s["disease"], []).append(s)
    ordered = [d for d in watch if d in groups]
    ordered += sorted(d for d in groups if d not in watch)

    lines += ["## 本週公告摘要", ""]
    for disease in ordered:
        lines.append(f"### {disease}")
        lines.append("")
        for s in groups[disease]:
            item: NewsItem = s["_item"]
            lines += [
                f"#### {item.title}",
                "",
                f"- **來源／關注程度／地區**：{item.source}／{s['severity']}／{s['region']}",
                f"- **病例概況**：{s['case_summary'] or '—'}",
                f"- **摘要**：{s['summary']}",
                f"- **防疫建議**：{s['advice'] or '—'}",
                f"- **原文連結**：{item.link}",
                "",
            ]

    report_path = reports_dir / f"{week_label}.md"
    report_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"報告已產生：{report_path}")
    return report_path
