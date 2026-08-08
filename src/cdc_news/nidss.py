"""解析 NIDSS（傳染病統計資料查詢系統）頁面內嵌的圖表資料。

NIDSS 的儀表板（Home/Index?op=N）、疾病頁（nndss/Disease?id=X）與
LARS 頁（misc/lars?id=X）都把圖表資料以 `hcJson.push({...})` 的 JSON
內嵌在頁面 script 中，結構為：

    {"Title": "圖表標題", "tooltip_valueSuffix": "人",
     "xAxis_categories": ["202630", "202631", ...],   # NIDSS 年週標籤
     "series": [{"name": "確定病例數", "data": [10, 12, ...]}, ...]}

注意：NIDSS 的週編號與 ISO 週不同，「目前是第幾週」以頁面說明文字
（「本週為2026年31週」）為準，勿以日期自行換算。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

import requests
from bs4 import BeautifulSoup

USER_AGENT = "cdc-epidemic-news/0.1 (+https://github.com)"
REQUEST_TIMEOUT = 60

_PUSH_MARKER = "hcJson.push("


@dataclass
class Chart:
    title: str
    weeks: list[str]                      # NIDSS 年週標籤，如 "202631"
    suffix: str                           # 數值單位（人/人次/%/‰/空字串）
    series: dict[str, list] = field(default_factory=dict)  # 名稱 -> 數列

    def week_values(self, week_label: str) -> dict[str, float] | None:
        """回傳指定年週各 series 的數值；該週不在資料範圍時回傳 None。"""
        try:
            idx = self.weeks.index(week_label)
        except ValueError:
            return None
        out: dict[str, float] = {}
        for name, data in self.series.items():
            if idx < len(data) and data[idx] is not None:
                out[name] = data[idx]
        return out or None


@dataclass
class Page:
    url: str
    charts: list[Chart]
    parse_errors: list[str]
    current_week: tuple[int, int] | None   # (年, NIDSS週)，取自頁面說明文字
    summary: dict[str, str]                # 疾病頁摘要表（上週累計、今年累計⋯）


_page_cache: dict[str, Page] = {}


def fetch_page(url: str) -> Page:
    """抓取並解析 NIDSS 頁面（同一執行內快取同網址）。"""
    if url in _page_cache:
        return _page_cache[url]
    resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    text = resp.content.decode("utf-8-sig", errors="replace")
    charts, errors = parse_charts(text)
    page = Page(
        url=url,
        charts=charts,
        parse_errors=errors,
        current_week=parse_current_week(text),
        summary=parse_summary_table(text),
    )
    _page_cache[url] = page
    return page


def parse_charts(text: str) -> tuple[list[Chart], list[str]]:
    """解析所有 hcJson.push({...})；JSON 解析失敗時退回正則擷取。"""
    # NIDSS 的 Vlab 圖表資料陣列含 JS 的 undefined（非合法 JSON），換成 null
    text = re.sub(r"(?<=[\[,])\s*undefined\s*(?=[,\]])", "null", text)
    decoder = json.JSONDecoder()
    charts: list[Chart] = []
    errors: list[str] = []

    # 找出每個 push 區塊的邊界
    starts = [m.end() for m in re.finditer(re.escape(_PUSH_MARKER), text)]
    for i, start in enumerate(starts):
        end_bound = starts[i + 1] if i + 1 < len(starts) else len(text)
        chart = None
        try:
            obj, _ = decoder.raw_decode(text, start)
            if isinstance(obj, dict):
                chart = _chart_from_obj(obj)
        except ValueError as exc:
            segment = text[start:end_bound]
            chart = _chart_from_segment(segment)
            title = _re_str(segment, "Title") or "?"
            if chart is None:
                errors.append(f"「{title}」JSON 與備援解析皆失敗：{exc}")
            else:
                errors.append(f"「{title}」JSON 解析失敗（{exc}），已用備援解析")
        if chart is not None:
            charts.append(chart)
    return charts, errors


def _chart_from_obj(obj: dict) -> Chart | None:
    weeks = [str(c) for c in obj.get("xAxis_categories") or []]
    series = {
        str(s.get("name", "")): s.get("data") or []
        for s in obj.get("series") or []
        if isinstance(s, dict)
    }
    if not weeks or not series:
        return None  # 地圖等非週趨勢圖
    return Chart(
        title=str(obj.get("Title", "")).strip(),
        weeks=weeks,
        suffix=str(obj.get("tooltip_valueSuffix", "")),
        series=series,
    )


def _re_str(segment: str, key: str) -> str | None:
    m = re.search(r'"%s"\s*:\s*"((?:[^"\\]|\\.)*)"' % re.escape(key), segment)
    return json.loads(f'"{m.group(1)}"') if m else None


def _decode_at(segment: str, key: str):
    """在區塊中找到 "key": 後解析其 JSON 值。"""
    m = re.search(r'"%s"\s*:\s*' % re.escape(key), segment)
    if not m:
        return None
    try:
        value, _ = json.JSONDecoder().raw_decode(segment, m.end())
        return value
    except ValueError:
        return None


def _chart_from_segment(segment: str) -> Chart | None:
    """備援：逐欄位擷取（Title / 單位 / 年週 / 各 series 的 name+data）。"""
    weeks = _decode_at(segment, "xAxis_categories")
    if not weeks:
        return None
    series: dict[str, list] = {}
    data_list = _decode_at(segment, "series")
    if isinstance(data_list, list):
        series = {
            str(s.get("name", "")): s.get("data") or []
            for s in data_list if isinstance(s, dict)
        }
    else:
        # series 陣列整體解不開時，逐一擷取 name+data 配對
        for m in re.finditer(
                r'\{"name"\s*:\s*"((?:[^"\\]|\\.)*)"[^{\[]*?"data"\s*:\s*(\[[^\]]*\])',
                segment):
            try:
                series[json.loads(f'"{m.group(1)}"')] = json.loads(m.group(2))
            except ValueError:
                continue
    if not series:
        return None
    return Chart(
        title=_re_str(segment, "Title") or "",
        weeks=[str(w) for w in weeks],
        suffix=_re_str(segment, "tooltip_valueSuffix") or "",
        series=series,
    )


def parse_current_week(text: str) -> tuple[int, int] | None:
    """從頁面說明文字解析目前的 NIDSS 年週（「本週為2026年31週」）。

    LARS 頁的寫法沒有年份（「本週為31週」），改從查詢日期範圍結尾取年份。
    """
    m = re.search(r"本週為(\d{4})年(\d{1,2})週", text)
    if m:
        return int(m.group(1)), int(m.group(2))
    m = re.search(r"本週為(\d{1,2})週", text)
    if m:
        week = int(m.group(1))
        ym = re.search(r"日期範圍為[\d/]+[-至][\s]*(\d{4})/", text)
        if ym:
            return int(ym.group(1)), week
    return None


def parse_summary_table(text: str) -> dict[str, str]:
    """解析疾病頁的摘要表（上週累計、今年累計、去年總數、死亡數⋯）。"""
    soup = BeautifulSoup(text, "html.parser")
    for table in soup.find_all("table"):
        rows = [[c.get_text(strip=True) for c in tr.find_all(["th", "td"])]
                for tr in table.find_all("tr")]
        rows = [r for r in rows if len(r) == 2]
        if len(rows) < 3:
            continue
        if any("累計" in r[0] for r in rows):
            return {r[0]: r[1] for r in rows}
    return {}


def find_chart(charts: list[Chart], title_contains: str | None) -> Chart | None:
    """依標題關鍵字（空白分隔、須全部命中）找圖表；未給關鍵字時取第一張。"""
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
