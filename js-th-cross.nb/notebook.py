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

This walks end-to-end through cross-compiling Haskell with stable-haskell
GHC 9.14 and the cross-aware Cabal to **JavaScript
(`javascript-unknown-ghcjs`)**, evaluating Template Haskell splices via GHC's
JS interpreter in Node.js while running `Setup.hs` and build tools natively
on the build machine.

What you'll do:

1. Set up a build tree (`CROSS_ROOT`) with per-machine build identity.
2. Check the **JS & C toolchain** (`emcc`, `node`) the target needs.
3. Fetch and build GHC 9.14 plus the cross-aware Cabal from source (or reuse a cached build).
4. Prepare the JavaScript target source tree and teach GHC about `javascript-unknown-ghcjs`.
5. Build the JS target libraries (`stage3-js`).
6. Smoke-test the target context: a TH splice evaluated in Node.
7. Build and run the `cross-report` application — catalogue validation at compile time, Fibonacci residues at runtime.
8. Break the splice input on purpose, then prove the compiler is still one binary.

There are two changes worth keeping in mind:

* **GHC selects a target's settings and libraries.** The compiler executable stays native. Invoking it as `javascript-unknown-ghcjs-ghc` selects that target directory.
* **Cabal accepts compiler commands for both machines.** `-w` selects the compiler for the application and its libraries; `-W` selects the native compiler for setup programs and build tools.

Both GHC commands resolve to the same executable. The target-prefixed name selects a different set of settings and libraries. The native and target commands report different platforms and use separate package databases.

| Part | Compiled for | Runs where |
|---|---|---|
| GHC and Cabal | build machine | build machine |
| `Setup.hs`, `deriveConstants`, `genapply` | build machine | build machine |
| Splice code and the JS interpreter | JavaScript | Node on the build machine |
| Report application and its libraries | JavaScript | Node here; a browser or Node when deployed |

Cabal calls the application's machine the **host**. GHC calls it the **target**. This notebook uses *build machine* and *target* below, and spells out Cabal's names where they appear in project files.

## Before we start

