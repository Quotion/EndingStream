Option Explicit

Dim shell, http, message, statusPage

Set shell = CreateObject("WScript.Shell")
statusPage = "http://127.0.0.1:8080/ending-status.html"

On Error Resume Next
Set http = CreateObject("WinHttp.WinHttpRequest.5.1")
http.SetTimeouts 1000, 1000, 1500, 1500
http.Open "GET", "http://127.0.0.1:8080/api/health", False
http.Send

If Err.Number <> 0 Then
  message = "Ending is not responding on 127.0.0.1:8080." & vbCrLf & _
            "Start it with start-ending-hidden.vbs or check data\ending-backend.log."
  Err.Clear
  shell.Popup message, 10, "Ending status", 48
  WScript.Quit 1
End If

If http.Status = 200 And InStr(http.ResponseText, """ok"":true") > 0 Then
  message = "Ending is running." & vbCrLf & statusPage
  shell.Popup message, 8, "Ending status", 64
  WScript.Quit 0
End If

message = "Ending answered, but health check was not OK. HTTP " & http.Status & vbCrLf & _
          "Check data\ending-backend.log."
shell.Popup message, 10, "Ending status", 48
WScript.Quit 1
