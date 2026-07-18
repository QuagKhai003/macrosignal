' MacroSignal hidden launcher (batch 6.4) - Task Scheduler runs THIS, and it
' runs run_saturday.cmd with no console window (window style 0). The first
' live firing died with STATUS_CONTROL_C_EXIT: a visible console popped up on
' the desktop and was closed. Invisible = unkillable-by-reflex.
CreateObject("WScript.Shell").Run _
    "cmd /c ""D:\Code\macrosignal\run_saturday.cmd""", 0, False
