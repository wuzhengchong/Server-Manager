# 短信同步助手 - 无 Gradle 构建脚本（aapt2 + javac + d8 + apksigner）
# 用法: powershell -ExecutionPolicy Bypass -File build.ps1
param(
    [string]$ToolsDir = "C:\Users\wzc\.cache\android-build"
)

$ErrorActionPreference = "Stop"
$Root = $PSScriptRoot
$App = Join-Path $Root "app"
$Build = Join-Path $Root ".build"
$Out = Join-Path $Root "output"
$KS = Join-Path $Root "message.keystore"

# ---------- 定位工具 ----------
$aapt2 = Get-ChildItem -Path $ToolsDir -Recurse -Filter "aapt2.exe" -ErrorAction SilentlyContinue | Select-Object -First 1
$apksigner = Get-ChildItem -Path $ToolsDir -Recurse -Filter "apksigner.bat" -ErrorAction SilentlyContinue | Select-Object -First 1
$zipalign = Get-ChildItem -Path $ToolsDir -Recurse -Filter "zipalign.exe" -ErrorAction SilentlyContinue | Select-Object -First 1
$d8 = Get-ChildItem -Path $ToolsDir -Recurse -Filter "d8.bat" -ErrorAction SilentlyContinue | Select-Object -First 1
$androidJar = Get-ChildItem -Path $ToolsDir -Recurse -Filter "android.jar" -ErrorAction SilentlyContinue |
    Where-Object { $_.FullName -match "android-34" } | Select-Object -First 1
if (-not $androidJar) {
    $androidJar = Get-ChildItem -Path $ToolsDir -Recurse -Filter "android.jar" -ErrorAction SilentlyContinue | Select-Object -First 1
}
foreach ($t in @($aapt2, $apksigner, $zipalign, $d8, $androidJar)) {
    if (-not $t) { Write-Error "缺少工具，请先解压 build-tools 与 platform 到 $ToolsDir"; exit 1 }
}
$btDir = Split-Path $aapt2.FullName   # d8.bat 依赖其所在目录的 lib
Write-Host "工具链: $($aapt2.FullName)"
Write-Host "android.jar: $($androidJar.FullName)"

# ---------- 清理/准备 ----------
if (Test-Path $Build) { Remove-Item $Build -Recurse -Force }
New-Item -ItemType Directory -Force -Path $Build, $Out | Out-Null

# 1) 编译资源
& $aapt2.FullName compile --dir (Join-Path $App "res") -o (Join-Path $Build "res.zip")
if ($LASTEXITCODE -ne 0) { Write-Error "aapt2 compile 失败"; exit 1 }

# 2) 链接资源 + 生成 R.java
& $aapt2.FullName link `
    -o (Join-Path $Build "app.unsigned.apk") `
    -I $androidJar.FullName `
    --manifest (Join-Path $App "AndroidManifest.xml") `
    --min-sdk-version 29 --target-sdk-version 34 `
    --version-code 4 --version-name "1.3" `
    --java (Join-Path $Build "gen") `
    --auto-add-overlay `
    (Join-Path $Build "res.zip")
if ($LASTEXITCODE -ne 0) { Write-Error "aapt2 link 失败"; exit 1 }

# 3) 编译 Java
$srcGlob = Join-Path $Build "gen\com\aiwo\smsgate\R.java"
$sources = (Get-ChildItem -Recurse -Filter "*.java" -Path (Join-Path $App "src")).FullName + $srcGlob
$classesDir = Join-Path $Build "classes"
New-Item -ItemType Directory -Force -Path $classesDir | Out-Null
& javac -encoding UTF-8 -source 11 -target 11 `
    -classpath $androidJar.FullName `
    -d $classesDir -nowarn -Xlint:none `
    $sources
if ($LASTEXITCODE -ne 0) { Write-Error "javac 失败"; exit 1 }

# 4) dex
$classFiles = (Get-ChildItem -Recurse -Filter "*.class" -Path $classesDir).FullName
& (Join-Path $btDir "d8.bat") --release --lib $androidJar.FullName --min-api 29 `
    --output $Build $classFiles
if ($LASTEXITCODE -ne 0) { Write-Error "d8 失败"; exit 1 }
if (-not (Test-Path (Join-Path $Build "classes.dex"))) { Write-Error "未生成 classes.dex"; exit 1 }

# 5) 将 classes.dex 加入 APK
Add-Type -AssemblyName System.IO.Compression.FileSystem
$apkPath = Join-Path $Build "app.unsigned.apk"
$zip = [System.IO.Compression.ZipFile]::Open($apkPath, "Update")
try {
    [System.IO.Compression.ZipFileExtensions]::CreateEntryFromFile(
        $zip, (Join-Path $Build "classes.dex"), "classes.dex",
        [System.IO.Compression.CompressionLevel]::Optimal) | Out-Null
} finally { $zip.Dispose() }

# 6) zipalign
$aligned = Join-Path $Build "app.aligned.apk"
& $zipalign.FullName -f 4 $apkPath $aligned
if ($LASTEXITCODE -ne 0) { Write-Error "zipalign 失败"; exit 1 }

# 7) 签名（无 keystore 则生成自签名）
if (-not (Test-Path $KS)) {
    & keytool -genkeypair -keystore $KS -alias aiwo -keyalg RSA -keysize 2048 `
        -validity 10000 -storepass aiwo123456 -keypass aiwo123456 `
        -dname "CN=AiwoSms,O=Aiwo,C=CN"
    if ($LASTEXITCODE -ne 0) { Write-Error "keytool 生成签名失败"; exit 1 }
    Write-Host "已生成自签名密钥库: $KS（请妥善保管，升级安装需同一签名）"
}
$finalApk = Join-Path $Out "SmsSync-release.apk"
Copy-Item $aligned $finalApk -Force
& $apksigner.FullName sign --ks $KS --ks-pass pass:aiwo123456 --key-pass pass:aiwo123456 `
    --out $finalApk $aligned
if ($LASTEXITCODE -ne 0) { Write-Error "apksigner 签名失败"; exit 1 }
& $apksigner.FullName verify --print-certs $finalApk

Write-Host ""
Write-Host "构建完成: $finalApk"
Write-Host ("大小: {0:N1} MB" -f ((Get-Item $finalApk).Length / 1MB))
