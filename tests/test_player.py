import importlib.util
import json
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location('player', Path(__file__).resolve().parents[1] / 'tools/player.py')
player = importlib.util.module_from_spec(spec)
spec.loader.exec_module(player)


def test_default_game_uses_classic_binary_and_owned_workspace():
    args, cwd, env = player.command('play')
    assert Path(args[0]) == player.WORKSPACE / '.run/game_classic'
    assert args[1:] == ['--auto-start-map', str(player.WORKSPACE / 'assets/a3d/game_map_y8.a3d')]
    assert cwd == player.WORKSPACE


def test_numabe_uses_current_map_and_matching_mesh_and_train_roots():
    args, cwd, env = player.command('numabe')
    assert args[-1] == str(player.LIVE_MAP / 'output.a3d')
    assert env['ASCIICKER_ACTIVE_MESH_ROOT'] == str(player.LIVE_MAP / 'meshes')
    assert env['ASCIICKER_TRAIN_ROUTES'] == str(player.LIVE_MAP / 'output.trains.json')


def test_editor_uses_installed_environment_and_utf8():
    args, cwd, env = player.command('editor')
    assert args[:3] == [str(player.REPO / '.venv/bin/python'), '-X', 'utf8']
    assert args[-1] == 'editor'
    assert env['PYTHONUTF8'] == '1'


def test_missing_runtime_reports_retry_without_starting_child(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(player, 'REPO', tmp_path)
    monkeypatch.setattr(player, 'WORKSPACE', tmp_path / 'numabe')
    assert player.main(['--check']) == 1
    assert 'Run the installer again' in capsys.readouterr().err


def test_no_arguments_starts_game(monkeypatch):
    actions = []
    monkeypatch.setattr(player, 'run', lambda action: actions.append(action) or 0)
    assert player.main([]) == 0
    assert actions == ['play']


def test_launcher_can_quit_without_starting_game(monkeypatch):
    monkeypatch.setattr('builtins.input', lambda _: 'q')
    monkeypatch.setattr(player, 'run', lambda _: pytest.fail('Quit must not launch a game'))
    assert player.main(['launcher']) == 0


def test_launcher_routes_selection_and_returns_to_menu(monkeypatch):
    answers = iter(['2', 'q'])
    actions = []
    monkeypatch.setattr('builtins.input', lambda _: next(answers))
    monkeypatch.setattr(player, 'run', lambda action: actions.append(action) or 0)
    assert player.main(['launcher']) == 0
    assert actions == ['numabe']


def test_check_flag_is_noninteractive(monkeypatch, capsys):
    monkeypatch.setattr(player, 'check', lambda: {'status': 'ready'})
    assert player.main(['--check']) == 0
    assert json.loads(capsys.readouterr().out) == {'status': 'ready'}
