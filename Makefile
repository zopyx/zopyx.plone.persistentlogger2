PYTHON ?= 3.12
UV ?= uv
PACKAGE := zopyx.plone.persistentlogger

.PHONY: install test coverage check build clean bootstrap-demo

install:
	$(UV) sync --extra test

test:
	$(UV) run --extra test pytest -q --cov=$(PACKAGE) --cov-branch --cov-report=term-missing --cov-report=xml --cov-fail-under=99

coverage:
	$(UV) run --extra test pytest --cov=$(PACKAGE) --cov-branch --cov-report=term-missing --cov-report=xml --cov-fail-under=99

check:
	$(UV) run python -m compileall -q zopyx scripts
	$(UV) run python -c "from pathlib import Path; from xml.etree import ElementTree; [ElementTree.parse(p) for p in Path('zopyx').rglob('*.xml')]; [ElementTree.parse(p) for p in Path('zopyx').rglob('*.zcml')]; [ElementTree.parse(p) for p in Path('zopyx').rglob('*.pt')]"
	$(UV) pip check

build:
	$(UV) build

bootstrap-demo:
	./scripts/bootstrap-demo.sh

clean:
	rm -rf .coverage coverage.xml dist build
