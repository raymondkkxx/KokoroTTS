@echo off
chcp 65001 >nul
setlocal

if not exist ".\Python\python.exe" (
    echo [错误] 未找到 .\Python\python.exe，请确保此脚本放在项目根目录！
    pause
    exit /b 1
)

echo [1/3] 正在导出 Python 版本信息...
.\Python\python.exe -V > python-version.txt

echo [2/3] 正在导出依赖清单 requirements.txt...
.\Python\python.exe -m pip freeze > requirements.txt

echo [3/3] 正在检测 PyTorch CUDA 版本并自动配置官方源...
powershell -NoProfile -Command ^
  "$content = Get-Content -Path 'requirements.txt' -Raw; " ^
  "if ($content -match '\+(cu\d+)') { " ^
  "  $cudaTag = $matches[1]; " ^
  "  $indexUrl = '--extra-index-url https://download.pytorch.org/whl/' + $cudaTag; " ^
  "  if ($content -notmatch [regex]::Escape($indexUrl)) { " ^
  "    $newContent = $indexUrl + [Environment]::NewLine + $content; " ^
  "    [System.IO.File]::WriteAllText((Join-Path (Get-Location) 'requirements.txt'), $newContent, (New-Object System.Text.UTF8Encoding($false))); " ^
  "    Write-Host ('[成功] 检测到 ' + $cudaTag + '，已在顶部自动添加: ' + $indexUrl) -ForegroundColor Green; " ^
  "  } else { " ^
  "    Write-Host '[跳过] requirements.txt 顶部已包含对应的 PyTorch 源。' -ForegroundColor Yellow; " ^
  "  } " ^
  "} else { " ^
  "  Write-Host '[完成] 未检测到 +cu 后缀，无需添加额外源。' -ForegroundColor Cyan; " ^
  "}"

echo.
echo 导出完成！请将 python-version.txt、requirements.txt 与本脚本一并提交到 Git。
pause