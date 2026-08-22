' CARDZ area-P hidden launcher (contract C5).
'
' Usage:
'   wscript.exe //nologo //B pc_cdp_hidden_launch.vbs <rc-file> <log-file> <exe> [args...]
'
' Why: collect_control runs under WSL and launches the Windows backend Python
' for the PriceCharting CDP child. Plain WSL interop pops a Windows Terminal
' window, and 2026-08-22 proved a human closing that window kills the tick with
' CTRL_CLOSE. WScript.Shell.Run with window style 0 keeps it hidden.
'
' The child's stdout and stderr are redirected into <log-file> so a hung or
' crashed child stays diagnosable. The exit code is written to <rc-file>
' because a return code does NOT propagate back through WSL interop; the
' caller reads the rc-file, never the interop process's own exit status.
Option Explicit

Dim args, shell, fso, rcFile, logFile, inner, i, rc, out

Set args = WScript.Arguments
If args.Count < 3 Then
  WScript.Quit 2
End If

rcFile = args(0)
logFile = args(1)

inner = ""
For i = 2 To args.Count - 1
  If Len(inner) > 0 Then
    inner = inner & " "
  End If
  inner = inner & """" & args(i) & """"
Next
inner = inner & " > """ & logFile & """ 2>&1"

Set shell = CreateObject("WScript.Shell")
' /s plus one outer pair of quotes makes cmd strip exactly that pair and run
' the rest verbatim, so quoted paths inside survive.
rc = shell.Run("cmd.exe /s /c """ & inner & """", 0, True)

Set fso = CreateObject("Scripting.FileSystemObject")
Set out = fso.CreateTextFile(rcFile, True)
out.WriteLine rc
out.Close

WScript.Quit rc
