"""將週報 Markdown 轉成 HTML 郵件寄出，趨勢圖以 CID 內嵌顯示。

用法：
    python -m cdc_news.mailer reports/2026-W32.md

環境變數：
    MAIL_USERNAME  Gmail 地址（寄件帳號）
    MAIL_PASSWORD  Gmail 應用程式密碼
    MAIL_TO        收件人（省略時寄給 MAIL_USERNAME；多人以逗號分隔）
    MAIL_IMG_WIDTH 信中圖片顯示寬度 px（預設 600）
    GITHUB_REPOSITORY  owner/repo（附上 GitHub 完整版連結用，可省略）
"""

from __future__ import annotations

import os
import re
import smtplib
import sys
from email.message import EmailMessage
from email.utils import make_msgid
from pathlib import Path

import markdown

STYLE = (
    "font-family:system-ui,-apple-system,'Segoe UI','Noto Sans TC',sans-serif;"
    "max-width:860px;margin:0 auto;padding:16px;color:#222;line-height:1.7"
)

# 重點速覽卡片：依燈號決定左側色條
CARD_COLORS = {"🔴": "#d03b3b", "🟠": "#eb6834", "🟢": "#0ca30c", "⚪": "#898781"}
_CARD_LINE = re.compile(
    r"^- (🔴|🟠|🟢|⚪) \*\*(.+?)\*\*(?:：(.*))?$", re.M)


def _overview_cards(text: str) -> str:
    """把「重點速覽」段落的燈號條列改寫成 HTML 警示卡片。

    Markdown 轉 HTML 時原始 HTML 區塊會原樣通過，GitHub 上的 .md
    仍是條列格式不受影響（此轉換只作用於信件）。
    """
    m = re.search(r"(## 重點速覽\n+)(.*?)(?=\n## |\Z)", text, re.S)
    if not m:
        return text

    def card(cm: re.Match) -> str:
        emoji, headline, detail = cm.group(1), cm.group(2), cm.group(3)
        color = CARD_COLORS.get(emoji, CARD_COLORS["⚪"])
        detail_html = (f'<div style="color:#52514e;font-size:14px;margin-top:3px">'
                       f'{detail}</div>' if detail else "")
        return (f'<div style="border-left:4px solid {color};background:#f7f6f3;'
                f'padding:10px 14px;margin:10px 0;border-radius:0 8px 8px 0">'
                f'<div style="font-weight:700">{emoji} {headline}</div>'
                f'{detail_html}</div>')

    section, n = _CARD_LINE.subn(card, m.group(2))
    if not n:  # 舊格式（無燈號條列）就維持原樣
        return text
    return text[:m.start()] + m.group(1) + section + text[m.end():]


def build_message(report_path: Path) -> EmailMessage:
    text = report_path.read_text(encoding="utf-8")
    week = report_path.stem

    repo = os.environ.get("GITHUB_REPOSITORY")
    if repo:
        text = (f"> 網頁完整版：https://github.com/{repo}/blob/main/"
                f"reports/{report_path.name}\n\n") + text

    # 圖片改為 cid: 內嵌引用，記下 (檔案路徑, cid)
    images: list[tuple[Path, str]] = []

    def replace_image(m: re.Match) -> str:
        alt, src = m.group(1), m.group(2)
        path = report_path.parent / src
        if not path.exists():
            return m.group(0)
        cid = make_msgid()  # 形如 <...@host>
        images.append((path, cid))
        width = os.environ.get("MAIL_IMG_WIDTH", "600")
        return (f'<img src="cid:{cid[1:-1]}" alt="{alt}" width="{width}" '
                f'style="width:{width}px;max-width:100%;height:auto;'
                f'display:block;margin:8px 0">')

    text = re.sub(r"!\[([^\]]*)\]\(([^)\s]+)\)", replace_image, text)
    text = _overview_cards(text)
    body = markdown.markdown(text, extensions=["tables", "sane_lists"])
    html = f'<html><body style="{STYLE}">{body}</body></html>'

    user = os.environ["MAIL_USERNAME"]
    msg = EmailMessage()
    msg["Subject"] = f"疫情週報 {week}"
    msg["From"] = f"CDC 疫情週報 <{user}>"
    msg["To"] = os.environ.get("MAIL_TO") or user
    msg.set_content("此郵件為 HTML 格式，請以支援 HTML 的郵件軟體開啟。"
                    f"網頁版：https://github.com/{repo or ''}")
    msg.add_alternative(html, subtype="html")

    html_part = msg.get_payload()[-1]
    for path, cid in images:
        html_part.add_related(path.read_bytes(), "image", "png", cid=cid)
    return msg


def main() -> None:
    if len(sys.argv) != 2:
        sys.exit("用法：python -m cdc_news.mailer <週報.md>")
    report_path = Path(sys.argv[1])
    if not report_path.exists():
        sys.exit(f"找不到報告檔 {report_path}")

    msg = build_message(report_path)
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=60) as server:
        server.login(os.environ["MAIL_USERNAME"], os.environ["MAIL_PASSWORD"])
        server.send_message(msg)
    print(f"已寄出：{msg['Subject']} → {msg['To']}")


if __name__ == "__main__":
    main()
