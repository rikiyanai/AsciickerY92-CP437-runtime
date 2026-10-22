# Numabe's migrated Classic workspace. Never builds in, or launches from, Y9.
SHELL := /bin/bash
NPROC ?= 4
MAC_SUFFIX := $(if $(filter Darwin,$(shell uname -s)),_mac,)
.PHONY: editor game server web-classic test source-verify run-game
editor:
	python3 artifacts/experiments/fl4876_manual_world/map_editor.py --port 8792 --build .run/fl4876_builds/live_numabe
game:
	$(MAKE) -f apps/game-classic/makefile_game_classic$(MAC_SUFFIX) -j$(NPROC)
server:
	$(MAKE) -f makefile_server -j$(NPROC)
web-classic:
	bash apps/game-classic/build_web_classic.sh
test:
	python3 -m pytest -q -p no:cacheprovider scripts/tests/test_fl4876_a3d_edit.py scripts/tests/test_a3d_terrain_extend.py scripts/tests/test_a3d_terrain_flatten.py scripts/tests/test_a3d_terrain_paint.py scripts/tests/test_numabe_source_manifest.py scripts/tests/test_fl4809_connected_product_scope.py scripts/tests/test_fl4876_station_track_card_fix.py -k 'not test_connected_scope_source_manifest_verifies'
source-verify:
	python3 -m scripts.pipeline.source_manifest --verify assets/source_bundles/numabe_connected_neighborhood_scope_20260915/source_manifest.json
run-game:
	python3 ../tools/numabe/run.py game
