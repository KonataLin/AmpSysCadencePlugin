; ============================================================
; AmpSys Cadence Plugin - Schematic extraction and GUI launch
; ============================================================

procedure(ampsysGetTermOrder(libName cellName)
  let((cdf simInfo termOrder simSec toList)
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
    termOrder
  )
)

procedure(ampsysExportNetlist(lib cell view outFile)
  let((cv port nDev skipLibs)
    cv = dbOpenCellViewByType(lib cell view nil "r")
    unless(cv
      ampsysWarn(sprintf(nil "[AmpSys] Cannot open %s/%s/%s" lib cell view))
      return(nil)
    )

    skipLibs = '("basic" "analogLib" "functional" "ahdlLib")
    unless(ampsysEnsureDir(ampsysProjectDir(lib cell))
      ampsysWarn(sprintf(nil "[AmpSys] Cannot create workspace directory:\n%s" ampsysProjectDir(lib cell)))
      return(nil)
    )

    port = outfile(outFile "w")
    unless(port
      ampsysWarn(sprintf(nil "[AmpSys] Cannot write netlist:\n%s" outFile))
      return(nil)
    )

    fprintf(port "* CDL/SPICE netlist for AmpSys\n")
    fprintf(port "* Source: %s/%s/%s\n" lib cell view)
    fprintf(port "* Generated: %s\n\n" ampsysTimestamp())

    fprintf(port ".SUBCKT %s" cell)
    foreach(term cv~>terminals
      fprintf(port " %s" term~>name)
    )
    fprintf(port "\n")

    nDev = 0
    foreach(inst cv~>instances
      unless(member(inst~>libName skipLibs)
        fprintf(port "%s" inst~>name)
        let((netMap pins termOrder)
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
          if(and(termOrder listp(termOrder))
          then
            foreach(tn termOrder
              fprintf(port " %s" netMap[sprintf(nil "%s" tn)])
            )
          else
            foreach(tm inst~>master~>terminals
              fprintf(port " %s" netMap[tm~>name])
            )
          )
        )

        fprintf(port " %s" inst~>cellName)
        let((cdfId)
          cdfId = errset(cdfGetInstCDF(inst))
          when(and(cdfId car(cdfId))
            foreach(param car(cdfId)~>parameters
              when(member(param~>name '("w" "l" "W" "L" "nf" "nfin" "nFin" "m" "fingers" "lr" "wr"))
                let((pval)
                  pval = sprintf(nil "%A" param~>value)
                  unless(rexMatchp("iPar" pval)
                    fprintf(port " %s=%s" param~>name pval)
                  )
                )
              )
            )
          )
        )
        fprintf(port "\n")
        nDev = nDev + 1
      )
    )

    fprintf(port ".ENDS %s\n" cell)
    close(port)
    dbClose(cv)
    printf("[AmpSys] Exported %d instances -> %s\n" nDev outFile)
    t
  )
)

procedure(ampsysLaunchGUI(projectFile netlistFile lib cell view)
  let((cmd)
    cmd = sprintf(nil "%s \"%s/ampsys_gui.py\" --project \"%s\" --netlist \"%s\" --cadence-lib \"%s\" --cadence-cell \"%s\" --cadence-view \"%s\"%s"
      ampsys_pycmd ampsys_cli projectFile netlistFile lib cell view ampsys_launch_suffix)
    printf("[AmpSys] Launching GUI:\n  %s\n" cmd)
    errset(system(cmd))
  )
)

procedure(ampsysExtractCurrentSchematic()
  let((cv lib cell view runDir netlist project)
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
    when(ampsysExportNetlist(lib cell view netlist)
      ampsysLaunchGUI(project netlist lib cell view)
      ampsysStartApplyPoll(runDir)
    )
  )
)

procedure(ampsysOpenGUIOnly()
  let((cv lib cell view runDir project netlist)
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
      when(ampsysExportNetlist(lib cell view netlist)
        ampsysLaunchGUI(project netlist lib cell view)
      )
      ampsysStartApplyPoll(runDir)
    else
      project = strcat(ampsys_work "/ampsys_project.json")
      ampsysEnsureDir(ampsys_work)
      ampsysLaunchGUI(project "" "" "" "schematic")
    )
  )
)

printf("  [AmpSys] Extract loaded.\n")
