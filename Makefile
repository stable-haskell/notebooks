# Self-documenting Makefile for the stable-haskell notebooks repo.
#
#   make notebook        regenerate generated notebooks from their notebook.py
#   make install-hooks   activate the git clean filter (run once per clone)
#   make strip           strip outputs from all notebooks in place
#   make check           fail if any tracked notebook still has cell outputs

# `strip`/`check` use `read -d ''` + process substitution (bashisms) to stay
# safe with spaces in notebook filenames, so require bash rather than /bin/sh.
SHELL  := bash

PYTHON ?= python3
STRIP  := tools/strip-ipynb.py
# Notebook discovery. Kept as a recipe (not a $(shell) var) and driven through
# -print0 / read -d '' so notebook names with spaces (e.g. "WASM Hello.ipynb")
# survive — the repo convention allows spaces in notebook filenames.
FIND   := find . -name '*.ipynb' -not -path '*/.ipynb_checkpoints/*'

.DEFAULT_GOAL := help

# Subdued terminal styling.
B := \033[1m
N := \033[0m

.PHONY: help
help:
	@printf "$(B)stable-haskell notebooks$(N)\n\n"
	@printf "  $(B)make notebook$(N)       ↻  regenerate notebooks from their notebook.py\n"
	@printf "  $(B)make install-hooks$(N)  ⚙  activate the git clean filter (run once per clone)\n"
	@printf "  $(B)make strip$(N)          ✂  strip outputs from all notebooks in place\n"
	@printf "  $(B)make check$(N)          ✓  fail if any notebook still has cell outputs\n"
	@printf "\nNotebooks found:\n"
	@$(FIND) | sed 's#^\./#  • #'

# Regenerate any notebook that has a sibling notebook.py generator. The generator
# writes the canonical clean form (same as the strip filter), so this never
# introduces cell outputs.
.PHONY: notebook
notebook:
	@find . -name notebook.py -not -path '*/.ipynb_checkpoints/*' -print0 \
	  | while IFS= read -r -d '' gen; do \
	      dir=$$(dirname "$$gen"); \
	      nb=$$(find "$$dir" -maxdepth 1 -name '*.ipynb' -not -path '*/.ipynb_checkpoints/*' | head -1); \
	      [ -n "$$nb" ] || nb="$$dir/$$(basename "$$dir" .nb).ipynb"; \
	      $(PYTHON) "$$gen" "$$nb"; \
	    done

# Configure the local repo to run the clean filter named in .gitattributes.
# clean = strip on `git add`; smudge = identity (working tree keeps its outputs).
.PHONY: install-hooks
install-hooks:
	@git config filter.nbstrip.clean "$(PYTHON) $(STRIP)"
	@git config filter.nbstrip.smudge cat
	@printf "$(B)✓$(N) git clean filter 'nbstrip' configured for this clone.\n"
	@printf "  Notebook outputs are now stripped from every commit automatically.\n"

.PHONY: strip
strip:
	@$(FIND) -print0 | while IFS= read -r -d '' nb; do \
	  $(PYTHON) $(STRIP) "$$nb"; printf "$(B)✂$(N) %s\n" "$$nb"; \
	done

# CI-friendly guard: nonzero exit if a tracked notebook carries outputs.
.PHONY: check
check:
	@fail=0; \
	while IFS= read -r -d '' nb; do \
	  n=$$($(PYTHON) -c "import json,sys; nb=json.load(open(sys.argv[1])); print(sum(len(c.get('outputs',[])) for c in nb['cells'] if c['cell_type']=='code'))" "$$nb"); \
	  if [ "$$n" != "0" ]; then printf "✗ %s has %s output block(s)\n" "$$nb" "$$n"; fail=1; fi; \
	done < <($(FIND) -print0); \
	if [ "$$fail" = "0" ]; then printf "$(B)✓$(N) all notebooks are output-free\n"; fi; \
	exit $$fail
