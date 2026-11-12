#!/usr/bin/env python3
"""Generator for "WASM TH Cross.ipynb" (python3 kernel, nbformat 4.5).

The notebook is generated from this script so edits are reproducible and the
serialized form matches tools/strip-ipynb.py (clean cells, no outputs). Run via
`make notebook`, or: python3 wasm-th-cross.nb/notebook.py "wasm-th-cross.nb/WASM TH Cross.ipynb"

Walks end-to-end through cross-compiling Haskell with Template Haskell from
source to WebAssembly/WASI (wasm32-unknown-wasi) on Linux (x86_64, aarch64)
and macOS (arm64): building GHC 9.14 + cross-aware Cabal from source,
configuring the wasm target, building dynamic + vanilla target libraries
(`v dyn`) so dyld.mjs can load .so libraries in Node during splice
evaluation, and running the final statically linked WASI executable under
wasmtime. Contrasts with wasm-hello.nb, which consumes the prebuilt bindist.
"""
import json
import sys

cells = []


def md(cid, text):
    cells.append(("markdown", cid, text))


def code(cid, text):
    cells.append(("code", cid, text))


# ---- 0. title -------------------------------------------------------------
md("wasm-th-title", r"""
# Cross Compiling Haskell with Template Haskell to WebAssembly/WASI

Build [stable-haskell GHC 9.14](https://github.com/stable-haskell/ghc/tree/stable-ghc-9.14)
and cross-aware Cabal
([`andrea/feature/cross-compilation-2`](https://github.com/andreabedini/cabal/tree/andrea/feature/cross-compilation-2))
from source, then cross-compile a TH report generator to `wasm32-unknown-wasi`
on Linux (`x86_64`, `aarch64`) and macOS (`arm64`).

1. **Single native GHC binary.** `wasm32-unknown-wasi-ghc` is a symlink to native
   `ghc`, selecting `lib/targets/wasm32-unknown-wasi/lib/{settings,package.conf.d}`.
2. **Dual-compiler Cabal.** `-w` selects `wasm32-unknown-wasi-ghc` (`host:*`);
   `-W` selects native `ghc` (`build:*` for `Setup.hs` and build tools).
3. **Two execution paths.** TH splices run through `dyld.mjs` in Node, loading
   dynamic `.so` target libraries (hence `RTS ways = "v dyn"`, `shared: True`);
   the final application links statically
   (`-static -fno-PIC -optc-fno-PIC -optc-U__PIC__`) and runs under `wasmtime`.

| Part | Compiled for | Runs where |
|---|---|---|
| GHC & Cabal | build machine | build machine |
| `Setup.hs` & tools | build machine | build machine |
| Splice & `dyld.mjs` | `wasm32-unknown-wasi` | in Node on the build machine |
| Report `.wasm` app | `wasm32-unknown-wasi` | under `wasmtime` |

Python 3 kernel; every cell starts with `%%bash`, nonzero exit fails it. Run in order; config under `CROSS_ROOT` survives restarts:

```sh
jupyter notebook "WASM TH Cross.ipynb"
```
""")

md("wasm-th-refs", r"""
## References

- Docs: <https://stable-haskell.github.io/ghc/>; wasm backend: <https://downloads.haskell.org/ghc/latest/docs/users_guide/wasm.html>
- Contrast (prebuilt bindist): `wasm-hello.nb`; bootstrap: <https://gitlab.haskell.org/ghc/ghc-wasm-meta>
- WASI SDK: <https://github.com/WebAssembly/wasi-sdk>; pinned archives: <https://github.com/haskell-wasm/ghc-wasm-bindists>; wasmtime: <https://wasmtime.dev/>
""")

# ---- 1. environment -------------------------------------------------------
md("wasm-th-place-md", r"""
## 1. A place to build

Fresh source build: set absolute, whitespace-free `CROSS_ROOT` before starting
Jupyter (default `~/cross-ghc-current`). This cell detects `BUILD_ARCH`,
`BUILD_OS`, `BUILD_VENDOR`, and `BUILD_PLATFORM`, which flow into the RTS
headers and the `Setup.hs` identity check.
""")

code("wasm-th-gate", r"""
%%bash
set -euo pipefail
export PATH="$PATH:/usr/sbin:/sbin"
fail() { echo "$*" >&2; exit 1; }
OS=$(uname -s); M=$(uname -m)
case "$OS" in
  Linux) BUILD_OS=linux; BUILD_VENDOR=unknown ;;
  Darwin) BUILD_OS=darwin; BUILD_VENDOR=apple ;;
  *) fail "Unsupported OS: $OS (need Linux or Darwin)." ;;
esac
case "$M" in
  x86_64) BUILD_ARCH=x86_64 ;;
  arm64|aarch64) BUILD_ARCH=aarch64 ;;
  *) fail "Unsupported machine: $M (need x86_64 or arm64/aarch64)." ;;
esac
BUILD_PLATFORM="$BUILD_ARCH-$BUILD_VENDOR-$BUILD_OS"
CROSS_ROOT=${CROSS_ROOT:-$HOME/cross-ghc-current}
[[ "$CROSS_ROOT" == /* && "$CROSS_ROOT" != *[[:space:]]* ]] || fail "CROSS_ROOT must be absolute and contain no whitespace."
mkdir -p "$CROSS_ROOT"
CROSS_ROOT=$(cd "$CROSS_ROOT" && pwd -P)
DIST="$CROSS_ROOT/ghc-src/_build/dist"
GHC_SRC="$CROSS_ROOT/ghc-src"
CABAL_SRC="$CROSS_ROOT/cabal-src"
SRC="$CROSS_ROOT/ghc-target-src-wasm"
CABAL="$CROSS_ROOT/cabal-dist/cabal"
STAGE3="$CROSS_ROOT/stage3-wasm"
FIBO="$CROSS_ROOT/cross-report-wasm"
WASM_SDK="$CROSS_ROOT/wasm-sdk-dist"
# Re-runs are cheap once the compiler exists (or under CI): only demand the
# full 32 GiB planning allowance for a from-scratch build.
if [ -z "${MIN_FREE_GB:-}" ]; then
  if [ "${CI:-false}" = true ] || [ -x "$DIST/bin/ghc" ]; then MIN_FREE_GB=4; else MIN_FREE_GB=32; fi
fi
avail=$(df -Pk "$CROSS_ROOT" | awk 'NR==2 {print $4}')
(( avail >= MIN_FREE_GB*1024*1024 )) || fail "Allow at least ${MIN_FREE_GB} GiB free at $CROSS_ROOT."
printf '%s\n' "$CROSS_ROOT" > "$HOME/.cross-ghc-wasm-root"
{
  printf 'export CROSS_ROOT=%q\n' "$CROSS_ROOT"
  printf 'export DIST=%q\n' "$DIST"
  printf 'export GHC_SRC=%q\n' "$GHC_SRC"
  printf 'export CABAL_SRC=%q\n' "$CABAL_SRC"
  printf 'export BOOT_GHC=%q\n' "${BOOT_GHC:-ghc-9.8.4}"
  printf 'export BOOT_CABAL=%q\n' "${BOOT_CABAL:-cabal}"
  printf 'export MAKE_BIN=%q\n' "${MAKE_BIN:-make}"
  printf 'export SRC=%q\n' "$SRC"
  printf 'export CABAL=%q\n' "$CABAL"
  printf 'export STAGE3=%q\n' "$STAGE3"
  printf 'export FIBO=%q\n' "$FIBO"
  printf 'export WASM_SDK=%q\n' "$WASM_SDK"
  printf 'export BUILD_JOBS=%q\n' "${BUILD_JOBS:-4}"
  printf 'export GHC_JOBS=%q\n' "${GHC_JOBS:-2}"
  printf 'export BUILD_ARCH=%q\n' "$BUILD_ARCH"
  printf 'export BUILD_OS=%q\n' "$BUILD_OS"
  printf 'export BUILD_VENDOR=%q\n' "$BUILD_VENDOR"
  printf 'export BUILD_PLATFORM=%q\n' "$BUILD_PLATFORM"
  printf 'export MIN_FREE_GB=%q\n' "$MIN_FREE_GB"
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
  if [ -n "${WASM_SDK:-}" ] && [ -d "$WASM_SDK/bin" ]; then
    case ":$PATH:" in *":$WASM_SDK/bin:"*) ;; *) export PATH="$WASM_SDK/bin:$PATH" ;; esac
  fi
}
EOF
} > "$CROSS_ROOT/gate-wasm.env"
cp "$CROSS_ROOT/gate-wasm.env" "$CROSS_ROOT/gate.env"
printf 'Build tree: %s\nBuild machine: %s\nTarget: wasm32-unknown-wasi\n' "$CROSS_ROOT" "$BUILD_PLATFORM"
""")

