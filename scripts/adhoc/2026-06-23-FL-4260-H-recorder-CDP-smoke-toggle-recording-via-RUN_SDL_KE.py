# Ad hoc script: FL-4260 H-recorder CDP smoke: toggle recording via RUN_SDL_KEY(H=11), move camera to force TERM++ cell deltas, toggle off
# Created: 2026-06-23
# Canonical gap: <describe what tool should own this>

import sys, time
sys.path.insert(0, 'scripts')
from fl4260_cdp_audit import send_cdp

def step(cmd, params=None, wait=1.5):
    r = send_cdp(cmd, params)
    print(f">> {cmd} {params or ''} -> {str(r.get('result'))[:160]}")
    time.sleep(wait)
    return r

step('OPEN_TERMPP', wait=2.5)
step('RUN_SDL_KEY', '11', wait=2.5)             # H -> toggle recording ON
step('SET_CAMERA', '64 64 40960 90 30', wait=2.0)  # rotate view -> cell changes
step('SET_CAMERA', '70 70 40960 120 35', wait=2.0) # move/rotate more -> cell changes
step('SET_CAMERA', '64 64 40960 45 30', wait=2.0)  # back -> cell changes
step('RUN_SDL_KEY', '11', wait=2.5)             # H -> toggle recording OFF
print("driver done")
