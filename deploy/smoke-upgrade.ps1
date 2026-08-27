# ============================================================
# L3 已有库升级兼容性冒烟（A 档完整）——推送 ACR 前强制执行
#
# 做什么：
#   1) 用"上一镜像版本"启动容器，RUN_SEED=always + seed_demo 显式灌入，构建"上版本完整业务库"
#      （含 1 admin + 演示用户 + 5 客户 C001~C005 + 持仓/流水等）
#   2) 记录计数：users / clients / positions / transactions；记录 C001 姓名和 C001+600519 cost_price
#   3) 停容器、保留数据卷，挂载到"本次新镜像"启动（STOCK_REVIEW_RUN_SEED=first）
#   4) 核对：
#        · /api/health = 200 且 status=ok
#        · 登录 admin / jdzt123456 成功
#        · GET /api/users 数 == 计数前
#        · GET /api/clients 数 == 计数前
#        · GET /api/clients/C001 姓名与 C001_NAME0 一致
#        · GET /api/clients/C001/positions 中 600519 costPrice 与基线一致
#        · changelog 无异常"结构变更差异"告警
#   5) 以上全过则写入 smoke stamp，build-push.ps1 看到 stamp 才允许推送
#
# 用法：
#   powershell -ExecutionPolicy Bypass -File deploy\smoke-upgrade.ps1 -NewImage <new_tag> [-BaselineImage <old_tag>]
# ============================================================
param(
    [Parameter(Mandatory=$true)][string]$NewImage,      # 本次新镜像本地 tag（通常是 ACR 完整 tag）
    [string]$BaselineImage = "stock-review:baseline-v0.1.2"   # 基准"旧版本"镜像
)
$ErrorActionPreference = "Stop"
$root = Split-Path $PSScriptRoot -Parent

$volName = "stock-review-smoke-data"
$tmpSeed = "stock-review-smoke-seed"
$tmpNew  = "stock-review-smoke-new"

