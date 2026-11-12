#!/usr/bin/env python3
"""Generator for "JS TH Cross.ipynb" (python3 kernel, nbformat 4.5).

The notebook is generated from this script so edits are reproducible and the
serialized form matches tools/strip-ipynb.py (clean cells, no outputs). Run via:
python3 js-th-cross.nb/notebook.py "js-th-cross.nb/JS TH Cross.ipynb"

Walks end-to-end through cross-compiling Haskell with GHC 9.14 and a
cross-aware Cabal to JavaScript (javascript-unknown-ghcjs), evaluating
Template Haskell splices via GHC's JS interpreter in Node.js while running
Setup.hs and build tools natively on the build machine. Runs on both Linux
(x86_64, aarch64) and macOS (arm64 / aarch64).
"""
import json
import sys

cells = []


def md(cid, text):
    cells.append(("markdown", cid, text))


def code(cid, text):
    cells.append(("code", cid, text))


# ---- 0. title -------------------------------------------------------------
md("js-title", r"""
# Cross Compiling Haskell with GHC 9.14 and Cabal — to JavaScript

Cross-compile to **JavaScript (`javascript-unknown-ghcjs`)**: Template Haskell splices evaluate in Node via `ghc-interp.js`, while `Setup.hs` and build tools run natively on the build machine.

1. **Single native GHC binary**: invoking `javascript-unknown-ghcjs-ghc` (a symlink to native `ghc`) selects `lib/targets/javascript-unknown-ghcjs/lib/{settings,package.conf.d}`.
2. **Dual-compiler Cabal**: `-w` selects `javascript-unknown-ghcjs-ghc` (`host:*`), `-W` selects native `ghc` (`build:*`) for `Setup.hs` and codegen tools.

| Part | Compiled for | Runs where |
|---|---|---|
| GHC and Cabal | build machine | build machine |
| `Setup.hs`, `deriveConstants`, `genapply` | build machine | build machine |
| Splice code and `ghc-interp.js` | JavaScript | Node on the build machine |
| Report app and its libraries | JavaScript | Node here; browser or Node when deployed |

Prerequisites: set `CROSS_ROOT` (absolute, no whitespace) before starting Jupyter; `GHC_REF`/`CABAL_REF` pin the source refs (defaults track branch heads, resolved in `sources.lock`); run via `nix develop` for the pinned `emcc`/`node`/LLVM toolchain.
""")

# ---- 1. place to build ----------------------------------------------------
md("js-place", r"""
## A place to build

`CROSS_ROOT` holds the native GHC tree, target tree, Cabal store, and app. Native and target sources use separate checkouts; the vanilla-`v` RTS edit applies to the target tree only.
""")

code("js-gate", r"""
%%bash
set -euo pipefail
export PATH="$PATH:/usr/sbin:/sbin"
fail() { echo "$*" >&2; exit 1; }
case "$(uname -s)" in
  Linux) BUILD_OS=linux; BUILD_VENDOR=unknown ;;
  Darwin) BUILD_OS=darwin; BUILD_VENDOR=apple ;;
  *) fail "Use Linux or macOS for this walkthrough." ;;
esac
case "$(uname -m)" in
  x86_64) BUILD_ARCH=x86_64 ;;
  aarch64|arm64) BUILD_ARCH=aarch64 ;;
  *) fail "Unsupported machine architecture: $(uname -m)." ;;
esac
BUILD_PLATFORM="${BUILD_ARCH}-${BUILD_VENDOR}-${BUILD_OS}"
CROSS_ROOT=${CROSS_ROOT:-$HOME/cross-ghc-current}
[[ "$CROSS_ROOT" == /* && "$CROSS_ROOT" != *[[:space:]]* ]] || fail "CROSS_ROOT must be absolute and contain no whitespace."
mkdir -p "$CROSS_ROOT"
CROSS_ROOT=$(cd "$CROSS_ROOT" && pwd -P)
DIST="$CROSS_ROOT/ghc-src/_build/dist"
GHC_SRC="$CROSS_ROOT/ghc-src"
CABAL_SRC="$CROSS_ROOT/cabal-src"
CABAL="$CROSS_ROOT/cabal-dist/cabal"
SRC="$CROSS_ROOT/ghc-target-src-js"
STAGE3="$CROSS_ROOT/stage3-js"
FIBO="$CROSS_ROOT/cross-report-js"
EM_CACHE="$CROSS_ROOT/em-cache"
mkdir -p "$EM_CACHE"
# Fresh source builds need room for two GHC trees; reusing an existing
# compiler needs much less. MIN_FREE_GB overrides the default.
if [[ -n "${MIN_FREE_GB:-}" ]]; then need_gb="$MIN_FREE_GB"
elif [[ "${CI:-}" == "true" || -x "$DIST/bin/ghc" ]]; then need_gb=4
else need_gb=32; fi
avail=$(df -Pk "$CROSS_ROOT" | awk 'NR==2 {print $4}')
(( avail >= need_gb*1024*1024 )) || fail "Allow at least ${need_gb} GiB free at $CROSS_ROOT (override with MIN_FREE_GB)."
if [[ "$BUILD_OS" == linux ]]; then
  mem_mib=$(awk '/MemTotal/ {print int($2/1024)}' /proc/meminfo)
else
  mem_mib=$(($(sysctl -n hw.memsize)/1024/1024))
fi
if (( mem_mib < 16*1024 )); then
  echo "Less than 16 GiB RAM: the full compiler build may need more memory or swap." >&2
fi
if [[ -n "${MAKE_BIN:-}" ]]; then
  :
elif command -v make >/dev/null && python3 -c 'import sys; v=sys.argv[1].split("."); raise SystemExit(0 if (int(v[0]),int(v[1]))>=(4,4) else 1)' "$(make --version 2>/dev/null | awk 'NR==1{print $3}')" 2>/dev/null; then
  MAKE_BIN=make
elif command -v gmake >/dev/null; then
  MAKE_BIN=gmake
else
  MAKE_BIN=make
fi
printf '%s\n' "$CROSS_ROOT" > "$HOME/.cross-ghc-js-root"
{
  printf 'CROSS_ROOT=%q\n' "$CROSS_ROOT"
  printf 'DIST=%q\n' "$DIST"
  printf 'GHC_SRC=%q\n' "$GHC_SRC"
  printf 'CABAL_SRC=%q\n' "$CABAL_SRC"
  printf 'CABAL=%q\n' "$CABAL"
  printf 'BOOT_GHC=%q\n' "${BOOT_GHC:-ghc-9.8.4}"
  printf 'BOOT_CABAL=%q\n' "${BOOT_CABAL:-cabal}"
  printf 'MAKE_BIN=%q\n' "$MAKE_BIN"
  printf 'SRC=%q\n' "$SRC"
  printf 'STAGE3=%q\n' "$STAGE3"
  printf 'FIBO=%q\n' "$FIBO"
  printf 'BUILD_ARCH=%q\n' "$BUILD_ARCH"
  printf 'BUILD_OS=%q\n' "$BUILD_OS"
  printf 'BUILD_VENDOR=%q\n' "$BUILD_VENDOR"
  printf 'BUILD_PLATFORM=%q\n' "$BUILD_PLATFORM"
  printf 'EM_CACHE=%q\n' "$EM_CACHE"
  printf 'BUILD_JOBS=%q\n' "${BUILD_JOBS:-4}"
  printf 'GHC_JOBS=%q\n' "${GHC_JOBS:-2}"
  printf 'export CABAL_DIR=%q\n' "$CROSS_ROOT/cabal-home"
  cat <<'EOF'
cross_enter() {
  set -euo pipefail
  cd "$CROSS_ROOT"
  unset CC CXX AR RANLIB
  export PATH="$PATH:/usr/sbin:/sbin"
  if [[ -d "$HOME/.ghcup/bin" && ":$PATH:" != *":$HOME/.ghcup/bin:"* ]]; then
    export PATH="$HOME/.ghcup/bin:$PATH"
  fi
  if [[ -d "$CROSS_ROOT/bin" && ":$PATH:" != *":$CROSS_ROOT/bin:"* ]]; then
    export PATH="$CROSS_ROOT/bin:$PATH"
  fi
  if [[ -d "$CROSS_ROOT/emsdk/upstream/emscripten" && ":$PATH:" != *":$CROSS_ROOT/emsdk/upstream/emscripten:"* ]]; then
    export PATH="$CROSS_ROOT/emsdk/upstream/emscripten:$PATH"
  fi
}
sha256_file() {
  if command -v sha256sum >/dev/null; then sha256sum "$1" | awk '{print $1}';
  else shasum -a 256 "$1" | awk '{print $1}'; fi
}
export EM_CACHE
EOF
} > "$CROSS_ROOT/gate-js.env"
cp "$CROSS_ROOT/gate-js.env" "$CROSS_ROOT/gate.env"
printf 'Build tree: %s\nBuild machine: %s (%s)\n' "$CROSS_ROOT" "$BUILD_PLATFORM" "$(uname -srm)"
""")
# ---- 2. JS & C toolchain ------------------------------------------------
md("js-tools-md", r"""
## The JS & C toolchain (`emcc`, `node`)

The JS backend links via emscripten (`emcc`, `emar`, `emranlib`) and inspects objects with `llvm-objdump`. `ghc-interp.js` and the final `.jsexe/all.js` run under Node.js 22+.
Inside `nix develop` all three are pinned; outside Nix the cell falls back to Homebrew (macOS) or an `emsdk` checkout plus static Node (Linux).
""")

