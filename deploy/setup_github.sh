#!/usr/bin/env bash
# =====================================================================
# 一鍵部署到 GitHub：建立 repo → 設定 Secrets/Variables → 推送 →
# 開啟 GitHub Pages → 觸發測試、網頁部署與首次試跑，並等待結果。
#
# 需求：git、GitHub CLI（gh，https://cli.github.com），並先執行 `gh auth login`
# 用法（在專案根目錄）：  bash deploy/setup_github.sh
# Windows 可用 Git Bash 執行，或改用 deploy/setup_github.ps1
# =====================================================================
set -euo pipefail
cd "$(dirname "$0")/.."

command -v gh  >/dev/null || { echo "❌ 找不到 gh，請先安裝 GitHub CLI"; exit 1; }
command -v git >/dev/null || { echo "❌ 找不到 git"; exit 1; }
gh auth status >/dev/null 2>&1 || { echo "❌ 請先執行 gh auth login"; exit 1; }

OWNER=$(gh api user --jq .login)
read -rp "Repo 名稱 [mol-news-bot]: " REPO;            REPO=${REPO:-mol-news-bot}
read -rp "公開(public)/私有(private) [public]: " VIS;   VIS=${VIS:-public}
read -rp "Telegram Bot Token: " TG_TOKEN
read -rp "Telegram Chat ID（群組為 -100 開頭）: " TG_CHAT
read -rp "Supabase Project URL（https://xxxx.supabase.co）: " SB_URL
read -rsp "Supabase service_role key（輸入不顯示）: " SB_SERVICE; echo
read -rp "Supabase anon key: " SB_ANON
read -rp "夜間靜音時段，例 23-7（留空＝不靜音）: " QUIET
read -rp "Supabase 資料庫連線字串（選填，填了會自動建表；postgresql://...）: " SB_DB_URL

FULL="$OWNER/$REPO"
WEB_URL="https://$OWNER.github.io/$REPO/"

if [[ "$VIS" == "private" ]]; then
  echo "⚠ 私有 repo：GitHub Pages 需付費方案；Actions 每月免費 2,000 分鐘，每 30 分鐘執行一次會接近上限。"
fi

# ---- 1. 建立資料表（選填） ----
if [[ -n "$SB_DB_URL" ]]; then
  if command -v psql >/dev/null; then
    echo "▶ 建立 Supabase 資料表…"
    psql "$SB_DB_URL" -v ON_ERROR_STOP=1 -q -f sql/schema.sql
  else
    echo "⚠ 找不到 psql，請改到 Supabase SQL Editor 手動執行 sql/schema.sql"
  fi
fi

# ---- 2. 建立 repo 並推送 ----
if [[ ! -d .git ]]; then
  git init -q -b main
fi
git add -A
git -c user.name="${GIT_AUTHOR_NAME:-$OWNER}" -c user.email="${GIT_AUTHOR_EMAIL:-$OWNER@users.noreply.github.com}" \
  commit -qm "初始版本：勞動部新聞收錄與推播系統" || true

if gh repo view "$FULL" >/dev/null 2>&1; then
  echo "▶ repo 已存在，直接推送"
  git remote get-url origin >/dev/null 2>&1 || git remote add origin "https://github.com/$FULL.git"
  git push -u origin main
else
  echo "▶ 建立 $FULL（$VIS）並推送"
  gh repo create "$FULL" --"$VIS" --source . --remote origin --push \
    --description "勞動部新聞收錄與 Telegram 推播系統"
fi

# ---- 3. Secrets / Variables ----
echo "▶ 設定 Secrets / Variables"
gh secret set TELEGRAM_BOT_TOKEN   -R "$FULL" -b "$TG_TOKEN"
gh secret set TELEGRAM_CHAT_ID     -R "$FULL" --body="$TG_CHAT"
gh secret set SUPABASE_URL         -R "$FULL" -b "$SB_URL"
gh secret set SUPABASE_SERVICE_KEY -R "$FULL" -b "$SB_SERVICE"
gh variable set SUPABASE_URL       -R "$FULL" -b "$SB_URL"
gh variable set SUPABASE_ANON_KEY  -R "$FULL" -b "$SB_ANON"
gh variable set WEB_URL            -R "$FULL" -b "$WEB_URL"
[[ -n "$QUIET" ]] && gh variable set QUIET_HOURS -R "$FULL" -b "$QUIET"

# ---- 4. 開啟 GitHub Pages（以 Actions 部署） ----
echo "▶ 開啟 GitHub Pages"
gh api -X POST "repos/$FULL/pages" -f build_type=workflow >/dev/null 2>&1 \
  || gh api -X PUT "repos/$FULL/pages" -f build_type=workflow >/dev/null 2>&1 \
  || echo "⚠ 無法自動開啟 Pages，請到 Settings → Pages → Source 選 GitHub Actions"

# ---- 5. 觸發：測試 → 網頁部署 → 首次試跑（dry-run，不發 Telegram） ----
sleep 5
echo "▶ 觸發網頁部署（內含測試）"
gh workflow run pages.yml -R "$FULL" --ref main
echo "▶ 觸發首次試跑（dry-run）"
gh workflow run news.yml  -R "$FULL" --ref main -f mode=dry-run
sleep 10

for wf in pages.yml news.yml; do
  RUN_ID=$(gh run list -R "$FULL" -w "$wf" -L 1 --json databaseId --jq '.[0].databaseId')
  echo "▶ 等待 $wf（run $RUN_ID）…"
  gh run watch "$RUN_ID" -R "$FULL" --exit-status || {
    echo "❌ $wf 失敗，紀錄：gh run view $RUN_ID -R $FULL --log-failed"; exit 1; }
done

cat <<EOF

✅ 部署完成
  Repo       ：https://github.com/$FULL
  查詢網頁   ：$WEB_URL
  Actions    ：https://github.com/$FULL/actions

下一步：
  1. 到 Actions →「勞動部新聞推播」→ Run workflow，mode 選 normal，確認群組收到訊息
  2. 之後每 30 分鐘自動執行；若要更準時，請依 README 5.3 設定 cron-job.org
  3. 修改程式或關鍵字後 git push，會自動跑測試、測試通過才重新部署網頁
EOF