# ---- 2. WASI SDK, libffi, node, wasmtime ----------------------------------
md("wasm-th-sdk-md", r"""
## 2. WASI SDK, `libffi-wasm`, `node`, `wasmtime`

GHC shells out to `wasm32-unknown-wasi-clang(++)`, `-ar`/`-ranlib`/`-nm`,
`wasm-ld`, `llc`/`opt`/`llvm-as`; we fetch a WASI SDK plus wasm `libffi` and
bridge those names (`bootstrap.sh` fallback on non-`x86_64-linux`).
`node` (>= 22) hosts the TH interpreter (`dyld.mjs`); `wasmtime` runs the final
statically linked WASI executable.
""")

code("wasm-th-sdk", r"""
%%bash
set -euo pipefail
ROOT=$(cat "$HOME/.cross-ghc-wasm-root")
source "$ROOT/gate-wasm.env"
cross_enter
mkdir -p "$WASM_SDK/bin" "$WASM_SDK/downloads"
case "$BUILD_OS/$BUILD_ARCH" in
  linux/x86_64)
    WASI_ARCHIVE="wasi-sdk.tar.gz"
    WASI_SHA='+hsjbYR/pqtavssIuMGAcXiBj2izMCmEXrFRV9W9vkc=' ;;
  linux/aarch64)
    WASI_ARCHIVE="wasi-sdk-aarch64-linux.tar.gz"
    WASI_SHA='GAUk09LQXgF4YFo33BMUbUf/szWBrhQiIuqdoj6r9IQ=' ;;
  darwin/*)
    WASI_ARCHIVE="wasi-sdk-aarch64-darwin.tar.gz"
    WASI_SHA='/NgLdK8gyPTy+Xg4UmINuOgO188/0WLnYAGIGfp94ro=' ;;
  *) echo "Unsupported build platform: $BUILD_OS/$BUILD_ARCH" >&2; exit 1 ;;
esac
WASI_URL="https://github.com/haskell-wasm/ghc-wasm-bindists/releases/download/20251219T213239/$WASI_ARCHIVE"
LIBFFI_URL=https://github.com/haskell-wasm/ghc-wasm-bindists/releases/download/20250310T060803/libffi-wasm
LIBFFI_SHA='Um9Gcu9zKl0pzFYL3RmAnyFJozOPKVoq1NnU2jilI0s='
fetch() {
  [ -f "$WASM_SDK/downloads/$2" ] || \
    curl -fLsS --retry 3 --connect-timeout 20 --max-time 900 -o "$WASM_SDK/downloads/$2" "$1"
}
check_b64() {
  python3 - "$WASM_SDK/downloads/$1" "$2" <<'PY'
import base64, hashlib, sys
d = hashlib.file_digest(open(sys.argv[1], 'rb'), 'sha256').digest()
assert base64.b64decode(sys.argv[2]) == d, 'hash mismatch for ' + sys.argv[1]
PY
}
install_pinned() {
  fetch "$WASI_URL" "$WASI_ARCHIVE"
  check_b64 "$WASI_ARCHIVE" "$WASI_SHA"
  if [ ! -x "$WASM_SDK/wasi-sdk/bin/clang" ]; then
    rm -rf "$WASM_SDK/wasi-sdk"
    mkdir -p "$WASM_SDK/wasi-sdk"
    tar --no-same-owner -xzf "$WASM_SDK/downloads/$WASI_ARCHIVE" \
      -C "$WASM_SDK/wasi-sdk" --strip-components=1
  fi
  fetch "$LIBFFI_URL" libffi-wasm
  check_b64 libffi-wasm "$LIBFFI_SHA"
  python3 - "$WASM_SDK" <<'PY'
import sys, zipfile
from pathlib import Path
b = Path(sys.argv[1])
# This release asset is a zip archive despite having no filename extension.
with zipfile.ZipFile(b / 'downloads/libffi-wasm') as z:
    for n in z.namelist():
        if n.endswith('/'):
            continue
        parts = Path(n).parts[2:]
        if not parts or parts[0] not in ('include', 'lib'):
            continue
        dest = b / 'wasi-sdk/share/wasi-sysroot' / parts[0] / 'wasm32-wasi' / Path(*parts[1:])
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(z.read(n))
print('libffi-wasm overlaid into the WASI sysroot')
PY
}
install_bootstrap() {
  command -v jq >/dev/null || { echo "jq missing (this flake provides it)" >&2; exit 1; }
  command -v unzip >/dev/null || { echo "unzip missing (this flake provides it)" >&2; exit 1; }
  curl -fsSL https://gitlab.haskell.org/ghc/ghc-wasm-meta/-/raw/master/bootstrap.sh | \
    FLAVOUR=9.12 SKIP_GHC=1 PREFIX="$WASM_SDK/.ghc-wasm" sh
  mkdir -p "$WASM_SDK/bin" "$WASM_SDK/downloads"
}
install_pinned || install_bootstrap
# Locate the SDK's clang dir (layout differs between the pinned archive and
# bootstrap.sh) and bridge the wasm32-unknown-wasi-* names GHC expects, plus
# the LLVM tools from PATH (this flake's llvm/lld) with SDK fallback.
SDK_BIN=""
for d in "$WASM_SDK/wasi-sdk/bin" "$WASM_SDK/.ghc-wasm/wasi-sdk/bin"; do
  [ -x "$d/clang" ] && SDK_BIN=$d && break
done
if [ -z "$SDK_BIN" ]; then
  found=$(find "$WASM_SDK" -name clang -type f | head -1)
  [ -n "$found" ] && SDK_BIN=$(dirname "$found")
fi
[ -n "$SDK_BIN" ] || { echo "clang not found under $WASM_SDK" >&2; exit 1; }
SYSROOT=$(find "$WASM_SDK" -path '*share/wasi-sysroot' -maxdepth 5 -type d | head -1)
[ -n "$SYSROOT" ] || { echo "wasi-sysroot not found under $WASM_SDK" >&2; exit 1; }
[ -f "$SYSROOT/include/wasm32-wasi/ffi.h" ] || \
  { echo "libffi headers missing from the WASI sysroot ($SYSROOT)" >&2; exit 1; }
mkbridge() { printf '#!/bin/sh\nexec "%s" "$@"\n' "$2" > "$WASM_SDK/bin/$1"; chmod +x "$WASM_SDK/bin/$1"; }
mkclang() {
  cat > "$WASM_SDK/bin/$1" <<EOF
#!/bin/sh
unset CPATH C_INCLUDE_PATH CPLUS_INCLUDE_PATH OBJC_INCLUDE_PATH LIBRARY_PATH NIX_CFLAGS_COMPILE NIX_LDFLAGS
exec "$2" --target=wasm32-unknown-wasi --sysroot="$SYSROOT" -I"$SYSROOT/include/wasm32-wasi" -L"$SYSROOT/lib/wasm32-wasi" "\$@"
EOF
  chmod +x "$WASM_SDK/bin/$1"
}
llvm_or_sdk() {
  if [ -x "$SDK_BIN/$1" ]; then echo "$SDK_BIN/$1"
  elif [ -x "$SDK_BIN/$2" ]; then echo "$SDK_BIN/$2"
  else command -v "$1"
  fi
}
mkclang wasm32-unknown-wasi-clang "$SDK_BIN/clang"
mkclang wasm32-unknown-wasi-clang++ "$SDK_BIN/clang++"
mkbridge wasm32-unknown-wasi-ar "$(llvm_or_sdk llvm-ar ar)"
mkbridge wasm32-unknown-wasi-ranlib "$(llvm_or_sdk llvm-ranlib ranlib)"
mkbridge wasm32-unknown-wasi-nm "$(llvm_or_sdk llvm-nm nm)"
mkbridge wasm32-unknown-wasi-objdump "$(llvm_or_sdk llvm-objdump objdump)"
export PATH="$WASM_SDK/bin:$PATH"
WASM_LD=$([ -x "$SDK_BIN/wasm-ld" ] && echo "$SDK_BIN/wasm-ld" || command -v wasm-ld)
WASM_LLC=$([ -x "$SDK_BIN/llc" ] && echo "$SDK_BIN/llc" || command -v llc)
WASM_OPT=$([ -x "$SDK_BIN/opt" ] && echo "$SDK_BIN/opt" || command -v opt)
WASM_AS=$([ -x "$SDK_BIN/llvm-as" ] && echo "$SDK_BIN/llvm-as" || command -v llvm-as)
for t in "$WASM_LD" "$WASM_LLC" "$WASM_OPT" "$WASM_AS"; do
  [ -x "$t" ] || { echo "missing LLVM tool: $t" >&2; exit 1; }
done
{
  printf 'export WASM_BIN=%q\n' "$WASM_SDK/bin"
  printf 'export WASM_SYSROOT=%q\n' "$SYSROOT"
  printf 'export WASM_LD=%q\n' "$WASM_LD"
  printf 'export WASM_LLC=%q\n' "$WASM_LLC"
  printf 'export WASM_OPT=%q\n' "$WASM_OPT"
  printf 'export WASM_AS=%q\n' "$WASM_AS"
} >> "$CROSS_ROOT/gate-wasm.env"
cp "$CROSS_ROOT/gate-wasm.env" "$CROSS_ROOT/gate.env"
wasm32-unknown-wasi-clang --version | head -1
ls "$WASM_SDK/bin"
""")

