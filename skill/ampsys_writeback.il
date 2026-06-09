; ============================================================
; AmpSys Cadence Plugin - Sizing writeback
; ============================================================

defvar(ampsys_result_devices nil)
defvar(ampsys_result_lib "")
defvar(ampsys_result_cell "")
defvar(ampsys_result_view "schematic")
defvar(ampsys_writeback_width_mode "auto")
defvar(ampsys_writeback_finger_aliases nil)

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

procedure(ampsysFindCDFParam(inst pname)
  let((cdfId param)
    cdfId = nil
    param = nil
    errset(cdfId = cdfGetInstCDF(inst))
    when(cdfId
      param = cdfFindParamByName(cdfId pname)
    )
    param
  )
)

procedure(ampsysAnyParamExists(inst names)
  let((found)
    found = nil
    when(listp(names)
      foreach(pname names
        unless(found
          when(ampsysFindCDFParam(inst pname)
            found = t
          )
        )
      )
    )
    found
  )
)

procedure(ampsysSetInstParam(inst pname pval)
  let((param ok)
    ok = nil
    param = ampsysFindCDFParam(inst pname)
    when(param
      param~>value = pval
      ok = t
      when(param~>callback
        errset(evalstring(param~>callback))
      )
    )
    unless(ok
      when(pname
        errset(dbReplaceProp(inst pname "string" pval))
        ok = t
      )
    )
    ok
  )
)

procedure(ampsysSetBestInstParam(inst aliases pval)
  let((chosen)
    chosen = nil
    when(listp(aliases)
      foreach(pname aliases
        unless(chosen
          when(ampsysFindCDFParam(inst pname)
            chosen = pname
          )
        )
      )
      unless(chosen
        chosen = car(aliases)
      )
    )
    when(chosen
      ampsysSetInstParam(inst chosen pval)
    )
  )
)

procedure(ampsysApplyParamAction(inst action)
  let((kind aliases value useFinger)
    kind = car(action)
    cond(
      ((equal(kind "width"))
        aliases = cadddr(action)
        if(equal(ampsys_writeback_width_mode "finger")
        then
          useFinger = t
        else
          if(equal(ampsys_writeback_width_mode "total")
          then
            useFinger = nil
          else
            useFinger = ampsysAnyParamExists(inst ampsys_writeback_finger_aliases)
          )
        )
        value = if(useFinger then cadr(action) else caddr(action))
        ampsysSetBestInstParam(inst aliases value)
      )
      ((equal(kind "length"))
        ampsysSetBestInstParam(inst caddr(action) cadr(action))
      )
      ((equal(kind "fingers"))
        ampsysSetBestInstParam(inst caddr(action) cadr(action))
      )
      ((equal(kind "multiplier"))
        ampsysSetBestInstParam(inst caddr(action) cadr(action))
      )
      ((equal(kind "value"))
        ampsysSetBestInstParam(inst caddr(action) cadr(action))
      )
      (t
        ampsysSetInstParam(inst car(action) cadr(action))
      )
    )
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
      dbClose(cv)
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
            ampsysApplyParamAction(inst pair)
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
    ampsys_result_view = "schematic"
    ampsys_writeback_width_mode = "auto"
    ampsys_writeback_finger_aliases = nil
    unless(car(errset(load(resultFile)))
      ampsysWarn(sprintf(nil "[AmpSys] Could not load result file:\n%s" resultFile))
      return(nil)
    )
    cv = ampsysCurrentCV()
    if(and(boundp('ampsys_result_lib) nequal(ampsys_result_lib ""))
    then
      lib = ampsys_result_lib
      cell = ampsys_result_cell
      view = if(and(boundp('ampsys_result_view) nequal(ampsys_result_view "")) then ampsys_result_view else "schematic")
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
        if(isFile(resultFile)
        then
          unless(ampsysApplyResultFile(resultFile)
            printf("[AmpSys] Apply request was not completed. Check result file: %s\n" resultFile)
          )
        else
          printf("[AmpSys] Apply request ignored because result file does not exist yet: %s\n" resultFile)
        )
      )
    )
  )
  hiRegTimer("ampsysApplyPoll()" 2)
)

printf("  [AmpSys] Writeback loaded.\n")
