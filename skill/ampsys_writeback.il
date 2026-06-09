; ============================================================
; AmpSys Cadence Plugin - Sizing writeback
; ============================================================

defvar(ampsys_result_devices nil)
defvar(ampsys_result_lib "")
defvar(ampsys_result_cell "")

procedure(ampsysFindInstance(cv instName)
  let((found)
    found = nil
    foreach(inst cv~>instances
      when(equal(inst~>name instName)
        found = inst
      )
    )
    found
  )
)

procedure(ampsysSetInstParam(inst pname pval)
  let((cdfId param ok)
    ok = nil
    cdfId = nil
    errset(cdfId = cdfGetInstCDF(inst))
    when(cdfId
      param = cdfFindParamByName(cdfId pname)
      when(param
        param~>value = pval
        ok = t
        when(param~>callback
          errset(evalstring(param~>callback))
        )
      )
    )
    unless(ok
      errset(dbReplaceProp(inst pname "string" pval))
    )
    ok
  )
)

procedure(ampsysApplyResultToCell(lib cell view)
  let((cv applied missed)
    cv = dbOpenCellViewByType(lib cell view nil "a")
    unless(cv
      ampsysWarn(sprintf(nil "[AmpSys] Cannot open %s/%s/%s for edit." lib cell view))
      return(nil)
    )
    unless(listp(ampsys_result_devices)
      ampsysWarn("[AmpSys] Result file has no device records. Run optimization first.")
      return(nil)
    )
    applied = 0
    missed = 0
    foreach(entry ampsys_result_devices
      let((inst params instName)
        instName = car(entry)
        params = cadr(entry)
        inst = ampsysFindInstance(cv instName)
        if(inst
        then
          foreach(pair params
            ampsysSetInstParam(inst car(pair) cadr(pair))
          )
          applied = applied + 1
          printf("[AmpSys] Applied %s\n" instName)
        else
          missed = missed + 1
          printf("[AmpSys] WARN: instance %s not found\n" instName)
        )
      )
    )
    dbSave(cv)
    printf("[AmpSys] Writeback complete: applied=%d missed=%d saved=%s/%s/%s\n"
      applied missed lib cell view)
    t
  )
)

procedure(ampsysApplyResultFile(resultFile)
  let((cv lib cell view)
    unless(isFile(resultFile)
      ampsysWarn(sprintf(nil "[AmpSys] No result to apply yet.\nExpected file:\n%s\n\nRun optimization in the AmpSys GUI, then click Confirm and Apply in Cadence." resultFile))
      return(nil)
    )
    ampsys_result_devices = nil
    ampsys_result_lib = ""
    ampsys_result_cell = ""
    unless(car(errset(load(resultFile)))
      ampsysWarn(sprintf(nil "[AmpSys] Could not load result file:\n%s" resultFile))
      return(nil)
    )
    cv = ampsysCurrentCV()
    if(and(boundp('ampsys_result_lib) nequal(ampsys_result_lib ""))
    then
      lib = ampsys_result_lib
      cell = ampsys_result_cell
      view = "schematic"
    else
      unless(cv
        ampsysWarn("[AmpSys] No current schematic and result has no lib/cell.")
        return(nil)
      )
      lib = cv~>libName
      cell = cv~>cellName
      view = cv~>viewName
    )
    ampsysApplyResultToCell(lib cell view)
  )
)

procedure(ampsysApplyLastResult()
  let((cv runDir resultFile)
    cv = ampsysCurrentCV()
    unless(cv
      ampsysWarn("[AmpSys] Open the target schematic first.")
      return(nil)
    )
    ampsysSetCurrentFromCV(cv)
    runDir = ampsysProjectDir(cv~>libName cv~>cellName)
    resultFile = strcat(runDir "/ampsys_result.il")
    ampsysApplyResultFile(resultFile)
  )
)

procedure(ampsysStartApplyPoll(runDir)
  unless(member(runDir _ampsysPollDirs)
    _ampsysPollDirs = cons(runDir _ampsysPollDirs)
  )
  unless(_ampsysPollTimerOn
    _ampsysPollTimerOn = t
    hiRegTimer("ampsysApplyPoll()" 2)
    printf("[AmpSys] Apply poll started for %s\n" runDir)
  )
)

procedure(ampsysApplyPoll()
  foreach(runDir _ampsysPollDirs
    let((flag resultFile)
      flag = strcat(runDir "/apply.request")
      resultFile = strcat(runDir "/ampsys_result.il")
      when(isFile(flag)
        printf("[AmpSys] Apply request detected: %s\n" flag)
        ampsysDeleteFile(flag)
        unless(ampsysApplyResultFile(resultFile)
          printf("[AmpSys] Apply request was not completed. Check result file: %s\n" resultFile)
        )
      )
    )
  )
  hiRegTimer("ampsysApplyPoll()" 2)
)

printf("  [AmpSys] Writeback loaded.\n")
