Option Explicit
Dim ws, fso, currentDir, pythonExe, folder, file
Dim pyFileCount, pyFiles(), i, targetPy
Dim promptText, userInput, chosenIdx

Set ws = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

' 1. 获取当前脚本所在目录（项目根目录）及同级 Python 文件夹下的 python.exe
currentDir = fso.GetParentFolderName(WScript.ScriptFullName)
ws.CurrentDirectory = currentDir

pythonExe = fso.BuildPath(currentDir, "Python\python.exe")

' 校验项目根目录的 python.exe 是否存在
If Not fso.FileExists(pythonExe) Then
    MsgBox "Python executable not found at:" & vbCrLf & pythonExe, vbCritical, "Launch Error"
    WScript.Quit
End If

' 2. 统计当前目录下的 .py 文件数量
Set folder = fso.GetFolder(currentDir)
pyFileCount = 0

For Each file In folder.Files
    If LCase(fso.GetExtensionName(file.Name)) = "py" Then
        pyFileCount = pyFileCount + 1
    End If
Next

' 3. 校验并选择入口脚本
If pyFileCount = 0 Then
    MsgBox "No Python (.py) scripts found in the current folder!", vbCritical, "Launch Error"
    WScript.Quit
ElseIf pyFileCount = 1 Then
    ' 唯一脚本直接运行
    For Each file In folder.Files
        If LCase(fso.GetExtensionName(file.Name)) = "py" Then
            targetPy = file.Name
            Exit For
        End If
    Next
Else
    ' 多个脚本弹窗询问序号
    ReDim pyFiles(pyFileCount)
    promptText = "Multiple Python scripts found. Enter the index to launch:" & vbCrLf & vbCrLf
    
    i = 1
    For Each file In folder.Files
        If LCase(fso.GetExtensionName(file.Name)) = "py" Then
            pyFiles(i) = file.Name
            promptText = promptText & "[" & i & "] " & file.Name & vbCrLf
            i = i + 1
        End If
    Next

    Do
        userInput = InputBox(promptText, "Select Python Script", "1")
        If userInput = "" Then WScript.Quit
        
        If IsNumeric(userInput) Then
            chosenIdx = CInt(userInput)
            If chosenIdx >= 1 And chosenIdx <= pyFileCount Then
                targetPy = pyFiles(chosenIdx)
                Exit Do
            End If
        End If
        MsgBox "Invalid index! Please enter a valid number.", vbExclamation, "Prompt"
    Loop
End If

' 4. 完全静默执行：调用项目根目录的 python.exe，输出丢弃到 nul
ws.Run "cmd /c """"" & pythonExe & """ """ & targetPy & """ > nul 2>&1""", 0, False

Set folder = Nothing
Set fso = Nothing
Set ws = Nothing