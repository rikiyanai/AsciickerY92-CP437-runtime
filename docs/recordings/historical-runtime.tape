Output docs/historical-runtime.gif

Require sh

Set Shell "zsh"
Set FontSize 18
Set Width 1200
Set Height 760
Set TypingSpeed 20ms
Set Theme "Catppuccin Mocha"

Type "./build.sh" Enter
Sleep 3s
Type "./run-runtime.sh --verify-corpus" Enter
Sleep 3s
Type "./run-runtime.sh" Enter
Sleep 3s
Type "lln"
Sleep 2s
Type "j"
Sleep 2s
Type "q"
Sleep 2s
