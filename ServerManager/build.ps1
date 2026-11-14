# 编译爱沃服务器管理工具（使用 Windows 自带 .NET Framework csc.exe，无需安装任何东西）
$ErrorActionPreference = "Stop"

$csc = "C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe"
if (-not (Test-Path $csc)) { $csc = "C:\Windows\Microsoft.NET\Framework\v4.0.30319\csc.exe" }
if (-not (Test-Path $csc)) { throw "未找到 csc.exe（.NET Framework 编译器）" }

$out = Join-Path $PSScriptRoot "ServerManager.exe"
$srcs = Get-ChildItem -LiteralPath $PSScriptRoot -Filter *.cs | ForEach-Object { $_.FullName }

& $csc /nologo /target:winexe /optimize+ /out:$out `
    /r:System.dll /r:System.Core.dll /r:System.Data.dll `
    /r:System.Drawing.dll /r:System.Windows.Forms.dll `
    /r:System.Management.dll /r:System.Web.Extensions.dll `
    $srcs

if (Test-Path $out) {
    $kb = [math]::Round((Get-Item $out).Length / 1KB, 0)
    Write-Host "构建完成: $out ($kb KB)"
} else {
    throw "编译失败"
}
