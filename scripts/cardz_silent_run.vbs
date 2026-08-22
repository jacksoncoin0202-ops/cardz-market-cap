' CARDZ generic hidden runner (contract C2).
'
' WHY THIS EXISTS -- empirical, probed 2026-08-22. Do NOT "simplify" this back
' to "powershell.exe -WindowStyle Hidden":
'   (a) Windows 11 defaults to Windows Terminal, so a Task Scheduler action that
'       runs powershell.exe under InteractiveToken is delegated to WT and STILL
'       pops a visible OpenConsole window, even with -WindowStyle Hidden. A human
'       used that window as their terminal and closed it -> the tick console got
'       CTRL_CLOSE -> exit 0xC000013A. 9 dead ticks in one day.
'   (b) wscript.exe //nologo //B with WScript.Shell.Run(cmd, 0, True) creates only
'       a classic hidden conhost, no WT window at all, and the child's rc comes
'       back through WScript.Quit (verified rc=7).
'   (c) rc propagates to Task Scheduler but NOT through WSL interop (observed
'       WSL_RC=0): anything launched from WSL through this vbs must write its own
'       rc file.
'
' Usage: wscript.exe //nologo //B "<repo>\scripts\cardz_silent_run.vbs" <exe> <args...>
Option Explicit
Dim sh, i, a, cmd
If WScript.Arguments.Count < 1 Then WScript.Quit 64
Set sh = CreateObject("WScript.Shell")
cmd = ""
For i = 0 To WScript.Arguments.Count - 1
  a = WScript.Arguments(i)
  If InStr(a, " ") > 0 Or InStr(a, """") > 0 Then
    a = """" & Replace(a, """", """""") & """"
  End If
  If cmd = "" Then cmd = a Else cmd = cmd & " " & a
Next
WScript.Quit sh.Run(cmd, 0, True)
