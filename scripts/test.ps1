# Run automated tests
$ErrorActionPreference = "Stop"
$root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$py = Join-Path $root ".venv\Scripts\python.exe"
# 测试临时目录重定向到项目盘（D:\项目）：测试会造 150MB/2GB 级假文件，
# 留在系统盘会累积占满磁盘（历史上曾导致整次构建在测试阶段中断）。
$tmp = Join-Path $root ".cache\pytest-tmp"
New-Item -ItemType Directory -Force -Path $tmp | Out-Null
Push-Location $root
$env:TMP = $tmp
$env:TEMP = $tmp
try { & $py -m pytest tests -q --tb=short --basetemp $tmp }
finally {
    Pop-Location
    # 测试会造约 10GB 的 2GB/150MB 级假文件（单次实测 10.2GB）：
    # 跑完必须清掉，否则会一直堆在 .cache\pytest-tmp 里。
    Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue
}
exit $LASTEXITCODE