code("js-tools", r"""
%%bash
set -euo pipefail
ROOT=$(cat "$HOME/.cross-ghc-js-root")
# shellcheck disable=SC1091
source "$ROOT/gate-js.env"
cross_enter
export EM_CACHE

if ! command -v emnm >/dev/null && command -v emcc >/dev/null; then
  mkdir -p "$CROSS_ROOT/bin"
  emcc_path=$(command -v emcc)
  if grep -q '/share/emscripten/emcc' "$emcc_path" 2>/dev/null; then
    sed 's|/share/emscripten/emcc|/share/emscripten/emnm|' "$emcc_path" > "$CROSS_ROOT/bin/emnm"
    chmod +x "$CROSS_ROOT/bin/emnm"
  elif command -v llvm-nm >/dev/null; then
    ln -sfn "$(command -v llvm-nm)" "$CROSS_ROOT/bin/emnm"
  fi
  export PATH="$CROSS_ROOT/bin:$PATH"
fi

missing=()
for t in emcc em++ emar emranlib emnm; do
  command -v "$t" >/dev/null || missing+=("$t")
done
if command -v llvm-objdump >/dev/null; then OBJDUMP_BIN=llvm-objdump
elif command -v llvm-objdump-19 >/dev/null; then OBJDUMP_BIN=llvm-objdump-19
else missing+=("llvm-objdump"); OBJDUMP_BIN=""; fi
node_ok=0
if command -v node >/dev/null; then
  MAJ=$(node --version | sed 's/^v//; s/\..*$//')
  if [[ "$MAJ" -ge 22 ]]; then node_ok=1; else echo "node too old: $(node --version) (need >= 22)"; fi
fi
(( node_ok )) || missing+=("node>=22")

if (( ${#missing[@]} )); then
  echo "Missing tools: ${missing[*]} — trying a platform fallback…"
  case "$BUILD_OS" in
    darwin)
      command -v brew >/dev/null || { echo "Install Homebrew, or enter this flake ('nix develop') for the pinned tools." >&2; exit 1; }
      brew install emscripten node llvm
      ;;
    linux)
      # emsdk is the reliable emscripten source on Linux (distro packages lag).
      if [[ ! -d "$CROSS_ROOT/emsdk/.git" ]]; then
        git clone --depth 1 https://github.com/emscripten-core/emsdk.git "$CROSS_ROOT/emsdk"
      fi
      "$CROSS_ROOT/emsdk/emsdk" install latest
      "$CROSS_ROOT/emsdk/emsdk" activate latest
      # shellcheck disable=SC1091
      source "$CROSS_ROOT/emsdk/emsdk_env.sh"
      case "$BUILD_ARCH" in
        x86_64) NODE_ARCH=x64 ;;
        aarch64) NODE_ARCH=arm64 ;;
      esac
      NODE_VERSION=${NODE_VERSION:-v22.17.0}
      NODE_DIST="$CROSS_ROOT/node-dist-$NODE_VERSION"
      if [[ ! -x "$NODE_DIST/bin/node" ]]; then
        mkdir -p "$NODE_DIST"
        curl -fL --retry 3 -o "$CROSS_ROOT/node.tar.xz" \
          "https://nodejs.org/dist/$NODE_VERSION/node-$NODE_VERSION-linux-$NODE_ARCH.tar.xz"
        tar --strip-components=1 -xf "$CROSS_ROOT/node.tar.xz" -C "$NODE_DIST"
      fi
      export PATH="$NODE_DIST/bin:$PATH"
      printf 'PATH=%q:$PATH\n' "$NODE_DIST/bin" >> "$ROOT/gate-js.env"
      cp "$ROOT/gate-js.env" "$ROOT/gate.env"
      if ! command -v llvm-objdump >/dev/null && ! command -v llvm-objdump-19 >/dev/null; then
        SUDO=""; [[ $(id -u) == 0 ]] || SUDO=sudo
        $SUDO apt-get update && $SUDO env DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends llvm || true
      fi
      ;;
  esac
  for t in emcc em++ emar emranlib emnm; do
    command -v "$t" >/dev/null || { echo "Still missing: $t" >&2; exit 1; }
  done
  if command -v llvm-objdump >/dev/null; then OBJDUMP_BIN=llvm-objdump
  elif command -v llvm-objdump-19 >/dev/null; then OBJDUMP_BIN=llvm-objdump-19
  else echo "Still missing: llvm-objdump" >&2; exit 1; fi
  command -v node >/dev/null || { echo "Still missing: node" >&2; exit 1; }
  MAJ=$(node --version | sed 's/^v//; s/\..*$//')
  (( MAJ >= 22 )) || { echo "node too old after fallback: $(node --version)" >&2; exit 1; }
fi

printf 'OBJDUMP_BIN=%q\n' "$OBJDUMP_BIN" >> "$ROOT/gate-js.env"
      cp "$ROOT/gate-js.env" "$ROOT/gate.env"
echo "node $(node --version)"
emcc --version | head -3
em++ --version | head -1
"$OBJDUMP_BIN" --version | head -2
echo "JS toolchain ok on $BUILD_PLATFORM"
""")

# ---- 3. fetching GHC + Cabal ----------------------------------------------
md("js-fetch-md", r"""
## Fetching GHC 9.14 and the cross-aware Cabal

Stage 2 native GHC comes from `stable-haskell/ghc:stable-ghc-9.14`; tutorial Cabal from `andrea/feature/cross-compilation-2`, plus a local wired-in boot-library patch (a missing `ghc-internal` may build only with `allow-boot-library-installs: True`).
GHC's Makefile uses its own `cabal.project.stage0` builder; Andrea's Cabal is the one used for target libraries and the app.
`GHC_REF`/`CABAL_REF` select refs; `sources.lock` records the commits. An existing `$CABAL` + `$DIST/bin/ghc` matching `build-sources.lock` is reused, not rebuilt.
""")

