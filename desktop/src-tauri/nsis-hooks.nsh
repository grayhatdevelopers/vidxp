!macro NSIS_HOOK_PREINSTALL
  ; Keep per-user program files separate from VidXP's shared models and indexes.
  StrCpy $INSTDIR "$LOCALAPPDATA\Programs\${PRODUCTNAME}"
  SetOutPath "$INSTDIR"
!macroend
