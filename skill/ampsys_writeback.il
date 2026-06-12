; ============================================================
; AmpSys Cadence Plugin - Sizing writeback
; ============================================================

defvar(ampsys_result_devices nil)
defvar(ampsys_result_lib "")
defvar(ampsys_result_cell "")
defvar(ampsys_result_view "schematic")
defvar(ampsys_writeback_width_mode "auto")
defvar(ampsys_writeback_finger_width_aliases nil)
defvar(ampsys_writeback_finger_aliases nil)

procedure(ampsysFindInstance(cv instName)
  let((found)
    found = nil
    ampsysLog(sprintf(nil "Find instance start: target=%s in=%s/%s/%s" instName cv~>libName cv~>cellName cv~>viewName))
    foreach(inst cv~>instances
      when(equal(inst~>name instName)
        found = inst
      )
    )
    if(found
    then
      ampsysLog(sprintf(nil "Find instance ok: target=%s lib=%s cell=%s" instName found~>libName found~>cellName))
    else
      ampsysLog(sprintf(nil "Find instance missed: target=%s" instName))
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

procedure(ampsysFindCDFParamByPrompt(inst pattern)
  let((cdfId found param prompt pname)
    cdfId = nil
    found = nil
    errset(cdfId = cdfGetInstCDF(inst))
    when(cdfId
      foreach(param cdfId~>parameters
        unless(found
          prompt = if(param~>prompt then param~>prompt else "")
          pname = if(param~>name then param~>name else "")
          when(or(rexMatchp(pattern prompt) rexMatchp(pattern pname))
            ampsysLog(sprintf(nil "Prompt alias exists: inst=%s name=%s prompt=%s pattern=%s"
              inst~>name pname prompt pattern))
            found = param
          )
        )
      )
    )
    unless(found
      ampsysLog(sprintf(nil "Prompt alias missing: inst=%s pattern=%s" inst~>name pattern))
    )
    found
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

procedure(ampsysSetCDFParamObject(inst param pval)
  let((pname ok)
    ok = nil
    when(param
      pname = param~>name
      ampsysLog(sprintf(nil "Write CDF param object: inst=%s param=%s prompt=%s value=%s"
        inst~>name pname param~>prompt pval))
      param~>value = pval
      ok = t
      when(param~>callback
        ampsysLog(sprintf(nil "Run CDF callback: inst=%s param=%s callback=%s"
          inst~>name pname param~>callback))
        errset(evalstring(param~>callback))
      )
    )
    ok
  )
)

procedure(ampsysSetInstParam(inst pname pval)
  let((param ok)
    ok = nil
    param = ampsysFindCDFParam(inst pname)
    when(param
      ampsysLog(sprintf(nil "Write CDF param: inst=%s param=%s value=%s" inst~>name pname pval))
      param~>value = pval
      ok = t
      when(param~>callback
        ampsysLog(sprintf(nil "Run CDF callback: inst=%s param=%s callback=%s" inst~>name pname param~>callback))
        errset(evalstring(param~>callback))
      )
    )
    unless(ok
      when(pname
        ampsysLog(sprintf(nil "Write db property fallback: inst=%s prop=%s value=%s" inst~>name pname pval))
        errset(dbReplaceProp(inst pname "string" pval))
        ok = t
      )
    )
    ok
  )
)

procedure(ampsysSetExistingInstParam(inst aliases pval)
  let((chosen ok)
    chosen = nil
    ok = nil
    ampsysLog(sprintf(nil "Choose existing writeback alias: inst=%s aliases=%L value=%s" inst~>name aliases pval))
    when(listp(aliases)
      foreach(pname aliases
        unless(chosen
          if(ampsysFindCDFParam(inst pname)
          then
            ampsysLog(sprintf(nil "Alias exists: inst=%s alias=%s" inst~>name pname))
            chosen = pname
          else
            ampsysLog(sprintf(nil "Alias missing: inst=%s alias=%s" inst~>name pname))
          )
        )
      )
    )
    when(chosen
      ampsysLog(sprintf(nil "Chosen existing writeback alias: inst=%s alias=%s value=%s" inst~>name chosen pval))
      ok = ampsysSetInstParam(inst chosen pval)
    )
    ok
  )
)

procedure(ampsysSetBestInstParam(inst aliases pval)
  let((chosen)
    chosen = nil
    ampsysLog(sprintf(nil "Choose writeback alias: inst=%s aliases=%L value=%s" inst~>name aliases pval))
    when(listp(aliases)
      foreach(pname aliases
        unless(chosen
          if(ampsysFindCDFParam(inst pname)
          then
            ampsysLog(sprintf(nil "Alias exists: inst=%s alias=%s" inst~>name pname))
            chosen = pname
          else
            ampsysLog(sprintf(nil "Alias missing: inst=%s alias=%s" inst~>name pname))
          )
        )
      )
      unless(chosen
        chosen = car(aliases)
      )
    )
    when(chosen
      ampsysLog(sprintf(nil "Chosen writeback alias: inst=%s alias=%s value=%s" inst~>name chosen pval))
      ampsysSetInstParam(inst chosen pval)
    )
  )
)

procedure(ampsysSetFingerWidthParam(inst fingerAliases fallbackAliases pval)
  let((param ok)
    ok = nil
    param = ampsysFindCDFParamByPrompt(inst "[Ff]inger.*[Ww]idth")
    when(param
      ok = ampsysSetCDFParamObject(inst param pval)
    )
    unless(ok
      ok = ampsysSetExistingInstParam(inst fingerAliases pval)
    )
    unless(ok
      ampsysLog(sprintf(nil "Finger width CDF param not found; fallback to generic width aliases: inst=%s value=%s"
        inst~>name pval))
      ok = ampsysSetBestInstParam(inst fallbackAliases pval)
    )
    ok
  )
)

procedure(ampsysSetTotalWidthParam(inst aliases pval)
  let((param ok)
    ok = nil
    param = ampsysFindCDFParamByPrompt(inst "[Tt]otal.*[Ww]idth")
    when(param
      ok = ampsysSetCDFParamObject(inst param pval)
    )
    unless(ok
      ok = ampsysSetBestInstParam(inst aliases pval)
    )
    ok
  )
)

procedure(ampsysSetExplicitTotalWidthParam(inst pval)
  let((param ok)
    ok = nil
    param = ampsysFindCDFParamByPrompt(inst "[Tt]otal.*[Ww]idth")
    when(param
      ok = ampsysSetCDFParamObject(inst param pval)
    )
    unless(ok
      ampsysLog(sprintf(nil "Explicit total width CDF param not found; skip generic fallback: inst=%s value=%s"
        inst~>name pval))
    )
    ok
  )
)

procedure(ampsysApplyParamAction(inst action)
  let((kind aliases value useFinger)
    kind = car(action)
    ampsysLog(sprintf(nil "Apply param action: inst=%s action=%L" inst~>name action))
    case(kind
      ("width"
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
        ampsysLog(sprintf(nil "Width writeback decision: inst=%s mode=%s useFinger=%L value=%s fingerValue=%s totalValue=%s"
          inst~>name ampsys_writeback_width_mode useFinger value cadr(action) caddr(action)))
        if(useFinger
        then
          ampsysSetFingerWidthParam(inst ampsys_writeback_finger_width_aliases aliases value)
          ampsysSetExplicitTotalWidthParam(inst caddr(action))
        else
          ampsysSetTotalWidthParam(inst aliases value)
        )
      )
      ("length"
        ampsysSetBestInstParam(inst caddr(action) cadr(action))
      )
      ("fingers"
        ampsysSetBestInstParam(inst caddr(action) cadr(action))
      )
      ("multiplier"
        ampsysSetBestInstParam(inst caddr(action) cadr(action))
      )
      ("value"
        ampsysSetBestInstParam(inst caddr(action) cadr(action))
      )
      (t
        ampsysSetInstParam(inst car(action) cadr(action))
      )
    )
  )
)

procedure(ampsysApplyResultToCell(lib cell view)
  let((cv applied missed nRecords)
    nRecords = if(listp(ampsys_result_devices) then length(ampsys_result_devices) else 0)
    ampsysLog(sprintf(nil "Apply result to cell start: lib=%s cell=%s view=%s records=%d"
      lib cell view nRecords))
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
          ampsysLog(sprintf(nil "WARN: instance %s not found during writeback" instName))
        )
      )
    )
    dbSave(cv)
    ampsysLog(sprintf(nil "Writeback complete: applied=%d missed=%d saved=%s/%s/%s"
      applied missed lib cell view))
    t
  )
)

procedure(ampsysApplyResultFile(resultFile)
  let((cv lib cell view nRecords)
    ampsysLog(sprintf(nil "Apply result file start: %s" resultFile))
    unless(isFile(resultFile)
      ampsysWarn(sprintf(nil "[AmpSys] No result to apply yet.\nExpected file:\n%s\n\nRun optimization in the AmpSys GUI, then click Confirm and Apply in Cadence." resultFile))
      return(nil)
    )
    ampsys_result_devices = nil
    ampsys_result_lib = ""
      ampsys_result_cell = ""
      ampsys_result_view = "schematic"
      ampsys_writeback_width_mode = "auto"
      ampsys_writeback_finger_width_aliases = nil
      ampsys_writeback_finger_aliases = nil
    unless(car(errset(load(resultFile)))
      ampsysWarn(sprintf(nil "[AmpSys] Could not load result file:\n%s" resultFile))
      return(nil)
    )
    nRecords = if(listp(ampsys_result_devices) then length(ampsys_result_devices) else 0)
    ampsysLog(sprintf(nil "Result file loaded: records=%d lib=%s cell=%s view=%s widthMode=%s fingerWidthAliases=%L fingerAliases=%L"
      nRecords ampsys_result_lib ampsys_result_cell ampsys_result_view ampsys_writeback_width_mode ampsys_writeback_finger_width_aliases ampsys_writeback_finger_aliases))
    when(nRecords <= 0
      ampsysWarn(sprintf(nil "[AmpSys] Result file has 0 sizing records:\n%s\n\nRun optimization again before applying." resultFile))
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
    ampsysLog("Menu action: Apply Last Result")
    cv = ampsysCurrentCV()
    unless(cv
      ampsysWarn("[AmpSys] Open the target schematic first.")
      return(nil)
    )
    ampsysSetCurrentFromCV(cv)
    runDir = ampsysProjectDir(cv~>libName cv~>cellName)
    resultFile = strcat(runDir "/ampsys_result.il")
    ampsysLog(sprintf(nil "Apply Last Result paths: runDir=%s resultFile=%s" runDir resultFile))
    ampsysApplyResultFile(resultFile)
  )
)

procedure(ampsysStartApplyPoll(runDir)
  ampsysLog(sprintf(nil "Start apply poll requested: runDir=%s" runDir))
  unless(member(runDir _ampsysPollDirs)
    _ampsysPollDirs = cons(runDir _ampsysPollDirs)
  )
  unless(_ampsysPollTimerOn
    _ampsysPollTimerOn = t
    hiRegTimer("ampsysApplyPoll()" 2)
    ampsysLog(sprintf(nil "Apply poll started for %s" runDir))
  )
)

procedure(ampsysApplyPoll()
  foreach(runDir _ampsysPollDirs
    let((flag resultFile)
      flag = strcat(runDir "/apply.request")
      resultFile = strcat(runDir "/ampsys_result.il")
      when(isFile(flag)
        ampsysLog(sprintf(nil "Apply request detected: flag=%s resultFile=%s" flag resultFile))
        ampsysDeleteFile(flag)
        if(isFile(resultFile)
        then
          unless(ampsysApplyResultFile(resultFile)
            ampsysLog(sprintf(nil "Apply request was not completed. Check result file: %s" resultFile))
          )
        else
          ampsysLog(sprintf(nil "Apply request ignored because result file does not exist yet: %s" resultFile))
        )
      )
    )
  )
  hiRegTimer("ampsysApplyPoll()" 2)
)

ampsysLog("Writeback loaded.")
