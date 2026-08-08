"""產生週報「監測數據」段落：NIDSS 圖表數據，依類別分節。

設定來自 config 的 nidss_monitor：
    nidss_monitor:
      - section: 類別名稱
        items:
          - name: 顯示名稱
            url: NIDSS 頁面網址
            title_contains: 圖表標題關鍵字（空白分隔、須全部命中；省略時取第一張）
            series_keyword: 只顯示名稱含此關鍵字的 series（如「確定」）
            summary_url: 疾病頁網址（附上累計統計摘要）

統計的目標週為「上一完整週」，以 NIDSS 頁面自帶的「本週為N週」文字
為準（NIDSS 週編號與 ISO 週不同）。若某圖表資料落後（如變異株定序），
自動退到該圖表最近有資料的一週並標示。
"""

from __future__ import annotations

from pathlib import Path

from .nidss import Chart, fetch_page, find_chart, week_label

SUMMARY_PATTERNS = ("上週累計", "今年累計數", "去年總數", "死亡")
MAX_GROUPS = 10


def _is_pct(name: str) -> bool:
    return "%" in name


def _is_threshold(name: str) -> bool:
    return any(t in name for t in ("閾值", "預警"))


def _fmt(value) -> str:
    if isinstance(value, float) and not value.is_integer():
        return f"{value:,.1f}"
    return f"{int(value):,}"


def _pretty(label: str) -> str:
    return f"{label[:4]}年第{int(label[4:])}週"


def _anchor_week(config: dict) -> tuple[int, int] | None:
    """從設定內任一頁面解析目前的 NIDSS 年週（疾病頁與 LARS 頁都有）。"""
    for section in config.get("nidss_monitor", []):
        for item in section.get("items", []):
            for key in ("summary_url", "url"):
                url = item.get(key)
                if not url:
                    continue
                try:
                    page = fetch_page(url)
                except Exception:
                    continue
                if page.current_week:
                    return page.current_week
    return None


def _last_with_values(chart: Chart, target: str):
    """找 target 當週或之前最近有資料的一週，回傳 (週標籤, 當週值, 前一週值)。"""
    for i in range(len(chart.weeks) - 1, -1, -1):
        label = chart.weeks[i]
        if label > target:
            continue
        vals = chart.week_values(label)
        if vals:
            prev = chart.week_values(chart.weeks[i - 1]) if i > 0 else None
            return label, vals, prev
    return None, None, None


def _filter(vals: dict | None, keyword: str | None) -> dict | None:
    if not vals or not keyword:
        return vals
    filtered = {k: v for k, v in vals.items() if keyword in k}
    return filtered or vals


def _summary_line(summary_url: str) -> str | None:
    try:
        page = fetch_page(summary_url)
    except Exception:
        return None
    picks = []
    for key, value in page.summary.items():
        if not any(p in key for p in SUMMARY_PATTERNS):
            continue
        display = key.split("(")[-1].rstrip(")").strip() if "(" in key else key
        picks.append(f"{display} {value}")
    return f"  - 統計摘要：{'、'.join(picks)}" if picks else None


def _render_chart(item: dict, chart: Chart, chart_ctx: dict) -> str | None:
    """畫趨勢圖，回傳 Markdown 圖片行；失敗或無可畫 series 時回傳 None。"""
    keyword = item.get("series_keyword")
    names = list(chart.series)
    if keyword:
        matched = [n for n in names if keyword in n]
        names = matched or names
    count_names = [n for n in names if not _is_pct(n) and not _is_threshold(n)]
    threshold_names = [n for n in chart.series if _is_threshold(n)]
    filename = chart_ctx["filename"]
    kind = item.get("chart_type", "line")
    unit = "各類占比" if kind == "share" else f"單位：{chart.suffix or '數'}"
    try:
        from .charts import render_trend
        ok = render_trend(
            chart, count_names, threshold_names,
            title=f"{item.get('name', '')}（{unit}）",
            out_path=chart_ctx["dir"] / filename,
            weeks_window=chart_ctx["weeks"],
            kind=kind,
        )
    except Exception:
        return None
    if not ok:
        return None
    return f"![{item.get('name', '')} 趨勢圖]({chart_ctx['prefix']}/{filename})"


