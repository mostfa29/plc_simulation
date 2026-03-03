' TopDrive AI - Silent Background Launcher
' This script starts rig_monitor.py completely hidden (no CMD window).
' Place a shortcut to this file in shell:startup for auto-start on login.

Dim objShell, scriptDir, pythonw, script

Set objShell = CreateObject("WScript.Shell")

' Get the directory where this VBS file lives
scriptDir = CreateObject("Scripting.FileSystemObject").GetParentFolderName(WScript.ScriptFullName)

Dim fso
Set fso = CreateObject("Scripting.FileSystemObject")

' ---- Find pythonw.exe ----
pythonw = ""

' Method 1: Read from pythonw_path.txt (written by install_service.bat)
Dim configFile
configFile = scriptDir & "\pythonw_path.txt"
If fso.FileExists(configFile) Then
    Dim ts
    Set ts = fso.OpenTextFile(configFile, 1)
    If Not ts.AtEndOfStream Then
        pythonw = Trim(ts.ReadLine())
    End If
    ts.Close
    ' Verify the path is still valid
    If pythonw <> "" And Not fso.FileExists(pythonw) Then
        pythonw = ""
    End If
End If

' Method 2: Try user-local AppData installs (most common on modern Windows)
If pythonw = "" Then
    Dim userProfile
    userProfile = objShell.ExpandEnvironmentStrings("%LOCALAPPDATA%")
    Dim userPaths, up
    userPaths = Array( _
        userProfile & "\Programs\Python\Python314\pythonw.exe", _
        userProfile & "\Programs\Python\Python313\pythonw.exe", _
        userProfile & "\Programs\Python\Python312\pythonw.exe", _
        userProfile & "\Programs\Python\Python311\pythonw.exe", _
        userProfile & "\Programs\Python\Python310\pythonw.exe" _
    )
    For Each up In userPaths
        If fso.FileExists(up) Then
            pythonw = up
            Exit For
        End If
    Next
End If

' Method 3: Try system-wide Python install locations
If pythonw = "" Then
    Dim locations, loc
    locations = Array( _
        "C:\Python314\pythonw.exe", _
        "C:\Python313\pythonw.exe", _
        "C:\Python312\pythonw.exe", _
        "C:\Python311\pythonw.exe", _
        "C:\Python310\pythonw.exe", _
        "C:\Program Files\Python314\pythonw.exe", _
        "C:\Program Files\Python313\pythonw.exe", _
        "C:\Program Files\Python312\pythonw.exe", _
        "C:\Program Files\Python311\pythonw.exe", _
        "C:\Program Files\Python310\pythonw.exe" _
    )
    For Each loc In locations
        If fso.FileExists(loc) Then
            pythonw = loc
            Exit For
        End If
    Next
End If

' Method 4: Use 'where' command (skip WindowsApps alias)
If pythonw = "" Then
    Dim exec, line
    Set exec = objShell.Exec("cmd /c where pythonw.exe 2>nul")
    Do While Not exec.StdOut.AtEndOfStream
        line = Trim(exec.StdOut.ReadLine())
        If line <> "" Then
            ' Skip Windows Store alias
            If InStr(LCase(line), "windowsapps") = 0 Then
                pythonw = line
                Exit Do
            End If
        End If
    Loop
End If

' Method 5: Try 'py' launcher to locate pythonw
If pythonw = "" Then
    Dim pyExec, pyPath
    Set pyExec = objShell.Exec("cmd /c py -3 -c ""import sys,os; print(os.path.join(os.path.dirname(sys.executable),'pythonw.exe'))"" 2>nul")
    If Not pyExec.StdOut.AtEndOfStream Then
        pyPath = Trim(pyExec.StdOut.ReadLine())
        If pyPath <> "" And fso.FileExists(pyPath) Then
            pythonw = pyPath
        End If
    End If
End If

If pythonw = "" Then
    MsgBox "Cannot find pythonw.exe." & vbCrLf & vbCrLf & _
           "Please run install_service.bat first," & vbCrLf & _
           "or install Python from https://python.org", _
           vbCritical, "TopDrive AI"
    WScript.Quit 1
End If

script = scriptDir & "\rig_monitor.py"

If Not fso.FileExists(script) Then
    MsgBox "rig_monitor.py not found at:" & vbCrLf & script, vbCritical, "TopDrive AI"
    WScript.Quit 1
End If

' Launch completely hidden (0 = hidden, False = don't wait)
objShell.Run """" & pythonw & """ """ & script & """ --service", 0, False
