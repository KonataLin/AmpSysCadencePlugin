; ============================================================
; AmpSys Cadence Plugin - Initialization
; .cdsinit:
;   load(strcat(getShellEnvVar("AMPSYS_PLUGIN_ROOT") "/skill/ampsys_init.il"))
; ============================================================

let( (skillDir)
  skillDir = strcat(
    if(getShellEnvVar("AMPSYS_PLUGIN_ROOT") then getShellEnvVar("AMPSYS_PLUGIN_ROOT")
       else if(isDir("/opt/AmpSysCadencePlugin") then "/opt/AmpSysCadencePlugin" else "."))
    "/skill")

  printf("\n=== Loading AmpSys Cadence Plugin ===\n")
  load(strcat(skillDir "/ampsys_globals.il"))
  load(strcat(skillDir "/ampsys_extract.il"))
  load(strcat(skillDir "/ampsys_writeback.il"))
  load(strcat(skillDir "/ampsys_menu.il"))
  printf("=== AmpSys Cadence Plugin Ready ===\n\n")
)
