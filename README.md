# CDC 疫情新聞回報與整理摘要

自動抓取衛福部疾病管制署（疾管署 CDC）的疫情新聞，使用 Claude API 產生結構化摘要，並彙整成每日疫情摘要報告。

## 功能

- **新聞抓取**：從疾管署 RSS feed（新聞稿、致醫界通函等）定期抓取最新疫情消息
- **AI 摘要**：使用 Claude API 對每則新聞產生結構化摘要（疾病名稱、疫情等級、影響範圍、防疫建議）
- **每日報告**：將當日新聞彙整成一份 Markdown 格式的疫情摘要報告
- **自動排程**：內附 GitHub Actions workflow，可每日自動執行並將報告 commit 回 repo

## 專案結構

```
cdc-epidemic-news/
├── README.md
├── requirements.txt
├── config.example.yaml      # 設定檔範本（複製為 config.yaml 使用）
├── .github/
│   └── workflows/
│       └── daily-report.yml # 每日自動產生報告的 GitHub Actions
├── src/
│   └── cdc_news/
│       ├── __init__.py
│       ├── fetcher.py       # 抓取疾管署新聞（RSS / 網頁）
│       ├── summarizer.py    # Claude API 摘要
│       ├── report.py        # 產生每日彙整報告
│       └── main.py          # CLI 進入點
├── data/                    # 抓取的原始新聞（JSON，依日期存放）
└── reports/                 # 產出的每日摘要報告（Markdown）
```

## 快速開始

### 1. 安裝相依套件

```bash
cd cdc-epidemic-news
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. 設定

```bash
cp config.example.yaml config.yaml
export ANTHROPIC_API_KEY="你的 API key"
```

> **注意**：請到 [疾管署 RSS 訂閱頁](https://www.cdc.gov.tw/RSS) 確認你要訂閱的 feed 網址
> （新聞稿、疫情訊息、致醫界通函等各有不同網址），填入 `config.yaml` 的 `feeds` 欄位。

### 3. 執行

```bash
# 抓取最新新聞並存到 data/
python -m cdc_news.main fetch

# 對已抓取的新聞產生摘要並輸出每日報告到 reports/
python -m cdc_news.main report

# 一次完成：抓取 + 摘要 + 報告
python -m cdc_news.main run
```

## 每日自動執行

`.github/workflows/daily-report.yml` 會在每天台北時間早上 8 點自動執行，
並把新的報告 commit 回 repo。啟用方式：

1. 在 GitHub repo 的 **Settings → Secrets and variables → Actions** 新增 `ANTHROPIC_API_KEY`
2. Workflow 會自動依排程執行，也可在 Actions 頁面手動觸發（workflow_dispatch）

## 報告範例格式

```markdown
# 疫情每日摘要 2026-08-07

## 重點速覽
- 登革熱：南部新增 X 例本土病例，疫情等級⋯
- 流感：⋯

## 各則新聞摘要
### [新聞標題]
- 疾病：登革熱
- 等級：⋯
- 摘要：⋯
- 防疫建議：⋯
- 原文連結：https://...
```