code("wasm-th-runtimes", r"""
%%bash
set -euo pipefail
ROOT=$(cat "$HOME/.cross-ghc-wasm-root")
source "$ROOT/gate-wasm.env"
cross_enter
command -v node >/dev/null || { echo "node missing — this flake provides nodejs_22" >&2; exit 1; }
echo "node $(node --version)"
MAJ=$(node --version | sed 's/^v//; s/\..*$//')
[ "$MAJ" -ge 22 ] || { echo "need Node.js >= 22 for dyld.mjs, found $(node --version)" >&2; exit 1; }
echo "OK: Node.js >= 22 for the wasm interpreter"
# wasmtime runs the final WASI module. Prefer PATH (this flake); otherwise
# install the official release for this platform under the SDK bridge dir.
if ! command -v wasmtime >/dev/null; then
  case "$BUILD_OS/$BUILD_ARCH" in
    linux/x86_64) T=x86_64-linux ;;
    linux/aarch64) T=aarch64-linux ;;
    darwin/x86_64) T=x86_64-macos ;;
    darwin/aarch64) T=aarch64-macos ;;
    *) echo "unsupported platform for wasmtime release: $BUILD_OS/$BUILD_ARCH" >&2; exit 1 ;;
  esac
  WASMTIME_VERSION=${WASMTIME_VERSION:-v25.0.2}
  curl -fLsS --retry 3 --connect-timeout 20 --max-time 900 \
    -o "$WASM_SDK/downloads/wasmtime.tar.xz" \
    "https://github.com/bytecodealliance/wasmtime/releases/download/$WASMTIME_VERSION/wasmtime-$WASMTIME_VERSION-$T.tar.xz"
  tar --no-same-owner -xJf "$WASM_SDK/downloads/wasmtime.tar.xz" \
    -C "$WASM_SDK/bin" --strip-components=1
fi
wasmtime --version
# Helper for later cells: run a WASI module with tail calls (GHC needs them)
# and the working directory preopened. Falls back to the Python API when the
# CLI is unavailable but `import wasmtime` works.
cat > "$WASM_SDK/bin/wasm-run.py" <<'PY'
import sys, os
import wasmtime
config = wasmtime.Config()
config.wasm_tail_call = True
engine = wasmtime.Engine(config)
store = wasmtime.Store(engine)
wasi = wasmtime.WasiConfig()
wasi.argv = [sys.argv[1], *sys.argv[2:]]
wasi.inherit_stdout()
wasi.inherit_stderr()
wasi.inherit_stdin()
wasi.preopen_dir(os.getcwd(), '.')
store.set_wasi(wasi)
linker = wasmtime.Linker(engine)
linker.define_wasi()
module = wasmtime.Module.from_file(engine, sys.argv[1])
instance = linker.instantiate(store, module)
try:
    instance.exports(store)['_start'](store)
except wasmtime.ExitTrap as e:
    raise SystemExit(e.code)
PY
{ printf 'run_wasm() {\n'
  printf '  if command -v wasmtime >/dev/null; then\n'
  printf '    wasmtime run --wasm tail-call=y --dir=. "$@"\n'
  printf '  else\n'
  printf '    python3 "$WASM_SDK/bin/wasm-run.py" "$@"\n'
  printf '  fi\n'
  printf '}\n'
} >> "$CROSS_ROOT/gate-wasm.env"
cp "$CROSS_ROOT/gate-wasm.env" "$CROSS_ROOT/gate.env"
run_wasm() {
  if command -v wasmtime >/dev/null; then
    wasmtime run --wasm tail-call=y --dir=. "$@"
  else
    python3 "$WASM_SDK/bin/wasm-run.py" "$@"
  fi
}
printf 'int main(void){return 0;}\n' > /tmp/wasm-cc-check.c
wasm32-unknown-wasi-clang -o /tmp/wasm-cc-check.wasm /tmp/wasm-cc-check.c
run_wasm /tmp/wasm-cc-check.wasm
echo "wasi toolchain smoke test ok"
""")

