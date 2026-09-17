# =====================================================================
# 一鍵部署到 GitHub（Windows PowerShell 版）
# 需求：git、GitHub CLI（winget install GitHub.cli），並先執行 gh auth login
# 用法（在專案根目錄）：
#   powershell -ExecutionPolicy Bypass -File deploy\setup_github.ps1
# =====================================================================
$ErrorActionPreference = "Continue"   # 以 $LASTEXITCODE 自行判斷，避免 PS 5.1 把 git 的 stderr 當成錯誤
[Console]::OutputEncoding = [Text.Encoding]::UTF8
Set-Location (Join-Path $PSScriptRoot "..")

foreach ($c in "gh", "git") {
  if (-not (Get-Command $c -ErrorAction SilentlyContinue)) { throw "找不到 $c，請先安裝" }
}
gh auth status *> $null
if ($LASTEXITCODE -ne 0) { throw "請先執行 gh auth login" }

function Ask($msg, $default = "") {
  $v = Read-Host $msg
  if ([string]::IsNullOrWhiteSpace($v)) { $default } else { $v.Trim() }
}
function AskSecret($msg) {
  $s = Read-Host $msg -AsSecureString
  [Runtime.InteropServices.Marshal]::PtrToStringAuto([Runtime.InteropServices.Marshal]::SecureStringToBSTR($s))
}

$Owner     = (gh api user --jq .login)
$Repo      = Ask "Repo 名稱 [mol-news-bot]" "mol-news-bot"
$Vis       = Ask "公開(public)/私有(private) [public]" "public"
$TgToken   = AskSecret "Telegram Bot Token（輸入不顯示）"
$TgChat    = Ask "Telegram Chat ID（群組為 -100 開頭）"
$SbUrl     = Ask "Supabase Project URL（https://xxxx.supabase.co）"
$SbService = AskSecret "Supabase service_role key（輸入不顯示）"
$SbAnon    = Ask "Supabase anon key"
$Quiet     = Ask "夜間靜音時段，例 23-7（留空＝不靜音）"

$Full   = "$Owner/$Repo"
$WebUrl = "https://$Owner.github.io/$Repo/"
if ($Vis -eq "private") {
  Write-Warning "私有 repo：GitHub Pages 需付費方案；Actions 免費額度可能不足。"
}

Write-Host "▶ 提醒：請先到 Supabase SQL Editor 執行 sql\schema.sql" -ForegroundColor Yellow

# ---- 建立 repo 並推送 ----
if (-not (Test-Path .git)) { git init -q -b main }
git add -A
git -c user.name="$Owner" -c user.email="$Owner@users.noreply.github.com" commit -qm "初始版本：勞動部新聞收錄與推播系統" 2>$null

gh repo view $Full *> $null
if ($LASTEXITCODE -eq 0) {
  Write-Host "▶ repo 已存在，直接推送"
  git remote get-url origin *> $null
  if ($LASTEXITCODE -ne 0) { git remote add origin "https://github.com/$Full.git" }
  git push -u origin main
} else {
  Write-Host "▶ 建立 $Full（$Vis）並推送"
  gh repo create $Full "--$Vis" --source . --remote origin --push --description "勞動部新聞收錄與 Telegram 推播系統"
}

# ---- Secrets / Variables ----
Write-Host "▶ 設定 Secrets / Variables"
gh secret set TELEGRAM_BOT_TOKEN   -R $Full -b $TgToken
gh secret set TELEGRAM_CHAT_ID     -R $Full --body="$TgChat"
gh secret set SUPABASE_URL         -R $Full -b $SbUrl
gh secret set SUPABASE_SERVICE_KEY -R $Full -b $SbService
gh variable set SUPABASE_URL       -R $Full -b $SbUrl
gh variable set SUPABASE_ANON_KEY  -R $Full -b $SbAnon
gh variable set WEB_URL            -R $Full -b $WebUrl
if ($Quiet) { gh variable set QUIET_HOURS -R $Full -b $Quiet }

# ---- GitHub Pages ----
Write-Host "▶ 開啟 GitHub Pages"
gh api -X POST "repos/$Full/pages" -f build_type=workflow *> $null
if ($LASTEXITCODE -ne 0) { gh api -X PUT "repos/$Full/pages" -f build_type=workflow *> $null }
if ($LASTEXITCODE -ne 0) { Write-Warning "請到 Settings → Pages → Source 選 GitHub Actions" }

# ---- 觸發測試 / 部署 / 試跑 ----
Start-Sleep 5
gh workflow run pages.yml -R $Full --ref main
gh workflow run news.yml  -R $Full --ref main -f mode=dry-run
Start-Sleep 10
foreach ($wf in "pages.yml", "news.yml") {
  $id = gh run list -R $Full -w $wf -L 1 --json databaseId --jq ".[0].databaseId"
  Write-Host "▶ 等待 $wf（run $id）…"
  gh run watch $id -R $Full --exit-status
  if ($LASTEXITCODE -ne 0) { throw "$wf 失敗，查看：gh run view $id -R $Full --log-failed" }
}

Write-Host ""
Write-Host "✅ 部署完成" -ForegroundColor Green
Write-Host "  Repo     ：https://github.com/$Full"
Write-Host "  查詢網頁 ：$WebUrl"
Write-Host "  下一步：Actions →「勞動部新聞推播」→ Run workflow（mode=normal）確認群組收到訊息"
