; ============================================================
; AmpSys Cadence Plugin - Globals
; ============================================================

defvar(ampsys_root getShellEnvVar("AMPSYS_PLUGIN_ROOT"))
unless(ampsys_root
  if(isDir("/opt/AmpSysCadencePlugin")
  then
    ampsys_root = "/opt/AmpSysCadencePlugin"
  else
    ampsys_root = "."
  )
)

defvar(ampsys_work strcat(ampsys_root "/workspace"))
defvar(ampsys_cli  strcat(ampsys_root "/cli"))

defvar(ampsys_pycmd getShellEnvVar("AMPSYS_PYCMD"))
unless(ampsys_pycmd
  ; User requested py -3.  On Linux, install_linux.sh can create a py shim.
  ampsys_pycmd = "py -3"
)

procedure(ampsysOnWindows()
  or(getShellEnvVar("ComSpec") getShellEnvVar("COMSPEC") equal(getShellEnvVar("OS") "Windows_NT"))
)

defvar(ampsys_launch_prefix getShellEnvVar("AMPSYS_LAUNCH_PREFIX"))
unless(ampsys_launch_prefix
  ampsys_launch_prefix = ""
)

defvar(ampsys_launch_suffix getShellEnvVar("AMPSYS_LAUNCH_SUFFIX"))
unless(ampsys_launch_suffix
  ampsys_launch_suffix = ""
)

defvar(ampsys_current_lib "")
defvar(ampsys_current_cell "")
defvar(ampsys_current_view "schematic")
defvar(ampsys_current_run_dir "")

defvar(_ampsysTagged nil)
defvar(_ampsysMenuTimerOn nil)
defvar(_ampsysPollTimerOn nil)
defvar(_ampsysPollDirs nil)

procedure(ampsysTimestamp()
  getCurrentTime()
)

procedure(ampsysEnsureDir(dir)
  unless(isDir(dir)
    unless(car(errset(createDir(dir)))
      if(ampsysOnWindows()
      then
        errset(system(sprintf(nil "mkdir \"%s\"" dir)))
      else
        errset(system(sprintf(nil "mkdir -p \"%s\"" dir)))
      )
    )
  )
  isDir(dir)
)

procedure(ampsysAppendLogFile(path line)
  when(path
    errset(
      let((port)
        port = outfile(path "a")
        when(port
          fprintf(port "%s\n" line)
          close(port)
        )
      )
    )
  )
  t
)

procedure(ampsysSkillLogPath()
  strcat(ampsys_work "/ampsys_skill.log")
)

procedure(ampsysRunSkillLogPath()
  if(and(ampsys_current_run_dir nequal(ampsys_current_run_dir ""))
  then
    strcat(ampsys_current_run_dir "/ampsys_skill.log")
  else
    nil
  )
)

procedure(ampsysLog(text)
  let((line runLog)
    line = sprintf(nil "%s [AmpSys] %s" ampsysTimestamp() text)
    printf("%s\n" line)
    ampsysEnsureDir(ampsys_work)
    ampsysAppendLogFile(ampsysSkillLogPath() line)
    runLog = ampsysRunSkillLogPath()
    when(runLog
      ampsysEnsureDir(ampsys_current_run_dir)
      ampsysAppendLogFile(runLog line)
    )
    t
  )
)

procedure(ampsysDialog(text dialogType)
  ampsysLog(text)
  errset(hiDisplayAppDBox(
    ?name gensym("ampsysMsg")
    ?dboxBanner "AmpSys"
    ?dboxText text
    ?dialogType dialogType
    ?dialogStyle 'modal
    ?buttonLayout 'Close
  ))
  nil
)

procedure(ampsysWarn(text)
  ampsysLog(strcat("WARN: " text))
  ampsysDialog(text hicWarningDialog)
)

procedure(ampsysInfo(text)
  ampsysLog(strcat("INFO: " text))
  ampsysDialog(text hicInformationDialog)
)

procedure(ampsysDeleteFile(path)
  when(isFile(path)
    ampsysLog(sprintf(nil "Deleting file: %s" path))
    unless(car(errset(deleteFile(path)))
      if(ampsysOnWindows()
      then
        errset(system(sprintf(nil "del /q \"%s\"" path)))
      else
        errset(system(sprintf(nil "rm -f \"%s\"" path)))
      )
    )
  )
  t
)

procedure(ampsysProjectDir(lib cell)
  strcat(ampsys_work "/" lib "_" cell)
)

procedure(ampsysProjectJson(lib cell)
  strcat(ampsysProjectDir(lib cell) "/project.json")
)

procedure(ampsysNetlistPath(lib cell)
  strcat(ampsysProjectDir(lib cell) "/" cell ".sp")
)

procedure(ampsysCurrentCV()
  let((cv)
    cv = nil
    errset(cv = geGetEditCellView())
    unless(cv
      errset(cv = geGetWindowCellView(hiGetCurrentWindow()))
    )
    cv
  )
)

procedure(ampsysSetCurrentFromCV(cv)
  when(cv
    ampsys_current_lib = cv~>libName
    ampsys_current_cell = cv~>cellName
    ampsys_current_view = cv~>viewName
    ampsys_current_run_dir = ampsysProjectDir(ampsys_current_lib ampsys_current_cell)
    ampsysEnsureDir(ampsys_current_run_dir)
    ampsysLog(sprintf(nil "Current schematic set: lib=%s cell=%s view=%s runDir=%s"
      ampsys_current_lib ampsys_current_cell ampsys_current_view ampsys_current_run_dir))
  )
)

ampsysLog(sprintf(nil "Globals loaded. root=%s work=%s cli=%s pycmd=%s launch_prefix=%s launch_suffix=%s"
  ampsys_root ampsys_work ampsys_cli ampsys_pycmd ampsys_launch_prefix ampsys_launch_suffix))
