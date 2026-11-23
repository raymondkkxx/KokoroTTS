Option Explicit
Dim ws, fso, currentDir, pyExe, targetPy

Set ws = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

' 1. 获取工作目录
currentDir = fso.GetParentFolderName(WScript.ScriptFullName)
ws.CurrentDirectory = currentDir

' 2. 注入便携离线缓存与音素环境变量
ws.Environment("PROCESS")("HF_HOME") = currentDir & "\cache\huggingface"
ws.Environment("PROCESS")("TORCH_HOME") = currentDir & "\cache\torch"
ws.Environment("PROCESS")("PHONEMIZER_ESPEAK_PATH") = currentDir & "\espeak-ng"
ws.Environment("PROCESS")("PATH") = currentDir & "\espeak-ng;" & currentDir & "\python;" & currentDir & "\python\Scripts;" & ws.Environment("PROCESS")("PATH")

' 3. 锁定便携 Python 解释器 (优先使用 python.exe)
If fso.FileExists(currentDir & "\python\python.exe") Then
    pyExe = currentDir & "\python\python.exe"
ElseIf fso.FileExists(currentDir & "\venv\Scripts\python.exe") Then
    pyExe = currentDir & "\venv\Scripts\python.exe"
Else
    MsgBox "未找到便携版 python.exe！" & vbCrLf & "请确认 python 文件夹在当前目录下。", vbCritical, "启动失败"
    WScript.Quit
End If

' 4. 锁定入口脚本
targetPy = currentDir & "\tts_player.py"
If Not fso.FileExists(targetPy) Then
    MsgBox "未在当前目录找到 tts_player.py！", vbCritical, "启动失败"
    WScript.Quit
End If

' 5. 通过 0 参数隐藏运行窗口，绝不产生控制台黑框
ws.Run """" & pyExe & """ """ & targetPy & """", 0, False

Set fso = Nothing
Set ws = Nothing