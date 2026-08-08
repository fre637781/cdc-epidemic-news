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

from .nidss import Chart, fetch_page, find_chart, week_label

SUMMARY_PATTERNS = ("上週累計", "今年累計數", "去年總數", "死亡")
MAX_GROUPS = 10


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


def _render_item(item: dict, anchor: tuple[int, int] | None) -> list[str]:
    name = item.get("name", "?")
    try:
        page = fetch_page(item["url"])
    except Exception as exc:
        return [f"- **{name}**：頁面抓取失敗（{type(exc).__name__}）"]

    chart = find_chart(page.charts, item.get("title_contains"))
    if chart is None:
        titles = "、".join((c.title or "（無標題）") for c in page.charts[:8])
        return [f"- **{name}**：⚠ 找不到對應圖表；頁面現有圖表：{titles}"]

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
        pct = {k: v for k, v in values.items() if "%" in k}
        thr = {k: v for k, v in values.items()
               if k not in pct and any(t in k for t in ("閾值", "預警"))}
        counts = {k: v for k, v in values.items() if k not in pct and k not in thr}
        return counts, pct, thr

    counts, pct, thresholds = split(vals)
    prev_counts, _, _ = split(prev_vals)

    suffix = chart.suffix
    is_rate = suffix in ("%", "‰")
    lines: list[str] = []
    groups = sorted(counts.items(), key=lambda kv: kv[1], reverse=True)

    if is_rate:
        body = "、".join(f"{k} {_fmt(v)}{suffix}" for k, v in groups[:MAX_GROUPS])
        lines.append(f"- **{name}**（{_pretty(label)}）：{body}")
    else:
        total = sum(counts.values())
        head = f"- **{name}**（{_pretty(label)}）：共 {_fmt(total)}{suffix}"
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
        if len(groups) > 1:
            shown = "、".join(f"{k} {_fmt(v)}" for k, v in groups[:MAX_GROUPS])
            more = f"⋯（另 {len(groups) - MAX_GROUPS} 類）" if len(groups) > MAX_GROUPS else ""
            lines.append(f"  - {shown}{more}")

    if item.get("summary_url"):
        summary = _summary_line(item["summary_url"])
        if summary:
            lines.append(summary)
    return lines


def build_monitor_sections(config: dict) -> list[str]:
    """產生「監測數據」整段 Markdown 行（各類別小節）。"""
    anchor = _anchor_week(config)
    lines: list[str] = []
    for section in config.get("nidss_monitor", []):
        lines += [f"### {section.get('section', '')}", ""]
        for item in section.get("items", []):
            lines += _render_item(item, anchor)
        lines.append("")
    if lines and anchor:
        lines.append(
            f"> 數據取自 NIDSS（目前為 {anchor[0]}年第{anchor[1]}週，統計上一完整週；"
            "定序類資料有時間落後，以各項標示的週次為準）。最新資料可能因後續回報而變動。"
        )
        lines.append("")
    return lines