# ---- 3. fetching & building GHC 9.14 + cross-aware Cabal -------------------
md("wasm-th-fetch-md", r"""
## 3. Stage 2 native GHC + cross-aware Cabal

Fetch `stable-haskell/ghc#stable-ghc-9.14` and
`andreabedini/cabal#andrea/feature/cross-compilation-2`, recording commits in
`sources.lock` (fresh `CROSS_ROOT` when changing refs). Cabal gets a local
wired-in boot-library patch: a *missing* wired-in unit may build only with
`allow-boot-library-installs: True`; installed units keep exact-unit pins.
""")

code("wasm-th-checkout", r"""
%%bash
set -euo pipefail
ROOT=$(cat "$HOME/.cross-ghc-wasm-root")
source "$ROOT/gate-wasm.env"
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
  cmp -s <(head -2 "$CROSS_ROOT/build-sources.lock") "$CROSS_ROOT/sources.lock" || {
    echo "Sources changed. Start with a fresh CROSS_ROOT for this toolchain." >&2; exit 1; }
fi
""")

code("wasm-th-build-native", r"""
%%bash
set -euo pipefail
ROOT=$(cat "$HOME/.cross-ghc-wasm-root")
source "$ROOT/gate-wasm.env"
cross_enter

BOOT_GHC=$(command -v "$BOOT_GHC" || { echo "bootstrap GHC '$BOOT_GHC' not found (need 9.8.4; ghcup install ghc 9.8.4)" >&2; exit 1; })
BOOT_CABAL=$(command -v "$BOOT_CABAL" || { echo "bootstrap cabal '$BOOT_CABAL' not found" >&2; exit 1; })
MAKE_BIN=$(command -v "$MAKE_BIN" || { echo "make '$MAKE_BIN' not found" >&2; exit 1; })
[[ $("$BOOT_GHC" --numeric-version) == 9.8.4 ]] || { echo "need bootstrap GHC 9.8.4" >&2; exit 1; }
make_version=$("$MAKE_BIN" --version | awk 'NR==1 {print $3}')
python3 - "$make_version" <<'PY_MAKE'
import sys
assert tuple(int(p) for p in sys.argv[1].split('.')[:2]) >= (4, 4), \
  f"Need GNU Make >= 4.4, found {sys.argv[1]}"
print("make ok:", sys.argv[1])
PY_MAKE
command -v cc >/dev/null || { echo "no C compiler on PATH (Xcode CLT on macOS, build-essential on Linux)" >&2; exit 1; }
command -v autoconf >/dev/null || { echo "autoconf missing (this flake provides it)" >&2; exit 1; }
mkdir -p "$CABAL_DIR" "$CROSS_ROOT/cabal-dist"
if [[ ! -f "$CABAL_DIR/config" ]]; then "$BOOT_CABAL" user-config init; fi
sed -i.bak 's|url: http://hackage.haskell.org/|url: https://hackage.haskell.org/|' "$CROSS_ROOT/cabal-home/config"
"$BOOT_CABAL" update
lock_ok() { [[ -f "$CROSS_ROOT/build-sources.lock" ]] && \
  cmp -s <(head -2 "$CROSS_ROOT/build-sources.lock") "$CROSS_ROOT/sources.lock"; }
if [[ -x "$CABAL" ]] && lock_ok; then
  echo "cached tutorial Cabal matches sources.lock — skipping rebuild"
  "$CABAL" --version
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
  sha256sum "$CROSS_ROOT/cabal-boot.patch" 2>/dev/null >> "$CROSS_ROOT/build-sources.lock" || \
    shasum -a 256 "$CROSS_ROOT/cabal-boot.patch" >> "$CROSS_ROOT/build-sources.lock"
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
# Use GHC's own compiler-build driver and its pinned internal Cabal builder.
# In particular, do not set CABAL=$CABAL or USE_SYSTEM_CABAL here.
if [[ -x "$DIST/bin/ghc" && -x "$DIST/bin/ghc-pkg" ]] && lock_ok; then
  echo "cached stage2 compiler matches sources.lock — skipping rebuild"
  "$DIST/bin/ghc-pkg" recache
else
  cd "$GHC_SRC"
  if ! grep -q '^optional-packages: \./nonexistent/\*\.cabal' "$GHC_SRC/cabal.project.stage0"; then
    printf '\noptional-packages: ./nonexistent/*.cabal\n' >> "$GHC_SRC/cabal.project.stage0"
  fi
  "$MAKE_BIN" -j"$BUILD_JOBS" stage2 GHC0="$BOOT_GHC" CABAL0="$BOOT_CABAL" \
    THREADS="$BUILD_JOBS" DYNAMIC=0 CABAL_ARGS="-j$BUILD_JOBS --disable-optimization --ghc-option=-j$GHC_JOBS"
fi
[[ -x "$DIST/bin/ghc" && -x "$DIST/bin/ghc-pkg" ]]
"$DIST/bin/ghc" --numeric-version
"$DIST/bin/ghc" -list-targets
"$DIST/bin/ghc-pkg" check
for name in deriveConstants genapply ghc-toolchain-bin; do
  [[ -x "$DIST/bin/$name" ]]
  printf '%s\n' "$DIST/bin/$name" > "$CROSS_ROOT/$name.path"
done
""")

# ---- 4. wasm target source worktree -----------------------------------------
md("wasm-th-tree-md", r"""
## 4. Wasm target source worktree

Second checkout at the same GHC commit for the wasm libraries. `rts/rts.cabal`
keeps `nonthreaded-nodebug` **and** `nonthreaded-nodebug-dynamic` buildable
(vanilla + dynamic for `shared: True`); the rest are disabled. `time-wasm`
adapts `time-1.15` to WASI's pointer `clockid_t` (`ClockID = Ptr ()` plus the
realtime constant wrapper, as in `base`).
""")

