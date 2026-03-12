Set WshShell = CreateObject("WScript.Shell")
Set objFSO = CreateObject("Scripting.FileSystemObject")

strPath = objFSO.GetParentFolderName(WScript.ScriptFullName)
WshShell.CurrentDirectory = strPath

Function AcharPython()
    Dim pyExe, testRun

    ' 1. Tenta "python" no PATH
    On Error Resume Next
    Set testRun = WshShell.Exec("python --version")
    If Err.Number = 0 Then AcharPython = "python" : Exit Function
    On Error GoTo 0

    ' 2. Tenta "python3"
    On Error Resume Next
    Set testRun = WshShell.Exec("python3 --version")
    If Err.Number = 0 Then AcharPython = "python3" : Exit Function
    On Error GoTo 0

    ' 3. Tenta launcher "py" do Windows
    On Error Resume Next
    Set testRun = WshShell.Exec("py --version")
    If Err.Number = 0 Then AcharPython = "py" : Exit Function
    On Error GoTo 0

    ' 4. Procura nos caminhos comuns
    Dim u : u = WshShell.ExpandEnvironmentStrings("%USERPROFILE%")
    Dim la : la = WshShell.ExpandEnvironmentStrings("%LOCALAPPDATA%")
    Dim pf : pf = WshShell.ExpandEnvironmentStrings("%ProgramFiles%")
    Dim pf86 : pf86 = WshShell.ExpandEnvironmentStrings("%ProgramFiles(x86)%")

    Dim caminhos : caminhos = Array( _
        u & "\AppData\Local\Programs\Python\Python313\python.exe", _
        u & "\AppData\Local\Programs\Python\Python312\python.exe", _
        u & "\AppData\Local\Programs\Python\Python311\python.exe", _
        u & "\AppData\Local\Programs\Python\Python310\python.exe", _
        u & "\AppData\Local\Programs\Python\Python39\python.exe",  _
        u & "\AppData\Local\Programs\Python\Python38\python.exe",  _
        la & "\Programs\Python\Python313\python.exe", _
        la & "\Programs\Python\Python312\python.exe", _
        la & "\Programs\Python\Python311\python.exe", _
        la & "\Programs\Python\Python310\python.exe", _
        la & "\Programs\Python\Python39\python.exe",  _
        "C:\Python313\python.exe", _
        "C:\Python312\python.exe", _
        "C:\Python311\python.exe", _
        "C:\Python310\python.exe", _
        "C:\Python39\python.exe",  _
        "C:\Python38\python.exe",  _
        pf & "\Python313\python.exe", _
        pf & "\Python312\python.exe", _
        pf & "\Python311\python.exe", _
        pf & "\Python310\python.exe", _
        pf86 & "\Python312\python.exe", _
        pf86 & "\Python311\python.exe", _
        pf86 & "\Python310\python.exe", _
        u & "\AppData\Local\Microsoft\WindowsApps\python3.exe", _
        u & "\AppData\Local\Microsoft\WindowsApps\python.exe" _
    )

    For Each pyExe In caminhos
        If objFSO.FileExists(pyExe) Then
            AcharPython = """" & pyExe & """"
            Exit Function
        End If
    Next

    AcharPython = ""
End Function

Dim pythonExe : pythonExe = AcharPython()

If pythonExe = "" Then
    Dim msg
    msg = "Python nao foi encontrado!" & vbCrLf & vbCrLf & _
          "Para instalar:" & vbCrLf & _
          "1. Acesse: https://python.org/downloads" & vbCrLf & _
          "2. Baixe e instale o Python" & vbCrLf & _
          "3. MARQUE a caixa: [v] Add Python to PATH" & vbCrLf & _
          "4. Reinicie o computador e tente novamente" & vbCrLf & vbCrLf & _
          "Deseja abrir o site de download agora?"
    If MsgBox(msg, vbYesNo + vbExclamation, "Python nao encontrado") = vbYes Then
        WshShell.Run "https://python.org/downloads"
    End If
Else
    WshShell.Run pythonExe & " """ & strPath & "\servidor.py""", 1, False
    WScript.Sleep 2500
    WshShell.Run "http://localhost:8080"
End If
