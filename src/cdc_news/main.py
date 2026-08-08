"""CLI 進入點。

用法：
    python -m cdc_news.main fetch    # 抓取最新新聞
    python -m cdc_news.main report   # 產生上一週的疫情週報
    python -m cdc_news.main run      # 抓取 + 週報
    python -m cdc_news.main verify   # 檢測所有來源網址與欄位設定（不呼叫 AI）
    python -m cdc_news.main preview  # 產生監測數據段落＋趨勢圖到 reports/preview（不呼叫 AI）
    python -m cdc_news.main probe URL [URL...]   # 探測網頁/資料端點結構（開發診斷用）
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import requests
import yaml
from bs4 import BeautifulSoup

from .fetcher import USER_AGENT, fetch_all, fetch_feed, fetch_html_list
from .report import build_report


def probe(urls: list[str]) -> None:
    """探測網址回應的結構：表格、iframe、內嵌圖表設定、資料端點線索。"""
    for url in urls:
        print("=" * 70)
        print("URL:", url)
        try:
            resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=60)
        except Exception as exc:
            print(f"✗ 連線失敗：{type(exc).__name__}: {exc}")
            continue
        ct = resp.headers.get("Content-Type", "")
        text = resp.content.decode("utf-8-sig", errors="replace")
        print(f"status={resp.status_code} content-type={ct} bytes={len(resp.content)}")

        if "json" in ct or url.endswith(".json"):
            print("JSON 開頭：", text[:600].replace("\n", " "))
            continue
        if "html" not in ct and not text.lstrip().startswith("<"):
            print("純文字開頭：", text[:600])
            continue

        soup = BeautifulSoup(text, "html.parser")
        if soup.title:
            print("頁面標題：", soup.title.get_text(strip=True))
        iframes = [i.get("src") for i in soup.find_all("iframe")]
        if iframes:
            print("iframes：", iframes[:20])
        tables = soup.find_all("table")
        print(f"表格數：{len(tables)}")
        for t in tables[:5]:
            trs = t.find_all("tr")
            if trs:
                headers = [c.get_text(strip=True) for c in trs[0].find_all(["th", "td"])]
                print(f"  表頭：{headers[:12]}（共 {len(trs) - 1} 列）")

        script_text = "\n".join(s.get_text() for s in soup.find_all("script"))
        print("script 關鍵字：",
              {p: script_text.count(p) for p in ("Highcharts", "series", "categories", "getJSON", "ajax")})
        series_names = re.findall(r"['\"]?name['\"]?\s*:\s*['\"]([^'\"]{1,50})['\"]", script_text)
        if series_names:
            print("series 名稱：", series_names[:50])
        # 印出前幾段含 series 的 script 片段，供分析內嵌資料結構
        shown = 0
        pos = 0
        while shown < 4:
            idx = script_text.find("series", pos)
            if idx == -1:
                break
            start = max(0, idx - 200)
            excerpt = script_text[start:idx + 1300].replace("\n", " ")
            excerpt = re.sub(r"\s{2,}", " ", excerpt)
            print(f"--- series 片段 {shown + 1} ---")
            print(excerpt[:1400])
            pos = idx + 1500
            shown += 1
        script_urls = sorted(set(re.findall(
            r"['\"]((?:https?://[^'\"]+|/[A-Za-z0-9_/.\-]+)(?:\?[^'\"]{0,120})?)['\"]",
            script_text)))
        interesting = [u for u in script_urls
                       if any(k in u.lower() for k in ("misc", "json", "csv", "chart", "data", "lars"))]
        if interesting:
            print("script 內的資料網址線索：", interesting[:40])
        misc_links = sorted({a["href"] for a in soup.find_all("a", href=True)
                             if "misc" in a["href"].lower()})
        if misc_links:
            print("misc 連結：", misc_links[:20])

        # push 陣列名稱與 Vlab 圖表的嵌入方式
        push_names = sorted(set(re.findall(r"(\w+)\.push\(", script_text)))
        if push_names:
            print("push 陣列名稱：", push_names)
        vidx = script_text.find("Vlab")
        if vidx != -1:
            start = max(0, vidx - 400)
            print("--- Vlab 前後文 ---")
            print(re.sub(r"\s{2,}", " ", script_text[start:vidx + 900].replace("\n", " "))[:1300])

        # NIDSS 內嵌圖表清單與摘要表
        from .nidss import parse_charts, parse_current_week, parse_summary_table
        charts, errors = parse_charts(text)
        if charts:
            print(f"內嵌圖表數：{len(charts)}")
            for c in charts:
                print(f"  ▸ 標題「{c.title}」單位「{c.suffix}」"
                      f"週範圍 {c.weeks[0]}~{c.weeks[-1]}；series：{list(c.series)}")
        for err in errors:
            print("  ⚠", err)
            m = re.search(r"char (\d+)", err)
            if m:
                pos_err = int(m.group(1))
                ctx = text[max(0, pos_err - 200):pos_err + 200].replace("\n", "⏎")
                print(f"    錯誤位置前後文：…{ctx}…")
        current = parse_current_week(text)
        if current:
            print(f"目前年週：{current[0]}年第{current[1]}週")
        summary = parse_summary_table(text)
        if summary:
            print("摘要表：", summary)


def verify(config: dict) -> None:
    """逐一檢測新聞來源與統計資料集，印出診斷資訊。"""
    print("=== 新聞來源 ===")
    for feed in config.get("feeds", []):
        try:
            if feed.get("type") == "html_list":
                items = fetch_html_list(feed["name"], feed["url"])
            else:
                items = fetch_feed(feed["name"], feed["url"])
            status = f"OK，取得 {len(items)} 則"
            if items:
                status += f"；來源名稱「{items[0].source}」；第一則：{items[0].title[:40]}"
            elif feed.get("type") != "html_list":
                status = "⚠ feed 可解析但 0 則項目，請確認網址"
        except Exception as exc:
            status = f"✗ 失敗：{type(exc).__name__}: {exc}"
        print(f"[{feed['name']}] {status}")

    print()
    print("=== NIDSS 監測數據（週報實際輸出預覽）===")
    from .monitor import build_monitor_sections
    for line in build_monitor_sections(config):
        print(line)


def preview(config: dict) -> None:
    """產生「監測數據」段落與趨勢圖到 reports/preview*，供調整格式用。"""
    from .monitor import build_monitor_sections

    reports_dir = Path(config.get("reports_dir", "reports"))
    charts_dir = reports_dir / "assets" / "preview"
    lines = ["# 監測數據段落預覽", ""]
    lines += build_monitor_sections(config, charts_dir=charts_dir,
                                    assets_prefix="assets/preview")
    out = reports_dir / "preview.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"預覽已產生：{out}（圖檔在 {charts_dir}/）")


def load_config(path: str = "config.yaml") -> dict:
    config_path = Path(path)
    if not config_path.exists():
        sys.exit(f"找不到設定檔 {path}，請先 cp config.example.yaml config.yaml 並填入 RSS 網址。")
    return yaml.safe_load(config_path.read_text(encoding="utf-8"))


def main() -> None:
    parser = argparse.ArgumentParser(description="CDC 疫情新聞回報與整理摘要")
    parser.add_argument("command",
                        choices=["fetch", "report", "run", "verify", "preview", "probe"])
    parser.add_argument("urls", nargs="*", help="probe 指令的目標網址")
    parser.add_argument("--config", default="config.yaml", help="設定檔路徑")
    args = parser.parse_args()

    if args.command == "probe":
        probe(args.urls)
        return

    config = load_config(args.config)

    if args.command == "verify":
        verify(config)
        return

    if args.command == "preview":
        preview(config)
        return

    if args.command in ("fetch", "run"):
        items = fetch_all(config)
        print(f"新抓取 {len(items)} 則新聞。")

    if args.command in ("report", "run"):
        build_report(config)


if __name__ == "__main__":
    main()