code("wasm-th-tree", r"""
%%bash
set -euo pipefail
ROOT=$(cat "$HOME/.cross-ghc-wasm-root")
source "$ROOT/gate-wasm.env"
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
  sed -i.bak 's/ghc-boot-th/ghc-boot-th-next/g' ghc-boot-th-next.cabal.in
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

# Wasm needs the dynamic RTS too: keep every nonthreaded-nodebug RTS
# sublibrary (vanilla + dynamic) buildable, disable the rest.
git -C "$SRC" checkout -- rts/rts.cabal 2>/dev/null || true
python3 - "$SRC/rts/rts.cabal" <<'PY_PATCH'
import re, sys
from pathlib import Path
path = Path(sys.argv[1])
text = path.read_text()
kept = []
out = []
last = 0
for m in re.finditer(r"(?ms)^library (\S+)\n.*?(?=^library |\Z)", text):
    name = m.group(1)
    out.append(text[last:m.start()])
    block = m.group()
    if name.startswith("nonthreaded-nodebug"):
        kept.append(name)
    elif not re.search(r"(?m)^    buildable:\s*False\s*$", block):
        block, count = re.subn(r"(?m)^(    import:.*\n)", r"\1    buildable: False\n", block, count=1)
        if count != 1:
            raise SystemExit("Unexpected import layout: " + name)
    out.append(block)
    last = m.end()
out.append(text[last:])
path.write_text(''.join(out))
assert "nonthreaded-nodebug" in kept, "vanilla RTS sublibrary missing"
print("Buildable RTS sublibraries kept:", ", ".join(sorted(kept)))
PY_PATCH

# Adapt time-1.15 to WASI's pointer clockid_t (idempotent).
cat > "$CROSS_ROOT/prepare-time-wasm.py" <<'PY_EOF'
from pathlib import Path
import subprocess, tarfile, hashlib, os
b = Path(os.environ['CROSS_ROOT'])
p = b / 'downloads/time-1.15.tar.gz'
dest = b / 'sources/time-wasm'
p.parent.mkdir(parents=True, exist_ok=True)
if not p.exists():
    subprocess.run(['curl', '-fLsS', '--retry', '3', '--connect-timeout', '20',
                    '--max-time', '120', '-o', str(p),
                    'https://hackage.haskell.org/package/time-1.15/time-1.15.tar.gz'],
                   check=True)
digest = hashlib.file_digest(p.open('rb'), 'sha256').hexdigest()
assert digest == '4fa2138f5c9af96528bedb0e5ba659fe6a0f30331a3a53ce224f5f62c9736218', 'time source hash mismatch'
if not (dest / 'time.cabal').exists():
    dest.mkdir(parents=True, exist_ok=True)
    with tarfile.open(p) as t:
        for e in t.getmembers():
            parts = Path(e.name).parts
            if len(parts) < 2:
                continue
            e.name = str(Path(*parts[1:]))
            t.extract(e, dest, filter='data')
f = dest / 'lib/Data/Time/Clock/Internal/CTimespec.hsc'
s = f.read_text()
old = 'type ClockID = CClockId'
new = '''-- WASI clockid_t is an opaque pointer, as in GHC base's ClockGetTime.
#if defined(wasm32_HOST_ARCH)
type ClockID = Ptr ()
#else
type ClockID = CClockId
#endif'''
if new not in s:
    assert s.count(old) == 1
    f.write_text(s.replace(old, new))
f = dest / 'lib/include/HsTime.h'
s = f.read_text()
old = '#define HS_CLOCK_REALTIME (uintptr_t)(CLOCK_REALTIME)'
new = '''#if defined(__wasi__)
#define HS_CLOCK_REALTIME CLOCK_REALTIME
#else
#define HS_CLOCK_REALTIME (uintptr_t)(CLOCK_REALTIME)
#endif'''
if new not in s:
    assert s.count(old) == 1
    f.write_text(s.replace(old, new))
print('WASI time uses pointer clock IDs and preserves the realtime pointer constant')
PY_EOF
CROSS_ROOT="$CROSS_ROOT" python3 "$CROSS_ROOT/prepare-time-wasm.py"
""")

# ---- 5. teaching GHC about wasm32-unknown-wasi ----------------------------
md("wasm-th-target-md", r"""
## 5. Teaching GHC about `wasm32-unknown-wasi`

`ghc-toolchain-bin` writes `lib/targets/wasm32-unknown-wasi/lib/settings` from
the bridge tools, patched to `RTS ways = "v dyn"` with dynamic-library flags
`YES`. Then install `dyld.mjs`/`prelude.mjs`/`ghc-interp.js` beside it, create
the `wasm32-unknown-wasi-*` symlinks, and init `package.conf.d`. No GHC rebuild.
""")

code("wasm-th-target", r"""
%%bash
set -euo pipefail
ROOT=$(cat "$HOME/.cross-ghc-wasm-root")
source "$ROOT/gate-wasm.env"
cross_enter
export PATH="$WASM_BIN:$PATH"
cat > "$CROSS_ROOT/configure-target.py" <<'PY_EOF'
from pathlib import Path
import subprocess, ast, json, os, shutil
root = Path(os.environ['CROSS_ROOT'])
dist = Path(os.environ['DIST'])
triple = 'wasm32-unknown-wasi'
env = dict(os.environ, PATH=os.environ['WASM_BIN'] + ':' + os.environ['PATH'])
lib = dist / 'lib/targets' / triple / 'lib'
lib.mkdir(parents=True, exist_ok=True)
args = [str(dist / 'bin/ghc-toolchain-bin'), '--triple', triple,
        '--disable-ld-override', '--output-settings', '-o', str(lib / 'settings'),
        '--cc', 'wasm32-unknown-wasi-clang', '--cxx', 'wasm32-unknown-wasi-clang++',
        '--ar', 'wasm32-unknown-wasi-ar', '--ranlib', 'wasm32-unknown-wasi-ranlib',
        '--nm', 'wasm32-unknown-wasi-nm',
        '--merge-objs', os.environ['WASM_LD'], '--merge-objs-opt=-r',
        '--disable-tables-next-to-code', '--disable-libffi-adjustors',
        '--llc', os.environ['WASM_LLC'], '--opt', os.environ['WASM_OPT'],
        '--llvm-as', os.environ['WASM_AS']]
for flag in ['-Wl,--keep-section=ghc_wasm_jsffi', '-Wl,--keep-section=target_features',
             '-Wl,--stack-first', '-Wl,--strip-debug']:
    args.append('--cc-link-opt=' + flag)
r = subprocess.run(args, cwd=os.environ['SRC'], env=env,
                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
print(r.stdout)
if r.returncode:
    raise SystemExit('ghc-toolchain-bin failed for ' + triple)
settings = dict(ast.literal_eval((lib / 'settings').read_text()))
settings['RTS ways'] = 'v dyn'
for key in ['target is dynamic', 'target ships dynamic libraries']:
    settings[key] = 'YES'
for key in ['target is profiled', 'target ships profiling libraries']:
    settings[key] = 'NO'
(lib / 'settings').write_text(
    '[' + ',\n'.join('(' + json.dumps(k) + ',' + json.dumps(v) + ')' for k, v in settings.items()) + ']\n')
for exe in ['ghc', 'ghc-pkg', 'hsc2hs']:
    p = dist / 'bin' / (triple + '-' + exe)
    if not p.exists():
        p.symlink_to(exe)
tools = lib.parent / 'bin'
tools.mkdir(exist_ok=True)
p = tools / 'unlit'
if not p.exists():
    p.symlink_to(dist / 'bin/unlit')
if not (lib / 'package.conf.d').exists():
    subprocess.run([str(dist / 'bin/ghc-pkg'), 'init', str(lib / 'package.conf.d')], check=True)
for name in ['dyld.mjs', 'post-link.mjs', 'prelude.mjs']:
    shutil.copyfile(root / 'ghc-src/utils/jsffi' / name, lib / name)
shutil.copyfile(root / 'ghc-src/ghc-interp.js', lib / 'ghc-interp.js')
dyld_src = (lib / 'dyld.mjs').read_text()
old_ctor = '''    this.readStream = stream.Readable.toWeb(
      fs.createReadStream(undefined, { fd: in_fd })
    );
    this.writeStream = stream.Writable.toWeb(
      fs.createWriteStream(undefined, { fd: out_fd })
    );'''
new_ctor = '''    this.readStream = new ReadableStream({
      pull(controller) {
        return new Promise((resolve, reject) => {
          const buf = Buffer.alloc(65536);
          fs.read(in_fd, buf, 0, buf.length, null, (err, bytesRead) => {
            if (err) return reject(err);
            if (bytesRead === 0) { controller.close(); return resolve(); }
            controller.enqueue(new Uint8Array(buf.buffer, buf.byteOffset, bytesRead));
            resolve();
          });
        });
      }
    }, { highWaterMark: 0 });
    this.writeStream = new WritableStream({
      write(chunk) {
        fs.writeSync(out_fd, chunk);
      }
    });'''
assert old_ctor in dyld_src
dyld_src = dyld_src.replace(old_ctor, new_ctor, 1)
old_fetch = "stream.Readable.toWeb(fs.createReadStream(p))"
assert old_fetch in dyld_src
dyld_src = dyld_src.replace(old_fetch, "await fs.promises.readFile(p)", 1)
old_load = "    await dyld.addLibrarySearchPath(libdir);\n\n    await dyld.loadDLL(ghciSoPath);"
sysroot_lib = str(Path(os.environ['WASM_SYSROOT']) / 'lib/wasm32-wasi')
stage3_dynlib = str(Path(os.environ['STAGE3']) / 'dynlib')
new_load = (
    "    await dyld.addLibrarySearchPath(libdir);\n"
    f"    await dyld.addLibrarySearchPath({json.dumps(sysroot_lib)});\n"
    f"    await dyld.addLibrarySearchPath({json.dumps(stage3_dynlib)});\n\n"
    "    await dyld.loadDLL(ghciSoPath);"
)
assert old_load in dyld_src
dyld_src = dyld_src.replace(old_load, new_load, 1)
old_entry = '''  if (!process.env.GHCI_BROWSER) {
    const rpc = new DyLDHost();
    await main({
      rpc,
      libdir,
      ghciSoPath,
      args,
    });
    return;
  }'''
new_entry = '''  if (!process.env.GHCI_BROWSER) {
    const rpc = new DyLDHost();
    try {
      await main({
        rpc,
        libdir,
        ghciSoPath,
        args,
      });
      process.exit(0);
    } catch (err) {
      console.error(err);
      process.exit(1);
    }
  }'''
assert old_entry in dyld_src
dyld_src = dyld_src.replace(old_entry, new_entry, 1)
(lib / 'dyld.mjs').write_text(dyld_src)
(lib / 'dyld.mjs').chmod(0o755)
print(triple, 'settings ready:', settings['target platform string'])
PY_EOF
python3 "$CROSS_ROOT/configure-target.py"
grep -E -- '"RTS ways"|"target ships dynamic libraries"|"target is dynamic"' \
  "$DIST/lib/targets/wasm32-unknown-wasi/lib/settings"
ls -l "$DIST/bin/wasm32-unknown-wasi-ghc" "$DIST/bin/wasm32-unknown-wasi-ghc-pkg" \
  "$DIST/bin/wasm32-unknown-wasi-hsc2hs"
ls "$DIST/lib/targets/wasm32-unknown-wasi/lib/"*.mjs "$DIST/lib/targets/wasm32-unknown-wasi/lib/ghc-interp.js"
"$DIST/bin/wasm32-unknown-wasi-ghc" --info | grep -E 'Target platform|RTS ways'
""")

