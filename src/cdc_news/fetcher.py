"""抓取疾管署疫情新聞。

從設定檔指定的 RSS feed 抓取新聞項目，並補抓內文，
以 JSON 格式存入 data/YYYY-MM-DD/ 目錄。
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from datetime import date, datetime
from pathlib import Path

import feedparser
import requests
from bs4 import BeautifulSoup

USER_AGENT = "cdc-epidemic-news/0.1 (+https://github.com)"
REQUEST_TIMEOUT = 30


@dataclass
class NewsItem:
    id: str            # 依連結產生的穩定 ID，用於去重
    source: str        # feed 名稱（新聞稿、致醫界通函⋯）
    title: str
    link: str
    published: str     # ISO 8601
    body: str          # 內文純文字（抓不到時為空字串）

    @staticmethod
    def make_id(link: str) -> str:
        return hashlib.sha256(link.encode("utf-8")).hexdigest()[:16]


def fetch_feed(name: str, url: str) -> list[NewsItem]:
    """抓取單一 RSS feed，回傳新聞項目清單。"""
    parsed = feedparser.parse(url, agent=USER_AGENT)
    items: list[NewsItem] = []
    for entry in parsed.entries:
        link = entry.get("link", "")
        if not link:
            continue
        published = ""
        if entry.get("published_parsed"):
            published = datetime(*entry.published_parsed[:6]).isoformat()
        items.append(
            NewsItem(
                id=NewsItem.make_id(link),
                source=name,
                title=entry.get("title", "").strip(),
                link=link,
                published=published,
                body="",
            )
        )
    return items


def fetch_article_body(url: str) -> str:
    """抓取新聞內文純文字；失敗時回傳空字串（摘要仍可用標題進行）。"""
    try:
        resp = requests.get(
            url, headers={"User-Agent": USER_AGENT}, timeout=REQUEST_TIMEOUT
        )
        resp.raise_for_status()
    except requests.RequestException:
        return ""
    soup = BeautifulSoup(resp.text, "html.parser")
    # 疾管署新聞頁的主要內容區塊；若版型變動則退回整頁文字
    main = soup.select_one(".news-v3-in, .content, main") or soup.body
    if main is None:
        return ""
    text = main.get_text("\n", strip=True)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text[:20000]  # 避免超長內文吃掉過多 token


def load_seen_ids(data_dir: Path) -> set[str]:
    """讀取 data/ 下所有已抓過的新聞 ID，用於去重。"""
    seen: set[str] = set()
    for path in data_dir.glob("*/*.json"):
        seen.add(path.stem)
    return seen


def save_items(items: list[NewsItem], data_dir: Path, day: date | None = None) -> list[Path]:
    """將新聞項目存成 data/YYYY-MM-DD/<id>.json。回傳寫入的檔案路徑。"""
    day = day or date.today()
    out_dir = data_dir / day.isoformat()
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for item in items:
        path = out_dir / f"{item.id}.json"
        path.write_text(
            json.dumps(asdict(item), ensure_ascii=False, indent=2), encoding="utf-8"
        )
        written.append(path)
    return written


def fetch_all(config: dict) -> list[NewsItem]:
    """依設定抓取所有 feed，去重後補抓內文並存檔。"""
    data_dir = Path(config.get("data_dir", "data"))
    data_dir.mkdir(parents=True, exist_ok=True)
    seen = load_seen_ids(data_dir)
    max_items = int(config.get("max_items_per_run", 20))

    new_items: list[NewsItem] = []
    for feed in config.get("feeds", []):
        for item in fetch_feed(feed["name"], feed["url"]):
            if item.id in seen or len(new_items) >= max_items:
                continue
            item.body = fetch_article_body(item.link)
            new_items.append(item)

    save_items(new_items, data_dir)
    return new_items
