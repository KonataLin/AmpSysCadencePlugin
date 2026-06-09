; ============================================================
; AmpSys Cadence Plugin - Menus
; ============================================================

procedure(ampsysAddMenu(win)
  let((m1 m2 m3 pm)
    when(and(win not(member(win _ampsysTagged)))
      m1 = hiCreateMenuItem(?name gensym("ampsysM") ?itemText "Open AmpSys GUI" ?callback "ampsysOpenGUIOnly()")
      m2 = hiCreateMenuItem(?name gensym("ampsysM") ?itemText "Extract Current Schematic..." ?callback "ampsysExtractCurrentSchematic()")
      m3 = hiCreateMenuItem(?name gensym("ampsysM") ?itemText "Apply Last Result" ?callback "ampsysApplyLastResult()")
      pm = hiCreatePulldownMenu(gensym("ampsysPM") "AmpSys" list(m1 m2 m3))
      hiInsertBannerMenu(win pm hiGetNumMenus(win))
      _ampsysTagged = cons(win _ampsysTagged)
      printf("[AmpSys] Menu added to window %L\n" win)
    )
  )
)

errset(ampsysAddMenu(hiGetCIWindow()))

procedure(_ampsysScanWindows()
  let((ciw)
    ciw = hiGetCIWindow()
    foreach(win hiGetWindowList()
      when(win
        unless(or(equal(win ciw) member(win _ampsysTagged))
          errset(ampsysAddMenu(win))
        )
      )
    )
  )
  hiRegTimer("_ampsysScanWindows()" 2)
)

unless(_ampsysMenuTimerOn
  _ampsysMenuTimerOn = t
  hiRegTimer("_ampsysScanWindows()" 2)
  printf("[AmpSys] Menu timer started.\n")
)

errset(hiSetBindKeys("Schematics" list(
  list("Ctrl Shift<Key>a" "ampsysExtractCurrentSchematic()")
)))

printf("  [AmpSys] Menu loaded. Shortcut: Ctrl+Shift+A in Schematics.\n")
