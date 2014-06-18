#!/usr/bin/make
PYTHON := /usr/bin/env python

lint:
	@pyflakes hooks/*.py unit_tests
	@charm proof

test:
	@echo Starting tests...
	@$(PYTHON) /usr/bin/nosetests --nologcapture --with-coverage -v unit_tests


bin/charm_helpers_sync.py:
	@mkdir -p bin
	@bzr cat lp:charm-helpers/tools/charm_helpers_sync/charm_helpers_sync.py \
		> bin/charm_helpers_sync.py

sync: bin/charm_helpers_sync.py
	@$(PYTHON) bin/charm_helpers_sync.py -c charm-helpers-sync.yaml

publish: lint test
	bzr push lp:charms/glance-simplestreams-sync
	bzr push lp:charms/trusty/glance-simplestreams-sync