def _merged_chart(item: dict) -> Chart:
    """依 item['sources'] 抓多個查詢（如本土/境外的表單查詢），合併為一張圖。

    每個 source 取一條符合 series_keyword 的 series，改以 source 的
    label 命名，組成同一張多 series 的 Chart。
    """
    keyword = item.get("series_keyword")
    merged: dict[str, list] = {}
    weeks: list[str] | None = None
    suffix = ""
    for src in item["sources"]:
        page = fetch_page(src["url"], form=src.get("form"))
        chart = find_chart(page.charts, item.get("title_contains"))
        if chart is None:
            raise ValueError(f"「{src.get('label', '?')}」查詢找不到對應圖表")
        names = [n for n in chart.series if keyword and keyword in n] or list(chart.series)
        if weeks is None:
            weeks, suffix = chart.weeks, chart.suffix
        merged[src.get("label", names[0])] = chart.series[names[0]]
    return Chart(title=item.get("name", ""), weeks=weeks or [],
                 suffix=suffix, series=merged)


def _render_years_item(item: dict, chart: Chart, anchor: tuple[int, int] | None,
                       chart_ctx: dict | None) -> list[str]:
    """年度同期比較圖（chart_type: years）：series 名稱是年度（2025、2026⋯）。

    文字行呈現「今年上一完整週的數值＋去年同期」；跨年比較由圖表呈現。
    """
    name = item.get("name", "?")
    years = sorted(n for n in chart.series if n.strip().isdigit())
    if not years:
        return [f"- **{name}**：⚠ 年度比較圖找不到年度序列（現有：{list(chart.series)[:6]}）"]
    this_year = years[-1]
    data = chart.series.get(this_year) or []

    # 目標為上一完整週；該週無資料時退到今年最後有資料的一週
    idx = None
    if anchor and anchor[1] > 1:
        wk = anchor[1] - 1
        for fmt in (f"{wk:02d}", str(wk)):
            if fmt in chart.weeks:
                idx = chart.weeks.index(fmt)
                break
    if idx is None or idx >= len(data) or data[idx] is None:
        idx = max((j for j, v in enumerate(data) if v is not None), default=None)
    if idx is None:
        return [f"- **{name}**：無可用資料"]

    lines: list[str] = []
    image_line = _render_chart(item, chart, chart_ctx) if chart_ctx else None
    if image_line:
        lines += [image_line, ""]

    week_no = int(chart.weeks[idx]) if chart.weeks[idx].isdigit() else idx + 1
    head = f"- **{name}**（{this_year}年第{week_no}週）：{_fmt(data[idx])}{chart.suffix}"
    for prev in reversed(years[:-1]):  # 最近一個有同週資料的往年
        pdata = chart.series.get(prev) or []
        if idx < len(pdata) and pdata[idx] is not None:
            head += f"，{prev}年同期 {_fmt(pdata[idx])}{chart.suffix}"
            break
    lines.append(head)

    if item.get("summary_url"):
        summary = _summary_line(item["summary_url"])
        if summary:
            lines.append(summary)
    if image_line:
        lines.append("")
    return lines