# ---- 6. building the wasm target libraries ----------------------------------
md("wasm-th-stage3-md", r"""
## 6. Wasm target libraries (`stage3-wasm`)

Cabal builds `$SRC` + `time-wasm` + `ghc-experimental` (the wasm interpreter's
dependency) with `-w` (wasm) / `-W` (native). `shared: True` with
`-tables-next-to-code +dynamic` yields the `.so` files `dyld.mjs` loads; the
RTS way builds first, then registrations export into `package.conf.d`.
""")

code("wasm-th-stage3", r"""
%%bash
set -euo pipefail
ROOT=$(cat "$HOME/.cross-ghc-wasm-root")
source "$ROOT/gate-wasm.env"
cross_enter
export PATH="$WASM_BIN:$PATH"
export CABAL_DIR
TRIPLE=wasm32-unknown-wasi
tdb="$DIST/lib/targets/$TRIPLE/lib/package.conf.d"
mkdir -p "$STAGE3"
if ! "$DIST/bin/$TRIPLE-ghc-pkg" --package-db="$tdb" field base id >/dev/null 2>&1 \
  || ! "$DIST/bin/$TRIPLE-ghc-pkg" --package-db="$tdb" field ghc-internal id >/dev/null 2>&1; then
  if [[ -z "${FORCE_STAGE3_REBUILD:-}" && -d "$STAGE3/exported-conf" ]] && [[ -f "$STAGE3/sources.lock" ]] && cmp -s "$STAGE3/sources.lock" "$CROSS_ROOT/sources.lock"; then
    for conf in "$STAGE3/exported-conf"/*.conf; do
      [[ -e "$conf" ]] || continue
      "$DIST/bin/$TRIPLE-ghc-pkg" --package-db="$tdb" update --force "$conf"
    done
    "$DIST/bin/$TRIPLE-ghc-pkg" --package-db="$tdb" recache
    if "$DIST/bin/$TRIPLE-ghc-pkg" --package-db="$tdb" check >/dev/null 2>&1 \
      && "$DIST/bin/$TRIPLE-ghc-pkg" --package-db="$tdb" field base id >/dev/null 2>&1 \
      && "$DIST/bin/$TRIPLE-ghc-pkg" --package-db="$tdb" field ghc-internal id >/dev/null 2>&1; then
      echo "Restored cached stage3-wasm registrations from $STAGE3/exported-conf — skipping rebuild."
    else
      rm -f "$tdb"/*.conf
      "$DIST/bin/$TRIPLE-ghc-pkg" --package-db="$tdb" recache
    fi
  fi
fi
if [[ -z "${FORCE_STAGE3_REBUILD:-}" ]] && \
   "$DIST/bin/$TRIPLE-ghc-pkg" --package-db="$tdb" field base id >/dev/null 2>&1 && \
   "$DIST/bin/$TRIPLE-ghc-pkg" --package-db="$tdb" field ghc-internal id >/dev/null 2>&1; then
  echo "stage3-wasm already registered — skipping rebuild (set FORCE_STAGE3_REBUILD=1 to redo)"
else
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
  $SRC/libraries/ghc-experimental
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
  $CROSS_ROOT/sources/time-wasm
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
  shared: True
  library-for-ghci: False
  executable-dynamic: True
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
  flags: +use-system-libffi -tables-next-to-code +dynamic -profiling -debug -threaded
  ghc-options: -j$GHC_JOBS -O0 -no-rts
  ghc-options: "-optc-DProjectVersion=\\"914\\""
  ghc-options: "-optc-DHostArch=\\"wasm32\\""
  ghc-options: "-optc-DHostOS=\\"wasi\\""
  ghc-options: "-optc-DHostPlatform=\\"wasm32-unknown-wasi\\""
  ghc-options: "-optc-DHostVendor=\\"unknown\\""
  ghc-options: "-optc-DBuildArch=\\"$BUILD_ARCH\\""
  ghc-options: "-optc-DBuildOS=\\"$BUILD_OS\\""
  ghc-options: "-optc-DBuildPlatform=\\"$BUILD_PLATFORM\\""
  ghc-options: "-optc-DBuildVendor=\\"$BUILD_VENDOR\\""
  ghc-options: "-optc-DGhcUnregisterised=\\"NO\\""
  ghc-options: "-optc-DTablesNextToCode=\\"YES\\""
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
  ghc-experimental source,
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
export NM="$WASM_BIN/wasm32-unknown-wasi-nm"
export OBJDUMP="$WASM_BIN/wasm32-unknown-wasi-objdump"
unset CC CXX AR RANLIB || true
cd "$STAGE3"
cabal_build() {
  "$CABAL" --store-dir="$STAGE3/store" build -j"$BUILD_JOBS" \
    -w "$DIST/bin/$TRIPLE-ghc" \
    -W "$DIST/bin/ghc" \
    --with-hc-pkg="$DIST/bin/$TRIPLE-ghc-pkg" \
    --with-build-hc-pkg="$DIST/bin/ghc-pkg" \
    --with-gcc="$WASM_BIN/wasm32-unknown-wasi-clang" \
    --with-hsc2hs="$DIST/bin/hsc2hs" \
    --with-build-hsc2hs="$DIST/bin/hsc2hs" \
    --hsc2hs-options=-x \
    --ghc-option="-ghcversion-file=$SRC/rts/include/ghcversion.h" \
    "$@"
}
# Complete the RTS way first. This prevents ghc-internal interfaces from
# retaining placeholder RTS unit IDs during bootstrap.
cabal_build rts:lib:nonthreaded-nodebug
cabal_build all
# Export only host library registrations, from both in-place and store DBs.
python3 - "$STAGE3" "$tdb" "$DIST/bin/$TRIPLE-ghc-pkg" <<'PY_REGISTER'
import json, re, subprocess, sys
from pathlib import Path
stage, dest, ghcpkg = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
plan = json.loads((stage / 'dist-newstyle/cache/plan.json').read_text())
assert plan['arch'] == 'wasm32'
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
    for block in re.split(r"(?m)^---\s*$", dump):
        match = re.search(r"(?m)^id:\s*(\S+)", block)
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
fi
# Cabal builds ghc-internal with both library-vanilla and shared enabled, so it
# compiles cbits/RtsIface.c with -fPIC (-D__PIC__) and archives that PIC object
# into libHSghc-internal*.a. On wasm32, RtsIfaceSymbols.h gates JSFFI symbols
# (JSVal_con_info, raiseJSException_closure, threadDelay_closure) behind
# defined(wasm32_HOST_ARCH) && defined(__PIC__). Recompile RtsIface.c without
# __PIC__ for the static archive so standalone WASI binaries do not pull in
# ghc_wasm_jsffi imports, while leaving libHSghc-internal*.so untouched for TH.
mkdir -p "$STAGE3/rts-iface-static"
inc_flags=("-I$SRC/libraries/ghc-internal/include" "-I$SRC/rts/include")
for pkg in rts-headers ghc-internal; do
  for d in $("$DIST/bin/$TRIPLE-ghc-pkg" --package-db="$tdb" field "$pkg" include-dirs --simple-output); do
    inc_flags+=("-I$d")
  done
done
"$DIST/bin/$TRIPLE-ghc" -c -O0 -static -fno-PIC -optc-fno-PIC -optc-U__PIC__ \
  -ghcversion-file="$SRC/rts/include/ghcversion.h" \
  "${inc_flags[@]}" \
  "$SRC/libraries/ghc-internal/cbits/RtsIface.c" \
  -o "$STAGE3/rts-iface-static/RtsIface.o"
! "$WASM_BIN/wasm32-unknown-wasi-nm" -u "$STAGE3/rts-iface-static/RtsIface.o" | grep -q JSVal_con_info
find "$STAGE3/dist-newstyle" -name 'RtsIface.o' -type f -exec cp "$STAGE3/rts-iface-static/RtsIface.o" {} \;
find "$STAGE3/dist-newstyle" "$STAGE3/store" -name 'libHSghc-internal*.a' -type f | while read -r a; do
  "$WASM_BIN/wasm32-unknown-wasi-ar" d "$a" RtsIface.o || true
  "$WASM_BIN/wasm32-unknown-wasi-ar" r "$a" "$STAGE3/rts-iface-static/RtsIface.o"
  "$WASM_BIN/wasm32-unknown-wasi-ranlib" "$a"
done
mkdir -p "$STAGE3/dynlib"
find "$STAGE3/dist-newstyle" "$STAGE3/store" -name '*.so' -type f | while read -r so; do
  ln -sf "$so" "$STAGE3/dynlib/$(basename "$so")"
done
for package in base template-haskell ghci text containers directory; do
  "$DIST/bin/$TRIPLE-ghc-pkg" --package-db="$tdb" field "$package" id | head -3
done
"$DIST/bin/$TRIPLE-ghc-pkg" --package-db="$tdb" check
""")

