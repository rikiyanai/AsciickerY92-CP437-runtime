"""Run all startup animations in sequence, looping forever.

Shows a brief label between each. Press Ctrl-C to exit.

Run: python3 scripts/run_all.py
"""
from __future__ import annotations
import sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent.parent))

import anim_A_autosplash as A
import anim_B_rainbow    as B
import anim_B_hellish    as BH
import anim_B_cool       as BC
import anim_C_pulse      as C
import anim_D_scanline   as D
import anim_E_throbber   as E
import anim_F_fire       as F
import anim_G_sprites    as G
import anim_H_scanfire   as H

BOLD  = "\033[1m"
CYAN  = "\033[36m"
DIM   = "\033[2m"
RESET = "\033[0m"

SEQUENCE = [
    ("A  auto-splash",           A.main),
    ("B  rainbow (full)",        B.main),
    ("B  rainbow (hellish)",     BH.main),
    ("B  rainbow (cool)",        BC.main),
    ("C  CRT pulse",             C.main),
    ("D  scanline",              D.main),
    ("E  throbber",              E.main),
    ("F  ASCII fire bg",         F.main),
    ("G  sprite overlay",        G.main),
    ("H  scanline → fire",       H.main),
]


def _label(name: str) -> None:
    print(f"\n{BOLD}{CYAN}━━━ {name} ━━━{RESET}  {DIM}(q/Ctrl-C to quit){RESET}")
    time.sleep(0.4)


try:
    while True:
        for name, fn in SEQUENCE:
            _label(name)
            fn()
            time.sleep(0.15)
except KeyboardInterrupt:
    print(f"\n{DIM}bye{RESET}")
