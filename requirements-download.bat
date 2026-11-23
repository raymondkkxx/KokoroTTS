@echo off
chcp 65001 >nul
setlocal

if not exist ".\Python\python.exe" (
    echo [错误] 未找到 .\Python\python.exe！
    echo 请先按照 README.md 说明，将对应版本的 Python 安装到本项目的 Python 文件夹中。
    pause
    exit /b 1
)

if not exist "requirements.txt" (
    echo [错误] 未找到 requirements.txt，请确认仓库文件完整！
    pause
    exit /b 1
)

echo [1/2] 正在升级 pip...
.\Python\python.exe -m pip install --upgrade pip

echo [2/2] 正在安装全部依赖（包含 PyTorch GPU 版，文件较大请耐心等待）...
.\Python\python.exe -m pip install -r requirements.txt

if %errorlevel% neq 0 (
    echo.
    echo [安装失败] 部分依赖下载或安装出错，请检查网络后重新双击本脚本继续安装。
    pause
    exit /b 1
)

echo.
echo [安装完成] 环境配置成功！现在可以双击 run-test.bat 或 run.vbs 运行程序了。
pause