code("js-fetch", r"""
%%bash
set -euo pipefail
ROOT=$(cat "$HOME/.cross-ghc-js-root")
source "$ROOT/gate-js.env"
cross_enter

checkout_ref() {
  local url=$1 ref=$2 dest=$3
  if [[ ! -d "$dest/.git" ]]; then
    git init "$dest"
    git -C "$dest" remote add origin "$url"
  else
    [[ $(git -C "$dest" remote get-url origin) == "$url" ]]
    git -C "$dest" checkout -- cabal-install/src/Distribution/Client/Dependency.hs cabal.project.stage0 2>/dev/null || true
    git -C "$dest" diff --exit-code
    git -C "$dest" diff --cached --exit-code
  fi
  git -C "$dest" fetch --depth 1 origin "$ref"
  git -C "$dest" checkout --detach FETCH_HEAD
}
checkout_ref https://github.com/stable-haskell/ghc.git \
  "${GHC_REF:-stable-ghc-9.14}" "$GHC_SRC"
checkout_ref https://github.com/andreabedini/cabal.git \
  "${CABAL_REF:-andrea/feature/cross-compilation-2}" "$CABAL_SRC"
{
  printf 'GHC_COMMIT=%s\n' "$(git -C "$GHC_SRC" rev-parse HEAD)"
  printf 'CABAL_COMMIT=%s\n' "$(git -C "$CABAL_SRC" rev-parse HEAD)"
} > "$CROSS_ROOT/sources.lock"
cat "$CROSS_ROOT/sources.lock"
if [[ -f "$CROSS_ROOT/build-sources.lock" ]]; then
  cmp -s <(head -n 2 "$CROSS_ROOT/build-sources.lock") "$CROSS_ROOT/sources.lock" || {
    echo "Sources changed. Start with a fresh CROSS_ROOT for this toolchain." >&2; exit 1;
  }
fi
""")
# ---- 4. building GHC + Cabal -----------------------------------------------
md("js-build-md", r"""
## Building GHC 9.14 and the cross-aware Cabal

Builds tutorial `cabal` (with the boot-library patch) then the native stage 2 distribution — the slow cell.
Cache-aware: a `$CABAL` + `$DIST/bin/ghc` matching `build-sources.lock` plus the patch checksum skips the rebuild. Records `deriveConstants`/`genapply`/`ghc-toolchain-bin` paths for later cells.
""")

code("js-build", r"""
%%bash
set -euo pipefail
ROOT=$(cat "$HOME/.cross-ghc-js-root")
source "$ROOT/gate-js.env"
cross_enter

BOOT_GHC=$(command -v "$BOOT_GHC")
BOOT_CABAL=$(command -v "$BOOT_CABAL")
MAKE_BIN=$(command -v "$MAKE_BIN")
[[ $("$BOOT_GHC" --numeric-version) == 9.8.4 ]]
python3 - "$("$MAKE_BIN" --version | awk 'NR==1 {print $3}')" <<'PY_MAKE'
import sys
parts = sys.argv[1].split(".")
assert (int(parts[0]), int(parts[1])) >= (4, 4), "Need GNU Make 4.4+ (set MAKE_BIN)"
PY_MAKE
mkdir -p "$CABAL_DIR" "$CROSS_ROOT/cabal-dist"
if [[ ! -f "$CABAL_DIR/config" ]]; then "$BOOT_CABAL" user-config init; fi
sed -i.bak 's|url: http://hackage.haskell.org/|url: https://hackage.haskell.org/|' "$CABAL_DIR/config"
rm -f "$CABAL_DIR/config.bak"
"$BOOT_CABAL" update

# Cache-aware: skip rebuilding what a previous run already built from these
# same sources. Remove build-sources.lock (or set FORCE_GHC_REBUILD=1 for the
# compiler) to force a rebuild.
locks_match() {
  [[ -f "$CROSS_ROOT/build-sources.lock" ]] \
    && head -n 2 "$CROSS_ROOT/build-sources.lock" | cmp -s "$CROSS_ROOT/sources.lock" -
}
if [[ -x "$CABAL" ]] && locks_match; then
  echo "Tutorial Cabal already built from these sources — skipping rebuild."
else
  cp "$CROSS_ROOT/sources.lock" "$CROSS_ROOT/build-sources.lock"
# This branch pins wired-in libraries even for an empty target installation.
# With explicit boot-library permission, permit only missing wired-in units
# to be built. Existing installed units retain their exact-unit pins.
python3 - "$CABAL_SRC" <<'PY_CABAL_BOOT'
from pathlib import Path
import sys
p = Path(sys.argv[1]) / 'cabal-install/src/Distribution/Client/Dependency.hs'
s = p.read_text()
old = "      | (pkgName, unitId) <- wiredInUnitIds\n      ]"
new = '''      | (pkgName, unitId) <- wiredInUnitIds
      -- Explicit target bootstrap: do not require a missing installed unit.
      , not (asBool (depResolverAllowBootLibInstalls params))
          || isJust (InstalledPackageIndex.lookupUnitId
               (onMachine (depResolverInstalledPkgIndex params) Host) unitId)
      ]'''
if old in s:
    p.write_text(s.replace(old, new, 1))
else:
    assert 'onMachine (depResolverInstalledPkgIndex params) Host) unitId)' in s, 'Review the Cabal boot patch against this revision'
PY_CABAL_BOOT
git -C "$CABAL_SRC" diff -- cabal-install/src/Distribution/Client/Dependency.hs > "$CROSS_ROOT/cabal-boot.patch"
sha256_file "$CROSS_ROOT/cabal-boot.patch" | awk '{print $1}' >> "$CROSS_ROOT/build-sources.lock"
# Build the Cabal used for target libraries and the application.
cd "$CABAL_SRC"
"$BOOT_CABAL" install --project-file=cabal.release.project \
  --with-compiler="$BOOT_GHC" -j"$BUILD_JOBS" --disable-tests --disable-benchmarks \
  --installdir="$CROSS_ROOT/cabal-dist" --install-method=copy \
  --overwrite-policy=always cabal-install:exe:cabal
fi
"$CABAL" --version
"$CABAL" build --help | grep -E -- '-w PATH'
"$CABAL" build --help | grep -E -- '-W PATH'

if [[ -z "${FORCE_GHC_REBUILD:-}" ]] && [[ -x "$DIST/bin/ghc" ]] && [[ -x "$DIST/bin/ghc-pkg" ]] && locks_match; then
  echo "Native compiler already built from these sources — skipping stage2 rebuild."
  "$DIST/bin/ghc-pkg" recache
else
  # Use GHC's own compiler-build driver and its pinned internal Cabal builder.
  # In particular, do not set CABAL=$CABAL or USE_SYSTEM_CABAL here.
  cd "$GHC_SRC"
  if ! grep -q '^optional-packages: \./nonexistent/\*\.cabal' "$GHC_SRC/cabal.project.stage0"; then
    printf '\noptional-packages: ./nonexistent/*.cabal\n' >> "$GHC_SRC/cabal.project.stage0"
  fi
  "$MAKE_BIN" -j"$BUILD_JOBS" stage2 GHC0="$BOOT_GHC" CABAL0="$BOOT_CABAL" \
    THREADS="$BUILD_JOBS" DYNAMIC=0 CABAL_ARGS="-j$BUILD_JOBS --disable-optimization --ghc-option=-j$GHC_JOBS"
  [[ -x "$DIST/bin/ghc" && -x "$DIST/bin/ghc-pkg" ]]
fi
"$DIST/bin/ghc" --numeric-version
"$DIST/bin/ghc" -list-targets
"$DIST/bin/ghc-pkg" check

for name in deriveConstants genapply ghc-toolchain-bin; do
  [[ -x "$DIST/bin/$name" ]]
  printf '%s\n' "$DIST/bin/$name" > "$CROSS_ROOT/$name.path"
done
echo "Compiler, Cabal and native configuration tools ready."
""")

# ---- 5. target source tree ------------------------------------------------
md("js-src-md", r"""
## One compiler, and a separate target tree

Second checkout at the same GHC commit holds the JavaScript libraries; the vanilla-RTS edit lands here only, after the native distribution is built.
`rts/rts.cabal` is restricted to vanilla `v` (other sublibraries get unconditional `buildable: False`); `ghc-boot-th-next` input is staged locally and excluded from the target build.
""")

