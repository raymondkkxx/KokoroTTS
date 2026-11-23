@echo off
chcp 936 >nul
cd /d "%~dp0"
setlocal enabledelayedexpansion

:: 指定当前目录（项目根目录）Python 文件夹中的 python.exe
set "python_exe=%~dp0Python\python.exe"
if not exist "!python_exe!" goto :no_python

set "py_count=0"

:: 遍历当前目录下的所有 .py 文件并记录编号
for %%f in (*.py) do (
    set /a py_count+=1
    set "py_!py_count!=%%f"
)

:: 判断文件数量
if !py_count! equ 0 goto :no_py
if !py_count! equ 1 (
    set "target_py=!py_1!"
    goto :run_py
)

:: 存在多个 Python 文件时，进入交互选择菜单
:select_py
cls
echo ========================================================
echo 检测到当前目录下存在 !py_count! 个 Python 脚本，请选择：
echo ========================================================
for /l %%i in (1, 1, !py_count!) do (
    echo   [%%i] !py_%%i!
)
echo ========================================================
echo.

set "choice="
set /p "choice=请输入要运行的脚本序号 (1-!py_count!): "

:: 校验输入有效性
if not defined choice goto :invalid_choice
for /f "delims=0123456789" %%a in ("!choice!") do goto :invalid_choice
if !choice! lss 1 goto :invalid_choice
if !choice! gtr !py_count! goto :invalid_choice

set "target_py=!py_%choice%!"
goto :run_py

:invalid_choice
echo.
echo [错误] 输入的序号无效，按任意键重新选择...
pause >nul
goto :select_py

:run_py
echo.
echo ========================================================
echo [已选定] !target_py!
echo [运行中] 正在启动 Python 脚本...
echo ========================================================
echo.

:: 使用项目根目录的 Python 运行选定脚本（报错信息会直接显示在当前窗口）
"!python_exe!" "!target_py!"
set "exit_code=!errorlevel!"

echo.
echo ========================================================
if !exit_code! equ 0 (
    echo [完成] 脚本运行结束（退出码: 0）
) else (
    echo [报错] 脚本运行出错（退出码: !exit_code!），请查看上方报错信息！
)
echo ========================================================
echo 按任意键关闭窗口...
pause >nul
exit

:no_python
echo ========================================================
echo [错误] 未找到 Python 解释器：
echo !python_exe!
echo ========================================================
echo 按任意键关闭窗口...
pause >nul
exit

:no_py
echo ========================================================
echo [错误] 当前目录下未找到任何 .py 文件！
echo ========================================================
echo 按任意键关闭窗口...
pause >nul
exit