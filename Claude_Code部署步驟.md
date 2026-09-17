# 用 Claude Code 部署：操作步驟

整個流程約 20–30 分鐘，其中你需要動手的只有「事前準備」和最後確認是否正式推播。

## 一、事前準備（手動，約 15 分鐘）

| # | 項目 | 做法 |
|---|---|---|
| 1 | 安裝工具 | Git、Python 3.11 以上、GitHub CLI（Windows：`winget install GitHub.cli`）、Claude Code（依官方說明安裝：https://docs.claude.com/en/docs/claude-code/setup） |
| 2 | 登入 GitHub | 終端機執行 `gh auth login`，依提示用瀏覽器登入 |
| 3 | 建立 Telegram Bot | Telegram 找 @BotFather → `/newbot` → 記下 Token；把 Bot 加進勞動部群組（建議先用測試群組）；群組內發送 `/start@你的Bot名稱`（Bot 預設只收得到指令訊息）後，瀏覽器開 `https://api.telegram.org/bot<Token>/getUpdates` 找到 `"chat":{"id":-100…}` |
| 4 | 建立 Supabase | supabase.com 註冊 → New project（Region 選 Tokyo 或 Singapore）→ 左側 **SQL Editor** 貼上 `sql/schema.sql` 全文 → Run → **Project Settings → API** 記下 Project URL、`anon` key、`service_role` key |
| 5 | 填寫 .env | 在專案資料夾執行 `cp .env.example .env`（Windows：`copy .env.example .env`），用記事本填入上面取得的 5 個值。**不要把 .env 內容貼到對話裡** |

> `.env` 已列入 `.gitignore` 不會被上傳，且 `.claude/settings.json` 已禁止 Claude Code 讀取它；金鑰由 `deploy/push_secrets.sh` 直接寫入 GitHub Secrets，過程不會顯示金鑰值。

## 二、交給 Claude Code 執行

```bash
cd mol-news-bot
claude
```

進入後輸入：

```
/deploy mol-news-bot
```

Claude Code 會依 `.claude/commands/deploy.md` 的 8 個步驟自動執行：

1. 前置檢查（gh 登入、.env 是否存在）
2. 本機跑測試、檢查新聞來源
3. 建立 GitHub repo 並推送 ← **會詢問你是否同意**
4. 寫入 Secrets／Variables，並驗證 Supabase 與 Telegram 連線 ← **會詢問**
5. 開啟 GitHub Pages
6. 觸發 CI 測試＋網頁發布，失敗會自動讀紀錄、修正、重推
7. 在 GitHub Actions 上實際檢查每個 RSS 來源，失效的自動找新網址修正
8. 試跑（dry-run，不發 Telegram），檢視判斷結果並調整關鍵字
9. 回報結果，**詢問你**是否正式推播第一則訊息

過程中標示「會詢問」的動作（建 repo、git push、觸發 workflow、寫入金鑰）會跳出確認，按 Yes 即可；若想全部自動，可在確認時選「Yes, and don't ask again」。

## 三、完成後

- 查詢網頁：`https://<你的帳號>.github.io/mol-news-bot/`
- 之後每 30 分鐘自動推播；修改 `config/keywords.yaml` 或程式後 `git push`，會自動測試、通過才重新部署網頁。
- 想要推播時間更準：依 README 5.4 設定 cron-job.org。
- 日後要調整，可直接在專案資料夾開 Claude Code 說明需求，例如：「把『勞資爭議』相關新聞的權重調高，並補測試」。

## 常見狀況

| 狀況 | 處理 |
|---|---|
| Claude Code 說 `.env` 某欄位未填 | 用記事本補上後告訴它「已填好，繼續」 |
| Supabase 檢查失敗 | 確認已在 SQL Editor 執行 `schema.sql`，URL 結尾不要有 `/` |
| Telegram 檢查失敗 | 確認 Bot 已在群組內、Chat ID 含負號；頻道需把 Bot 設為管理員 |
| 部內電腦連不到 GitHub / Supabase / Telegram | 改用家用或其他可連外網路的電腦部署；部署完成後排程在 GitHub 上執行，與你的電腦無關 |
| Windows 上 Claude Code 執行 bash 腳本 | Claude Code 在 Windows 需搭配 Git for Windows（內含 Git Bash），腳本可直接執行 |
