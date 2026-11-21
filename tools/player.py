#!/usr/bin/env python3
"""Player entry point shared by the Windows installer and Linux command."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

REPO = Path(__file__).resolve().parents[1]
WORKSPACE = REPO / 'numabe'
LIVE_MAP = WORKSPACE / '.run/fl4876_builds/live_numabe'


def command(action: str) -> tuple[list[str], Path, dict[str, str]]:
    env = os.environ.copy()
    env['PYTHONUTF8'] = '1'
    if action == 'editor':
        python = REPO / '.venv/bin/python'
        return ([str(python), '-X', 'utf8', str(REPO / 'tools/numabe/run.py'), 'editor'], REPO, env)
    game = WORKSPACE / '.run/game_classic'
    world = WORKSPACE / 'assets/a3d/game_map_y8.a3d'
    if action == 'numabe':
        world = LIVE_MAP / 'output.a3d'
        env['ASCIICKER_ACTIVE_MESH_ROOT'] = str(LIVE_MAP / 'meshes')
        env['ASCIICKER_TRAIN_ROUTES'] = str(LIVE_MAP / 'output.trains.json')
    return ([str(game), '--auto-start-map', str(world)], WORKSPACE, env)


def check() -> dict:
    required = [WORKSPACE / '.run/game_classic', WORKSPACE / '.run/server',
                WORKSPACE / 'assets/a3d/game_map_y8.a3d',
                WORKSPACE / 'assets/images/menu.png',
                WORKSPACE / 'assets/sprites', REPO / '.venv/bin/python']
    missing = [str(path.relative_to(REPO)) for path in required if not path.exists()]
    if missing:
        raise RuntimeError('Installation is incomplete: ' + ', '.join(missing) + '. Run the installer again.')
    for name in ('game_classic', 'server'):
        result = subprocess.run(['ldd', str(WORKSPACE / '.run' / name)], capture_output=True, text=True)
        if result.returncode or 'not found' in result.stdout:
            raise RuntimeError(f'{name} has missing system libraries. Run the installer again.')
    return {'status': 'ready', 'classic': True, 'local_server': True, 'workspace': str(WORKSPACE)}


def run(action: str) -> int:
    check()
    args, cwd, env = command(action)
    result = subprocess.call(args, cwd=cwd, env=env)
    return result if result >= 0 else 128 - result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='Play Asciicker Classic. No arguments starts the game.')
    parser.add_argument('action', nargs='?', default='play', choices=('play', 'launcher', 'numabe', 'editor', 'check'))
    parser.add_argument('--check', action='store_true', help='Check the installation without opening a window.')
    args = parser.parse_args(argv)
    try:
        if args.check or args.action == 'check':
            print(json.dumps(check()))
            return 0
        if args.action != 'launcher':
            return run(args.action)
        while True:
            print('\nAsciicker Classic\n  1  Play Classic\n  2  Walk the Numabe map\n  3  Open the Numabe map editor\n  q  Quit')
            selection = input('Choose [1]: ').strip().lower()
            if selection in ('q', 'quit', 'exit'):
                return 0
            action = {'': 'play', '1': 'play', '2': 'numabe', '3': 'editor'}.get(selection)
            if action:
                result = run(action)
                if result:
                    print(f'Exited with code {result}.')
            else:
                print('Choose 1, 2, 3 or q.')
    except (OSError, RuntimeError) as error:
        print(f'Asciicker: {error}', file=sys.stderr)
        return 1
    except (KeyboardInterrupt, EOFError):
        return 0


if __name__ == '__main__':
    raise SystemExit(main())