# ---- 7. target context & TH smoke test ---------------------------------------
md("wasm-th-context-md", r"""
## 7. Target context: TH via `dyld.mjs` in Node

`-fexternal-interpreter` executes splices through `dyld.mjs` in Node, which
hosts the wasm modules and links the `v dyn` / `shared: True` libraries above.
Node shares the build filesystem, so splice `readFile` paths resolve as usual
and `addDependentFile` just records inputs. The final app runs separately
under `wasmtime`.
""")

code("wasm-th-smoke", r"""
%%bash
set -euo pipefail
ROOT=$(cat "$HOME/.cross-ghc-wasm-root")
source "$ROOT/gate-wasm.env"
cross_enter
export PATH="$WASM_BIN:$PATH"
TRIPLE=wasm32-unknown-wasi
mkdir -p "$CROSS_ROOT/smoke-wasm"
cd "$CROSS_ROOT/smoke-wasm"
cat > Arch.hs <<'HS'
{-# LANGUAGE TemplateHaskell #-}
import Language.Haskell.TH.Syntax (lift)
import System.Info (arch, os)
main :: IO ()
main = print $(lift (arch, os))
HS
# -fexternal-interpreter evaluates the splice through dyld.mjs in Node,
# loading the dynamic target libraries built above.
"$DIST/bin/$TRIPLE-ghc" -fforce-recomp -O0 -fexternal-interpreter \
  -o arch Arch.hs
file arch* | head -5
BIN=arch
[ -f arch.wasm ] && BIN=arch.wasm
run_wasm "./$BIN" | tee smoke.out
grep -q '("wasm32","wasi")' smoke.out
echo "PASS: TH splice evaluated for ($TRIPLE) in Node, ran under wasmtime"
""")

# ---- 8. building & running the cross-report application ----------------------
md("wasm-th-app-md", r"""
## 8. Building and running `cross-report`

Native `Setup.hs` (`-W`) prints the build identity and writes `git --version`
to `build-tool.txt`; the target splice (`TH.hs`, `-w`) validates `catalogue.txt`,
records both files via `addDependentFile`, and embeds the table. The static
binary runs under `wasmtime` (`SPLICE_ARCH=` / `RUNTIME_ARCH=` checks below).
""")

code("wasm-th-app-src", r"""
%%bash
set -euo pipefail
ROOT=$(cat "$HOME/.cross-ghc-wasm-root")
source "$ROOT/gate-wasm.env"
cross_enter
mkdir -p "$FIBO"
cd "$FIBO"
cat > catalogue.txt <<'EOF'
small 10
century 100
large 100000
EOF
cat > TH.hs <<'EOF'
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
cat > Main.hs <<'EOF'
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
cat > Setup.hs <<'EOF'
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
cat > cross-report.cabal <<'EOF'
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
cat > cabal.project <<'EOF'
active-repositories: :none
allow-boot-library-installs: True
jobs: 2
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
ls -l catalogue.txt TH.hs Main.hs Setup.hs cross-report.cabal cabal.project
""")