code("js-src", r"""
%%bash
set -euo pipefail
ROOT=$(cat "$HOME/.cross-ghc-js-root")
# shellcheck disable=SC1091
source "$ROOT/gate-js.env"
cross_enter
source "$CROSS_ROOT/sources.lock"
if [[ ! -e "$SRC/.git" ]]; then
  git -C "$GHC_SRC" worktree add --detach "$SRC" "$GHC_COMMIT"
fi
[[ $(git -C "$SRC" rev-parse HEAD) == "$GHC_COMMIT" ]]
if [[ ! -f "$SRC/libraries/ghc-boot-th-next/ghc-boot-th-next.cabal.in" ]]; then
  rm -rf "$SRC/libraries/ghc-boot-th-next"
  cp -a "$SRC/libraries/ghc-boot-th" "$SRC/libraries/ghc-boot-th-next"
  cd "$SRC/libraries/ghc-boot-th-next"
  mv ghc-boot-th.cabal.in ghc-boot-th-next.cabal.in
  sed -i.bak 's/ghc-boot-th/ghc-boot-th-next/g' ghc-boot-th-next.cabal.in && rm -f ghc-boot-th-next.cabal.in.bak
  rm -f ghc-boot-th.cabal
fi
cd "$SRC"
if [[ ! -f "$SRC/configure" || "$SRC/configure.ac" -nt "$SRC/configure" ]]; then
  autoreconf -i
fi
if [[ ! -f "$SRC/config.status" ]]; then
  ./configure
fi
if [[ ! -f "$SRC/rts/configure" || "$SRC/rts/configure.ac" -nt "$SRC/rts/configure" ]]; then
  ( cd "$SRC/rts" && autoreconf -i )
fi
if [[ ! -f "$SRC/libraries/ghc-internal/configure" ]]; then
  ( cd "$SRC/libraries/ghc-internal" && autoreconf -i )
fi

git -C "$SRC" checkout -- rts/rts.cabal 2>/dev/null || true
python3 - "$SRC/rts/rts.cabal" <<'PY_PATCH'
import re, sys
from pathlib import Path
path = Path(sys.argv[1])
text = path.read_text()
for name in ("threaded-nodebug", "nonthreaded-debug", "threaded-debug"):
    pattern = rf"(?ms)^library {re.escape(name)}\n.*?(?=^library |\Z)"
    match = re.search(pattern, text)
    if match is None:
        raise SystemExit(f"Missing RTS sublibrary: {name}")
    block = match.group()
    # Four spaces is the unconditional field level here. Six spaces is not.
    if not re.search(r"(?m)^    buildable:\s*False\s*$", block):
        block, count = re.subn(r"(?m)^(    import:.*\n)", r"\1    buildable: False\n", block, count=1)
        if count != 1:
            raise SystemExit(f"Unexpected import layout: {name}")
        text = text[:match.start()] + block + text[match.end():]
    assert re.search(rf"(?m)^library {name}\n    import:[^\n]*\n    buildable: False$", text)
path.write_text(text)
print("Only the vanilla RTS sublibrary remains buildable.")
PY_PATCH
""")
# ---- 6. teaching GHC about javascript-unknown-ghcjs -------------------------
md("js-settings-md", r"""
## Teaching GHC about `javascript-unknown-ghcjs`

`ghc-toolchain-bin --triple=javascript-unknown-ghcjs` (with `--cc emcc`, `--ar emar`, `--ranlib emranlib`, `--nm emnm`, `--disable-tables-next-to-code`) writes `lib/targets/javascript-unknown-ghcjs/lib/settings` (RTS ways `v`; dynamic/profiled marked unavailable).
The full `javascript-unknown-ghcjs` name is what GHC's command-name lookup uses to find `lib/targets/<name>/lib`.
Ships `dyld/post-link/prelude.mjs` plus `ghc-interp.js` (with breakpoint hooks) into the target lib; `javascript-unknown-ghcjs-{ghc,ghc-pkg,hsc2hs}` symlinks select it.
""")

code("js-settings", r"""
%%bash
set -euo pipefail
ROOT=$(cat "$HOME/.cross-ghc-js-root")
# shellcheck disable=SC1091
source "$ROOT/gate-js.env"
cross_enter
export EM_CACHE
toolbin=$(cat "$CROSS_ROOT/ghc-toolchain-bin.path")
target_lib="$DIST/lib/targets/javascript-unknown-ghcjs/lib"
mkdir -p "$target_lib"
cd "$SRC"
"$toolbin" \
  --triple javascript-unknown-ghcjs \
  --disable-ld-override \
  --cc emcc \
  --cxx em++ \
  --ar emar \
  --ranlib emranlib \
  --nm emnm \
  --cpp emcc \
  --hs-cpp emcc \
  --cmm-cpp emcc \
  --disable-tables-next-to-code \
  --output-settings \
  -o "$target_lib/settings"
python3 - "$target_lib/settings" <<'PY_SETTINGS'
import ast, json, sys
from pathlib import Path
p = Path(sys.argv[1])
settings = dict(ast.literal_eval(p.read_text()))
settings['RTS ways'] = 'v'
for key in ('target is dynamic', 'target ships dynamic libraries',
            'target is profiled', 'target ships profiling libraries'):
    settings[key] = 'NO'
p.write_text('[' + ',\n'.join('(' + json.dumps(k) + ',' + json.dumps(v) + ')'
                              for k, v in settings.items()) + ']\n')
PY_SETTINGS
for f in dyld.mjs post-link.mjs prelude.mjs; do
  cp "$SRC/utils/jsffi/$f" "$target_lib/$f"
done
{
  printf 'globalThis.h$rts_breakpoint_io_action = null;\n'
  printf 'globalThis.h$rts_stop_on_exception = null;\n'
  cat "$SRC/ghc-interp.js"
} > "$target_lib/ghc-interp.js"
if [[ ! -d "$target_lib/package.conf.d" ]]; then
  "$DIST/bin/ghc-pkg" init "$target_lib/package.conf.d"
fi
ln -sfn ghc "$DIST/bin/javascript-unknown-ghcjs-ghc"
ln -sfn ghc-pkg "$DIST/bin/javascript-unknown-ghcjs-ghc-pkg"
ln -sfn hsc2hs "$DIST/bin/javascript-unknown-ghcjs-hsc2hs"

echo "---- platform string and RTS ways ----"
python3 - << PY
import ast
s = ast.literal_eval(open("$target_lib/settings").read())
d = dict(s)
for k in ("target platform string", "target arch", "cross compiling", "RTS ways", "Use interpreter", "Tables next to code", "Use LibFFI"):
    print(f"{k}: {d.get(k)}")
PY

# unlit runs on the build machine, even when processing target sources.
mkdir -p "$DIST/lib/targets/javascript-unknown-ghcjs/bin"
ln -sfn "$DIST/bin/unlit" "$DIST/lib/targets/javascript-unknown-ghcjs/bin/unlit"
echo "---- selected target ----"
echo -n "symlink:   "; "$DIST/bin/javascript-unknown-ghcjs-ghc" --print-libdir
echo -n "native:    "; "$DIST/bin/ghc" --print-libdir
linkdir=$("$DIST/bin/javascript-unknown-ghcjs-ghc" --print-libdir)
natdir=$("$DIST/bin/ghc" --print-libdir)
[[ "$linkdir" == "$target_lib" ]]
[[ "$natdir" != "$target_lib" ]]
echo "list-targets:"
"$DIST/bin/ghc" -list-targets
# One compiler: the target command must resolve to the exact same binary.
[[ "$DIST/bin/javascript-unknown-ghcjs-ghc" -ef "$DIST/bin/ghc" ]]
[[ $(sha256_file "$DIST/bin/javascript-unknown-ghcjs-ghc") == $(sha256_file "$DIST/bin/ghc") ]]
echo "ONE_GHC_SHA256=$(sha256_file "$DIST/bin/ghc")"
echo "settings ok"
""")
# ---- 7. building the JS target libraries -----------------------------------
md("js-stage3-md", r"""
## Building the JS target libraries (`stage3-js`)

`cabal.project` uses `build:any.<pkg> installed` to keep native boot deps installed and unprefixed `source` constraints for target boot libraries; `package host:*` sets static/vanilla flags with `--with-gcc=emcc`, and `ghc-internal` gets `--with-compiler=<target ghc>`.
Builds `rts:lib:nonthreaded-nodebug` first with `-w <target ghc> -W <native ghc>`, then `all`; host units (`"machine": "host"` in `plan.json`) are registered into the target package DB. Idempotent unless `FORCE_STAGE3_REBUILD=1`.
`allow-newer: all` relaxes bounds against the new `base`; the interpreter and app checks — not solving — establish compatibility.
""")

