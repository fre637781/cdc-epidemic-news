# CDC 疫情新聞回報與整理摘要

自動抓取衛福部疾病管制署（疾管署 CDC）的疫情新聞與開放資料統計，使用 Claude API 產生結構化摘要，每週彙整成一份疫情週報。

## 運作方式

```
每天 08:00   抓取新聞（RSS）存檔 → data/        （不呼叫 AI，零成本）
每週一 08:30 抓取病例統計（開放資料）+ AI 摘要一週新聞 → reports/週報.md
```

- **新聞來源**：新聞稿及疫情訊息、國際旅遊疫情速訊、致醫界通函
- **統計來源**：[疾管署開放資料平台](https://data.cdc.gov.tw/) 的病例／就診統計（純數字計算，不經 AI，無幻覺風險）
- **關注疾病**：登革熱、流感、腸病毒、COVID-19（可在設定檔調整）
- **AI 摘要**：Claude API 對每則新聞產生結構化摘要（疾病、關注程度、地區、病例概況、防疫建議）

## 專案結構

```
cdc-epidemic-news/
├── README.md
├── requirements.txt
├── config.example.yaml      # 設定檔範本（複製為 config.yaml 使用）
├── .github/workflows/
│   ├── daily-fetch.yml      # 每日抓取新聞
│   └── weekly-report.yml    # 每週一產生週報
├── src/cdc_news/
│   ├── fetcher.py           # 抓取疾管署新聞（RSS / 網頁內文）
│   ├── stats.py             # 抓取開放資料統計、計算週趨勢
│   ├── summarizer.py        # Claude API 摘要
│   ├── report.py            # 產生疫情週報
│   └── main.py              # CLI 進入點
├── data/                    # 抓取的原始新聞（JSON，依抓取日期存放）
└── reports/                 # 產出的週報（Markdown，依 ISO 週編號命名）
```

## 快速開始

### 1. 安裝相依套件

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. 設定（重要）

```bash
cp config.example.yaml config.yaml
export ANTHROPIC_API_KEY="你的 API key"
```

`config.example.yaml` 中標註**「需確認」**的網址是佔位值，使用前必須替換：

| 項目 | 到哪裡確認 |
|---|---|
| RSS feed 網址（3 個新聞來源） | [疾管署 RSS 訂閱頁](https://www.cdc.gov.tw/RSS) |
| 開放資料集網址（各疾病統計） | [疾管署開放資料平台](https://data.cdc.gov.tw/) 搜尋資料集，複製 CSV/JSON 下載連結 |

統計資料集同時要確認欄位名稱（`date_field`／`count_field`）與格式（`mode`），說明見設定檔註解。

### 3. 執行

```bash
export PYTHONPATH=src

python -m cdc_news.main fetch    # 抓取最新新聞存到 data/
python -m cdc_news.main report   # 產生上一週的週報到 reports/
python -m cdc_news.main run      # 抓取 + 週報
```

## 自動排程（GitHub Actions）

1. 在 repo 的 **Settings → Secrets and variables → Actions** 新增 `ANTHROPIC_API_KEY`
2. 確認 `config.yaml` 已提交，或維持用 `config.example.yaml` 的預設值（workflow 會自動複製）
3. 兩個 workflow 都可在 Actions 頁面手動觸發（workflow_dispatch）測試

| Workflow | 排程（台北時間） | 動作 | API 費用 |
|---|---|---|---|
| Daily fetch | 每天 08:00 | 抓新聞 → commit `data/` | 無 |
| Weekly report | 週一 08:30 | 統計 + AI 摘要 → commit 週報 | 有（每週一次）|

## 週報格式

```markdown
# 疫情週報 2026-W32
涵蓋期間：2026-08-03 ~ 2026-08-09

## 本週疫情數據
- 登革熱：2026-W32 共 123 例，較前一週 ↑ +15（+14%）
- 流感：⋯

## 重點速覽
- （AI 彙整的 3-6 點本週重點）

## 本週公告摘要
### 登革熱
#### [新聞標題]
- 來源／關注程度／地區：新聞稿／高／台南市
- 摘要：⋯
- 原文連結：https://...
```

> 週報由 AI 自動產生，內容以疾管署原文為準；「本週疫情數據」直接取自開放資料計算，不經 AI。