code("wasm-th-app-build", r"""
%%bash
set -euo pipefail
ROOT=$(cat "$HOME/.cross-ghc-wasm-root")
source "$ROOT/gate-wasm.env"
cross_enter
export PATH="$WASM_BIN:$PATH"
export CABAL_DIR
cd "$FIBO"
TRIPLE=wasm32-unknown-wasi
# Touch Setup.hs so Cabal re-runs the native setup step and we can check the
# build-machine identity it reports.
touch Setup.hs
"$CABAL" build -j2 -w "$DIST/bin/$TRIPLE-ghc" -W "$DIST/bin/ghc" \
  --with-hc-pkg="$DIST/bin/$TRIPLE-ghc-pkg" --with-build-hc-pkg="$DIST/bin/ghc-pkg" \
  exe:cross-report 2>&1 | tee build.log
grep -q "BUILD_MACHINE_ARCH=$BUILD_ARCH" build.log
grep -q "BUILD_MACHINE_OS=$BUILD_OS" build.log
echo "PASS: native Setup ran on $BUILD_PLATFORM"
"$CABAL" list-bin -w "$DIST/bin/$TRIPLE-ghc" -W "$DIST/bin/ghc" \
  --with-hc-pkg="$DIST/bin/$TRIPLE-ghc-pkg" --with-build-hc-pkg="$DIST/bin/ghc-pkg" \
  exe:cross-report > bin.path
cat bin.path
head -c 20 "$(cat bin.path)" | grep -q . || { echo "empty binary?" >&2; exit 1; }
file "$(cat bin.path)"
""")

code("wasm-th-app-run", r"""
%%bash
set -euo pipefail
ROOT=$(cat "$HOME/.cross-ghc-wasm-root")
source "$ROOT/gate-wasm.env"
cross_enter
export PATH="$WASM_BIN:$PATH"
cd "$FIBO"
BIN=$(cat bin.path)
run_wasm "$BIN" | tee run.out
grep -q '^small=55$' run.out
grep -q '^century=9261915075$' run.out
grep -q '^large=3428746875$' run.out
run_wasm "$BIN" --info | tee info.out
grep -q '^SPLICE_ARCH=wasm32$' info.out
grep -q '^SPLICE_OS=wasi$' info.out
grep -q '^RUNTIME_ARCH=wasm32$' info.out
grep -q '^RUNTIME_OS=wasi$' info.out
grep -q '^BUILD_TOOL=git version ' info.out
[ "$(run_wasm "$BIN" small)" = 55 ]
[ "$(run_wasm "$BIN" --list)" = "$(printf 'century\nlarge\nsmall')" ]
if run_wasm "$BIN" missing 2>err.out; then
  echo "expected unknown-entry failure" >&2; exit 1
fi
grep -q 'Unknown catalogue entry: missing' err.out
echo "PASS: cross-report.wasm table, --info, --list and unknown-entry handling"
""")

# ---- 9. negative validation & single-compiler identity ------------------------
md("wasm-th-final-md", r"""
## 9. Negative splice check and the single-compiler identity

A bad `catalogue.txt` must fail at compile time in the Node splice, not at
runtime; then we rebuild the good catalogue and confirm `wasm32-unknown-wasi-ghc`
is the same binary as `ghc` (`ONE_GHC_SHA256=`).
""")

code("wasm-th-negative", r"""
%%bash
set -euo pipefail
ROOT=$(cat "$HOME/.cross-ghc-wasm-root")
source "$ROOT/gate-wasm.env"
cross_enter
export PATH="$WASM_BIN:$PATH"
export CABAL_DIR
cd "$FIBO"
TRIPLE=wasm32-unknown-wasi
cp catalogue.txt catalogue.txt.good
printf 'bogus row contents\n' > catalogue.txt
sleep 1
touch catalogue.txt
if "$CABAL" build -j2 -w "$DIST/bin/$TRIPLE-ghc" -W "$DIST/bin/ghc" \
  --with-hc-pkg="$DIST/bin/$TRIPLE-ghc-pkg" --with-build-hc-pkg="$DIST/bin/ghc-pkg" \
  exe:cross-report >negative.err 2>&1; then
  echo "invalid catalogue was accepted — expected a splice failure" >&2; exit 1
fi
grep -q 'expected LABEL INDEX (0..100000)' negative.err
echo "PASS: invalid catalogue rejected during TH compilation"
cp catalogue.txt.good catalogue.txt
sleep 1
touch Setup.hs TH.hs Main.hs catalogue.txt
"$CABAL" build -j2 -w "$DIST/bin/$TRIPLE-ghc" -W "$DIST/bin/ghc" \
  --with-hc-pkg="$DIST/bin/$TRIPLE-ghc-pkg" --with-build-hc-pkg="$DIST/bin/ghc-pkg" \
  exe:cross-report
echo "PASS: restored catalogue rebuilt"
""")

code("wasm-th-one-ghc", r"""
%%bash
set -euo pipefail
ROOT=$(cat "$HOME/.cross-ghc-wasm-root")
source "$ROOT/gate-wasm.env"
cross_enter
NATIVE=$(cd "$DIST/bin" && pwd -P)/ghc
TARGET_LINK="$DIST/bin/wasm32-unknown-wasi-ghc"
[ -L "$TARGET_LINK" ] || { echo "$TARGET_LINK is not a symlink" >&2; exit 1; }
[[ $(readlink "$TARGET_LINK") == ghc ]] || { echo "unexpected symlink: $(readlink "$TARGET_LINK")" >&2; exit 1; }
if command -v sha256sum >/dev/null; then
  SUM="sha256sum"
else
  SUM="shasum -a 256"
fi
EXPECTED=$($SUM "$NATIVE" | awk '{print $1}')
ACTUAL=$($SUM "$TARGET_LINK" | awk '{print $1}')
[[ "$EXPECTED" == "$ACTUAL" ]]
echo "wasm32-unknown-wasi-ghc -> $(readlink "$TARGET_LINK")"
echo "ONE_GHC_SHA256=$EXPECTED"
"$DIST/bin/wasm32-unknown-wasi-ghc" --info | grep -E 'Target platform'
"$DIST/bin/ghc" --info | grep -E 'Target platform'
""")

md("wasm-th-cleanup-md", r"""
## 10. Cleanup

Everything lives under `CROSS_ROOT` (git-ignored). Uncomment below to remove
just the report or the whole tree; `sources.lock` guards against mixed revisions.
""")

code("wasm-th-cleanup", r"""
%%bash
set -euo pipefail
ROOT=$(cat "$HOME/.cross-ghc-wasm-root")
source "$ROOT/gate-wasm.env"
cross_enter
echo "CROSS_ROOT=$CROSS_ROOT"
du -sh "$CROSS_ROOT" 2>/dev/null || true
# Uncomment to remove just the report sources and build products:
# rm -rf "$FIBO"
# Uncomment to wipe the whole build tree (compiler, SDK, libraries, reports):
# rm -rf "$CROSS_ROOT"
echo "kept (uncomment a line above to remove)"
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
        "kernelspec": {"display_name": "Python 3 (ipykernel)", "language": "python", "name": "python3"},
        "language_info": {
            "file_extension": ".py",
            "mimetype": "text/x-python",
            "name": "python",
            "pygments_lexer": "ipython3",
            "version": "3",
        },
    },
    "nbformat": 4,
    "nbformat_minor": 5,
}

out = sys.argv[1] if len(sys.argv) > 1 else "WASM TH Cross.ipynb"
with open(out, "w") as f:
    json.dump(nb, f, indent=1, ensure_ascii=False)
    f.write("\n")
print(f"wrote {out} with {len(nb_cells)} cells")










