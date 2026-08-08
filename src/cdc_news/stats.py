"""從疾管署開放資料平台抓取統計資料，計算每週數字與趨勢。

資料來源設定於 config 的 stats_datasets（病例數）與 lab_datasets
（實驗室監測，多了病原體/型別分組），支援三種欄位配置：
- year_field + week_field: 每列帶「年份」與「週別」欄位（疾管署法定傳染病
  週報表的常見格式），count_field 加總（未設定時每列計 1）
- mode: line_list          每列一筆病例，依 date_field（完整日期）計數
- mode: weekly_count       date_field 為週字串、count_field 加總

lab_datasets 另可設 group_field（如「病原體」「型別」欄位），
週報會列出上週各分組的數字明細。

任何一個資料集抓取失敗都只會略過該項目，不影響報告產生。
"""

from __future__ import annotations

import csv
import io
import json
import re
from collections.abc import Iterator
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


def _row_count(row: dict, count_field: str | None) -> int | None:
    """讀取一列的數量值；未設定 count_field 時每列計 1。"""
    if not count_field:
        return 1
    try:
        return int(float(str(row.get(count_field, "0")).replace(",", "")))
    except ValueError:
        return None


def _iter_week_rows(rows: list[dict], entry: dict) -> Iterator[tuple[str, str, int]]:
    """逐列產出 (ISO週, 分組, 數量)；無 group_field 時分組為空字串。"""
    count_field = entry.get("count_field")
    group_field = entry.get("group_field")
    year_field, week_field = entry.get("year_field"), entry.get("week_field")
    date_field = entry.get("date_field")
    mode = entry.get("mode", "line_list")

    for row in rows:
        group = str(row.get(group_field, "")).strip() if group_field else ""

        # 年+週欄位格式（優先於 mode 判斷）
        if year_field and week_field:
            try:
                year = int(str(row.get(year_field, "")).strip())
                week_no = int(str(row.get(week_field, "")).strip())
            except ValueError:
                continue
            n = _row_count(row, count_field)
            if n is None:
                continue
            yield f"{year}-W{week_no:02d}", group, n
            continue

        raw = str(row.get(date_field, "")).strip() if date_field else ""
        if not raw:
            continue
        if mode == "line_list":
            d = _parse_date(raw)
            if d is None:
                continue
            yield _iso_week(d), group, 1
        else:  # weekly_count
            week = _parse_week(raw) or (_iso_week(_parse_date(raw)) if _parse_date(raw) else None)
            if week is None:
                continue
            n = _row_count(row, count_field)
            if n is None:
                continue
            yield week, group, n


def weekly_counts(rows: list[dict], entry: dict) -> dict[str, int]:
    """依資料集設定彙整出 {ISO週: 數量}（不分組）。"""
    counts: dict[str, int] = {}
    for week, _group, n in _iter_week_rows(rows, entry):
        counts[week] = counts.get(week, 0) + n
    return counts


def weekly_group_counts(rows: list[dict], entry: dict) -> dict[str, dict[str, int]]:
    """依資料集設定彙整出 {ISO週: {分組: 數量}}。"""
    counts: dict[str, dict[str, int]] = {}
    for week, group, n in _iter_week_rows(rows, entry):
        week_counts = counts.setdefault(week, {})
        week_counts[group] = week_counts.get(group, 0) + n
    return counts


def _report_weeks(report_day: date) -> tuple[str, str]:
    """回傳 (上一完整週, 再前一週) 的 ISO 週標籤。"""
    this_week_start = report_day - timedelta(days=report_day.weekday())
    last_week = _iso_week(this_week_start - timedelta(days=7))
    prev_week = _iso_week(this_week_start - timedelta(days=14))
    return last_week, prev_week


def _trend_text(last: int, prev: int | None) -> str:
    if not prev:
        return ""
    diff = last - prev
    pct = diff / prev * 100
    arrow = "↑" if diff > 0 else ("↓" if diff < 0 else "→")
    return f"，較前一週 {arrow} {diff:+d}（{pct:+.0f}%）"


def build_stats_lines(config: dict, report_day: date | None = None) -> list[str]:
    """產生週報「本週疫情數據」段落：各疾病上週病例數與前一週比較。"""
    report_day = report_day or date.today()
    last_week, prev_week = _report_weeks(report_day)

    lines: list[str] = []
    for entry in config.get("stats_datasets", []):
        disease = entry.get("disease", "?")
        try:
            counts = weekly_counts(fetch_rows(entry["url"]), entry)
        except Exception as exc:  # 單一資料集失敗不影響整份報告
            lines.append(f"- **{disease}**：資料抓取失敗（{type(exc).__name__}），請檢查資料集網址")
            continue

        last = counts.get(last_week)
        if last is None:
            lines.append(f"- **{disease}**：{last_week} 尚無資料")
            continue
        lines.append(
            f"- **{disease}**：{last_week} 共 {last:,} 例/人次"
            f"{_trend_text(last, counts.get(prev_week))}"
        )
    return lines


MAX_GROUPS_SHOWN = 8


def build_lab_lines(config: dict, report_day: date | None = None) -> list[str]:
    """產生週報「實驗室監測」段落：各監測項目上週的分組明細與總數趨勢。"""
    report_day = report_day or date.today()
    last_week, prev_week = _report_weeks(report_day)

    lines: list[str] = []
    for entry in config.get("lab_datasets", []):
        name = entry.get("name", "?")
        if not entry.get("url"):
            lines.append(f"- **{name}**：尚未設定資料集網址")
            continue
        try:
            grouped = weekly_group_counts(fetch_rows(entry["url"]), entry)
        except Exception as exc:
            lines.append(f"- **{name}**：資料抓取失敗（{type(exc).__name__}），請檢查資料集網址")
            continue

        week_data = grouped.get(last_week)
        if not week_data:
            lines.append(f"- **{name}**：{last_week} 尚無資料")
            continue

        total = sum(week_data.values())
        prev_total = sum(grouped.get(prev_week, {}).values()) or None
        lines.append(f"- **{name}**：{last_week} 共 {total:,}{_trend_text(total, prev_total)}")

        groups = sorted(week_data.items(), key=lambda kv: kv[1], reverse=True)
        shown = [f"{g or '（未分類）'} {n:,}" for g, n in groups[:MAX_GROUPS_SHOWN] if n]
        if shown and len(week_data) > 1:
            more = f" ⋯（另 {len(groups) - MAX_GROUPS_SHOWN} 類）" if len(groups) > MAX_GROUPS_SHOWN else ""
            lines.append(f"  - {('、'.join(shown))}{more}")
    return lines
