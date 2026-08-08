"""CLI 進入點。

用法：
    python -m cdc_news.main fetch    # 抓取最新新聞
    python -m cdc_news.main report   # 產生今日摘要報告
    python -m cdc_news.main run      # 抓取 + 報告
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

from .fetcher import fetch_all
from .report import build_report


def load_config(path: str = "config.yaml") -> dict:
    config_path = Path(path)
    if not config_path.exists():
        sys.exit(f"找不到設定檔 {path}，請先 cp config.example.yaml config.yaml 並填入 RSS 網址。")
    return yaml.safe_load(config_path.read_text(encoding="utf-8"))


def main() -> None:
    parser = argparse.ArgumentParser(description="CDC 疫情新聞回報與整理摘要")
    parser.add_argument("command", choices=["fetch", "report", "run"])
    parser.add_argument("--config", default="config.yaml", help="設定檔路徑")
    args = parser.parse_args()

    config = load_config(args.config)

    if args.command in ("fetch", "run"):
        items = fetch_all(config)
        print(f"新抓取 {len(items)} 則新聞。")

    if args.command in ("report", "run"):
        build_report(config)


if __name__ == "__main__":
    main()
