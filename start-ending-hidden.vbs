Option Explicit

Dim fso, shell, root, pythonw, launcher, cmd

Set fso = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")

root = fso.GetParentFolderName(WScript.ScriptFullName)
pythonw = root & "\venv\Scripts\pythonw.exe"
launcher = root & "\serve_hidden.pyw"

If Not fso.FileExists(pythonw) Then
  shell.Popup "Ending cannot start: venv\Scripts\pythonw.exe was not found.", 10, "Ending", 48
  WScript.Quit 1
End If

If Not fso.FileExists(launcher) Then
  shell.Popup "Ending cannot start: serve_hidden.pyw was not found.", 10, "Ending", 48
  WScript.Quit 1
End If

shell.CurrentDirectory = root
cmd = """" & pythonw & """ """ & launcher & """"
shell.Run cmd, 0, False