code("js-stage3", r"""
%%bash
set -euo pipefail
ROOT=$(cat "$HOME/.cross-ghc-js-root")
# shellcheck disable=SC1091
source "$ROOT/gate-js.env"
cross_enter
export EM_CACHE

TRIPLE=javascript-unknown-ghcjs
TARGET_ARCH=javascript
TARGET_OS=ghcjs
TARGET_PLATFORM=javascript-unknown-ghcjs
mkdir -p "$STAGE3"
tdb="$DIST/lib/targets/$TRIPLE/lib/package.conf.d"
PKG="$DIST/bin/$TRIPLE-ghc-pkg"
if [[ -z "${FORCE_STAGE3_REBUILD:-}" && -d "$tdb" ]] && "$PKG" check >/dev/null 2>&1; then
  shopt -s nullglob
  confs=("$tdb"/*.conf)
  shopt -u nullglob
  if (( ${#confs[@]} > 0 )); then
    echo "Target libraries already registered (${#confs[@]} units) — skipping stage3 build."
    echo "Set FORCE_STAGE3_REBUILD=1 before starting Jupyter to rebuild."
    "$PKG" check
    exit 0
  fi
fi
if [[ -z "${FORCE_STAGE3_REBUILD:-}" && -d "$STAGE3/exported-conf" ]] && [[ -f "$STAGE3/sources.lock" ]] && cmp -s "$STAGE3/sources.lock" "$CROSS_ROOT/sources.lock"; then
  for conf in "$STAGE3/exported-conf"/*.conf; do
    [[ -e "$conf" ]] || continue
    "$PKG" --package-db="$tdb" update --force "$conf"
  done
  "$PKG" --package-db="$tdb" recache
  if "$PKG" --package-db="$tdb" check >/dev/null 2>&1 \
    && "$PKG" --package-db="$tdb" field base id >/dev/null 2>&1; then
    echo "Restored cached stage3-js registrations from $STAGE3/exported-conf — skipping rebuild."
    exit 0
  else
    rm -f "$tdb"/*.conf
    "$PKG" --package-db="$tdb" recache
  fi
fi

cat > "$STAGE3/cabal.project" << EOF
active-repositories: :none
allow-boot-library-installs: True
tests: False
benchmarks: False
jobs: $BUILD_JOBS
allow-newer: all

packages:
  $SRC/rts-headers
  $SRC/rts-fs
  $SRC/rts
  $SRC/libraries/ghc-internal
  $SRC/libraries/ghc-prim
  $SRC/libraries/ghc-bignum
  $SRC/libraries/base
  $SRC/libraries/ghc-boot-th
  $SRC/libraries/template-haskell
  $SRC/libraries/ghc-boot
  $SRC/libraries/ghc-platform
  $SRC/libraries/ghc-heap
  $SRC/libraries/ghci
  $SRC/libraries/system-cxx-std-lib

  https://hackage.haskell.org/package/array-0.5.8.0/array-0.5.8.0.tar.gz
  https://hackage.haskell.org/package/binary-0.8.9.3/binary-0.8.9.3.tar.gz
  https://hackage.haskell.org/package/bytestring-0.12.2.0/bytestring-0.12.2.0.tar.gz
  https://hackage.haskell.org/package/containers-0.8/containers-0.8.tar.gz
  https://hackage.haskell.org/package/deepseq-1.5.1.0/deepseq-1.5.1.0.tar.gz
  https://hackage.haskell.org/package/directory-1.3.10.0/directory-1.3.10.0.tar.gz
  https://hackage.haskell.org/package/exceptions-0.10.11/exceptions-0.10.11.tar.gz
  https://hackage.haskell.org/package/file-io-0.1.5/file-io-0.1.5.tar.gz
  https://hackage.haskell.org/package/filepath-1.5.4.0/filepath-1.5.4.0.tar.gz
  https://hackage.haskell.org/package/os-string-2.0.8/os-string-2.0.8.tar.gz
  https://hackage.haskell.org/package/pretty-1.1.3.6/pretty-1.1.3.6.tar.gz
  https://hackage.haskell.org/package/time-1.15/time-1.15.tar.gz
  https://hackage.haskell.org/package/transformers-0.6.1.2/transformers-0.6.1.2.tar.gz
  https://hackage.haskell.org/package/unix-2.8.8.0/unix-2.8.8.0.tar.gz
  https://hackage.haskell.org/package/stm-2.5.3.1/stm-2.5.3.1.tar.gz
  https://hackage.haskell.org/package/mtl-2.3.1/mtl-2.3.1.tar.gz
  https://hackage.haskell.org/package/parsec-3.1.18.0/parsec-3.1.18.0.tar.gz
  https://hackage.haskell.org/package/process-1.6.29.0/process-1.6.29.0.tar.gz
  https://hackage.haskell.org/package/semaphore-compat-2.0.1/semaphore-compat-2.0.1.tar.gz
  https://hackage.haskell.org/package/text-2.1.3/text-2.1.3.tar.gz
  https://hackage.haskell.org/package/Cabal-syntax-3.14.1.0/Cabal-syntax-3.14.1.0.tar.gz
  https://hackage.haskell.org/package/alex-3.5.2.0/alex-3.5.2.0.tar.gz

package host:*
  library-vanilla: True
  shared: False
  library-for-ghci: False
  executable-dynamic: False
  executable-profiling: False
  ghc-options: -j$GHC_JOBS -O0

package ghc-internal
  configure-options: --with-compiler=$DIST/bin/$TRIPLE-ghc
  flags: +bignum-native
  ghc-options: -no-rts

package rts-fs
  ghc-options: -no-rts

package rts-headers
  ghc-options: -no-rts

package rts
  flags: +use-system-libffi -tables-next-to-code -dynamic -profiling -debug -threaded
  ghc-options: -j$GHC_JOBS -O0 -no-rts
  ghc-options: "-optc-DProjectVersion=\\"914\\""
  ghc-options: "-optc-DHostArch=\\"$TARGET_ARCH\\""
  ghc-options: "-optc-DHostOS=\\"$TARGET_OS\\""
  ghc-options: "-optc-DHostPlatform=\\"$TARGET_PLATFORM\\""
  ghc-options: "-optc-DHostVendor=\\"unknown\\""
  ghc-options: "-optc-DBuildArch=\\"$BUILD_ARCH\\""
  ghc-options: "-optc-DBuildOS=\\"$BUILD_OS\\""
  ghc-options: "-optc-DBuildPlatform=\\"$BUILD_PLATFORM\\""
  ghc-options: "-optc-DBuildVendor=\\"$BUILD_VENDOR\\""
  ghc-options: "-optc-DGhcUnregisterised=\\"NO\\""
  ghc-options: "-optc-DTablesNextToCode=\\"NO\\""
  ghc-options: "-optc-DFS_NAMESPACE=rts"

package ghci
  flags: +internal-interpreter +use-system-libffi

package unix
  flags: +os-string

package file-io
  flags: +os-string

package directory
  flags: +os-string

package process
  flags: +os-string

package text
  flags: -simdutf

constraints:
  build:any.ghc-internal installed,
  build:any.base installed,
  build:any.ghc-prim installed,
  build:any.ghc-bignum installed,
  build:any.template-haskell installed,
  build:any.ghc-boot-th installed,
  build:any.ghc-boot installed,
  build:any.ghc-platform installed,
  build:any.ghc-heap installed,
  build:any.rts installed,
  build:any.Cabal installed,
  build:any.Cabal-syntax installed,
  build:any.array installed,
  build:any.binary installed,
  build:any.bytestring installed,
  build:any.containers installed,
  build:any.deepseq installed,
  build:any.directory installed,
  build:any.filepath installed,
  build:any.pretty installed,
  build:any.stm installed,
  build:any.mtl installed,
  build:any.process installed,
  build:any.text installed,
  build:any.parsec installed,
  build:any.time installed,
  build:any.transformers installed,
  build:any.unix installed,
  build:any.exceptions installed,
  build:any.system-cxx-std-lib installed,
  build:any.os-string installed,
  build:any.file-io installed,
  build:any.semaphore-compat installed,
  array source,
  base source,
  binary source,
  bytestring source,
  containers source,
  deepseq source,
  directory source,
  exceptions source,
  file-io source,
  filepath source,
  ghc-bignum source,
  ghc-boot source,
  ghc-platform source,
  Cabal-syntax source,
  ghc-boot-th source,
  ghc-heap source,
  ghc-internal source,
  ghc-prim source,
  ghci source,
  os-string source,
  pretty source,
  rts source,
  rts-headers source,
  rts-fs source,
  template-haskell source,
  time source,
  stm source,
  mtl source,
  parsec source,
  process source,
  semaphore-compat source,
  text source,
  transformers source,
  unix source,
  system-cxx-std-lib source
EOF

export DERIVE_CONSTANTS GENAPPLY
DERIVE_CONSTANTS=$(cat "$CROSS_ROOT/deriveConstants.path")
GENAPPLY=$(cat "$CROSS_ROOT/genapply.path")
export NM=emnm
if command -v llvm-objdump >/dev/null; then export OBJDUMP=llvm-objdump
else export OBJDUMP=llvm-objdump-19; fi
unset CC CXX AR RANLIB || true

cd "$STAGE3"
cabal_build() {
"$CABAL" --store-dir="$STAGE3/store" build -j"$BUILD_JOBS" \
  -w "$DIST/bin/$TRIPLE-ghc" \
  -W "$DIST/bin/ghc" \
  --with-hc-pkg="$DIST/bin/$TRIPLE-ghc-pkg" \
  --with-build-hc-pkg="$DIST/bin/ghc-pkg" \
  --with-gcc=emcc \
  --with-hsc2hs="$DIST/bin/hsc2hs" \
  --with-build-hsc2hs="$DIST/bin/hsc2hs" \
  --hsc2hs-options=-x \
  --ghc-option="-ghcversion-file=$SRC/rts/include/ghcversion.h" \
  "$@"
}
# Complete the RTS way first. This prevents its interfaces from retaining
# placeholder RTS unit IDs during bootstrap.
cabal_build rts:lib:nonthreaded-nodebug
cabal_build all process:lib:process

# Export only host library registrations, from both in-place and store DBs.
python3 - "$STAGE3" "$tdb" "$DIST/bin/$TRIPLE-ghc-pkg" "$TARGET_ARCH" <<'PY_REGISTER'
import json, re, subprocess, sys
from pathlib import Path
stage, dest, ghcpkg = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
plan = json.loads((stage / 'dist-newstyle/cache/plan.json').read_text())
assert plan['arch'] == sys.argv[4]
host = {u['id'] for u in plan['install-plan'] if u.get('machine') == 'host'}
databases = set()
for tree in (stage / 'store', stage / 'dist-newstyle/packagedb'):
    if tree.exists():
        databases.update(p.parent for p in tree.rglob('package.cache'))
assert databases, 'No Cabal package databases found'
registrations = {}
for db in sorted(databases):
    dump = subprocess.check_output([ghcpkg, '--no-user-package-db',
          '--package-db=' + str(db), 'dump', '--expand-pkgroot'], text=True)
    for block in re.split(r'(?m)^---\s*$', dump):
        match = re.search(r'(?m)^id:\s*(\S+)', block)
        if match and match[1] in host:
            registrations[match[1]] = block.strip() + '\n'
assert registrations, 'No target library registrations matched the host plan'
export = stage / 'exported-conf'
export.mkdir(exist_ok=True)
for unit_id, block in registrations.items():
    conf = export / (unit_id + '.conf')
    conf.write_text(block)
    subprocess.run([ghcpkg, '--package-db=' + str(dest), 'update',
                    '--force', str(conf)], check=True)
subprocess.run([ghcpkg, '--package-db=' + str(dest), 'recache'], check=True)
subprocess.run([ghcpkg, '--package-db=' + str(dest), 'check'], check=True)
print('Registered', len(registrations), 'host library units')
PY_REGISTER
cp "$CROSS_ROOT/sources.lock" "$STAGE3/sources.lock"
for package in base template-haskell text containers; do
  "$DIST/bin/$TRIPLE-ghc-pkg" field "$package" id
done
""")
# ---- 8. target context & TH smoke test ---------------------------------------
md("js-smoke-md", r"""
## Target context & TH smoke test in Node

The external interpreter `ghc-interp.js` runs hosted by `node`: splices evaluate in Node's filesystem context (`readFile "catalogue.txt"` resolves there), while build-machine IO (`git --version`) stays in native `Setup.hs`.
The package source directory stays the working directory on both sides; `addDependentFile` records inputs for recompilation without transferring files.
Compiling `Arch.hs` with `-fexternal-interpreter` and running `arch.jsexe/all.js` under `node` must print `("javascript","ghcjs")`, proving the Node interpreter round-trip.
""")

