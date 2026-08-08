"""CLI 進入點。

用法：
    python -m cdc_news.main fetch    # 抓取最新新聞
    python -m cdc_news.main report   # 產生上一週的疫情週報
    python -m cdc_news.main run      # 抓取 + 週報
    python -m cdc_news.main verify   # 檢測所有來源網址與欄位設定（不呼叫 AI）
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

from .fetcher import fetch_all, fetch_feed, fetch_html_list
from .report import build_report
from .stats import fetch_rows, weekly_counts


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
    print("=== 統計資料集 ===")
    for entry in config.get("stats_datasets", []):
        disease = entry.get("disease", "?")
        try:
            rows = fetch_rows(entry["url"])
            if not rows:
                print(f"[{disease}] ⚠ 下載成功但 0 列資料")
                continue
            keys = list(rows[0].keys())
            counts = weekly_counts(rows, entry)
            recent = sorted(counts)[-3:]
            print(f"[{disease}] 共 {len(rows)} 列；欄位：{keys}")
            if counts:
                print(f"    近三週：{ {w: counts[w] for w in recent} }")
            else:
                print("    ⚠ 欄位設定對不上資料（weekly_counts 為空），請比對上面的欄位名")
        except Exception as exc:
            print(f"[{disease}] ✗ 失敗：{type(exc).__name__}: {exc}")


def load_config(path: str = "config.yaml") -> dict:
    config_path = Path(path)
    if not config_path.exists():
        sys.exit(f"找不到設定檔 {path}，請先 cp config.example.yaml config.yaml 並填入 RSS 網址。")
    return yaml.safe_load(config_path.read_text(encoding="utf-8"))


def main() -> None:
    parser = argparse.ArgumentParser(description="CDC 疫情新聞回報與整理摘要")
    parser.add_argument("command", choices=["fetch", "report", "run", "verify"])
    parser.add_argument("--config", default="config.yaml", help="設定檔路徑")
    args = parser.parse_args()

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
