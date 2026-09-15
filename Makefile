PYTHON ?= python3

.PHONY: check shims test e2e smoke smoke-gui sync sync-apply updates

## check: everything that must pass before and after a core re-vendor
check: shims test smoke

## shims: every core symbol linuxport touches still exists (AST, no imports)
shims:
	$(PYTHON) tools/check_shims.py --windows

## test: unit tests for the Linux layer (pure Python, temp dirs, no Qt)
test:
	$(PYTHON) -m pytest tests

## e2e: the end-to-end tests only -- a whole install, its removal, and the CLI
e2e:
	$(PYTHON) -m pytest tests/test_e2e.py

## smoke: headless run of the Linux layer against a fixture game
smoke:
	$(PYTHON) tools/smoke.py

## smoke-gui: the same, plus the PySide6 window rendered offscreen
smoke-gui:
	QT_QPA_PLATFORM=offscreen $(PYTHON) tools/smoke.py --gui

## sync: compare core/ with the newest upstream tag; changes nothing
sync:
	$(PYTHON) tools/sync_upstream.py $(if $(TAG),--tag $(TAG)) $(if $(FROM),--from $(FROM))

## sync-apply: replace core/ with the upstream tag and rewrite UPSTREAM
sync-apply:
	$(PYTHON) tools/sync_upstream.py --apply $(if $(TAG),--tag $(TAG)) $(if $(FROM),--from $(FROM))

## updates: what the eight route repositories released (existing script)
updates:
	$(PYTHON) scan_updates.py --notes
