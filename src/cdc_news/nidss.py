"""解析 NIDSS（傳染病統計資料查詢系統）頁面內嵌的圖表資料。

NIDSS 的儀表板（Home/Index?op=N）、疾病頁（nndss/Disease?id=X）與
LARS 頁（misc/lars?id=X）都把圖表資料以 `hcJson.push({...})` 的 JSON
內嵌在頁面 script 中，結構為：

    {"Title": "圖表標題", "tooltip_valueSuffix": "人",
     "xAxis_categories": ["202630", "202631", ...],   # 年週
     "series": [{"name": "確定病例數", "data": [10, 12, ...]}, ...]}

本模組抓取頁面、解析所有圖表，並提供依標題關鍵字取出特定圖表
每週數值的工具。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

import requests

USER_AGENT = "cdc-epidemic-news/0.1 (+https://github.com)"
REQUEST_TIMEOUT = 60

_PUSH_MARKER = "hcJson.push("


@dataclass
class Chart:
    title: str
    weeks: list[str]                      # 年週標籤，如 "202631"
    suffix: str                           # 數值單位（人/人次/%/空字串）
    series: dict[str, list] = field(default_factory=dict)  # 名稱 -> 數列

    def week_values(self, week_label: str) -> dict[str, int | float] | None:
        """回傳指定年週各 series 的數值；該週不在資料範圍時回傳 None。"""
        try:
            idx = self.weeks.index(week_label)
        except ValueError:
            return None
        out: dict[str, int | float] = {}
        for name, data in self.series.items():
            if idx < len(data) and data[idx] is not None:
                out[name] = data[idx]
        return out or None


_page_cache: dict[str, list[Chart]] = {}


def fetch_charts(url: str) -> list[Chart]:
    """抓取頁面並解析所有內嵌圖表（同一執行內快取同網址）。"""
    if url in _page_cache:
        return _page_cache[url]
    resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    charts = parse_charts(resp.content.decode("utf-8-sig", errors="replace"))
    _page_cache[url] = charts
    return charts


def parse_charts(text: str) -> list[Chart]:
    """從頁面原始碼解析所有 hcJson.push({...}) 圖表物件。"""
    decoder = json.JSONDecoder()
    charts: list[Chart] = []
    pos = 0
    while True:
        idx = text.find(_PUSH_MARKER, pos)
        if idx == -1:
            break
        start = idx + len(_PUSH_MARKER)
        try:
            obj, end = decoder.raw_decode(text, start)
        except ValueError:
            pos = start
            continue
        pos = end
        if not isinstance(obj, dict):
            continue
        weeks = [str(c) for c in obj.get("xAxis_categories") or []]
        series = {
            str(s.get("name", "")): s.get("data") or []
            for s in obj.get("series") or []
            if isinstance(s, dict)
        }
        if not weeks or not series:
            continue  # 地圖等非週趨勢圖
        charts.append(Chart(
            title=str(obj.get("Title", "")).strip(),
            weeks=weeks,
            suffix=str(obj.get("tooltip_valueSuffix", "")),
            series=series,
        ))
    return charts


def find_chart(charts: list[Chart], title_contains: str | None) -> Chart | None:
    """依標題關鍵字（可含多個以空白分隔、須全部命中）找圖表；未給關鍵字時取第一張。"""
    if not title_contains:
        return charts[0] if charts else None
    keywords = title_contains.split()
    for chart in charts:
        if all(k in chart.title for k in keywords):
            return chart
    return None


def week_label(year: int, week: int) -> str:
    """NIDSS 的年週標籤格式：202631。"""
    return f"{year}{week:02d}"


def parse_week_label(label: str) -> tuple[int, int] | None:
    m = re.match(r"^(\d{4})(\d{2})$", label)
    return (int(m.group(1)), int(m.group(2))) if m else None
