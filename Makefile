# mail-server image — build & test entrypoints.
# Run from anywhere: `make -C images/mail-server <target>`.
IMAGE        ?= mail-server:test
IMAGE_DIR    := $(CURDIR)
TESTS_DIR    := $(IMAGE_DIR)/tests
COMPOSE_FILE := $(TESTS_DIR)/compose.test.yml
PYTEST       ?= python3 -m pytest
PYTEST_FLAGS ?= -q

.PHONY: build test test-render itest buildx-smoke lint clean

## build: build the image as $(IMAGE)
build:
	docker build -t $(IMAGE) $(IMAGE_DIR)

## test-render: render-config unit tests only (no daemons, no compose)
test-render:
	cd $(TESTS_DIR) && $(PYTEST) $(PYTEST_FLAGS) test_render.py

## test: all daemon-free tests (harness smoke + render-config), no compose
test:
	cd $(TESTS_DIR) && $(PYTEST) $(PYTEST_FLAGS) -m "not integration"

# Full happy-path integration test against the compose stack. The session
# fixture in tests/conftest.py builds the image and brings the stack up; the
# trailing `down -v` is a belt-and-braces cleanup if pytest is interrupted.
## itest: full integration tests via the compose stack (includes e2e suite)
itest:
	cd $(TESTS_DIR) && $(PYTEST) -v test_e2e.py
	@docker compose -f $(COMPOSE_FILE) down -v >/dev/null 2>&1 || true

# Build-only multi-arch smoke: proves the Dockerfile builds for both target
# platforms (no push, no load — buildkit discards the result). Catches arch
# regressions (e.g. the Rspamd SVE2 / arm64 pin) before tagging a release.
## buildx-smoke: build-only multi-arch smoke (amd64 + arm64)
buildx-smoke:
	docker buildx build --platform linux/amd64,linux/arm64 $(IMAGE_DIR)

## lint: shellcheck scripts, validate compose, sanity-check SQL/YAML
lint:
	@echo "==> shellcheck"; \
	if command -v shellcheck >/dev/null 2>&1; then \
	  files=$$(find $(IMAGE_DIR)/rootfs $(IMAGE_DIR)/tests -type f \
	    \( -name '*.sh' -o -name 'run' -o -name 'healthcheck.sh' \) 2>/dev/null); \
	  if [ -n "$$files" ]; then shellcheck -x $$files; else echo "  (no shell scripts yet)"; fi; \
	else echo "  shellcheck not installed — skipping"; fi
	@echo "==> docker compose config"; \
	docker compose -f $(COMPOSE_FILE) config >/dev/null && echo "  OK"
	@echo "==> sql/yaml syntax"; \
	cd $(TESTS_DIR) && $(PYTEST) $(PYTEST_FLAGS) test_harness.py
	cd $(TESTS_DIR) && $(PYTEST) $(PYTEST_FLAGS) test_readme_links.py

## clean: tear down any leftover integration stack + scratch
clean:
	-docker compose -f $(COMPOSE_FILE) down -v 2>/dev/null
	rm -rf $(IMAGE_DIR)/build $(TESTS_DIR)/.rendered $(TESTS_DIR)/.pytest_cache
