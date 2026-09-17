# 勞動部新聞收錄與推播系統 — Claude Code 專案指引

## 專案概要
- 目的：取代委外服務，每 30 分鐘抓取各媒體 RSS 與 Google 新聞 → 關鍵字判斷是否與勞動部相關 → 推播到勞動部 Telegram 群組，並存入 Supabase 供新聞聯絡室查詢。
- 語言：Python 3.11+；相依套件見 `requirements.txt`（feedparser、requests、PyYAML）。
- 完整架構、部署與資安說明見 `README.md`。

## 目錄
- `news_bot/`：主程式（`main.py` 主流程、`fetcher.py`、`classifier.py`、`dedup.py`、`storage.py`、`notifier.py`、`check.py`、`check_sources.py`）
- `config/sources.yaml`：新聞來源；`config/keywords.yaml`：關鍵字、議題、輿情詞典
- `sql/schema.sql`：Supabase 資料表、查詢函式、RLS
- `web/index.html`：查詢網頁（`__SUPABASE_URL__` 等佔位字串由 `pages.yml` 部署時注入，**不要把實際值寫進 HTML**）
- `.github/workflows/`：`ci.yml` 測試、`pages.yml` 測試通過後部署網頁、`news.yml` 排程推播（手動 mode：normal / no-push / dry-run / check-sources）
- `deploy/`：`push_secrets.sh|ps1`（從 .env 寫入 GitHub Secrets）、`setup_github.*`（互動式一鍵部署，給人工使用）

## 常用指令
```bash
pip install -r requirements.txt pytest
python -m pytest -q                         # 必須全數通過才可 commit / push
python -m news_bot.check "標題" "摘要"        # 測試單篇關鍵字判斷
python -m news_bot.check_sources            # 檢查每個 RSS 來源
python -m news_bot.main --dry-run -v        # 實際抓取，只印訊息不推播
gh workflow run news.yml -f mode=dry-run    # 在 GitHub Actions 上試跑
```

## 安全規則（務必遵守）
1. **絕對不要讀取、顯示、cat、grep 或 commit `.env`**。金鑰只能透過 `bash deploy/push_secrets.sh <owner/repo>` 寫入 GitHub，該腳本不會輸出金鑰值。
2. 不要要求使用者在對話中貼上 Token 或金鑰；若 `.env` 未填好，請使用者自行編輯後再繼續。
3. commit 前執行 `git status` 確認沒有 `.env`、`news.db` 或其他含金鑰的檔案。
4. 不要刪除 GitHub repo、不要 force push、不要修改 `sql/schema.sql` 的 RLS 權限為可寫入。
5. `news.yml` 以 `mode=normal` 執行會真的發 Telegram 到正式群組；除非使用者明確同意，否則只用 `dry-run` 或 `check-sources`。

## 程式修改慣例
- 使用繁體中文撰寫註解、訊息與 commit message。
- 修改關鍵字邏輯或去重邏輯時，同步在 `tests/test_pipeline.py` 增加測試案例。
- RSS 來源失效時：優先到該媒體官網找新的 RSS 網址替換；找不到就註解掉並在註解說明原因，不要刪除整段。
- 修改 `keywords.yaml` 的議題名稱時，同步更新 `web/index.html` 的 `TOPICS` 陣列。
