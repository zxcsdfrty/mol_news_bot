# =====================================================================
# 從本機 .env 讀取金鑰並寫入 GitHub Secrets / Variables（Windows 版）
# 金鑰值不會顯示在畫面上。
# 用法：powershell -ExecutionPolicy Bypass -File deploy\push_secrets.ps1 <owner/repo>
# =====================================================================
param([Parameter(Mandatory = $true)][string]$Repo)
$ErrorActionPreference = "Continue"
[Console]::OutputEncoding = [Text.Encoding]::UTF8
Set-Location (Join-Path $PSScriptRoot "..")

if (-not (Test-Path .env)) { Write-Host "❌ 找不到 .env，請先複製 .env.example 為 .env 並填入"; exit 1 }

$envs = @{}
foreach ($line in Get-Content .env -Encoding UTF8) {
  if ($line -match '^\s*#' -or $line -notmatch '=') { continue }
  $k, $v = $line -split '=', 2
  $envs[$k.Trim()] = $v.Trim().Trim('"').Trim("'")
}
function Get-Val($k) { if ($envs.ContainsKey($k)) { $envs[$k] } else { "" } }
function Is-Placeholder($v) { [string]::IsNullOrEmpty($v) -or $v -like "*xxxx*" -or $v -like "*...*" }

$missing = $false
foreach ($k in "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "SUPABASE_URL", "SUPABASE_SERVICE_KEY", "SUPABASE_ANON_KEY") {
  if (Is-Placeholder (Get-Val $k)) { Write-Host "❌ .env 的 $k 尚未填入"; $missing = $true }
}
if ($missing) { exit 1 }

$owner, $name = $Repo -split '/', 2
$webUrl = Get-Val "WEB_URL"
if (-not $webUrl) { $webUrl = "https://$owner.github.io/$name/" }

Write-Host "▶ 寫入 $Repo"
foreach ($k in "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "SUPABASE_URL", "SUPABASE_SERVICE_KEY") {
  (Get-Val $k) | gh secret set $k -R $Repo *> $null
  if ($LASTEXITCODE -eq 0) { Write-Host "  ✔ secret   $k" } else { Write-Host "  ❌ secret $k"; exit 1 }
}
$vars = [ordered]@{
  SUPABASE_URL = Get-Val "SUPABASE_URL"; SUPABASE_ANON_KEY = Get-Val "SUPABASE_ANON_KEY"
  WEB_URL = $webUrl; QUIET_HOURS = Get-Val "QUIET_HOURS"; REQUIRE_LOGIN = Get-Val "REQUIRE_LOGIN"
  MAX_ITEMS_PER_PUSH = Get-Val "MAX_ITEMS_PER_PUSH"
}
foreach ($k in $vars.Keys) {
  if ($vars[$k]) { gh variable set $k -R $Repo --body="$($vars[$k])" *> $null; Write-Host "  ✔ variable $k" }
}

$dbUrl = Get-Val "SUPABASE_DB_URL"
if ($dbUrl -and (Get-Command psql -ErrorAction SilentlyContinue)) {
  Write-Host "▶ 建立 Supabase 資料表"
  psql $dbUrl -v ON_ERROR_STOP=1 -q -f sql/schema.sql
} else {
  Write-Host "ℹ 略過自動建表（請確認已在 Supabase SQL Editor 執行 sql\schema.sql）"
}

# 驗證 Supabase
$key = Get-Val "SUPABASE_SERVICE_KEY"
try {
  Invoke-RestMethod -Uri "$(Get-Val 'SUPABASE_URL')/rest/v1/news?select=id&limit=1" `
    -Headers @{ apikey = $key; Authorization = "Bearer $key" } | Out-Null
  Write-Host "  ✔ Supabase news 資料表可存取"
} catch { Write-Host "  ❌ Supabase 檢查失敗：$($_.Exception.Message)"; exit 1 }

# 驗證 Telegram
try {
  $r = Invoke-RestMethod -Uri "https://api.telegram.org/bot$(Get-Val 'TELEGRAM_BOT_TOKEN')/getChat?chat_id=$(Get-Val 'TELEGRAM_CHAT_ID')"
  if ($r.ok) { Write-Host "  ✔ Telegram Bot 可存取群組：$($r.result.title)" }
} catch { Write-Host "  ❌ Telegram 檢查失敗（Token 或 Chat ID 錯誤，或 Bot 未加入群組）"; exit 1 }

Write-Host "✅ 完成"
