# ============================================================
# 本地构建镜像并推送 ACR（在项目根目录执行）
# 用法：
#   首次/无 .env：  .\deploy\build-push.ps1 -Registry registry.cn-hangzhou.aliyuncs.com -Namespace my-ns -Repo stock-review
#   已有 .env：    .\deploy\build-push.ps1          （自动读取 IMAGE 地址并追加新 tag）
#   仅构建不推送：  .\deploy\build-push.ps1 -SkipPush
# ============================================================
param(
    [string]$Registry,          # ACR 注册表地址，如 registry.cn-hangzhou.aliyuncs.com
    [string]$Namespace,         # ACR 命名空间
    [string]$Repo = "stock-review",  # ACR 仓库名
    [string]$Tag = "",          # 版本 tag，默认自动 v0.1.x 递增
    [switch]$SkipPush           # 只构建不推送
)
$ErrorActionPreference = "Stop"
$root = Split-Path $PSScriptRoot -Parent

# ---- 读取/生成 .env ----
$envFile = Join-Path $PSScriptRoot ".env"
if (-not $Tag) {
    # 自动递增 tag：读取 .env 中上一个 tag，patch +1
    $last = if (Test-Path $envFile) { (Get-Content $envFile | Select-String 'IMAGE=.*:v(\d+)\.(\d+)\.(\d+)').Matches.Groups } else { $null }
    if ($last) { $Tag = "v$($last[1].Value).$($last[2].Value).$([int]$last[3].Value + 1)" }
    else { $Tag = "v0.1.0" }
}

if ($Registry -and $Namespace) {
    $image = "$Registry/$Namespace/${Repo}:${Tag}"
} elseif (Test-Path $envFile) {
    $base = (Get-Content $envFile | Where-Object { $_ -match '^IMAGE=' } | Select-Object -First 1) -replace '^IMAGE=', ''
    $image = "$($base.Split(':')[0]):${Tag}"
} else {
    Write-Host "错误：请通过 -Registry/-Namespace 参数指定 ACR 地址，或先创建 deploy/.env" -ForegroundColor Red
    exit 1
}
Write-Host "==> 目标镜像：$image" -ForegroundColor Cyan

# ---- 构建 ----
Write-Host "==> 构建镜像（上下文：项目根目录）..." -ForegroundColor Cyan
docker build -f (Join-Path $PSScriptRoot "Dockerfile") -t $image $root
if ($LASTEXITCODE -ne 0) { Write-Host "构建失败" -ForegroundColor Red; exit 1 }

if ($SkipPush) { Write-Host "==> 已跳过推送（-SkipPush）"; exit 0 }

# ---- 登录并推送（docker login 交互输入 ACR 固定密码）----
Write-Host "==> 推送前需登录 ACR（已登录过则自动跳过验证）..." -ForegroundColor Cyan
$registryHost = $image.Split('/')[0]
docker push $image
if ($LASTEXITCODE -ne 0) {
    Write-Host "推送失败：请先执行  docker login $registryHost  （用户名=阿里云全账号名，密码=ACR固定密码）后重试" -ForegroundColor Red
    exit 1
}

# ---- 同步 latest tag ----
docker tag $image "$($image.Split(':')[0]):latest"
docker push "$($image.Split(':')[0]):latest"

# ---- 回写 .env（记录最新版本，供下次递增）----
$envContent = if (Test-Path $envFile) { Get-Content $envFile -Raw } else { Get-Content (Join-Path $PSScriptRoot ".env.example") -Raw }
$envContent = $envContent -replace 'IMAGE=.*', "IMAGE=$image"
Set-Content -Path $envFile -Value $envContent -Encoding UTF8
Write-Host "==> 完成：$image 已推送，deploy/.env 已更新" -ForegroundColor Green
