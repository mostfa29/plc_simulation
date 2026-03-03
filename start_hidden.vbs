' TopDrive AI — Silent Background Launcher
' This script starts rig_monitor.py completely hidden (no CMD window).
' Place a shortcut to this file in shell:startup for auto-start on login.

Dim objShell, scriptDir, pythonw, script

Set objShell = CreateObject("WScript.Shell")

' Get the directory where this VBS file lives
scriptDir = CreateObject("Scripting.FileSystemObject").GetParentFolderName(WScript.ScriptFullName)

' Find pythonw.exe (no-console Python)
pythonw = scriptDir & "\..\..\..\..\AppData\Local\Programs\Python\Python311\pythonw.exe"

' Try common locations if above doesn't exist
Dim fso
Set fso = CreateObject("Scripting.FileSystemObject")

If Not fso.FileExists(pythonw) Then
    ' Try system-wide Python install locations
    Dim locations, loc
    locations = Array( _
        "C:\Python311\pythonw.exe", _
        "C:\Python312\pythonw.exe", _
        "C:\Python313\pythonw.exe", _
        "C:\Python314\pythonw.exe", _
        "C:\Python310\pythonw.exe", _
        "C:\Program Files\Python311\pythonw.exe", _
        "C:\Program Files\Python312\pythonw.exe", _
        "C:\Program Files\Python313\pythonw.exe", _
        "C:\Program Files\Python314\pythonw.exe" _
    )

    pythonw = ""
    For Each loc In locations
        If fso.FileExists(loc) Then
            pythonw = loc
            Exit For
        End If
    Next

    ' Last resort: use 'where' command to find it
    If pythonw = "" Then
        Dim exec
        Set exec = objShell.Exec("cmd /c where pythonw.exe 2>nul")
        pythonw = Trim(exec.StdOut.ReadLine())
        If pythonw = "" Then
            MsgBox "Cannot find pythonw.exe. Install Python and ensure it's in PATH.", vbCritical, "TopDrive AI"
            WScript.Quit 1
        End If
    End If
End If

script = scriptDir & "\rig_monitor.py"

If Not fso.FileExists(script) Then
    MsgBox "rig_monitor.py not found at:" & vbCrLf & script, vbCritical, "TopDrive AI"
    WScript.Quit 1
End If

' Launch completely hidden (0 = hidden, False = don't wait)
objShell.Run """" & pythonw & """ """ & script & """ --service", 0, False
