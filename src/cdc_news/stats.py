"""從疾管署開放資料平台抓取病例統計，計算每週數字與趨勢。

資料來源設定於 config 的 stats_datasets，支援兩種格式：
- line_list:    每列一筆病例（如 Dengue_Daily.csv），依 date_field 計數
- weekly_count: 每列已是週統計（date_field 取週、count_field 加總）

任何一個資料集抓取失敗都只會略過該疾病，不影響報告產生。
"""

from __future__ import annotations

import csv
import io
import json
import re
from datetime import date, datetime, timedelta

import requests

USER_AGENT = "cdc-epidemic-news/0.1 (+https://github.com)"
REQUEST_TIMEOUT = 60


def _iso_week(d: date) -> str:
    year, week, _ = d.isocalendar()
    return f"{year}-W{week:02d}"


def _parse_date(value: str) -> date | None:
    """容忍常見日期格式：2026-08-01、2026/08/01、20260801。"""
    value = value.strip()
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y%m%d"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    return None


def _parse_week(value: str) -> str | None:
    """容忍週欄位格式：2026-W31、202631、2026/31、31（跨年會有歧義，以當年處理）。"""
    value = value.strip()
    m = re.match(r"^(\d{4})[-/W]*W?(\d{1,2})$", value)
    if m:
        return f"{m.group(1)}-W{int(m.group(2)):02d}"
    if value.isdigit() and len(value) <= 2:
        return f"{date.today().year}-W{int(value):02d}"
    return None


def fetch_rows(url: str) -> list[dict]:
    """下載並解析 CSV 或 JSON，回傳 dict 列表。"""
    resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    content_type = resp.headers.get("Content-Type", "")
    text = resp.content.decode("utf-8-sig", errors="replace")
    if url.endswith(".json") or "json" in content_type:
        data = json.loads(text)
        return data if isinstance(data, list) else data.get("data", [])
    return list(csv.DictReader(io.StringIO(text)))


def weekly_counts(rows: list[dict], entry: dict) -> dict[str, int]:
    """依資料集設定彙整出 {ISO週: 數量}。"""
    counts: dict[str, int] = {}
    date_field = entry["date_field"]
    mode = entry.get("mode", "line_list")
    count_field = entry.get("count_field")

    for row in rows:
        raw = str(row.get(date_field, "")).strip()
        if not raw:
            continue
        if mode == "line_list":
            d = _parse_date(raw)
            if d is None:
                continue
            week = _iso_week(d)
            counts[week] = counts.get(week, 0) + 1
        else:  # weekly_count
            week = _parse_week(raw) or (_iso_week(_parse_date(raw)) if _parse_date(raw) else None)
            if week is None:
                continue
            try:
                n = int(float(str(row.get(count_field, "0")).replace(",", "")))
            except ValueError:
                continue
            counts[week] = counts.get(week, 0) + n
    return counts


def build_stats_lines(config: dict, report_day: date | None = None) -> list[str]:
    """產生週報「本週疫情數據」段落的 Markdown 行。

    對每個 stats_datasets 疾病，列出上一完整週的數字與前一週的比較。
    """
    report_day = report_day or date.today()
    # 報告日（週一）的「上一完整週」
    this_week_start = report_day - timedelta(days=report_day.weekday())
    last_week = _iso_week(this_week_start - timedelta(days=7))
    prev_week = _iso_week(this_week_start - timedelta(days=14))

    lines: list[str] = []
    for entry in config.get("stats_datasets", []):
        disease = entry.get("disease", "?")
        try:
            counts = weekly_counts(fetch_rows(entry["url"]), entry)
        except Exception as exc:  # 單一資料集失敗不影響整份報告
            lines.append(f"- **{disease}**：資料抓取失敗（{type(exc).__name__}），請檢查資料集網址")
            continue

        last = counts.get(last_week)
        prev = counts.get(prev_week)
        if last is None:
            lines.append(f"- **{disease}**：{last_week} 尚無資料")
            continue

        trend = ""
        if prev:
            diff = last - prev
            pct = diff / prev * 100
            arrow = "↑" if diff > 0 else ("↓" if diff < 0 else "→")
            trend = f"，較前一週 {arrow} {diff:+d}（{pct:+.0f}%）"
        lines.append(f"- **{disease}**：{last_week} 共 {last:,} 例/人次{trend}")
    return lines