def _render_item(item: dict, anchor: tuple[int, int] | None,
                 chart_ctx: dict | None = None) -> list[str]:
    name = item.get("name", "?")
    try:
        if item.get("sources"):
            chart = _merged_chart(item)
        else:
            page = fetch_page(item["url"])
            chart = find_chart(page.charts, item.get("title_contains"))
            if chart is None:
                titles = "、".join((c.title or "（無標題）") for c in page.charts[:8])
                return [f"- **{name}**：⚠ 找不到對應圖表；頁面現有圖表：{titles}"]
    except Exception as exc:
        return [f"- **{name}**：頁面抓取失敗（{type(exc).__name__}）"]

    if item.get("chart_type") == "years":
        return _render_years_item(item, chart, anchor, chart_ctx)

    if anchor:
        year, week = anchor
        # 上一完整週；跨年時以「前一年最大週」為上限
        target = week_label(year, week - 1) if week > 1 else f"{year - 1}99"
    else:
        target = chart.weeks[-1]

    label, vals, prev_vals = _last_with_values(chart, target)
    if label is None:
        return [f"- **{name}**：無可用資料"]
    keyword = item.get("series_keyword")
    vals = _filter(vals, keyword)
    prev_vals = _filter(prev_vals, keyword) or {}

    # 陽性率（Positive (%)）與閾值/預警線不能混入加總，各自分離標註
    def split(values: dict) -> tuple[dict, dict, dict]:
        pct = {k: v for k, v in values.items() if _is_pct(k)}
        thr = {k: v for k, v in values.items()
               if k not in pct and _is_threshold(k)}
        counts = {k: v for k, v in values.items() if k not in pct and k not in thr}
        return counts, pct, thr

    counts, pct, thresholds = split(vals)
    prev_counts, _, _ = split(prev_vals)

    lines: list[str] = []
    image_line = _render_chart(item, chart, chart_ctx) if chart_ctx else None
    if image_line:
        lines += [image_line, ""]

    suffix = chart.suffix
    is_rate = suffix in ("%", "‰")
    groups = sorted(counts.items(), key=lambda kv: kv[1], reverse=True)

    if is_rate:
        body = "、".join(f"{k} {_fmt(v)}{suffix}" for k, v in groups[:MAX_GROUPS])
        lines.append(f"- **{name}**（{_pretty(label)}）：{body}")
    else:
        total = sum(counts.values())
        head = f"- **{name}**（{_pretty(label)}）：共 {_fmt(total)}{suffix}"
        if item.get("sources") and len(groups) > 1:  # 本土/境外等分項直接標在行內
            head += "（" + "、".join(f"{k} {_fmt(v)}" for k, v in groups) + "）"
        prev_total = sum(prev_counts.values())
        if prev_total:
            diff = total - prev_total
            arrow = "↑" if diff > 0 else ("↓" if diff < 0 else "→")
            head += f"，較前一週 {arrow} {diff:+,.0f}（{diff / prev_total * 100:+.0f}%）"
        for k, v in pct.items():
            head += f"；陽性率 {_fmt(v)}%"
        for k, v in thresholds.items():
            head += f"（{k} {_fmt(v)}）"
        lines.append(head)
        # 有趨勢圖時分項組成由圖表呈現（sources 分項已標在行內），不再列明細
        if len(groups) > 1 and not image_line and not item.get("sources"):
            shown = "、".join(f"{k} {_fmt(v)}" for k, v in groups[:MAX_GROUPS])
            more = f"⋯（另 {len(groups) - MAX_GROUPS} 類）" if len(groups) > MAX_GROUPS else ""
            lines.append(f"  - {shown}{more}")

    if item.get("summary_url"):
        summary = _summary_line(item["summary_url"])
        if summary:
            lines.append(summary)
    if image_line:
        lines.append("")
    return lines


def build_monitor_sections(config: dict, charts_dir: Path | None = None,
                           assets_prefix: str = "") -> list[str]:
    """產生「監測數據」整段 Markdown 行（各類別小節）。

    charts_dir 給定時，每個項目另產生趨勢圖 PNG 到該目錄，
    Markdown 以 assets_prefix 為相對路徑引用圖檔。
    """
    anchor = _anchor_week(config)
    chart_weeks = int(config.get("chart_weeks", 26))
    lines: list[str] = []
    for si, section in enumerate(config.get("nidss_monitor", []), 1):
        lines += [f"### {section.get('section', '')}", ""]
        for ii, item in enumerate(section.get("items", []), 1):
            chart_ctx = None
            if charts_dir is not None:
                chart_ctx = {"dir": charts_dir, "prefix": assets_prefix,
                             "weeks": chart_weeks, "filename": f"{si}-{ii}.png"}
            lines += _render_item(item, anchor, chart_ctx)
        lines.append("")
    if lines and anchor:
        lines.append(
            f"> 數據取自 NIDSS（目前為 {anchor[0]}年第{anchor[1]}週，統計上一完整週；"
            "定序類資料有時間落後，以各項標示的週次為準）。最新資料可能因後續回報而變動。"
        )
        lines.append("")
    return lines
