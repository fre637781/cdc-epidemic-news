"""CLI 進入點。

用法：
    python -m cdc_news.main fetch    # 抓取最新新聞
    python -m cdc_news.main report   # 產生上一週的疫情週報
    python -m cdc_news.main run      # 抓取 + 週報
    python -m cdc_news.main verify   # 檢測所有來源網址與欄位設定（不呼叫 AI）
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
from .stats import fetch_rows, weekly_counts


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

    def check_dataset(label: str, entry: dict) -> None:
        if not entry.get("url"):
            print(f"[{label}] ⚠ 尚未設定網址")
            return
        try:
            rows = fetch_rows(entry["url"])
            if not rows:
                print(f"[{label}] ⚠ 下載成功但 0 列資料")
                return
            keys = list(rows[0].keys())
            counts = weekly_counts(rows, entry)
            recent = sorted(counts)[-3:]
            print(f"[{label}] 共 {len(rows)} 列；欄位：{keys}")
            if counts:
                print(f"    近三週：{ {w: counts[w] for w in recent} }")
            else:
                print("    ⚠ 欄位設定對不上資料（weekly_counts 為空），請比對上面的欄位名")
        except Exception as exc:
            print(f"[{label}] ✗ 失敗：{type(exc).__name__}: {exc}")

    print()
    print("=== 統計資料集 ===")
    for entry in config.get("stats_datasets", []):
        check_dataset(entry.get("disease", "?"), entry)

    print()
    print("=== 實驗室監測資料集 ===")
    for entry in config.get("lab_datasets", []):
        check_dataset(entry.get("name", "?"), entry)


def load_config(path: str = "config.yaml") -> dict:
    config_path = Path(path)
    if not config_path.exists():
        sys.exit(f"找不到設定檔 {path}，請先 cp config.example.yaml config.yaml 並填入 RSS 網址。")
    return yaml.safe_load(config_path.read_text(encoding="utf-8"))


def main() -> None:
    parser = argparse.ArgumentParser(description="CDC 疫情新聞回報與整理摘要")
    parser.add_argument("command", choices=["fetch", "report", "run", "verify", "probe"])
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

    if args.command in ("fetch", "run"):
        items = fetch_all(config)
        print(f"新抓取 {len(items)} 則新聞。")

    if args.command in ("report", "run"):
        build_report(config)


if __name__ == "__main__":
    main()