We use the current [`stable-haskell/ghc:stable-ghc-9.14`](https://github.com/stable-haskell/ghc/tree/stable-ghc-9.14) branch and Andrea Bedini's [`andrea/feature/cross-compilation-2`](https://github.com/andreabedini/cabal/tree/andrea/feature/cross-compilation-2) branch. We will build both from source. A native bootstrap GHC **9.8.4** and a working bootstrap `cabal` are still needed to build the tools written in Haskell.

This is a Jupyter notebook with a **Python 3 kernel**. Every executable cell starts with `%%bash`; Jupyter runs that cell in a fresh shell and reports a non-zero exit as a failed cell. Run the cells in order and resolve a failure before continuing. Configuration is saved under `CROSS_ROOT`, so it survives kernel restarts. From a shell with Jupyter installed:

```sh
jupyter notebook "JS TH Cross.ipynb"
```

Use a disposable machine (Linux or macOS), GNU Make **4.4 or newer** (`gmake` on some systems; this flake provides it), and a native GHC **9.8.4** plus a working bootstrap `cabal`. If needed, install these with GHCup before opening Jupyter:

```sh
ghcup install ghc 9.8.4
ghcup install cabal latest
```

Put `ghc-9.8.4` and `cabal` on Jupyter's `PATH`, or set `BOOT_GHC` and `BOOT_CABAL` to their absolute paths before starting it. Set `MAKE_BIN` if your GNU Make 4.4+ has another name. Building the compiler is substantially more work than building its target libraries: allow at least 32 GiB of free disk for a fresh build (4 GiB suffices once `$DIST/bin/ghc` exists) and plan for a machine with 16 GiB RAM or more. Set `BUILD_JOBS` for package and Make parallelism (default 4) and `GHC_JOBS` for modules within each compiler process (default 2). Set `CROSS_ROOT` before starting Jupyter to choose a location; keep it absolute and free of whitespace.
""")

# ---- 1. place to build ----------------------------------------------------
md("js-place", r"""
## A place to build

First we save the paths used by later cells. This is a complete source build, so use a fresh tree. Set `CROSS_ROOT` before starting Jupyter to choose a location (default: `~/cross-ghc-current`); keep it absolute and free of whitespace.

The native GHC source and target-library source use separate trees. We will leave the native compiler's RTS configuration alone and make the target-specific vanilla-RTS edit only in the target tree.
""")

code("js-gate", r"""
%%bash
set -euo pipefail
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
if [[ -n "${MAKE_BIN:-}" ]]; then :; elif command -v gmake >/dev/null; then MAKE_BIN=gmake; else MAKE_BIN=make; fi
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
  if [[ -d "$HOME/.ghcup/bin" && ":$PATH:" != *":$HOME/.ghcup/bin:"* ]]; then
    export PATH="$HOME/.ghcup/bin:$PATH"
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

Before building Haskell, let us make sure the JavaScript target's tools are available. The JS backend links through **emscripten** (`emcc`, `em++`, `emar`, `emranlib`, `emnm`), and GHC's JS interpreter (`ghc-interp.js`) runs in **Node.js 22+** — Node is also what runs the final `.jsexe/all.js` bundle. The target configure step inspects objects with `llvm-objdump` (or `llvm-objdump-19`); it does not need to execute them.

On both Linux and macOS these come from `PATH` — this flake provides them (pinned `nodejs_22`, `emscripten`, `llvmPackages.llvm`), so inside `nix develop` there is nothing to install. Outside Nix the cell falls back to Homebrew on macOS, and to an `emsdk` checkout plus a static Node download on Linux.
""")

code("js-tools", r"""
%%bash
set -euo pipefail
ROOT=$(cat "$HOME/.cross-ghc-js-root")
# shellcheck disable=SC1091
source "$ROOT/gate-js.env"
cross_enter
export EM_CACHE

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

Let us fetch the source we actually want to use. The branch names are defaults, so a fresh checkout follows their current heads. `sources.lock` records the resolved commits. `GHC_REF` and `CABAL_REF` can select another ref when reproducing a recorded build.

There are two Cabal roles during compiler construction. GHC's Makefile bootstraps a Cabal from its own `cabal.project.stage0`; that builder implements the local store layout its rules expect. The Cabal we build from **Andrea's branch** is the one we use for target libraries and the application. Passing Andrea's Cabal directly to that Makefile would not make its layout assumptions disappear.

| Tool | Source | Purpose |
|---|---|---|
| Bootstrap GHC and Cabal | existing native installation | build the Haskell tools |
| GHC's internal builder | GHC tree's `cabal.project.stage0` | construct native stage1/stage2 and the distribution |
| Tutorial Cabal | Andrea's cross-compilation branch | build target libraries and the application with native and target compilers |

### Bootstrapping an empty target

At the branch heads tested here, Cabal needs a small fix before it can bootstrap the target libraries. It pins GHC's wired-in units to installed unit IDs, even with `allow-boot-library-installs: True`. Our new target database has no `ghc-internal` yet.

The build cell permits a missing wired-in unit to be built only when boot-library installation is explicitly enabled. Existing installed units retain their exact-unit pins. This is a local patch to Andrea's branch, included in the commands below; it is not behaviour we can attribute to the unmodified branch.

If `$CABAL` and `$DIST/bin/ghc` already exist and `build-sources.lock` matches `sources.lock` — for example from a CI cache or an earlier notebook run in the same `CROSS_ROOT` — the build cell reuses them instead of rebuilding.
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
    git -C "$dest" checkout -- cabal-install/src/Distribution/Client/Dependency.hs 2>/dev/null || true
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

This cell builds the tutorial `cabal` from Andrea's branch (with the wired-in boot-library patch) and the native GHC `stage2` distribution. It is the slow cell — the source build dominates the walkthrough's running time.

**Cache-aware:** if `$CABAL` and `$DIST/bin/ghc` already exist and `build-sources.lock` matches `sources.lock` plus the recorded patch checksum, the cell reuses them and skips rebuilding. That is the path taken when resuming in a `CROSS_ROOT` restored from a CI cache or from an earlier notebook run.
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

We now have a native GHC distribution built from `stable-ghc-9.14`, and a Cabal built from Andrea's cross-compilation branch. The native distribution is at `ghc-src/_build/dist`. GHC's Makefile also places the native `ghc-toolchain-bin`, `deriveConstants` and `genapply` tools there (their paths were recorded at the end of the build cell).

Let us create a second checkout at the **same GHC commit** for the JavaScript libraries. The vanilla-RTS edit belongs here, after the native distribution has been built. The configure input for `ghc-boot-th-next` is supplied locally; we do not include that package in the target build.

The RTS's three other sublibraries contain `buildable: False` inside JavaScript conditionals. We insert an unconditional field after each sublibrary's `import` to disable those ways for this target build.
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
        text = text[:match.start()] + block + text[match.end()]
    assert re.search(rf"(?m)^library {name}\n    import:[^\n]*\n    buildable: False$", text)
path.write_text(text)
print("Only the vanilla RTS sublibrary remains buildable.")
PY_PATCH
""")
# ---- 6. teaching GHC about javascript-unknown-ghcjs -------------------------
md("js-settings-md", r"""
## Teaching GHC about `javascript-unknown-ghcjs`

Run `ghc-toolchain-bin` from the target source tree, where it can find `config.sub`. We supply the emscripten C and C++ compilers and ask for a `settings` file. Emscripten's driver handles compiling, linking and archiving, so `emar`, `emranlib` and `emnm` cover the remaining tools. `--disable-tables-next-to-code` matches the JS backend's expectations.

The directory name matters. The toolchain normalises the platform, but this GHC's command-name lookup uses the full `javascript-unknown-ghcjs` name to locate `lib/targets/<name>/lib`.

The generated settings contain `cross compiling = NO` and a fixed list of RTS ways. Those fields are hardcoded by this version of ghc-toolchain. They do not establish whether the selected code generator and libraries are native. We set the RTS ways to vanilla-only and mark dynamic and profiled libraries as unavailable for this target installation.

GHC also ships its JavaScript support files (`dyld.mjs`, `post-link.mjs`, `prelude.mjs`, `ghc-interp.js`) with the compiler sources. We copy them into the target lib directory, where the backend expects them: `ghc-interp.js` is the interpreter host that evaluates our splices in Node.
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
cp "$SRC/ghc-interp.js" "$target_lib/ghc-interp.js"
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

Here we use Andrea's Cabal with two compiler configurations. `-w` selects `javascript-unknown-ghcjs`; `-W` selects the native compiler for setup programs and build tools. The `build:any.<package> installed` constraints keep native boot dependencies installed. Unprefixed top-level `source` constraints select the target boot libraries from source.

The branch supports `package host:*` and `package build:*`. We put common target compiler options and static-library choices in `package host:*`. Named package stanzas have no machine prefix in this branch; they supply the individual package flags below. The native boot libraries remain installed, as required by the constraints. The target RTS drops position-independent code (emscripten does not want `-fPIC`), keeps tables-next-to-code off, and uses native bignum. Its C defines take the build machine's identity dynamically from `gate.env`, so the same cell works on Linux and macOS. The initial RTS-related packages use `-no-rts` to break the bootstrap dependency on an RTS we have not built yet. We also pass `--with-compiler` to the `ghc-internal` configure script explicitly: it uses that compiler to generate the primitive modules, and otherwise receives a bare `ghc` command.

We provide an explicit Cabal store under `stage3-js/store`. Andrea's Cabal uses compiler-keyed stores and an in-place package database for local packages. We therefore collect the **host library units from the build plan**, rather than assuming a fixed store layout. Registrations are read with expanded paths and checked after they are copied into the target database. Keep both the store and `dist-newstyle` tree: the registration files refer to artifacts there.

`allow-newer: all` relaxes dependency bounds against the new `base`. This is a compatibility concession; solving successfully is not sufficient to establish package compatibility. The interpreter and application checks follow it.

We explicitly request `all process:lib:process` as well as `all`. Merely listing its tarball in `packages` made it available to the solver but did not build it in this project: it was not in the selected dependency closure. The report's splice needs it. Registrations use `ghc-pkg update`; this GHC's package tool has no `--force-reinstall` option.

The cell is idempotent: if the target database already holds registrations, it skips the build unless `FORCE_STAGE3_REBUILD` is set.
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

We have one compiler, running on the build machine. Changing its name selects the target's settings and libraries. That gives us a way to produce target code. Template Haskell asks for one more thing: a place to execute some of that code while the build is still running.

That is the *target context*. For JavaScript, GHC selects **its JavaScript interpreter from the target settings**: `ghc-interp.js` runs in Node, with the filesystem and process facilities available to that Node process. There is no QEMU wrapper here. GHC stays outside, sends interpreter requests, and receives the results. Cabal, `Setup.hs` and native generators also stay on the build machine.

The context must still supply file IO and a working directory: a splice containing `runIO (readFile "catalogue.txt")` opens that file in the interpreter's context from its working directory, not in the native GHC process. We keep the package source directory as the working directory on both sides and use the same relative input names. `addDependentFile` records an input for recompilation; it does not transfer the file.

The cell below compiles a tiny program whose `main` prints the target `(arch, os)` pair lifted through a splice, then runs the resulting `Arch.jsexe/all.js` under Node. If that check succeeds, GHC has exchanged interpreter messages with the Node-hosted interpreter and embedded `("javascript","ghcjs")`.
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
  -o "$CROSS_ROOT/smoke-js/arch" "$CROSS_ROOT/smoke-js/Arch.hs"
out=$(node "$CROSS_ROOT/smoke-js/arch.jsexe/all.js")
printf 'Splice evaluated for: %s\n' "$out"
[[ "$out" == '("javascript","ghcjs")' ]]
""")

# ---- 9. the cross-report application ----------------------------------------
md("js-report-md", r"""
## A report prepared at compile time

On to the application. We will keep the Fibonacci generator in a separate module. Imported functions can be used in a top-level splice; moving the generator into the module containing that splice runs into the usual Template Haskell stage restriction. This restriction predates GHC 9.14.

Our catalogue has a label and an index on each line. The generator checks the labels, bounds the index, rejects duplicate labels, and records the file as a compilation dependency. It computes the residues and embeds ordinary Haskell values. Native `Setup.hs` records `git --version` in `build-tool.txt`; the target splice reads and tracks that file too. Both inputs use relative paths from the package source directory, shared with the interpreter's working directory. The splice performs file IO and computation only — it never launches a child process, so the Node-hosted interpreter needs no process support.

At runtime the application loads the embedded table into a `Map Text Integer`. It can list the catalogue, look up one entry, or show where its code was evaluated. It does not read the catalogue or run git at runtime.

The next cell writes the six application files. Cabal compiles `Setup.hs` with the build compiler: `process` is a build-side dependency there. `TH.hs` uses target `template-haskell` and reads the prepared files in the JS interpreter.
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

Here is the application build. `-w` selects `javascript-unknown-ghcjs`. `-W` selects the native compiler. The matching package tools are supplied explicitly too.

`Setup.hs` imports Cabal and `process`; it is compiled with the build compiler and invokes native `git`. `TH.hs` uses target `template-haskell` and reads the prepared files in the Node-hosted interpreter. A native `process` dependency belongs to Setup; target splice dependencies belong to the interpreter.

Do not export a cross `CC` globally. Keep the target C compiler in the target settings, and supply target-specific program options to Cabal where needed (`--with-gcc=emcc` went into the stage3 build). An ambient cross compiler can contaminate native setup configuration.

The build log must contain `BUILD_MACHINE_ARCH=<build arch>`. We also check the generated plan's `machine` annotations. A first build has work to do; a cached build may not rerun `Setup.hs`. The cell below cleans this small application build before collecting its evidence. It leaves the expensive library build in place.

The target boot libraries in this working tree have in-place unit IDs, while GHC's `--info` still reports the fixed `ghc-internal` ID from its native build. We keep `allow-boot-library-installs` enabled here for the local Cabal fix and explicitly require `any.ghc-internal installed`. This lets the solver use our installed target unit without trying to rebuild it.
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

Let us check the result. Both its splice and runtime architecture must be `javascript` / `ghcjs`, while the setup log reports the build machine's own architecture.

The catalogue lookups also need to return the right answers. In particular, the large entry is the same `fib(100000) mod 10^10 = 3428746875` used in the earlier example.
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

If the checks pass, we have exercised both compiler configurations in one Cabal application build. The native setup program ran on the build machine; native Setup recorded the tool version; the target interpreter read the catalogue and provenance file and produced the embedded values; the final JavaScript application used those values under Node.
""")

# ---- 12. negative splice + single-compiler identity --------------------------
md("js-negative-md", r"""
## Making the splice fail

A useful next check is to give the generator a bad catalogue. `addDependentFile` should cause GHC to revisit the splice, and validation should fail during compilation. This cell temporarily replaces the catalogue, checks for that failure, restores it, and rebuilds. The `trap` also restores the input if the check fails unexpectedly.

Finally, the target names select settings and package databases — but they must still resolve to the same native executable, whose hash must remain unchanged. Adding a target required its libraries and auxiliary tools; it did not require rebuilding GHC.
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

To try another application, replace the report package and keep the same compiler pair. Its target dependencies must be available or buildable, and any native generators must be in Cabal's build graph. TH that performs file or process IO also needs the relevant files and tools visible to the interpreter — for JavaScript, that means the Node process hosting `ghc-interp.js`.

The same one-compiler arrangement covers the other backends: each target gets its own settings file, package database and target-prefixed symlinks, while the compiler executable stays native. The target's execution environment is the separate choice — Node for JavaScript, a WASI runtime for `wasm32-wasi`, QEMU for foreign Linux targets.

### Source references

* [GHC branch `stable-ghc-9.14`](https://github.com/stable-haskell/ghc/tree/stable-ghc-9.14) and [Andrea's Cabal branch](https://github.com/andreabedini/cabal/tree/andrea/feature/cross-compilation-2).
* [GHC target selection](https://github.com/stable-haskell/ghc/blob/stable-ghc-9.14/ghc/Main.hs), [target settings reader](https://github.com/stable-haskell/ghc/blob/stable-ghc-9.14/compiler/GHC/Settings/IO.hs), and [toolchain settings generator](https://github.com/stable-haskell/ghc/blob/stable-ghc-9.14/utils/ghc-toolchain/exe/Main.hs).
* [RTS sublibraries](https://github.com/stable-haskell/ghc/blob/stable-ghc-9.14/rts/rts.cabal).
* [haskell.nix cross Template Haskell documentation](https://github.com/input-output-hk/haskell.nix/blob/master/docs/tutorials/cross-template-haskell.md).
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
