---
description: 部署勞動部新聞推播系統到 GitHub（建 repo、設定金鑰、測試、發布網頁、試跑、檢查來源）
argument-hint: "[repo 名稱，預設 mol-news-bot]"
---

請依下列步驟把本專案部署到我的 GitHub。repo 名稱：`$ARGUMENTS`（若為空白則使用 `mol-news-bot`），一律建立為 **public**。
先閱讀 `CLAUDE.md` 的安全規則並全程遵守；**不要讀取或顯示 `.env` 的內容**。

每完成一步，用一行回報結果；任何一步失敗，先讀錯誤訊息、修正後重試，同一問題連續失敗 3 次就停下來說明原因與建議。

## 步驟 0：前置檢查
1. `gh auth status` 確認已登入，並用 `gh api user --jq .login` 取得帳號（下稱 OWNER）。未登入就請我執行 `gh auth login` 後停止。
2. 確認 `.env` 檔案**存在**（只用 `test -f .env` 檢查，不可讀內容）。不存在就請我執行 `cp .env.example .env` 並填好下列欄位後再告訴你，然後停止：
   `TELEGRAM_BOT_TOKEN`、`TELEGRAM_CHAT_ID`、`SUPABASE_URL`、`SUPABASE_SERVICE_KEY`、`SUPABASE_ANON_KEY`（選填 `SUPABASE_DB_URL`、`QUIET_HOURS`）。
3. 安裝套件：`pip install -r requirements.txt pytest`。

## 步驟 1：本機測試
1. `python -m pytest -q`，必須全部通過；失敗就修正程式碼（不可刪除或弱化測試來讓它通過）。
2. `python -m news_bot.check_sources`，把結果摘要給我（本機網路可能被擋，失敗不中斷流程，步驟 6 會在 GitHub Actions 上再驗證）。

## 步驟 2：建立 repo 並推送
1. 若尚未 `git init`，以 `main` 分支初始化。
2. `git add -A` 後執行 `git status`，**確認 `.env`、`news.db` 沒有被加入**，再 commit（訊息：`初始版本：勞動部新聞收錄與推播系統`）。
3. 若 `OWNER/<repo>` 不存在：`gh repo create OWNER/<repo> --public --source . --remote origin --push --description "勞動部新聞收錄與 Telegram 推播系統"`；已存在則設定 remote 後 `git push -u origin main`。

## 步驟 3：寫入金鑰並驗證外部服務
1. 執行 `bash deploy/push_secrets.sh OWNER/<repo>`（此腳本不會印出金鑰值）。
2. 若回報 Supabase 檢查失敗，提醒我到 Supabase SQL Editor 執行 `sql/schema.sql`（或在 .env 填 `SUPABASE_DB_URL` 並安裝 psql），等我確認後重跑此步驟。
3. 若回報 Telegram 檢查失敗，提醒我確認 Bot 已加入群組、Chat ID 正確，等我確認後重跑。
4. `gh secret list -R OWNER/<repo>` 與 `gh variable list -R OWNER/<repo>` 確認都已建立。

## 步驟 4：開啟 GitHub Pages
執行 `gh api -X POST repos/OWNER/<repo>/pages -f build_type=workflow`；失敗改用 `-X PUT`；仍失敗就請我到 Settings → Pages → Source 選「GitHub Actions」，等我確認後繼續。

## 步驟 5：CI 測試與網頁發布
1. `gh workflow run pages.yml -R OWNER/<repo> --ref main`，等 10 秒後用 `gh run list -R OWNER/<repo> -w pages.yml -L 1` 取得 run id，`gh run watch <id> -R OWNER/<repo> --exit-status` 監看。
2. 失敗時用 `gh run view <id> -R OWNER/<repo> --log-failed` 找原因，修正、commit、push 後重新觸發，直到成功。
3. 成功後用 `curl -s -o /dev/null -w '%{http_code}' https://OWNER.github.io/<repo>/` 確認回應 200（剛部署可能要等 1–2 分鐘）。

## 步驟 6：在 GitHub Actions 上檢查新聞來源
1. `gh workflow run news.yml -R OWNER/<repo> -f mode=check-sources`，監看完成後用 `gh run view <id> -R OWNER/<repo> --log` 讀取來源檢查表格。
2. 對每個 ❌ 失效的 RSS 來源：到該媒體官網尋找目前的 RSS 網址並更新 `config/sources.yaml`；找不到就註解掉並註明原因。
3. 有修改就跑 `python -m pytest -q`，commit（`修正失效的新聞來源`）、push，再跑一次 check-sources，直到 RSS 來源至少 8 個可用、Google 新聞查詢全部可用。

## 步驟 7：試跑（不發 Telegram）
1. `gh workflow run news.yml -R OWNER/<repo> -f mode=dry-run`，監看完成。
2. 從 log 摘要：抓取則數、判定相關則數、去重後則數，以及前 5 則將推播的標題。
3. 檢視是否有明顯誤判（與勞動部無關）；若有，調整 `config/keywords.yaml`、在 `tests/test_pipeline.py` 補測試案例、push 後重跑 dry-run。

## 步驟 8：完成報告
列出：repo 網址、查詢網頁網址、Actions 網址、各 workflow 最終狀態、可用／失效來源數、做過哪些修正。
最後**詢問我**是否要執行 `gh workflow run news.yml -R OWNER/<repo> -f mode=normal` 正式推播到群組 —— 在我明確同意前不要執行。
並提醒我：若要推播更準時，可依 README 5.4 設定 cron-job.org 觸發。
