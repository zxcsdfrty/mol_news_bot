#!/usr/bin/env bash
# =====================================================================
# 從本機 .env 讀取金鑰並寫入 GitHub Secrets / Variables。
# 金鑰值不會顯示在畫面上（適合交給 Claude Code 執行，它只會看到鍵名）。
#
# 用法：bash deploy/push_secrets.sh <owner/repo>
# =====================================================================
set -euo pipefail
cd "$(dirname "$0")/.."

REPO="${1:?用法：bash deploy/push_secrets.sh <owner/repo>}"
[[ -f .env ]] || { echo "❌ 找不到 .env，請先 cp .env.example .env 並填入"; exit 1; }

# 只解析 KEY=VALUE，不 source（避免執行到任意指令；也相容 macOS 內建 bash 3.2）
get() {
  local line v
  line=$(grep -E "^[[:space:]]*$1[[:space:]]*=" .env | tail -n 1 | tr -d '\r') || true
  v="${line#*=}"
  [[ -z "$line" ]] && v=""
  v="${v#"${v%%[![:space:]]*}"}"; v="${v%"${v##*[![:space:]]}"}"
  v="${v%\"}"; v="${v#\"}"; v="${v%\'}"; v="${v#\'}"
  printf '%s' "$v"
}
placeholder() { [[ -z "$1" || "$1" == *xxxx* || "$1" == *"..."* ]]; }

missing=0
for k in TELEGRAM_BOT_TOKEN TELEGRAM_CHAT_ID SUPABASE_URL SUPABASE_SERVICE_KEY SUPABASE_ANON_KEY; do
  if placeholder "$(get "$k")"; then echo "❌ .env 的 $k 尚未填入"; missing=1; fi
done
[[ $missing -eq 0 ]] || exit 1

OWNER="${REPO%%/*}"; NAME="${REPO##*/}"
WEB_URL="$(get WEB_URL)"; [[ -n "$WEB_URL" ]] || WEB_URL="https://$OWNER.github.io/$NAME/"

set_secret() { printf '%s' "$(get "$1")" | gh secret set "$1" -R "$REPO" >/dev/null && echo "  ✔ secret   $1"; }
set_var()    { [[ -n "$2" ]] && gh variable set "$1" -R "$REPO" --body="$2" >/dev/null && echo "  ✔ variable $1"; return 0; }

echo "▶ 寫入 $REPO"
for k in TELEGRAM_BOT_TOKEN TELEGRAM_CHAT_ID SUPABASE_URL SUPABASE_SERVICE_KEY; do set_secret "$k"; done
set_var SUPABASE_URL      "$(get SUPABASE_URL)"
set_var SUPABASE_ANON_KEY "$(get SUPABASE_ANON_KEY)"
set_var WEB_URL           "$WEB_URL"
set_var QUIET_HOURS       "$(get QUIET_HOURS)"
set_var REQUIRE_LOGIN     "$(get REQUIRE_LOGIN)"
set_var MAX_ITEMS_PER_PUSH "$(get MAX_ITEMS_PER_PUSH)"

# 選填：自動建表
DB_URL="$(get SUPABASE_DB_URL)"
if [[ -n "$DB_URL" ]]; then
  if command -v psql >/dev/null; then
    echo "▶ 建立 Supabase 資料表"
    psql "$DB_URL" -v ON_ERROR_STOP=1 -q -f sql/schema.sql 2>&1 | grep -v NOTICE || true
    psql "$DB_URL" -tAc "select 'news 資料表筆數：' || count(*) from public.news"
  else
    echo "⚠ 未安裝 psql，請到 Supabase SQL Editor 手動執行 sql/schema.sql"
  fi
else
  echo "ℹ 未設定 SUPABASE_DB_URL，略過建表（請確認已在 SQL Editor 執行 sql/schema.sql）"
fi

# 驗證 Supabase REST 可連線且資料表存在（只印 HTTP 狀態碼）
code=$(curl -s -o /dev/null -w '%{http_code}' \
  -H "apikey: $(get SUPABASE_SERVICE_KEY)" -H "Authorization: Bearer $(get SUPABASE_SERVICE_KEY)" \
  "$(get SUPABASE_URL)/rest/v1/news?select=id&limit=1" || true)
if [[ "$code" == "200" ]]; then echo "  ✔ Supabase news 資料表可存取"
else echo "  ❌ Supabase 檢查失敗（HTTP $code）：確認 URL/金鑰正確、schema.sql 已執行"; exit 1; fi

# 驗證 Telegram Bot Token 與群組（只印結果）
tg=$(curl -s "https://api.telegram.org/bot$(get TELEGRAM_BOT_TOKEN)/getChat?chat_id=$(get TELEGRAM_CHAT_ID)" || true)
if [[ "$tg" == *'"ok":true'* ]]; then echo "  ✔ Telegram Bot 可存取群組"
else echo "  ❌ Telegram 檢查失敗：$(echo "$tg" | sed -E 's/.*"description":"([^"]*)".*/\1/')"; exit 1; fi

echo "✅ 完成"
