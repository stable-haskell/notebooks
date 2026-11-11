# Self-documenting Makefile for the stable-haskell notebooks repo.
#
#   make notebook        regenerate generated notebooks from their notebook.py
#   make install-hooks   activate the git clean filter (run once per clone)
#   make strip           strip outputs from all notebooks in place
#   make check           fail if any tracked notebook still has cell outputs
#   make check-generators fail if any notebook.py no longer reproduces its .ipynb
#   make flake-lock      update/verify flake.lock in the root and every *.nb/

# `strip`/`check`/`check-generators` use `read -d ''` + process substitution
# (bashisms) to stay safe with spaces in notebook filenames, so require bash
# rather than /bin/sh.
SHELL  := bash

PYTHON ?= python3
STRIP  := tools/strip-ipynb.py
# Notebook discovery. Kept as a recipe (not a $(shell) var) and driven through
# -print0 / read -d '' so notebook names with spaces (e.g. "WASM Hello.ipynb")
# survive — the repo convention allows spaces in notebook filenames.
FIND   := find . -name '*.ipynb' -not -name 'executed.ipynb' -not -path '*/.ipynb_checkpoints/*'

.DEFAULT_GOAL := help

# Subdued terminal styling.
B := \033[1m
N := \033[0m

.PHONY: help
help: ## show this help
	@printf "$(B)stable-haskell notebooks$(N)\n"
	@awk 'BEGIN { FS = ":.*##" } \
	  /^##@/ { printf "\n$(B)%s$(N)\n", substr($$0, 5); next } \
	  /^[a-zA-Z_0-9.-]+:.*##/ { printf "  $(B)%-15s$(N) %s\n", $$1, $$2 }' $(MAKEFILE_LIST)
	@printf "\nNotebooks found:\n"
	@$(FIND) | sed 's#^\./#  • #'

##@ Generation

# Regenerate any notebook that has a sibling notebook.py generator. The generator
# writes the canonical clean form (same as the strip filter), so this never
# introduces cell outputs.
.PHONY: notebook
notebook: ## regenerate notebooks from their notebook.py
	@find . -name notebook.py -not -path '*/.ipynb_checkpoints/*' -print0 \
	  | while IFS= read -r -d '' gen; do \
	      dir=$$(dirname "$$gen"); \
	      nb=$$(find "$$dir" -maxdepth 1 -name '*.ipynb' -not -name 'executed.ipynb' -not -path '*/.ipynb_checkpoints/*' | head -1); \
	      [ -n "$$nb" ] || nb="$$dir/$$(basename "$$dir" .nb).ipynb"; \
	      $(PYTHON) "$$gen" "$$nb"; \
	    done

##@ Hygiene

# Configure the local repo to run the clean filter named in .gitattributes.
# clean = strip on `git add`; smudge = identity (working tree keeps its outputs).
.PHONY: install-hooks
install-hooks: ## activate the git clean filter (run once per clone)
	@git config filter.nbstrip.clean "$(PYTHON) $(STRIP)"
	@git config filter.nbstrip.smudge cat
	@printf "$(B)✓$(N) git clean filter 'nbstrip' configured for this clone.\n"
	@printf "  Notebook outputs are now stripped from every commit automatically.\n"

.PHONY: strip
strip: ## strip outputs from all notebooks in place
	@$(FIND) -print0 | while IFS= read -r -d '' nb; do \
	  $(PYTHON) $(STRIP) "$$nb"; printf "$(B)✂$(N) %s\n" "$$nb"; \
	done

# Every notebook.py must reproduce its sibling .ipynb byte-for-byte (after
# stripping): regenerate each one into a temp file, strip it, and diff against
# the tracked notebook. A mismatch means `make notebook` was not re-run (or a
# hand edit drifted from the generator) — commit the regenerated notebook.
.PHONY: check-generators
check-generators: ## fail if any notebook.py no longer reproduces its .ipynb
	@fail=0; \
	while IFS= read -r -d '' gen; do \
	  dir=$$(dirname "$$gen"); \
	  nb=$$(find "$$dir" -maxdepth 1 -name '*.ipynb' -not -name 'executed.ipynb' -not -path '*/.ipynb_checkpoints/*' | head -1); \
	  if [ -z "$$nb" ]; then printf "✗ %s has no sibling .ipynb\n" "$$gen"; fail=1; continue; fi; \
	  tmp=$$(mktemp); \
	  if ! $(PYTHON) "$$gen" "$$tmp" 2>"$$tmp.err"; then \
	    printf "✗ %s failed to run\n" "$$gen"; cat "$$tmp.err"; fail=1; rm -f "$$tmp" "$$tmp.err"; continue; \
	  fi; \
	  $(PYTHON) $(STRIP) "$$tmp"; \
	  if cmp -s "$$tmp" "$$nb"; then printf "✓ %s reproduces %s\n" "$$gen" "$$nb"; \
	  else printf "✗ %s differs from %s (run \`make notebook\` and commit)\n" "$$gen" "$$nb"; diff "$$tmp" "$$nb" | head -20; fail=1; fi; \
	  rm -f "$$tmp" "$$tmp.err"; \
	done < <(find . -name notebook.py -not -path '*/.ipynb_checkpoints/*' -print0); \
	exit $$fail

# CI-friendly guard: nonzero exit if a tracked notebook carries outputs.
# Also verifies the generators (see check-generators above).
#
# Only tracked notebooks are inspected: an untracked draft (e.g. a notebook
# still being authored) must neither pass nor fail this gate — committing is
# what opts a notebook in. Outside a git checkout (e.g. the Nix store source
# used by `nix flake check`, where everything present is committed by
# construction) fall back to scanning the working tree.
.PHONY: check
check: check-generators ## fail if any tracked notebook still has cell outputs
	@fail=0; \
	discover() { \
	  if git rev-parse --is-inside-work-tree >/dev/null 2>&1; then \
	    git ls-files -z '*.ipynb'; \
	  else \
	    $(FIND) -print0; \
	  fi; \
	}; \
	while IFS= read -r -d '' nb; do \
	  n=$$($(PYTHON) -c "import json,sys; nb=json.load(open(sys.argv[1])); print(sum(len(c.get('outputs',[])) for c in nb['cells'] if c['cell_type']=='code'))" "$$nb"); \
	  if [ "$$n" != "0" ]; then printf "✗ %s has %s output block(s)\n" "$$nb" "$$n"; fail=1; fi; \
	done < <(discover); \
	if [ "$$fail" = "0" ]; then printf "$(B)✓$(N) all notebooks are output-free\n"; fi; \
	exit $$fail

##@ Flakes

# Update (and thereby verify) the flake lockfile in the root flake and in
# every notebook flake (*.nb/). Notebook dirs without a flake.nix — e.g. ones
# still being authored — are skipped.
.PHONY: flake-lock
flake-lock: ## update/verify flake.lock in the root and every *.nb/ flake
	@for dir in . *.nb; do \
	  if [ -f "$$dir/flake.nix" ]; then \
	    printf "$(B)◈$(N) locking %s\n" "$$dir"; \
	    (cd "$$dir" && nix flake lock); \
	  fi; \
	done