code("js-smoke", r"""
%%bash
set -euo pipefail
ROOT=$(cat "$HOME/.cross-ghc-js-root")
source "$ROOT/gate-js.env"
cross_enter
export EM_CACHE

mkdir -p "$CROSS_ROOT/smoke-js"
cat > "$CROSS_ROOT/smoke-js/Arch.hs" <<'EOF'
{-# LANGUAGE TemplateHaskell #-}
import Language.Haskell.TH.Syntax (lift)
import System.Info (arch, os)
main :: IO ()
main = print $(lift (arch, os))
EOF
"$DIST/bin/javascript-unknown-ghcjs-ghc" \
  -fforce-recomp -O0 -fexternal-interpreter \
  -package base -package template-haskell \
  -o "$CROSS_ROOT/smoke-js/arch" "$CROSS_ROOT/smoke-js/Arch.hs"
out=$(node "$CROSS_ROOT/smoke-js/arch.jsexe/all.js")
printf 'Splice evaluated for: %s\n' "$out"
[[ "$out" == '("javascript","ghcjs")' ]]
""")

# ---- 9. the cross-report application ----------------------------------------
md("js-report-md", r"""
## A report prepared at compile time

`TH.hs` validates `catalogue.txt` (labels, bounds, unique names), records it via `addDependentFile`, reads `build-tool.txt`, and lifts `(results, arch, os, gitVersion)`; splice file IO runs in the Node interpreter and never spawns processes.
Native `Setup.hs` (build-side `process` dep) writes `build-tool.txt` from `git --version`. The app embeds the table into a `Map` and serves list/lookup/`--info` with no runtime file IO.
""")

