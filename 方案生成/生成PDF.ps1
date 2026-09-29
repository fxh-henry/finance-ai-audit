# 一键重新生成应用方案 PDF：双击运行即可
# 流程：iCAN应用方案.md + 封面信息.txt  →  HTML  →  Chrome 无头打印  →  iCAN应用方案.pdf
$ErrorActionPreference = "Stop"
# 控制台按 UTF-8 输出，避免中文提示在窗口里显示成乱码
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONIOENCODING = "utf-8"

$here   = Split-Path -Parent $MyInvocation.MyCommand.Path
$proj   = Split-Path -Parent $here
$py     = "C:\Users\Administrator\.conda\envs\agent\python.exe"
$chrome = "C:\Program Files\Google\Chrome\Application\chrome.exe"
if (-not (Test-Path $chrome)) { $chrome = "C:\Program Files (x86)\Google\Chrome\Application\chrome.exe" }
if (-not (Test-Path $py))     { $py = "python" }

$md      = Join-Path $proj "iCAN应用方案.md"
$pdf     = Join-Path $proj "iCAN应用方案.pdf"
$tmpHtml = Join-Path $env:TEMP "iCAN_plan.html"
# 每次用全新的临时 profile：旧 profile 若被残留进程占用，Chrome 会静默失败
$prof    = Join-Path $env:TEMP ("_ican_chrome_" + [guid]::NewGuid().ToString("N").Substring(0, 8))

# 1) Markdown 转成打印用 HTML
& $py "$here\md2html.py" $md $tmpHtml "$here\封面信息.txt"
if ($LASTEXITCODE -ne 0) { throw "Markdown 转 HTML 失败" }

# 2) 先打印到临时 PDF，确认成功后再覆盖正式文件
$tmpPdf = Join-Path $env:TEMP "iCAN_plan_out.pdf"
if (Test-Path $tmpPdf) { Remove-Item -LiteralPath $tmpPdf -Force }
$chromeArgs = @(
  "--headless=new", "--disable-gpu", "--no-sandbox", "--no-first-run",
  "--no-pdf-header-footer", "--virtual-time-budget=20000",
  "--user-data-dir=$prof", "--print-to-pdf=$tmpPdf",
  ("file:///" + ($tmpHtml -replace '\\', '/'))
)
Start-Process -FilePath $chrome -ArgumentList $chromeArgs -Wait -NoNewWindow | Out-Null
if (-not (Test-Path $tmpPdf)) { throw "Chrome 打印失败，没有生成 PDF" }

# 3) 覆盖正式文件；若被 PDF 阅读器占用，会给出明确提示
try {
  Move-Item -LiteralPath $tmpPdf -Destination $pdf -Force -ErrorAction Stop
} catch {
  Write-Host "无法覆盖 $pdf（可能正被 PDF 阅读器打开），请关闭后重试。" -ForegroundColor Yellow
  throw
}

# 4) 清理本次使用的临时 profile（先确认路径确实在临时目录内再递归删除）
$profPath = [System.IO.Path]::GetFullPath($prof)
$tempPath = [System.IO.Path]::GetFullPath($env:TEMP)
if ($profPath.StartsWith($tempPath, [System.StringComparison]::OrdinalIgnoreCase)) {
  Remove-Item -LiteralPath $profPath -Recurse -Force -ErrorAction SilentlyContinue
}

Write-Host ""
Write-Host "完成：$pdf" -ForegroundColor Green
Write-Host ("文件大小：{0:N0} 字节" -f (Get-Item $pdf).Length) -ForegroundColor Green
Start-Sleep -Seconds 3