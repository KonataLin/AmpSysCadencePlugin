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

defvar(ampsys_launch_suffix getShellEnvVar("AMPSYS_LAUNCH_SUFFIX"))
unless(ampsys_launch_suffix
  ampsys_launch_suffix = " &"
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
      if(getShellEnvVar("ComSpec")
      then
        errset(system(sprintf(nil "mkdir \"%s\"" dir)))
      else
        errset(system(sprintf(nil "mkdir -p \"%s\"" dir)))
      )
    )
  )
  isDir(dir)
)

procedure(ampsysDialog(text dialogType)
  printf("%s\n" text)
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
  ampsysDialog(text hicWarningDialog)
)

procedure(ampsysInfo(text)
  ampsysDialog(text hicInformationDialog)
)

procedure(ampsysDeleteFile(path)
  when(isFile(path)
    unless(car(errset(deleteFile(path)))
      if(getShellEnvVar("ComSpec")
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
  )
)

printf("  [AmpSys] Globals loaded. root=%s pycmd=%s\n" ampsys_root ampsys_pycmd)