code("js-report-files", r"""
%%bash
set -euo pipefail
ROOT=$(cat "$HOME/.cross-ghc-js-root")
source "$ROOT/gate-js.env"
cross_enter

mkdir -p "$FIBO"
cat > "$FIBO/catalogue.txt" <<'EOF'
small 10
century 100
large 100000
EOF
cat > "$FIBO/TH.hs" <<'EOF'
{-# LANGUAGE BangPatterns #-}
{-# LANGUAGE TemplateHaskell #-}
module TH (catalogueTH) where

import Control.Monad (unless)
import Data.Char (isAsciiLower, isDigit)
import Data.List (nub)
import Language.Haskell.TH (Exp, Q)
import Language.Haskell.TH.Syntax (addDependentFile, lift, runIO)
import System.Info (arch, os)
import Text.Read (readMaybe)

-- The generator from the 2017 post: linear iterations on growing Integers.
-- The modulus is applied after computing the Fibonacci number.
fibo :: Integer -> Integer
fibo = (`rem` 10^10) . go (0, 1)
  where
    go (a, _) 0 = a
    go (!a, !b) n = go (b, a + b) (n - 1)

parseRow :: (Int, String) -> Either String (String, Integer)
parseRow (line, input) = case words input of
  [name, number]
    | not (null name) && all valid name
    , Just n <- readMaybe number
    , n >= 0 && n <= 100000 -> Right (name, n)
  _ -> Left $ "catalogue.txt:" ++ show line ++ ": expected LABEL INDEX (0..100000)"
  where
    valid c = isAsciiLower c || isDigit c || c == '-'

-- Values are lifted into the application. The IO happens in the interpreter.
catalogueTH :: FilePath -> Q Exp
catalogueTH file = do
  addDependentFile file
  rows <- runIO $ do
    input <- readFile file
    either fail pure (traverse parseRow (zip [1..] (lines input)))
  let names = map fst rows
  unless (not (null rows) && length names == length (nub names)) $
    fail "catalogue.txt must be nonempty and labels must be unique"
  addDependentFile "build-tool.txt"
  gitVersion <- runIO (readFile "build-tool.txt")
  let results = [(name, fibo n) | (name, n) <- rows]
  lift (results, arch, os, takeWhile (/= '\n') gitVersion)
EOF
cat > "$FIBO/Main.hs" <<'EOF'
{-# LANGUAGE OverloadedStrings #-}
{-# LANGUAGE TemplateHaskell #-}
module Main where

import qualified Data.Map.Strict as Map
import qualified Data.Text as T
import qualified Data.Text.IO as T
import System.Environment (getArgs)
import System.Exit (die)
import System.Info (arch, os)
import TH (catalogueTH)

embedded :: ([(String, Integer)], String, String, String)
embedded = $(catalogueTH "catalogue.txt")

table :: Map.Map T.Text Integer
table = Map.fromList [(T.pack name, value) | (name, value) <- rows]
  where (rows, _, _, _) = embedded

main :: IO ()
main = do
  args <- getArgs
  case args of
    [] -> mapM_ printRow (Map.toAscList table)
    ["--list"] -> mapM_ T.putStrLn (Map.keys table)
    ["--info"] -> do
      let (_, spliceArch, spliceOS, gitVersion) = embedded
      putStrLn $ "SPLICE_ARCH=" ++ spliceArch
      putStrLn $ "SPLICE_OS=" ++ spliceOS
      putStrLn $ "RUNTIME_ARCH=" ++ arch
      putStrLn $ "RUNTIME_OS=" ++ os
      putStrLn $ "BUILD_TOOL=" ++ gitVersion
    [name] -> case Map.lookup (T.pack name) table of
      Just value -> print value
      Nothing -> die ("Unknown catalogue entry: " ++ name)
    _ -> die "Usage: cross-report [--list | --info | LABEL]"
  where
    printRow (name, value) = T.putStrLn (name <> "=" <> T.pack (show value))
EOF
cat > "$FIBO/Setup.hs" <<'EOF'
import Distribution.Simple (defaultMain)
import System.Info (arch, os)
import System.Process (readProcess)
main :: IO ()
main = do
  putStrLn $ "BUILD_MACHINE_ARCH=" ++ arch
  putStrLn $ "BUILD_MACHINE_OS=" ++ os
  version <- readProcess "git" ["--version"] ""
  writeFile "build-tool.txt" version
  defaultMain
EOF
cat > "$FIBO/cross-report.cabal" <<EOF
cabal-version: 3.0
name: cross-report
version: 0.1.0.0
build-type: Custom
synopsis: A catalogue report prepared with cross-compiled Template Haskell
description: Tutorial application embedding validated data and tool provenance.
license: BSD-3-Clause
category: Development
maintainer: tutorial@example.invalid
extra-source-files: catalogue.txt

custom-setup
  setup-depends: base >= 4 && < 5, Cabal >= 3.0 && < 3.20, Cabal-syntax >= 3.14 && < 3.20, process >= 1.6 && < 1.7

executable cross-report
  default-language: Haskell2010
  main-is: Main.hs
  other-modules: TH
  hs-source-dirs: .
  build-depends:
      base >= 4.22 && < 4.23
    , template-haskell >= 2.24 && < 2.25
    , text >= 2.1 && < 2.2
    , containers >= 0.8 && < 0.9
EOF
cat > "$FIBO/cabal.project" <<EOF
active-repositories: :none
allow-boot-library-installs: True
jobs: $BUILD_JOBS
documentation: False
packages: .
package host:*
  shared: False
  executable-dynamic: False
  library-vanilla: True
  optimization: False
constraints:
  build:any.base installed,
  build:any.Cabal installed,
  build:any.Cabal-syntax installed,
  build:any.process installed,
  any.ghc-internal installed,
  base installed,
  template-haskell installed,
  text installed,
  containers installed
EOF
ls -l "$FIBO"
""")
# ---- 10. giving Cabal both compilers ------------------------------------------
md("js-cabal-md", r"""
## Giving Cabal both compilers

`-w javascript-unknown-ghcjs-ghc` (+ target `ghc-pkg`) builds the app; `-W ghc` (+ native `ghc-pkg`) builds `Setup.hs` and tools. Keep cross `CC` out of the environment; the target CC lives in the target settings.
Checks `BUILD_MACHINE_ARCH=<build arch>` in the log and `host`/`build` annotations in `plan.json`; `allow-boot-library-installs` + `any.ghc-internal installed` reuses the in-place target unit.
The cell cleans only this small app build before collecting evidence; the stage 3 library build stays in place.
""")

code("js-report-build", r"""
%%bash
set -euo pipefail
ROOT=$(cat "$HOME/.cross-ghc-js-root")
source "$ROOT/gate-js.env"
cross_enter
export EM_CACHE

cd "$FIBO"
# Ensure the log belongs to this build and Setup runs again.
"$CABAL" clean
"$CABAL" build -j"$BUILD_JOBS" \
  -w "$DIST/bin/javascript-unknown-ghcjs-ghc" \
  -W "$DIST/bin/ghc" \
  --with-hc-pkg="$DIST/bin/javascript-unknown-ghcjs-ghc-pkg" \
  --with-build-hc-pkg="$DIST/bin/ghc-pkg" \
  exe:cross-report 2>&1 | tee "$CROSS_ROOT/report-js-build.log"
grep -Fx "BUILD_MACHINE_ARCH=$BUILD_ARCH" "$CROSS_ROOT/report-js-build.log"
grep -Fx "BUILD_MACHINE_OS=$BUILD_OS" "$CROSS_ROOT/report-js-build.log"
python3 - "$FIBO/dist-newstyle/cache/plan.json" <<'PY_PLAN'
import json, sys
plan = json.load(open(sys.argv[1]))
units = plan['install-plan']
print('plan arch:', plan['arch'])
for unit in units:
    if unit.get('pkg-name') in {'cross-report', 'base', 'Cabal', 'template-haskell', 'text', 'process'}:
        print(unit.get('machine'), unit.get('pkg-name'), unit.get('component-name', ''), unit['type'])
app = [u for u in units if u.get('pkg-name') == 'cross-report' and (u.get('component-name') == 'exe:cross-report' or 'exe:cross-report' in u.get('components', {}))]
assert app and all(u.get('machine') == 'host' for u in app), 'Expected host application unit'
assert any(u.get('machine') == 'build' and u.get('pkg-name') == 'Cabal' for u in units), 'Expected build-side Cabal'
PY_PLAN
bin=$("$CABAL" list-bin \
  -w "$DIST/bin/javascript-unknown-ghcjs-ghc" -W "$DIST/bin/ghc" \
  --with-hc-pkg="$DIST/bin/javascript-unknown-ghcjs-ghc-pkg" \
  --with-build-hc-pkg="$DIST/bin/ghc-pkg" exe:cross-report)
[[ -n "$bin" ]]
printf '%s\n' "$bin" > "$CROSS_ROOT/report-js-bin.path"
""")

