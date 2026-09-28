# 一键重新生成应用方案 PDF：双击运行即可
# 流程：应用方案.md + 封面信息.txt  →  HTML  →  Chrome 无头打印  →  iCAN应用方案.pdf
$ErrorActionPreference = "Stop"
# 控制台按 UTF-8 输出，避免中文提示在窗口里显示成乱码
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONIOENCODING = "utf-8"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$proj = Split-Path -Parent $here
$py     = "C:\Users\Administrator\.conda\envs\agent\python.exe"
$chrome = "C:\Program Files\Google\Chrome\Application\chrome.exe"
$tmpHtml = Join-Path $env:TEMP "iCAN_plan.html"

if (-not (Test-Path $py))     { $py = "python" }
if (-not (Test-Path $chrome)) { $chrome = "C:\Program Files (x86)\Google\Chrome\Application\chrome.exe" }

& $py "$here\md2html.py" "$proj\iCAN应用方案.md" $tmpHtml "$here\封面信息.txt"
& $chrome --headless=new --disable-gpu --no-sandbox --no-first-run --no-pdf-header-footer `
          --virtual-time-budget=20000 --user-data-dir="$here\_chrome_profile" `
          --print-to-pdf="$proj\iCAN应用方案.pdf" ("file:///" + ($tmpHtml -replace '\\','/'))

Write-Host ""
Write-Host "完成：$proj\iCAN应用方案.pdf" -ForegroundColor Green
Start-Sleep -Seconds 3