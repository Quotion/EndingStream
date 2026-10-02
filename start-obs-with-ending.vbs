Option Explicit

Dim fso, shell, root, candidates, obsPath, i, startEnding

Set fso = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")

root = fso.GetParentFolderName(WScript.ScriptFullName)
startEnding = root & "\start-ending-hidden.vbs"

If fso.FileExists(startEnding) Then
  shell.Run """" & startEnding & """", 0, False
End If

candidates = Array( _
  shell.ExpandEnvironmentStrings("%ProgramFiles%") & "\obs-studio\bin\64bit\obs64.exe", _
  shell.ExpandEnvironmentStrings("%ProgramFiles(x86)%") & "\obs-studio\bin\64bit\obs64.exe", _
  shell.ExpandEnvironmentStrings("%LOCALAPPDATA%") & "\Programs\obs-studio\bin\64bit\obs64.exe" _
)

obsPath = ""
For i = 0 To UBound(candidates)
  If fso.FileExists(candidates(i)) Then
    obsPath = candidates(i)
    Exit For
  End If
Next

If obsPath = "" Then
  shell.Popup "OBS was not found in the default install folders. Start Ending with start-ending-hidden.vbs, then open OBS normally.", 12, "Ending", 48
  WScript.Quit 1
End If

shell.CurrentDirectory = fso.GetParentFolderName(obsPath)
shell.Run """" & obsPath & """", 1, False
