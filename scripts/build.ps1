# Build the Windows package: generate icon -> run tests -> PyInstaller onedir
$ErrorActionPreference = "Stop"
$root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$py = Join-Path $root ".venv\Scripts\python.exe"
Push-Location $root

Write-Host "== 1/3 Generate icon ==" -ForegroundColor Cyan
& $py scripts\make_icon.py
if ($LASTEXITCODE -ne 0) { throw "Icon generation failed" }

Write-Host "== 2/3 Run tests ==" -ForegroundColor Cyan
# 测试临时目录重定向到项目盘：测试会造 150MB/2GB 级假文件，
# 留在系统盘会累积占满磁盘（曾导致整次构建在测试阶段中断）。
$tmp = Join-Path $root ".cache\pytest-tmp"
New-Item -ItemType Directory -Force -Path $tmp | Out-Null
$env:TMP = $tmp
$env:TEMP = $tmp
& $py -m pytest tests -q --basetemp $tmp
if ($LASTEXITCODE -ne 0) {
    Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue
    throw "Tests failed, build aborted"
}
# 测试会造约 10GB 的 2GB/150MB 级假文件：跑完立刻清掉，再建空目录给 PyInstaller 用
Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force -Path $tmp | Out-Null

Write-Host "== 3/3 PyInstaller build ==" -ForegroundColor Cyan
# 构建缓存全部重定向到项目目录，不写系统盘
$env:PYINSTALLER_CONFIG_DIR = Join-Path $root ".cache\pyinstaller"
# 便携模式的数据目录（含索引库）会随 dist 一起被重建：先移走、构建完再放回，
# 避免每次打包都要重新扫描全盘（百万级索引重建代价很高）。
$dataDir = Join-Path $root "dist\LostFile\data"
$dataBak = Join-Path $root ".cache\portable-data"
Remove-Item -Recurse -Force $dataBak -ErrorAction SilentlyContinue
$hadData = Test-Path $dataDir
if ($hadData) {
    Move-Item -Force $dataDir $dataBak
    Write-Host "  已暂存便携数据目录（含索引库）" -ForegroundColor DarkGray
}
Remove-Item -Recurse -Force build, dist -ErrorAction SilentlyContinue
& $py -m PyInstaller --noconfirm --clean LostFile.spec
if ($LASTEXITCODE -ne 0) {
    if ($hadData) { Move-Item -Force $dataBak $dataDir -ErrorAction SilentlyContinue }
    throw "PyInstaller failed"
}

# Portable mode by default: data dir will be created next to the exe (dist/LostFile/data)
Set-Content -Path "dist\LostFile\portable.txt" -Value "portable" -Encoding ascii
Copy-Item README.md, LICENSE, CHANGELOG.md -Destination "dist\LostFile\" -ErrorAction SilentlyContinue
if ($hadData) {
    Move-Item -Force $dataBak $dataDir
    Write-Host "  已恢复便携数据目录（索引无需重扫）" -ForegroundColor DarkGray
}

$exe = Join-Path $root "dist\LostFile\LostFile.exe"
# 收尾：清掉测试/PyInstaller 的临时目录，不留残留
Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue
Write-Host ""
Write-Host ("Build OK: " + $exe) -ForegroundColor Green
Write-Host ("Portable folder: " + (Join-Path $root "dist\LostFile")) -ForegroundColor Green
Pop-Location