# ---- 11. running the report -------------------------------------------------
md("js-run-md", r"""
## Running the report

Run `cross-report.jsexe/all.js` under `node`: `--info` must show `SPLICE_ARCH=javascript` / `RUNTIME_ARCH=javascript`, with the setup log reporting the build arch.
Lookups return `small=55`, `century=9261915075`, `large=3428746875` (`fib(100000) mod 10^10`).
""")

code("js-run", r"""
%%bash
set -euo pipefail
ROOT=$(cat "$HOME/.cross-ghc-js-root")
source "$ROOT/gate-js.env"
cross_enter

bin=$(cat "$CROSS_ROOT/report-js-bin.path")
js=""
for cand in "$bin.jsexe/all.js" "$(dirname "$bin")/cross-report.jsexe/all.js" "$bin"; do
  if [[ -f "$cand" ]]; then js="$cand"; break; fi
done
[[ -n "$js" ]] || { echo "No .jsexe bundle found for $bin" >&2; exit 1; }
echo "Running $js"
run_report() { node "$js" "$@"; }
run_report | tee "$CROSS_ROOT/report-js.txt"
grep -Fx 'small=55' "$CROSS_ROOT/report-js.txt"
grep -Fx 'century=9261915075' "$CROSS_ROOT/report-js.txt"
grep -Fx 'large=3428746875' "$CROSS_ROOT/report-js.txt"
info=$(run_report --info)
printf '%s\n' "$info" | tee "$CROSS_ROOT/report-js-info.txt"
for expected in SPLICE_ARCH=javascript SPLICE_OS=ghcjs RUNTIME_ARCH=javascript RUNTIME_OS=ghcjs; do
  grep -Fx "$expected" <<< "$info"
done
grep -q '^BUILD_TOOL=git version ' <<< "$info"
[[ $(run_report small) == 55 ]]
[[ $(run_report century) == 9261915075 ]]
[[ $(run_report large) == 3428746875 ]]
[[ $(run_report --list) == $'century\nlarge\nsmall' ]]
grep -Fx "BUILD_MACHINE_ARCH=$BUILD_ARCH" "$CROSS_ROOT/report-js-build.log"
if run_report missing > "$CROSS_ROOT/report-js-error.txt" 2>&1; then
  echo "Unknown catalogue label unexpectedly succeeded" >&2; exit 1
fi
grep -Fx 'Unknown catalogue entry: missing' "$CROSS_ROOT/report-js-error.txt"
echo "Cross compilation checks passed."
""")

md("js-expected-md", r"""
Expected catalogue output, in map order:

```text
century=9261915075
large=3428746875
small=55
```
Passing means one Cabal build used both compilers: native setup recorded the tool version, the Node interpreter embedded the catalogue values, and the JS app served them.
""")

# ---- 12. negative splice + single-compiler identity --------------------------
md("js-negative-md", r"""
## Making the splice fail

A bad catalogue must fail compilation: `addDependentFile` triggers re-evaluation and validation rejects it; the cell restores the file (via `trap`) and rebuilds.
Target names select settings and package DBs but resolve to the same native binary — verified by `ONE_GHC_SHA256=` hash equality, with no GHC rebuild for the new target.
""")

code("js-negative", r"""
%%bash
set -euo pipefail
ROOT=$(cat "$HOME/.cross-ghc-js-root")
source "$ROOT/gate-js.env"
cross_enter
export EM_CACHE

cd "$FIBO"
cp catalogue.txt catalogue.txt.saved
trap 'mv -f catalogue.txt.saved catalogue.txt' EXIT
printf 'broken -1\n' > catalogue.txt
# Ensure the timestamp advances even on coarse timestamp filesystems.
sleep 1
touch catalogue.txt
build_report() {
  "$CABAL" build -j"$BUILD_JOBS" \
    -w "$DIST/bin/javascript-unknown-ghcjs-ghc" -W "$DIST/bin/ghc" \
    --with-hc-pkg="$DIST/bin/javascript-unknown-ghcjs-ghc-pkg" \
    --with-build-hc-pkg="$DIST/bin/ghc-pkg" exe:cross-report
}
if build_report > "$CROSS_ROOT/report-js-invalid-build.log" 2>&1; then
  echo "The invalid catalogue unexpectedly compiled" >&2; exit 1
fi
grep -F 'expected LABEL INDEX (0..100000)' "$CROSS_ROOT/report-js-invalid-build.log"
mv -f catalogue.txt.saved catalogue.txt
trap - EXIT
sleep 1
touch catalogue.txt
build_report
bin=$(cat "$CROSS_ROOT/report-js-bin.path")
js=""
for cand in "$bin.jsexe/all.js" "$(dirname "$bin")/cross-report.jsexe/all.js" "$bin"; do
  if [[ -f "$cand" ]]; then js="$cand"; break; fi
done
[[ $(node "$js" large) == 3428746875 ]]
echo "Compile-time validation and restored rebuild checked."
# One compiler throughout: the target command still resolves to the same binary.
[[ "$DIST/bin/javascript-unknown-ghcjs-ghc" -ef "$DIST/bin/ghc" ]]
[[ $(sha256_file "$DIST/bin/javascript-unknown-ghcjs-ghc") == $(sha256_file "$DIST/bin/ghc") ]]
echo "ONE_GHC_SHA256=$(sha256_file "$DIST/bin/ghc")"
""")

# ---- 13. references ---------------------------------------------------------
md("js-refs", r"""
## Going further & references

Reuse the same compiler pair for another app: target deps must be installed/buildable, native generators in the build graph, and splice IO files visible to the Node-hosted `ghc-interp.js` (same pattern covers WASI/QEMU backends).
* [GHC `stable-ghc-9.14`](https://github.com/stable-haskell/ghc/tree/stable-ghc-9.14) and [Andrea's Cabal branch](https://github.com/andreabedini/cabal/tree/andrea/feature/cross-compilation-2).
* [Target selection](https://github.com/stable-haskell/ghc/blob/stable-ghc-9.14/ghc/Main.hs), [settings reader](https://github.com/stable-haskell/ghc/blob/stable-ghc-9.14/compiler/GHC/Settings/IO.hs), [toolchain generator](https://github.com/stable-haskell/ghc/blob/stable-ghc-9.14/utils/ghc-toolchain/exe/Main.hs).
* [RTS sublibraries](https://github.com/stable-haskell/ghc/blob/stable-ghc-9.14/rts/rts.cabal), [haskell.nix cross-TH docs](https://github.com/input-output-hk/haskell.nix/blob/master/docs/tutorials/cross-template-haskell.md).
""")


# ---------------------------------------------------------------------------
def to_source(text):
    text = text.strip("\n")
    return text.splitlines(keepends=True)


nb_cells = []
for ctype, cid, text in cells:
    src = to_source(text)
    if ctype == "markdown":
        nb_cells.append({"cell_type": "markdown", "id": cid, "metadata": {}, "source": src})
    else:
        nb_cells.append({
            "cell_type": "code",
            "id": cid,
            "execution_count": None,
            "metadata": {"tags": []},
            "outputs": [],
            "source": src,
        })

nb = {
    "cells": nb_cells,
    "metadata": {
        "kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
        "language_info": {
            "name": "python",
            "version": "3",
        },
    },
    "nbformat": 4,
    "nbformat_minor": 5,
}

out = sys.argv[1] if len(sys.argv) > 1 else "JS TH Cross.ipynb"
with open(out, "w") as f:
    json.dump(nb, f, indent=1, ensure_ascii=False)
    f.write("\n")
print(f"wrote {out} with {len(nb_cells)} cells")
