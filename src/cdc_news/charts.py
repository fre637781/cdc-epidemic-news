"""將 NIDSS 週趨勢資料畫成 PNG 圖表，嵌入週報 Markdown。

三種畫法（kind）：
    line  折線趨勢圖（預設）
    bar   堆疊長條圖（各類檢出數的組成）
    share 100% 堆疊長條圖（各類占比，每週合計 100%）

設計依循固定的類別色盤（順序即身分，不循環產生新色）；
超過 8 個 series 時，其餘合併為「其他」。陽性率（%）與計數
單位不同，不畫在同一張圖（單一 y 軸原則），只保留在文字說明；
流行閾值/預警線以灰色虛線呈現。
"""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
from matplotlib import font_manager
from matplotlib import pyplot as plt

from .nidss import Chart

PALETTE = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100",
           "#e87ba4", "#008300", "#4a3aa7", "#e34948"]
SURFACE = "#fcfcfb"
INK = "#0b0b0b"
MUTED = "#898781"
GRID = "#e1e0d9"
BASELINE = "#c3c2b7"
THRESHOLD = "#898781"

MAX_SERIES = 8
DEFAULT_WEEKS = 26

_fonts_ready = False


def _setup_fonts() -> None:
    global _fonts_ready
    if _fonts_ready:
        return
    available = {f.name for f in font_manager.fontManager.ttflist}
    for name in ("Noto Sans CJK TC", "Noto Sans TC", "Noto Sans CJK JP",
                 "Noto Sans CJK SC", "Microsoft JhengHei"):
        if name in available:
            plt.rcParams["font.family"] = name
            break
    plt.rcParams["axes.unicode_minus"] = False
    _fonts_ready = True


def _window_values(chart: Chart, name: str, start: int, length: int) -> list:
    data = chart.series.get(name) or []
    return [data[start + j] if start + j < len(data) else None
            for j in range(length)]


def _tick_positions(n: int, max_ticks: int = 7) -> list[int]:
    if n <= max_ticks:
        return list(range(n))
    step = (n - 1) / (max_ticks - 1)
    return sorted({round(i * step) for i in range(max_ticks)})


def _tick_labels(weeks: list[str], positions: list[int]) -> list[str]:
    labels, last_year = [], None
    for p in positions:
        year, wk = weeks[p][:4], int(weeks[p][4:])
        labels.append(f"{year}年{wk}週" if year != last_year else f"{wk}週")
        last_year = year
    return labels


def render_trend(chart: Chart, count_names: list[str], threshold_names: list[str],
                 title: str, out_path: Path, weeks_window: int = DEFAULT_WEEKS,
                 kind: str = "line") -> bool:
    """畫一張趨勢圖；沒有可畫的計數 series 時回傳 False。"""
    count_names = [n for n in count_names if n in chart.series]
    if not count_names:
        return False
    _setup_fonts()

    # NIDSS 的週分類常涵蓋整年（未來週為空值），先修剪頭尾整段無資料的週
    n_weeks = len(chart.weeks)
    full = {n: [(chart.series[n][j] if j < len(chart.series[n]) else None)
                for j in range(n_weeks)] for n in count_names}
    idxs = [j for j in range(n_weeks) if any(v[j] is not None for v in full.values())]
    if not idxs:
        return False
    hi = idxs[-1]
    start = max(idxs[0], hi - weeks_window + 1)
    weeks = chart.weeks[start:hi + 1]
    x = list(range(len(weeks)))

    # 依窗格內總量排序決定畫線與配色順序；超出色盤的合併為「其他」
    def total(name: str) -> float:
        return sum(v for v in _window_values(chart, name, start, len(weeks))
                   if v is not None)

    ordered = sorted(count_names, key=total, reverse=True)
    folded: list[tuple[str, list]] = [
        (name, _window_values(chart, name, start, len(weeks)))
        for name in ordered[:MAX_SERIES if len(ordered) <= MAX_SERIES else MAX_SERIES - 1]
    ]
    if len(ordered) > MAX_SERIES:
        rest = ordered[MAX_SERIES - 1:]
        merged = []
        for j in range(len(weeks)):
            vals = [v for name in rest
                    if (v := _window_values(chart, name, start, len(weeks))[j]) is not None]
            merged.append(sum(vals) if vals else None)
        folded.append((f"其餘 {len(rest)} 類合併", merged))

    if kind == "share":
        # 每週各類占比（%）；合計為 0 的週不畫
        totals = [sum(v[j] for _, v in folded if v[j] is not None) or None
                  for j in range(len(weeks))]
        folded = [(name, [v[j] / totals[j] * 100
                          if v[j] is not None and totals[j] else None
                          for j in range(len(weeks))])
                  for name, v in folded]

    fig, ax = plt.subplots(figsize=(8, 3.2), dpi=150)
    fig.patch.set_facecolor(SURFACE)
    ax.set_facecolor(SURFACE)

    if kind in ("bar", "share"):
        bottom = [0.0] * len(weeks)
        for i, (name, values) in enumerate(folded):
            heights = [v if v is not None else 0.0 for v in values]
            ax.bar(x, heights, bottom=bottom, width=0.82,
                   color=PALETTE[i % len(PALETTE)], label=name, linewidth=0)
            bottom = [b + h for b, h in zip(bottom, heights)]
    else:
        for i, (name, values) in enumerate(folded):
            color = PALETTE[i % len(PALETTE)]
            # 稀疏資料（中間有缺週）折線會斷裂，加上資料點標記讓孤點可見
            present = [j for j, v in enumerate(values) if v is not None]
            sparse = bool(present) and (present[-1] - present[0] + 1) > len(present)
            ax.plot(x, values, lw=2, color=color, label=name, solid_capstyle="round",
                    marker="o" if sparse else None, markersize=3.5)
            if len(folded) <= 4 and present:  # 少量 series 時在線尾直接標值
                j = present[-1]
                ax.annotate(f"{values[j]:,.0f}", (x[j], values[j]),
                            xytext=(5, 0), textcoords="offset points",
                            fontsize=8, color=color, va="center")

    dashes = ["--", (0, (1, 1.5)), (0, (4, 1.5, 1, 1.5))]
    for i, name in enumerate(threshold_names):
        values = _window_values(chart, name, start, len(weeks))
        ax.plot(x, values, lw=1.5, ls=dashes[i % len(dashes)],
                color=THRESHOLD, label=name)

    ax.set_title(title, loc="left", fontsize=11, color=INK, pad=10)
    ax.set_ylim(bottom=0)
    if kind == "share":
        ax.set_ylim(0, 100)
        ax.set_yticks([0, 25, 50, 75, 100])
        ax.set_yticklabels(["0%", "25%", "50%", "75%", "100%"])
    ax.margins(x=0.01)

    positions = _tick_positions(len(weeks))
    ax.set_xticks(positions)
    ax.set_xticklabels(_tick_labels(weeks, positions), fontsize=8, color=MUTED)
    ax.tick_params(axis="y", labelsize=8, colors=MUTED, length=0)
    ax.tick_params(axis="x", colors=MUTED, length=3)
    ax.grid(axis="y", color=GRID, lw=0.8)
    ax.set_axisbelow(True)
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color(BASELINE)

    if len(folded) + len(threshold_names) >= 2:
        ax.legend(loc="upper left", bbox_to_anchor=(1.01, 1.0), frameon=False,
                  fontsize=8, labelcolor="#52514e", handlelength=1.4)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, bbox_inches="tight", facecolor=SURFACE)
    plt.close(fig)
    return True
