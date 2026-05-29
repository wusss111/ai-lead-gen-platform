# build.ps1 — Build Windows standalone package for Customer Platform
# Usage: .\packaging\build.ps1 [-Clean] [-Version "2.1.0"]

param(
    [switch]$Clean = $false,
    [string]$Version = "2.1.0"
)

$ErrorActionPreference = "Stop"
$ROOT = $PSScriptRoot | Split-Path -Parent
$DIST = "$ROOT\dist"
$BUILD = "$ROOT\build"
$PACKAGING = "$ROOT\packaging"
$RELEASE = "$PACKAGING\release"
$OUTPUT = "customer-platform-v$Version"

Write-Host "============================================" -ForegroundColor Cyan
Write-Host "  Building Customer Platform v$Version" -ForegroundColor Cyan
Write-Host "============================================" -ForegroundColor Cyan

# ── 1. Clean ──
if ($Clean) {
    Write-Host "[1/6] Cleaning old build artifacts ..." -ForegroundColor Yellow
    if (Test-Path $DIST) { Remove-Item -Recurse -Force $DIST }
    if (Test-Path $BUILD) { Remove-Item -Recurse -Force $BUILD }
    if (Test-Path $RELEASE) { Remove-Item -Recurse -Force $RELEASE }
} else {
    Write-Host "[1/6] Skipping clean (use -Clean to force)" -ForegroundColor Yellow
}

# ── 2. Dependencies ──
Write-Host "[2/6] Installing torch CPU-only + dependencies ..." -ForegroundColor Yellow
pip install torch --index-url https://download.pytorch.org/whl/cpu --quiet
pip install -r "$ROOT\requirements.txt" --quiet
Write-Host "       Dependencies OK"

# ── 3. Prepare runtime data ──
Write-Host "[3/6] Preparing runtime data directory ..." -ForegroundColor Yellow
$RT_DATA = "$PACKAGING\_runtime_data"
New-Item -ItemType Directory -Force -Path "$RT_DATA\redis" | Out-Null
New-Item -ItemType Directory -Force -Path "$RT_DATA\data\var\platform" | Out-Null
New-Item -ItemType Directory -Force -Path "$RT_DATA\data\cache\fetch" | Out-Null

# Redis binaries (only what's needed)
Copy-Item "$ROOT\var\redis\redis-server.exe" "$RT_DATA\redis\" -Force
Copy-Item "$ROOT\var\redis\redis-cli.exe" "$RT_DATA\redis\" -Force
Copy-Item "$ROOT\var\redis\redis.windows.conf" "$RT_DATA\redis\" -Force

# .env.example
Copy-Item "$ROOT\.env.example" "$RT_DATA\.env.example" -Force
Write-Host "       Runtime data OK"

# ── 4. PyInstaller ──
Write-Host "[4/6] Running PyInstaller (this may take 10-20 minutes) ..." -ForegroundColor Yellow
Push-Location $ROOT
try {
    pyinstaller --clean --noconfirm "$PACKAGING\customer_platform.spec"
    if ($LASTEXITCODE -ne 0) {
        Write-Host "ERROR: PyInstaller failed with exit code $LASTEXITCODE" -ForegroundColor Red
        exit $LASTEXITCODE
    }
} finally {
    Pop-Location
}
Write-Host "       PyInstaller OK"

# ── 5. Assemble release ──
Write-Host "[5/6] Assembling release ..." -ForegroundColor Yellow
$RELEASE_DIR = "$RELEASE\$OUTPUT"
New-Item -ItemType Directory -Force -Path $RELEASE_DIR | Out-Null

# Copy frozen output
Copy-Item -Recurse "$DIST\CustomerPlatform\*" "$RELEASE_DIR\" -Force

# Copy Redis
Copy-Item -Recurse "$RT_DATA\redis" "$RELEASE_DIR\redis" -Force

# Copy data dir structure
Copy-Item -Recurse "$RT_DATA\data" "$RELEASE_DIR\data" -Force

# Copy .env example
Copy-Item "$RT_DATA\.env.example" "$RELEASE_DIR\" -Force

# Create README
@"
外贸客户平台 v$Version - Windows 独立版
========================================

双击 CustomerPlatform.exe 即可运行，无需安装 Python！

【首次使用】
  1. 解压到任意目录（不要在压缩包里直接运行）
  2. 双击 CustomerPlatform.exe
  3. 启动器窗口弹出，服务自动启动（约 10-20 秒）
  4. 浏览器自动打开 http://127.0.0.1:8000
  5. 首次登录：用户名 admin，密码 admin123
     请立即到「销售管理」页修改密码！

【配置 API Key（必须）】
  1. 复制 .env.example 为 .env
  2. 用记事本打开 .env，填入 DEEPSEEK_API_KEY=你的密钥
  3. 重启 CustomerPlatform.exe 使配置生效

【配置发信邮箱（可选）】
  1. 编辑 .env 填写 SMTP 信息：
     SMTP_HOST=smtp.gmail.com
     SMTP_PORT=587
     SMTP_USER=your@gmail.com
     SMTP_PASSWORD=应用专用密码
     SMTP_FROM_EMAIL=your@gmail.com
     SMTP_FROM_NAME=你的名字
  2. 或者在平台上「销售管理」页绑定业务员邮箱

【停止程序】
  - 点击启动器窗口的"停止全部"按钮
  - 或直接关闭启动器窗口

【数据存储】
  - 默认存储在 data/ 目录
  - 修改 .env 中 PLATFORM_DATA_DIR 可更换位置

【系统要求】
  - Windows 10/11 64位
  - 至少 4GB 内存（推荐 8GB）
  - 解压后约 3GB 磁盘空间
  - 需要联网（调用 AI API）

【常见问题】
  Q: 提示"端口被占用"
  A: 检查是否有程序占用 6379（Redis）或 8000（Web），
     用命令：netstat -ano | findstr "6379 8000"

  Q: 杀毒软件报警
  A: PyInstaller 打包程序常被误报，请添加信任

  Q: 浏览器没自动打开
  A: 手动访问 http://127.0.0.1:8000

  Q: 知识库搜索失败
  A: 首次使用需联网下载 embedding 模型（sentence-transformers）
     存放在 C:\Users\<用户名>\.cache\torch\sentence_transformers\

"@ | Out-File -FilePath "$RELEASE_DIR\README.txt" -Encoding UTF8

# Create run.bat shortcut
@"
@echo off
start "" "%~dp0CustomerPlatform.exe"
"@ | Out-File -FilePath "$RELEASE_DIR\run.bat" -Encoding ASCII

Write-Host "       Release assembled at $RELEASE_DIR"

# ── 6. Create ZIP ──
Write-Host "[6/6] Creating ZIP archive (this may take a few minutes) ..." -ForegroundColor Yellow
$ZIP_PATH = "$RELEASE\$OUTPUT.zip"
if (Test-Path $ZIP_PATH) { Remove-Item $ZIP_PATH -Force }
Compress-Archive -Path "$RELEASE_DIR\*" -DestinationPath $ZIP_PATH -Force

$zipSize = [math]::Round((Get-Item $ZIP_PATH).Length / 1MB, 1)
Write-Host ""
Write-Host "============================================" -ForegroundColor Green
Write-Host "  Build Complete!" -ForegroundColor Green
Write-Host "  ZIP: $ZIP_PATH" -ForegroundColor Yellow
Write-Host "  Size: $zipSize MB" -ForegroundColor Yellow
Write-Host "============================================" -ForegroundColor Green
