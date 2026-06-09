; ============================================================
; AmpSys Cadence Plugin - Schematic extraction and GUI launch
; ============================================================

procedure(ampsysGetTermOrder(libName cellName)
  let((cdf simInfo termOrder simSec toList)
    ampsysLog(sprintf(nil "Query termOrder: lib=%s cell=%s" libName cellName))
    cdf = nil
    termOrder = nil
    errset(cdf = cdfGetCellCDF(ddGetObj(libName cellName)))
    when(cdf
      simInfo = nil
      errset(simInfo = cdf~>simInfo)
      when(and(simInfo listp(simInfo))
        foreach(simName '(auCdl spectre hspiceD)
          unless(termOrder
            simSec = member(simName simInfo)
            when(and(simSec cdr(simSec) listp(cadr(simSec)))
              toList = member('termOrder cadr(simSec))
              when(and(toList cdr(toList) listp(cadr(toList)))
                termOrder = cadr(toList)
              )
            )
          )
        )
      )
    )
    if(termOrder
    then
      ampsysLog(sprintf(nil "termOrder found: lib=%s cell=%s order=%L" libName cellName termOrder))
    else
      ampsysLog(sprintf(nil "termOrder not found; master terminal fallback will be used: lib=%s cell=%s" libName cellName))
    )
    termOrder
  )
)

procedure(ampsysExportNetlist(lib cell view outFile)
  let((cv port nDev skipLibs subcktPins)
    ampsysLog(sprintf(nil "Export netlist start: lib=%s cell=%s view=%s outFile=%s" lib cell view outFile))
    cv = dbOpenCellViewByType(lib cell view nil "r")
    unless(cv
      ampsysWarn(sprintf(nil "[AmpSys] Cannot open %s/%s/%s" lib cell view))
      return(nil)
    )

    skipLibs = '("basic" "analogLib" "functional" "ahdlLib")
    unless(ampsysEnsureDir(ampsysProjectDir(lib cell))
      ampsysWarn(sprintf(nil "[AmpSys] Cannot create workspace directory:\n%s" ampsysProjectDir(lib cell)))
      dbClose(cv)
      return(nil)
    )

    port = outfile(outFile "w")
    unless(port
      ampsysWarn(sprintf(nil "[AmpSys] Cannot write netlist:\n%s" outFile))
      dbClose(cv)
      return(nil)
    )

    fprintf(port "* CDL/SPICE netlist for AmpSys\n")
    fprintf(port "* Source: %s/%s/%s\n" lib cell view)
    fprintf(port "* Generated: %s\n\n" ampsysTimestamp())

    fprintf(port ".SUBCKT %s" cell)
    subcktPins = ""
    foreach(term cv~>terminals
      fprintf(port " %s" term~>name)
      subcktPins = strcat(subcktPins " " term~>name)
    )
    fprintf(port "\n")
    ampsysLog(sprintf(nil "Export subckt pins:%s" subcktPins))

    nDev = 0
    foreach(inst cv~>instances
      when(member(inst~>libName skipLibs)
        ampsysLog(sprintf(nil "Export skip instance=%s lib=%s cell=%s" inst~>name inst~>libName inst~>cellName))
      )
      unless(member(inst~>libName skipLibs)
        fprintf(port "%s" inst~>name)
        let((netMap pins termOrder nodeText orderSource)
          netMap = makeTable("ampsysNetMap" "NC")
          pins = inst~>instTerms
          unless(pins pins = inst~>instPins)
          when(pins
            foreach(ip pins
              let((tname netName)
                tname = nil
                netName = nil
                errset(tname = ip~>term~>name)
                unless(tname errset(tname = ip~>name))
                errset(netName = ip~>net~>name)
                unless(netName errset(netName = ip~>term~>net~>name))
                when(tname
                  netMap[tname] = if(netName then netName else "NC")
                )
              )
            )
          )

          termOrder = ampsysGetTermOrder(inst~>libName inst~>cellName)
          nodeText = ""
          if(and(termOrder listp(termOrder))
          then
            orderSource = "cdf"
            foreach(tn termOrder
              let((mapped)
                mapped = netMap[sprintf(nil "%s" tn)]
                nodeText = strcat(nodeText " " mapped)
              )
            )
          else
            orderSource = "master"
            foreach(tm inst~>master~>terminals
              nodeText = strcat(nodeText " " netMap[tm~>name])
            )
          )
          fprintf(port "%s" nodeText)
          ampsysLog(sprintf(nil "Export instance=%s lib=%s cell=%s orderSource=%s termOrder=%L nodes=%s"
            inst~>name inst~>libName inst~>cellName orderSource termOrder nodeText))
        )

        fprintf(port " %s" inst~>cellName)
        let((cdfId paramsText)
          paramsText = ""
          cdfId = errset(cdfGetInstCDF(inst))
          when(and(cdfId car(cdfId))
            foreach(param car(cdfId)~>parameters
              when(member(param~>name '("w" "l" "W" "L" "nf" "nfin" "nFin" "m" "fingers" "lr" "wr"))
                let((pval)
                  pval = sprintf(nil "%A" param~>value)
                  unless(rexMatchp("iPar" pval)
                    fprintf(port " %s=%s" param~>name pval)
                    paramsText = strcat(paramsText " " param~>name "=" pval)
                  )
                )
              )
            )
          )
          ampsysLog(sprintf(nil "Export params instance=%s params=%s" inst~>name paramsText))
        )
        fprintf(port "\n")
        nDev = nDev + 1
      )
    )

    fprintf(port ".ENDS %s\n" cell)
    close(port)
    dbClose(cv)
    ampsysLog(sprintf(nil "Export netlist done: instances=%d outFile=%s" nDev outFile))
    t
  )
)

procedure(ampsysLaunchGUI(projectFile netlistFile lib cell view)
  let((cmd status launchLog)
    launchLog = if(and(ampsys_current_run_dir nequal(ampsys_current_run_dir ""))
      then strcat(ampsys_current_run_dir "/ampsys_launch.log")
      else strcat(ampsys_work "/ampsys_launch.log"))
    cmd = sprintf(nil "%s%s \"%s/tools/launch_gui.py\" --gui \"%s/ampsys_gui.py\" --log \"%s\" -- --project \"%s\" --netlist \"%s\" --cadence-lib \"%s\" --cadence-cell \"%s\" --cadence-view \"%s\" >> \"%s\" 2>&1%s"
      ampsys_launch_prefix ampsys_pycmd ampsys_root ampsys_cli launchLog projectFile netlistFile lib cell view launchLog ampsys_launch_suffix)
    ampsysLog(sprintf(nil "Launching GUI command: %s" cmd))
    status = errset(system(cmd))
    ampsysLog(sprintf(nil "GUI launch system status=%L launchLog=%s project=%s netlist=%s" status launchLog projectFile netlistFile))
  )
)

procedure(ampsysExtractCurrentSchematic()
  let((cv lib cell view runDir netlist project)
    ampsysLog("Menu action: Extract Current Schematic")
    cv = ampsysCurrentCV()
    unless(cv
      ampsysWarn("[AmpSys] Open a schematic cellView first.")
      return(nil)
    )
    ampsysSetCurrentFromCV(cv)
    lib = cv~>libName
    cell = cv~>cellName
    view = cv~>viewName
    runDir = ampsysProjectDir(lib cell)
    netlist = ampsysNetlistPath(lib cell)
    project = ampsysProjectJson(lib cell)
    ampsysEnsureDir(runDir)
    ampsysLog(sprintf(nil "Extract paths: runDir=%s netlist=%s project=%s" runDir netlist project))
    when(ampsysExportNetlist(lib cell view netlist)
      ampsysLaunchGUI(project netlist lib cell view)
      ampsysStartApplyPoll(runDir)
    )
  )
)

procedure(ampsysOpenGUIOnly()
  let((cv lib cell view runDir project netlist)
    ampsysLog("Menu action: Open AmpSys GUI")
    cv = ampsysCurrentCV()
    if(cv
    then
      ampsysSetCurrentFromCV(cv)
      lib = cv~>libName
      cell = cv~>cellName
      view = cv~>viewName
      runDir = ampsysProjectDir(lib cell)
      project = ampsysProjectJson(lib cell)
      netlist = ampsysNetlistPath(lib cell)
      ampsysEnsureDir(runDir)
      ampsysLog(sprintf(nil "Open GUI with current schematic: runDir=%s netlist=%s project=%s" runDir netlist project))
      when(ampsysExportNetlist(lib cell view netlist)
        ampsysLaunchGUI(project netlist lib cell view)
      )
      ampsysStartApplyPoll(runDir)
    else
      project = strcat(ampsys_work "/ampsys_project.json")
      ampsysEnsureDir(ampsys_work)
      ampsysLog(sprintf(nil "Open GUI without current schematic: project=%s" project))
      ampsysLaunchGUI(project "" "" "" "schematic")
    )
  )
)

ampsysLog("Extract loaded.")
