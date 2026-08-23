SHELL := /bin/bash
NPROC := $(shell nproc 2>/dev/null || sysctl -n hw.ncpu 2>/dev/null || echo 4)
UNAME_S := $(shell uname -s)

ifeq ($(UNAME_S),Darwin)
  PLATFORM_SUFFIX := _mac
else
  PLATFORM_SUFFIX :=
endif

.PHONY: help server terminal web test clean \
        run-server run-terminal serve-web

help:
	@echo "Asciicker Y9-2 Runtime"
	@echo "  make server        Build .run/server"
	@echo "  make terminal      Build .run/game_term"
	@echo "  make web           Build .web browser artifacts"
	@echo "  make test          Run standalone contract checks"
	@echo "  make run-server    Start the authoritative server"
	@echo "  make run-terminal  Start the terminal client"
	@echo "  make serve-web     Serve .web at http://127.0.0.1:8765"
	@echo "  make clean         Remove generated build artifacts"

server:
	@$(MAKE) -f makefile_server -j$(NPROC)

terminal:
	@$(MAKE) -f makefile_game_term$(PLATFORM_SUFFIX) -j$(NPROC)

web:
	@./build-web.sh

test:
	@./scripts/run_standalone_checks.sh

run-server:
	@[ -x .run/server ] || { echo "Server not built; run: make server"; exit 1; }
	@./.run/server

run-terminal:
	@[ -x .run/game_term ] || { echo "Terminal client not built; run: make terminal"; exit 1; }
	@./.run/game_term

serve-web:
	@test -f .web/index.html || { echo "Web client not built; run: make web"; exit 1; }
	@python3 -m http.server 8765 --bind 127.0.0.1 --directory .web

clean:
	@./clean.sh
	@rm -rf .web .tmp-ems