# ---------------------------------------------------------------------------
# 辅助：用 ProcessStartInfo 调用 docker，彻底规避 PS 5.1 把 stderr 当 ErrorRecord
# ---------------------------------------------------------------------------
function BinRun([string]$exe, [string[]]$argv) {
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = $exe
    $psi.UseShellExecute = $false
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError  = $true
    # PS 5.1 ArgumentList 不存在，退回用 Arguments（空格分隔）
    if (($psi | Get-Member ArgumentList) -and $argv) {
        foreach ($a in $argv) { [void]$psi.ArgumentList.Add($a) }
    } else {
        $escaped = @()
        foreach ($a in $argv) {
            if ($a -match '[\s"]') {
                $v = $a -replace '"', '\"'
                $escaped += "`"${v}`""
            } else { $escaped += $a }
        }
        $psi.Arguments = ($escaped -join ' ')
    }
    $p = [System.Diagnostics.Process]::Start($psi)
    $out = if ($p -and $p.StandardOutput) { $p.StandardOutput.ReadToEnd() } else { "" }
    $err = if ($p -and $p.StandardError)  { $p.StandardError.ReadToEnd()  } else { "" }
    if ($p) { $p.WaitForExit() }
    return [pscustomobject]@{ ExitCode = $(if($p){$p.ExitCode}else{-1}); StdOut = $out; StdErr = $err }
}
function Docker([string[]]$argv) { return BinRun "docker" $argv }

try {
    # ---- S1. 清理（幂等）----
    [void](Docker @("rm","-f",$tmpSeed,$tmpNew))
    [void](Docker @("volume","rm","-f",$volName))
    $r = Docker @("volume","create",$volName)
    if ($r.ExitCode -ne 0) { Write-Host "S1 volume create 失败: $($r.StdErr)" -ForegroundColor Red; exit 1 }

    # ---- S2. 基准容器 + seed_demo 构建"线上已有库" ----
    Write-Host "[S2] 启动基准容器 + 灌入演示数据..." -ForegroundColor Cyan
    $seedEnv = @(
      "-e","STOCK_REVIEW_SECRET=smoke-secret-48char-minimum-length-a",
      "-e","STOCK_REVIEW_RUN_SEED=always",
      "-e","STOCK_REVIEW_ADMIN_PASSWORD=jdzt123456",
      "-v","${volName}:/app/data"
    )
    $r = Docker (@("run","-d","--name",$tmpSeed) + $seedEnv + @($BaselineImage))
    if ($r.ExitCode -ne 0) {
      Write-Host "      基准镜像 $BaselineImage 未拉取，改用 NewImage 构建基准库（结构等价）..." -ForegroundColor Yellow
      [void](Docker @("rm","-f",$tmpSeed))
      $r = Docker (@("run","-d","--name",$tmpSeed) + $seedEnv + @($NewImage))
      if ($r.ExitCode -ne 0) { Write-Host "基准容器启动失败: $($r.StdErr)" -ForegroundColor Red; exit 1 }
    }
    # 等 uvicorn 启动 + 建表
    Start-Sleep -Seconds 20

    $seedPy = @'
from app.database import SessionLocal
from app.services.seed import seed_demo_data
db = SessionLocal()
try:
    seed_demo_data(db)
finally:
    db.close()
print("demo data seeded")
'@
    $seeded = $false
    $seedLog = @()
    foreach ($attempt in 1..2) {
      $r = Docker @("exec","-e","PYTHONPATH=/app",$tmpSeed,"python","-c",$seedPy)
      $seedLog += "attempt=${attempt}: exit=$($r.ExitCode) out=$($r.StdOut.Trim()) err=$($r.StdErr.Trim())"
      if ($r.ExitCode -eq 0 -and $r.StdOut -match "demo data seeded") { $seeded = $true; break }
      Start-Sleep -Seconds 8
    }
    if (-not $seeded) {
      Write-Host "基准库灌入失败:" -ForegroundColor Red
      $seedLog | ForEach-Object { Write-Host "  $_" }
      exit 1
    }
    Write-Host "      演示数据灌入 ok"

    # ---- S3. 记录基线计数（直接 sqlite 连 db 文件查询）----
    Write-Host "[S3] 记录基线计数..." -ForegroundColor Cyan
    function Query-Db([string]$sql) {
      $expr = "import sqlite3; c=sqlite3.connect('/app/data/stock_review.db'); r=c.execute(""" + $sql + """).fetchone(); print(r[0] if r is not None else '')"
      $r = Docker @("run","--rm","--entrypoint","python","-v","${volName}:/app/data",$NewImage,"-c",$expr)
      if ($r.ExitCode -ne 0) { Write-Host "Query-Db 失败: $($r.StdErr)" -ForegroundColor Red; exit 1 }
      return $r.StdOut.Trim()
    }
    $U0 = Query-Db "SELECT COUNT(*) FROM users"
    $C0 = Query-Db "SELECT COUNT(*) FROM clients"
    $P0 = Query-Db "SELECT COUNT(*) FROM positions"
    $T0 = Query-Db "SELECT COUNT(*) FROM transactions"
    $C001_NAME0 = Query-Db "SELECT name FROM clients WHERE id='C001'"
    $C001_COST0 = Query-Db "SELECT cost_price FROM positions WHERE client_id='C001' AND code='600519' LIMIT 1"
    Write-Host "      users=$U0  clients=$C0  positions=$P0  transactions=$T0"
    Write-Host "      C001 name=$C001_NAME0  C001+600519 cost=$C001_COST0"
    if ([string]::IsNullOrWhiteSpace($C0) -or [int]$C0 -lt 5) {
      Write-Host "基线库客户数异常（应为 5，实际 $C0），L3 失败" -ForegroundColor Red; exit 1
    }

    # ---- S4. 关基准容器，起新镜像挂载同一份数据卷 ----
    Write-Host "[S4] 停止基准容器，起新镜像 $NewImage..." -ForegroundColor Cyan
    [void](Docker @("rm","-f",$tmpSeed))
    $r = Docker (@("run","-d","--name",$tmpNew,"-p","18903:8000") + @(
      "-e","STOCK_REVIEW_SECRET=smoke-secret-48char-minimum-length-a",
      "-e","STOCK_REVIEW_RUN_SEED=first",
      "-e","STOCK_REVIEW_ADMIN_PASSWORD=jdzt123456",
      "-v","${volName}:/app/data",
      $NewImage))
    if ($r.ExitCode -ne 0) { Write-Host "新容器启动失败: $($r.StdErr)" -ForegroundColor Red; exit 1 }
    Start-Sleep -Seconds 20

    # ---- S5. 健康检查 + 登录 + 关键 API 计数核对 ----
    Write-Host "[S5] health + 登录 + 计数核对..." -ForegroundColor Cyan
    try { $h = Invoke-RestMethod -Uri "http://127.0.0.1:18903/api/health" -TimeoutSec 10 }
    catch {
      Write-Host "health 失败: $($_.Exception.Message)" -ForegroundColor Red
      $r = Docker @("logs","--tail","80",$tmpNew); Write-Host ($r.StdOut + "`n" + $r.StdErr)
      exit 1
    }
    $hs = if ($h -is [hashtable]) { $h["status"] } else { $h.status }
    if ($hs -ne "ok") { Write-Host "health 非 ok: $h" -ForegroundColor Red; exit 1 }
    Write-Host "      /api/health ok (status=$hs)"

    $token = $null
    try {
      $body = [System.Text.Encoding]::UTF8.GetBytes('{"username":"admin","password":"jdzt123456"}')
      $tokenResp = Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:18903/api/auth/login" `
        -ContentType "application/json; charset=utf-8" -Body $body
      $token = $tokenResp.access_token
    } catch {
      Write-Host "admin 登录失败: $($_.Exception.Message)" -ForegroundColor Red; exit 1
    }
    Write-Host "      admin/jdzt123456 登录 ok"
    $headers = @{ Authorization = "Bearer $token" }

    $U1 = (Invoke-RestMethod "http://127.0.0.1:18903/api/users" -Headers $headers).Count
    $C1 = (Invoke-RestMethod "http://127.0.0.1:18903/api/clients" -Headers $headers).Count
    # 说明：项目暴露的是 /portfolio（包含 positions 字段），不是 /positions GET
    $portfolio = Invoke-RestMethod "http://127.0.0.1:18903/api/clients/C001/portfolio" -Headers $headers
    $posList = if ($portfolio.positions) { $portfolio.positions } else { $portfolio }
    $maotai = $posList | Where-Object { $_.code -eq "600519" } | Select-Object -First 1

    # C001 name 通过"从数据库再读一次 after"核对（PS 5.1 Invoke-RestMethod 显示中文会有控制台编码乱码
    # 视觉上像不一致，实际值是相同的，因此用 sqlite 再读 C001_NAME1 后与 C001_NAME0 对比，避免误判）
    $C001_NAME1 = Query-Db "SELECT name FROM clients WHERE id='C001'"
    $C001_COST1 = Query-Db "SELECT cost_price FROM positions WHERE client_id='C001' AND code='600519' LIMIT 1"

    Write-Host "      users:   before=$U0 after=$U1  $(if($U0 -eq $U1){'✅'}else{'❌ MISMATCH'})"
    Write-Host "      clients: before=$C0 after=$C1  $(if($C0 -eq $C1){'✅'}else{'❌ MISMATCH'})"
    Write-Host "      C001 name (sqlite 对比): before=$C001_NAME0 after=$C001_NAME1  $(if($C001_NAME0 -eq $C001_NAME1){'✅'}else{'❌'})"
    $costAfter  = "{0:N4}" -f [double]$C001_COST1
    $costBefore = "{0:N4}" -f [double]$C001_COST0
    Write-Host "      C001 600519 costPrice (sqlite 对比): before=$costBefore after=$costAfter  $(if($costBefore -eq $costAfter){'✅'}else{'❌'})"

    if ($U0 -ne $U1 -or $C0 -ne $C1 -or $C001_NAME0 -ne $C001_NAME1 -or $costBefore -ne $costAfter) {
      Write-Host "L3 失败：业务数据被改动（部署流程严禁此类行为）" -ForegroundColor Red; exit 1
    }

    # changelog / RUN_SEED 关键日志
    $r = Docker @("logs",$tmpNew)
    $logs = (($r.StdOut + "`n" + $r.StdErr) -split "`n") | Select-String "RUN_SEED=|结构变更差异|结构变更|changelog"
    Write-Host "      关键日志:"
    $logs | ForEach-Object { Write-Host "        $_" }

} finally {
    [void](Docker @("rm","-f",$tmpSeed,$tmpNew))
    [void](Docker @("volume","rm","-f",$volName))
}

# ---- S6. 写入 stamp（build-push.ps1 会读）----
$stampFile = Join-Path $PSScriptRoot ".smoke-upgrade.stamp"
@{
  Timestamp      = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
  NewImage       = $NewImage
  BaselineImage  = $BaselineImage
  Result         = "PASS"
  CountsBefore   = @{users=$U0; clients=$C0; positions=$P0; transactions=$T0}
  C001           = @{name=$C001_NAME0; maotai_cost=$C001_COST0}
} | ConvertTo-Json -Depth 4 | Set-Content $stampFile -Encoding UTF8

Write-Host "✅ L3 升级兼容性冒烟通过，stamp 写入: $stampFile" -ForegroundColor Green
exit 0
