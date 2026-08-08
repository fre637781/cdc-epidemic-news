"""抓取疾管署疫情新聞。

從設定檔指定的來源抓取新聞項目並補抓內文，
以 JSON 格式存入 data/YYYY-MM-DD/ 目錄。

來源類型（feed 設定的 type 欄位）：
- rss（預設）   標準 RSS/Atom feed；來源名稱自動採用 feed 自帶標題
- html_list     網頁列表頁（如「國際旅遊疫情速訊」訂閱頁），
                抓取頁面上連到 /Detail/ 的項目連結
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from datetime import date, datetime
from pathlib import Path
from urllib.parse import urljoin

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
    """抓取單一 RSS feed，回傳新聞項目清單。

    來源名稱優先採用 feed 自帶的標題（避免設定檔標錯），
    feed 沒有標題時才用設定檔的 name。
    """
    parsed = feedparser.parse(url, agent=USER_AGENT)
    feed_title = (parsed.feed.get("title") or "").strip()
    name = feed_title or name
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


def fetch_html_list(name: str, url: str,
                    link_contains: str = "/Detail/") -> list[NewsItem]:
    """抓取網頁列表頁（非 RSS 來源），擷取項目連結。

    以「連結網址含 link_contains」作為項目判斷條件——疾管署的公告
    與新聞詳細頁普遍是 /Detail/；旅遊疫情速訊等電子報列表則是
    Subscription/EpaperPreview。若連結位於表格列中且同列有日期欄
    （YYYY/MM/DD），一併取為發布日期。
    """
    try:
        resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
    except requests.RequestException:
        return []
    soup = BeautifulSoup(resp.text, "html.parser")
    items: list[NewsItem] = []
    seen_links: set[str] = set()
    for a in soup.find_all("a", href=True):
        href = urljoin(url, a["href"])
        title = a.get_text(" ", strip=True)
        if link_contains not in href or not title or href in seen_links:
            continue
        seen_links.add(href)
        published = ""
        row = a.find_parent("tr")
        if row:
            m = re.search(r"\b(\d{4}/\d{1,2}/\d{1,2})\b", row.get_text(" ", strip=True))
            if m:
                published = m.group(1)
        items.append(
            NewsItem(
                id=NewsItem.make_id(href),
                source=name,
                title=title,
                link=href,
                published=published,
                body="",
            )
        )
    return items[:30]


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
        if feed.get("type") == "html_list":
            fetched = fetch_html_list(feed["name"], feed["url"],
                                      feed.get("link_contains", "/Detail/"))
        else:
            fetched = fetch_feed(feed["name"], feed["url"])
        for item in fetched:
            if item.id in seen or len(new_items) >= max_items:
                continue
            item.body = fetch_article_body(item.link)
            new_items.append(item)

    save_items(new_items, data_dir)
    return new_items
