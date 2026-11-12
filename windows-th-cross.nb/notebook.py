#!/usr/bin/env python3
"""Generator for "Windows TH Cross.ipynb" (python3 kernel, nbformat 4.5).

The notebook is generated from this script so edits are reproducible and the
serialized form matches tools/strip-ipynb.py (clean cells, no outputs). Run via:
python3 windows-th-cross.nb/notebook.py "windows-th-cross.nb/Windows TH Cross.ipynb"

Walks end-to-end through cross-compiling Haskell with GHC 9.14 and Cabal to
x86_64-w64-mingw32 from source on Linux and macOS (aarch64-darwin): the Windows
target libraries against LLVM-MinGW UCRT + static libffi, Template Haskell via
a Windows iserv-proxy-interpreter.exe running under Wine behind a native
iserv-proxy, and the compiled Windows cross-report.exe application under Wine.
"""
import json
import sys

cells = []


def md(cid, text):
    cells.append(("markdown", cid, text))


def code(cid, text):
    cells.append(("code", cid, text))


# ---- 0. title -------------------------------------------------------------
md("win-title", r"""
# Cross Compiling Haskell to Windows with GHC 9.14, Cabal, Wine & iserv-proxy

Cross-compiles Haskell with Template Haskell to **x86_64-w64-mingw32** on Linux and macOS (`aarch64-darwin`), using stable-haskell GHC 9.14 and cross-aware Cabal.
Target libraries build against **LLVM-MinGW UCRT** plus static `libffi`; splices run in a Windows `iserv-proxy-interpreter.exe` under Wine; `cross-report.exe` runs under Wine.
The `cross-report` app validates a catalogue at compile time, embeds Fibonacci residues, and reports `git` provenance collected by native `Setup.hs`.

Three moving parts:

1. **Single native GHC binary**: `x86_64-w64-mingw32-ghc` is a symlink to native `ghc` selecting `lib/targets/x86_64-w64-mingw32/lib/{settings,package.conf.d}`.
2. **Dual-compiler Cabal**: `-w` selects `x86_64-w64-mingw32-ghc` (`host:*`), `-W` selects native `ghc` (`build:*`).
3. **Target C/CRT & TH execution**: LLVM-MinGW (UCRT) + static UCRT `libffi`, with native `iserv-proxy` bridging POSIX pipes over `--stdio` to `iserv-proxy-interpreter.exe` under headless `wine64`.

| Part | Compiled for | Runs where |
|---|---|---|
| GHC and Cabal | build machine | build machine |
| `Setup.hs`, `deriveConstants`, `genapply` | build machine | build machine |
| Splice code and the `iserv` interpreter | Windows x86_64 | Wine on the build machine |
| Report application and its libraries | Windows x86_64 | Wine here; Windows when deployed |

Cabal calls the application machine **host**; GHC calls it **target**.

Prerequisites:
- `CROSS_ROOT`: absolute, whitespace-free tree root holding `gate.env` and all builds.
- `GHC_REF`/`CABAL_REF`: override default branch heads; resolved commits recorded in `sources.lock`.
- Run via `nix develop` for Wine, Make, and the bootstrap toolchain.
""")

# ---- 1. environment -------------------------------------------------------
md("win-env-md", r"""
## 1. Environment & `gate.env`

`CROSS_ROOT` roots the build; `gate.env` pins `DIST`, `SRC`, `STAGE3`, `LLVM_MINGW`, `FFI_PREFIX`, and `WINEPREFIX`.
Detected build platform (`BUILD_ARCH`, `BUILD_OS`, `BUILD_VENDOR`, `BUILD_PLATFORM`) drives per-machine `Build*` flags.
Native GHC and Windows target-library sources live in separate trees (`ghc-src`, `ghc-target-src-windows`).
`BOOT_GHC`/`BOOT_CABAL` supply the bootstrap toolchain; `BUILD_JOBS`/`GHC_JOBS` set package and module parallelism.
A disk check guards cold builds versus re-runs against an existing `$DIST/bin/ghc`; `cross_enter()` resets `CC`/`CXX` per cell.
Configuration is saved under `CROSS_ROOT`, so it survives kernel restarts; run cells in order.
""")

code("win-env", r"""
%%bash
set -euo pipefail
export PATH="$PATH:/usr/sbin:/sbin"
fail() { echo "$*" >&2; exit 1; }

UNAME_S=$(uname -s); UNAME_M=$(uname -m)
case "$UNAME_S" in
  Linux) BUILD_OS=linux ;;
  Darwin) BUILD_OS=darwin ;;
  *) fail "Unsupported build machine OS: $UNAME_S (need Linux or Darwin)." ;;
esac
case "$UNAME_M" in
  x86_64) BUILD_ARCH=x86_64 ;;
  arm64|aarch64) BUILD_ARCH=aarch64 ;;
  *) fail "Unsupported build machine CPU: $UNAME_M (need x86_64 or arm64/aarch64)." ;;
esac
case "$BUILD_OS" in
  linux) BUILD_VENDOR=unknown; BUILD_PLATFORM="$BUILD_ARCH-unknown-linux" ;;
  darwin) BUILD_VENDOR=apple; BUILD_PLATFORM="$BUILD_ARCH-apple-darwin" ;;
esac

CROSS_ROOT=${CROSS_ROOT:-$HOME/cross-ghc-current}
[[ "$CROSS_ROOT" == /* && "$CROSS_ROOT" != *[[:space:]]* ]] || fail "CROSS_ROOT must be absolute and contain no whitespace."
mkdir -p "$CROSS_ROOT"
CROSS_ROOT=$(cd "$CROSS_ROOT" && pwd -P)

DIST="$CROSS_ROOT/ghc-src/_build/dist"
# Re-running against a finished compiler needs far less scratch space.
if [[ "${CI:-}" == "true" || -x "$DIST/bin/ghc" ]]; then
  MIN_FREE_GB=${MIN_FREE_GB:-4}
else
  MIN_FREE_GB=${MIN_FREE_GB:-32}
fi
avail_kb=$(df -Pk "$CROSS_ROOT" | awk 'NR==2 {print $4}')
(( avail_kb >= MIN_FREE_GB*1024*1024 )) || fail "Allow at least ${MIN_FREE_GB} GiB free under $CROSS_ROOT."

printf '%s\n' "$CROSS_ROOT" > "$HOME/.cross-ghc-root-windows"
{
  printf 'CROSS_ROOT=%q\n' "$CROSS_ROOT"
  printf 'BUILD_ARCH=%q\n' "$BUILD_ARCH"
  printf 'BUILD_OS=%q\n' "$BUILD_OS"
  printf 'BUILD_VENDOR=%q\n' "$BUILD_VENDOR"
  printf 'BUILD_PLATFORM=%q\n' "$BUILD_PLATFORM"
  printf 'DIST=%q\n' "$DIST"
  printf 'GHC_SRC=%q\n' "$CROSS_ROOT/ghc-src"
  printf 'CABAL_SRC=%q\n' "$CROSS_ROOT/cabal-src"
  printf 'BOOT_GHC=%q\n' "${BOOT_GHC:-ghc-9.8.4}"
  printf 'BOOT_CABAL=%q\n' "${BOOT_CABAL:-cabal}"
  printf 'MAKE_BIN=%q\n' "${MAKE_BIN:-make}"
  printf 'SRC=%q\n' "$CROSS_ROOT/ghc-target-src-windows"
  printf 'CABAL=%q\n' "$CROSS_ROOT/cabal-dist/cabal"
  printf 'STAGE3=%q\n' "$CROSS_ROOT/stage3-windows"
  printf 'FIBO=%q\n' "$CROSS_ROOT/cross-report-windows"
  printf 'LLVM_MINGW=%q\n' "$CROSS_ROOT/llvm-mingw-ucrt"
  printf 'FFI_PREFIX=%q\n' "$CROSS_ROOT/windows-ffi-ucrt"
  printf 'WINEPREFIX=%q\n' "$CROSS_ROOT/wineprefix"
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
  if [[ -n "${LLVM_MINGW:-}" && -d "$LLVM_MINGW/bin" && ":$PATH:" != *":$LLVM_MINGW/bin:"* ]]; then
    export PATH="$LLVM_MINGW/bin:$PATH"
  fi
}
EOF
} > "$CROSS_ROOT/gate-windows.env"
cp "$CROSS_ROOT/gate-windows.env" "$CROSS_ROOT/gate.env"
printf 'Build: %s-%s (%s); tree: %s; disk allowance: %s GiB\n' \
  "$BUILD_ARCH" "$BUILD_OS" "$BUILD_PLATFORM" "$CROSS_ROOT" "$MIN_FREE_GB"
""")

# ---- 2. windows C toolchain, libffi, wine -------------------------------
md("win-sdk-md", r"""
## 2. Windows C toolchain (LLVM-MinGW UCRT), static UCRT `libffi`, and Wine

LLVM-MinGW UCRT SDK (`20261104`, per-platform asset, hash-verified) supplies the target cc/cxx/ar/ranlib/nm/windres; UCRT matches the Windows RTS symbols here.
Debian's default MSVCRT toolchain is avoided for the same reason.
A preinstalled `x86_64-w64-mingw32-clang` on `PATH` wins over the download everywhere.
Static `libffi-3.4.8` is configured for `--host=x86_64-w64-mingw32` with that UCRT toolchain and installed into `windows-ffi-ucrt`.
The install is skipped when `libffi.a` already exists.
Wine runs under an isolated `WINEPREFIX` (`$CROSS_ROOT/wineprefix`, never `~/.wine`) with `WINEDLLOVERRIDES` and `WINEDEBUG="-all"` for headless runs.
`wineboot --init` primes the prefix; UCRT DLLs are staged into `drive_c/windows/system32`.
""")

code("win-sdk", r"""
%%bash
set -euo pipefail
ROOT=$(cat "$HOME/.cross-ghc-root-windows")
# shellcheck disable=SC1091
source "$ROOT/gate-windows.env"
cross_enter

LLVM_TAG=20261104
DL="$CROSS_ROOT/downloads"
mkdir -p "$DL"
case "$BUILD_OS/$BUILD_ARCH" in
  linux/x86_64)
    SDK_TARBALL="llvm-mingw-$LLVM_TAG-ucrt-ubuntu-22.04-x86_64.tar.xz"
    SDK_SHA256="919380403de87dca3c581c2053230cd298af5e891286628ba0613b7fed370926" ;;
  linux/aarch64)
    SDK_TARBALL="llvm-mingw-$LLVM_TAG-ucrt-ubuntu-22.04-aarch64.tar.xz"
    SDK_SHA256="595e95bdffabfc62559fe26e30bdfbccf277cbaed4902793555015ee47306b94" ;;
  darwin/*)
    SDK_TARBALL="llvm-mingw-$LLVM_TAG-ucrt-macos-universal.tar.xz"
    SDK_SHA256="66cbd5178c4a71b0d473e68ede172f8eb7d15d023e67f39201d0a69e21f6bf3c" ;;
  *) echo "Unsupported build platform: $BUILD_OS/$BUILD_ARCH" >&2; exit 1 ;;
esac
SDK_URL="https://github.com/mstorsjo/llvm-mingw/releases/download/$LLVM_TAG/$SDK_TARBALL"

# A preinstalled LLVM-MinGW UCRT toolchain wins over the download.
if command -v x86_64-w64-mingw32-clang >/dev/null 2>&1; then
  LLVM_MINGW=$(dirname "$(dirname "$(command -v x86_64-w64-mingw32-clang)")")
  { grep -v '^LLVM_MINGW=' "$ROOT/gate-windows.env"; printf 'LLVM_MINGW=%q\n' "$LLVM_MINGW"; } > "$ROOT/gate-windows.env.tmp"
  mv "$ROOT/gate-windows.env.tmp" "$ROOT/gate-windows.env"
  cp "$ROOT/gate-windows.env" "$ROOT/gate.env"
  echo "Using preinstalled LLVM-MinGW at $LLVM_MINGW"
else
  if [[ ! -f "$DL/$SDK_TARBALL" ]]; then
    curl -fL --retry 3 -o "$DL/$SDK_TARBALL" "$SDK_URL"
  fi
  if [[ -n "${SDK_SHA256:-}" ]]; then
    python3 - "$DL/$SDK_TARBALL" "$SDK_SHA256" <<'PY_SDK'
import hashlib, sys
digest = hashlib.file_digest(open(sys.argv[1], 'rb'), 'sha256').hexdigest()
assert digest == sys.argv[2], f"SDK hash mismatch: {digest}"
print("SDK hash ok")
PY_SDK
  else
    python3 - "$DL/$SDK_TARBALL" <<'PY_SDK_RECORD'
import hashlib, sys
print("recorded sha256:", hashlib.file_digest(open(sys.argv[1], 'rb'), 'sha256').hexdigest())
PY_SDK_RECORD
    echo "No pinned hash for $SDK_TARBALL on $BUILD_OS/$BUILD_ARCH; recorded above."
  fi
  if [[ ! -x "$LLVM_MINGW/bin/x86_64-w64-mingw32-clang" ]]; then
    mkdir -p "$LLVM_MINGW"
    tar -xf "$DL/$SDK_TARBALL" -C "$LLVM_MINGW" --strip-components=1
  fi
fi
export PATH="$LLVM_MINGW/bin:$PATH"
"$LLVM_MINGW/bin/x86_64-w64-mingw32-clang" --version | head -1

# Static UCRT libffi for the Windows target RTS (idempotent).
if [[ -f "$FFI_PREFIX/lib/libffi.a" ]]; then
  echo "UCRT libffi already installed at $FFI_PREFIX — skipping."
else
  LIBFFI_TARBALL=libffi-3.4.8.tar.gz
  LIBFFI_URL="https://github.com/libffi/libffi/releases/download/v3.4.8/$LIBFFI_TARBALL"
  LIBFFI_SHA256=bc9842a18898bfacb0ed1252c4febcc7e78fa139fd27fdc7a3e30d9d9356119b
  [[ -f "$DL/$LIBFFI_TARBALL" ]] || curl -fL --retry 3 -o "$DL/$LIBFFI_TARBALL" "$LIBFFI_URL"
  python3 - "$DL/$LIBFFI_TARBALL" "$LIBFFI_SHA256" <<'PY_FFI'
import hashlib, sys
digest = hashlib.file_digest(open(sys.argv[1], 'rb'), 'sha256').hexdigest()
assert digest == sys.argv[2], f"libffi hash mismatch: {digest}"
print("libffi hash ok")
PY_FFI
  ffi_src="$CROSS_ROOT/libffi-ucrt-src"
  rm -rf "$ffi_src"; mkdir -p "$ffi_src"
  tar -xzf "$DL/$LIBFFI_TARBALL" -C "$ffi_src" --strip-components=1
  cd "$ffi_src"
  CC="$LLVM_MINGW/bin/x86_64-w64-mingw32-clang" \
  CXX="$LLVM_MINGW/bin/x86_64-w64-mingw32-clang++" \
  AR="$LLVM_MINGW/bin/x86_64-w64-mingw32-ar" \
  RANLIB="$LLVM_MINGW/bin/x86_64-w64-mingw32-ranlib" \
  NM="$LLVM_MINGW/bin/x86_64-w64-mingw32-nm" \
    ./configure --host=x86_64-w64-mingw32 --disable-shared --enable-static --prefix="$FFI_PREFIX"
  make -j"$BUILD_JOBS"
  make install
  [[ -f "$FFI_PREFIX/lib/libffi.a" ]] || cp "$FFI_PREFIX/lib/libffi.lib" "$FFI_PREFIX/lib/libffi.a"
  [[ -f "$FFI_PREFIX/lib/libffi.a" ]]
  echo "UCRT libffi installed at $FFI_PREFIX"
fi

# Wine discovery: $WINE_BIN overrides, then wine64, then wine.
# On darwin, check the Wine app bundles and fall back to the pinned Gcenx Wine build.
WINE_BIN=${WINE:-$(command -v wine64 || command -v wine || true)}
if [[ -z "${WINE_BIN:-}" && "$BUILD_OS" == "darwin" ]]; then
  /usr/sbin/softwareupdate --install-rosetta --agree-to-license 2>/dev/null || true
  for wine_dir in \
    "$CROSS_ROOT/wine-macos/Wine Stable.app/Contents/Resources/wine/bin" \
    "/Applications/Wine Stable.app/Contents/Resources/wine/bin" \
    "/Applications/Wine Crossover.app/Contents/SharedSupport/wine/bin"; do
    if [[ -x "$wine_dir/wine64" ]]; then
      export PATH="$wine_dir:$PATH"
      WINE_BIN="$wine_dir/wine64"
      break
    elif [[ -x "$wine_dir/wine" ]]; then
      export PATH="$wine_dir:$PATH"
      WINE_BIN="$wine_dir/wine"
      break
    fi
  done
  if [[ -z "${WINE_BIN:-}" ]]; then
    WINE_VER="11.0_1"
    WINE_TARBALL="wine-stable-${WINE_VER}-osx64.tar.xz"
    WINE_URL="https://github.com/Gcenx/macOS_Wine_builds/releases/download/${WINE_VER}/${WINE_TARBALL}"
    WINE_SHA256="b50dc50ec7f41d58b115a6b685d4d1315ba3c797bd3aa0f49213f2703cb82388"
    [[ -f "$DL/$WINE_TARBALL" ]] || curl -fL --retry 3 -o "$DL/$WINE_TARBALL" "$WINE_URL"
    python3 - "$DL/$WINE_TARBALL" "$WINE_SHA256" <<'PY_WINE'
import hashlib, sys
digest = hashlib.file_digest(open(sys.argv[1], 'rb'), 'sha256').hexdigest()
assert digest == sys.argv[2], f"Wine hash mismatch: {digest}"
print("Wine hash ok")
PY_WINE
    mkdir -p "$CROSS_ROOT/wine-macos"
    tar -xJf "$DL/$WINE_TARBALL" -C "$CROSS_ROOT/wine-macos"
    xattr -dr com.apple.quarantine "$CROSS_ROOT/wine-macos/Wine Stable.app" 2>/dev/null || true
    wine_dir="$CROSS_ROOT/wine-macos/Wine Stable.app/Contents/Resources/wine/bin"
    export PATH="$wine_dir:$PATH"
    WINE_BIN="$wine_dir/wine"
  fi
fi
[[ -n "${WINE_BIN:-}" && -x "$WINE_BIN" ]] || {
  echo "No wine64/wine on PATH; enter this notebook's flake dev shell (Wine ships per platform there)." >&2; exit 1; }
{ grep -v '^WINE_BIN=' "$ROOT/gate-windows.env"; printf 'WINE_BIN=%q\n' "$WINE_BIN"; } > "$ROOT/gate-windows.env.tmp"
mv "$ROOT/gate-windows.env.tmp" "$ROOT/gate-windows.env"
cp "$ROOT/gate-windows.env" "$ROOT/gate.env"
"$WINE_BIN" --version
# Isolated, headless prefix: no GUI/Gecko/Mono prompts, no pointer aborts.
export WINEPREFIX="$CROSS_ROOT/wineprefix"
export WINEDLLOVERRIDES="mscoree,mshtml=;winemac.drv=d"
export WINEDEBUG="-all"
export MVK_CONFIG_LOG_LEVEL=0
mkdir -p "$WINEPREFIX"
"$WINE_BIN" wineboot --init </dev/null
"${WINE_BIN%/*}/wineserver" -k 2>/dev/null || wineserver -k 2>/dev/null || true
mkdir -p "$WINEPREFIX/drive_c/windows/system32"
for dll in "$LLVM_MINGW/x86_64-w64-mingw32/bin"/*.dll; do
  [[ -f "$dll" ]] && cp -f "$dll" "$WINEPREFIX/drive_c/windows/system32/"
done
echo "Wine prefix ready at $WINEPREFIX"
""")

# ---- 3. fetch & build GHC 9.14 + cross-aware Cabal -----------------------
md("win-fetch-md", r"""
## 3. Stage 2 native GHC + cross-aware Cabal

Builds native stage 2 GHC from `stable-haskell/ghc:stable-ghc-9.14` and Cabal from `andrea/feature/cross-compilation-2`.
GHC's Makefile uses its own pinned stage0 Cabal builder; the Andrea-branch Cabal builds the target libraries and the app.
A wired-in boot-library patch lets Cabal build a missing wired-in unit (e.g. `ghc-internal`) only with `allow-boot-library-installs: True`.
Existing installed wired-in unit IDs keep their exact-unit pins.
Commits are pinned in `sources.lock`; rebuild is skipped when `$CABAL` and `$DIST/bin/ghc` already match (`FORCE_REBUILD=1` overrides).
Use a fresh `CROSS_ROOT` when changing commits; never mix databases across revisions.
""")

code("win-fetch", r"""
%%bash
set -euo pipefail
ROOT=$(cat "$HOME/.cross-ghc-root-windows")
source "$ROOT/gate-windows.env"
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

# True when a previous run recorded these exact sources (plus the boot patch).
built_matches() {
  [[ -f "$CROSS_ROOT/build-sources.lock" ]] || return 1
  head -n 2 "$CROSS_ROOT/build-sources.lock" | cmp -s - "$CROSS_ROOT/sources.lock"
}
if [[ -f "$CROSS_ROOT/build-sources.lock" ]] && ! built_matches; then
  echo "Sources changed. Start with a fresh CROSS_ROOT for this toolchain." >&2; exit 1
fi

BOOT_GHC=$(command -v "$BOOT_GHC")
BOOT_CABAL=$(command -v "$BOOT_CABAL")
MAKE_BIN=$(command -v "$MAKE_BIN")
[[ $("$BOOT_GHC" --numeric-version) == 9.8.4 ]]
make_version=$("$MAKE_BIN" --version | awk 'NR==1 {print $3}')
python3 - "$make_version" <<'PY_MAKE'
import sys
have = tuple(int(x) for x in sys.argv[1].split('.')[:2])
assert have >= (4, 4), f"Need GNU Make 4.4+, found {sys.argv[1]}"
print("make ok:", sys.argv[1])
PY_MAKE
mkdir -p "$CABAL_DIR" "$CROSS_ROOT/cabal-dist"
if [[ ! -f "$CABAL_DIR/config" ]]; then "$BOOT_CABAL" user-config init; fi
sed -i.bak 's|url: http://hackage.haskell.org/|url: https://hackage.haskell.org/|' "$CABAL_DIR/config"
"$BOOT_CABAL" update

if [[ -z "${FORCE_REBUILD:-}" && -x "$CABAL" ]] && built_matches; then
  echo "Cabal already built for these sources — skipping."
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
  sha256sum "$CROSS_ROOT/cabal-boot.patch" 2>/dev/null >> "$CROSS_ROOT/build-sources.lock" \
    || shasum -a 256 "$CROSS_ROOT/cabal-boot.patch" >> "$CROSS_ROOT/build-sources.lock"
  cd "$CABAL_SRC"
  "$BOOT_CABAL" install --project-file=cabal.release.project \
    --with-compiler="$BOOT_GHC" -j"$BUILD_JOBS" --disable-tests --disable-benchmarks \
    --installdir="$CROSS_ROOT/cabal-dist" --install-method=copy \
    --overwrite-policy=always cabal-install:exe:cabal
fi
"$CABAL" --version
"$CABAL" build --help | grep -E -- '-w PATH'
"$CABAL" build --help | grep -E -- '-W PATH'

if [[ -z "${FORCE_REBUILD:-}" && -x "$DIST/bin/ghc" ]] && built_matches; then
  echo "Native stage2 already built for these sources — skipping."
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
fi
[[ -x "$DIST/bin/ghc" && -x "$DIST/bin/ghc-pkg" ]]
"$DIST/bin/ghc" --numeric-version
"$DIST/bin/ghc" -list-targets
"$DIST/bin/ghc-pkg" check
""")

# ---- 4. windows target source worktree -----------------------------------
md("win-tree-md", r"""
## 4. Windows target source worktree

Second checkout at the same GHC commit holds the Windows libraries; `ghc-boot-th-next` is staged locally and excluded from the target build.
`rts/rts.cabal` is restricted to the vanilla `v` way: the three non-vanilla sublibraries get unconditional `buildable: False`.
Only the vanilla RTS archive feeds the later `stage3` link, keeping the Windows RTS static and unprofiled.
Target `configure` inputs are generated in place (`autoreconf`) before the Cabal build.
The edit lands after the native dist exists, so the bootstrap build is unaffected.
""")

code("win-tree", r"""
%%bash
set -euo pipefail
ROOT=$(cat "$HOME/.cross-ghc-root-windows")
# shellcheck disable=SC1091
source "$ROOT/gate-windows.env"
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

# ---- 5. teaching GHC about x86_64-w64-mingw32 ---------------------------
md("win-settings-md", r"""
## 5. Teaching GHC about `x86_64-w64-mingw32`

`ghc-toolchain-bin --triple=x86_64-w64-mingw32` (LLVM-MinGW UCRT cc/cxx/ar/ranlib/nm, `--target-prefix x86_64-w64-mingw32-`) writes `lib/targets/x86_64-w64-mingw32/lib/settings`.
Hardcoded `ghc-toolchain` fields are corrected: RTS ways forced to `v`, dynamic/profiled properties set to `NO` for the static target.
Target-prefixed symlinks (`x86_64-w64-mingw32-{ghc,ghc-pkg,hsc2hs}`) select the Windows settings and package database from one binary.
`unlit` is shared into the target bindir since it always runs on the build machine.
Selected target verified via `--print-libdir` for both the symlink and `-target=` spellings.
`deriveConstants`/`genapply` paths are recorded; `NM`/`OBJDUMP` point at the UCRT toolchain for the stage3 build.
""")

code("win-settings", r"""
%%bash
set -euo pipefail
ROOT=$(cat "$HOME/.cross-ghc-root-windows")
# shellcheck disable=SC1091
source "$ROOT/gate-windows.env"
cross_enter

# Native tools for target configuration. deriveConstants inspects target objects
# with nm/objdump; it does not need to execute those objects.
for name in deriveConstants genapply ghc-toolchain-bin; do
  [[ -x "$DIST/bin/$name" ]]
  printf '%s\n' "$DIST/bin/$name" > "$CROSS_ROOT/$name.path"
done
toolbin=$(cat "$CROSS_ROOT/ghc-toolchain-bin.path")

target_lib="$DIST/lib/targets/x86_64-w64-mingw32/lib"
mkdir -p "$target_lib"
export PATH="$LLVM_MINGW/bin:$PATH"
cd "$SRC"
"$toolbin" \
  --triple x86_64-w64-mingw32 \
  --target-prefix x86_64-w64-mingw32- \
  --cc "$LLVM_MINGW/bin/x86_64-w64-mingw32-clang" \
  --cxx "$LLVM_MINGW/bin/x86_64-w64-mingw32-clang++" \
  --ar "$LLVM_MINGW/bin/x86_64-w64-mingw32-ar" \
  --ranlib "$LLVM_MINGW/bin/x86_64-w64-mingw32-ranlib" \
  --nm "$LLVM_MINGW/bin/x86_64-w64-mingw32-nm" \
  --windres "$LLVM_MINGW/bin/x86_64-w64-mingw32-windres" \
  --disable-ld-override \
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
if [[ ! -d "$target_lib/package.conf.d" ]]; then
  "$DIST/bin/ghc-pkg" init "$target_lib/package.conf.d"
fi
ln -sfn ghc "$DIST/bin/x86_64-w64-mingw32-ghc"
ln -sfn ghc-pkg "$DIST/bin/x86_64-w64-mingw32-ghc-pkg"
ln -sfn hsc2hs "$DIST/bin/x86_64-w64-mingw32-hsc2hs"

echo "---- platform string (w64) and RTS ways ----"
TARGET_LIB="$target_lib" python3 - <<'PY_SHOW'
import ast, os
s = ast.literal_eval(open(os.environ['TARGET_LIB'] + '/settings').read())
d = dict(s)
for k in ("target platform string", "target arch", "target os", "RTS ways",
          "Use LibFFI", "target is dynamic", "target is profiled"):
    print(f"{k}: {d.get(k)}")
PY_SHOW

# unlit runs on the build machine, even when processing target sources.
mkdir -p "$DIST/lib/targets/x86_64-w64-mingw32/bin"
ln -sfn "$DIST/bin/unlit" "$DIST/lib/targets/x86_64-w64-mingw32/bin/unlit"
echo "---- selected target ----"
echo -n "symlink:   "; "$DIST/bin/x86_64-w64-mingw32-ghc" --print-libdir
echo -n "-target=:  "; "$DIST/bin/ghc" -target=x86_64-w64-mingw32 --print-libdir
echo -n "native:    "; "$DIST/bin/ghc" --print-libdir
linkdir=$("$DIST/bin/x86_64-w64-mingw32-ghc" --print-libdir)
natdir=$("$DIST/bin/ghc" --print-libdir)
[[ "$linkdir" == "$target_lib" ]]
[[ "$natdir" != "$target_lib" ]]
[[ $("$DIST/bin/ghc" -target=x86_64-w64-mingw32 --print-libdir) == "$target_lib" ]]
echo "list-targets:"
"$DIST/bin/ghc" -list-targets
echo "settings ok"
""")

# ---- 6. building the windows target libraries ---------------------------
md("win-stage3-md", r"""
## 6. Building the Windows target libraries (`stage3-windows`)

Dual-compiler build: `-w x86_64-w64-mingw32-ghc` for `host:*`, `-W ghc` for `build:*` (plus matching `--with-hc-pkg`/`--with-build-hc-pkg` and `--with-gcc`).
Native boot deps stay `installed` (`build:any.* installed`); target boot libraries stay `source`.
`Win32` (+`os-string`) replaces `unix`; `package rts` points at the static UCRT `libffi` via `extra-include-dirs`/`extra-lib-dirs` with `Build*`/`Host*` CPP defines.
The non-threaded RTS way builds first so `ghc-internal` interfaces lose no placeholder RTS unit IDs.
Only plan units with `"machine": "host"` register into the target `package.conf.d`; skips when `base` is already registered.
Cabal store lives under `stage3-windows/store`; cached `exported-conf` restores existing registrations.
""")

code("win-stage3", r"""
%%bash
set -euo pipefail
ROOT=$(cat "$HOME/.cross-ghc-root-windows")
# shellcheck disable=SC1091
source "$ROOT/gate-windows.env"
cross_enter
mkdir -p "$STAGE3"
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
  https://hackage.haskell.org/package/stm-2.5.3.1/stm-2.5.3.1.tar.gz
  https://hackage.haskell.org/package/mtl-2.3.1/mtl-2.3.1.tar.gz
  https://hackage.haskell.org/package/parsec-3.1.18.0/parsec-3.1.18.0.tar.gz
  https://hackage.haskell.org/package/process-1.6.29.0/process-1.6.29.0.tar.gz
  https://hackage.haskell.org/package/semaphore-compat-2.0.1/semaphore-compat-2.0.1.tar.gz
  https://hackage.haskell.org/package/text-2.1.3/text-2.1.3.tar.gz
  https://hackage.haskell.org/package/Cabal-syntax-3.14.1.0/Cabal-syntax-3.14.1.0.tar.gz
  https://hackage.haskell.org/package/Win32-2.14.2.1/Win32-2.14.2.1.tar.gz
  https://hackage.haskell.org/package/hsc2hs-0.68.10/hsc2hs-0.68.10.tar.gz
  https://hackage.haskell.org/package/alex-3.5.2.0/alex-3.5.2.0.tar.gz

package host:*
  library-vanilla: True
  shared: False
  library-for-ghci: False
  executable-dynamic: False
  executable-profiling: False
  ghc-options: -j$GHC_JOBS -O0

package ghc-internal
  configure-options: --with-compiler=$DIST/bin/x86_64-w64-mingw32-ghc
  flags: +bignum-native
  ghc-options: -no-rts

package rts-fs
  ghc-options: -no-rts

package rts-headers
  ghc-options: -no-rts

package rts
  flags: +use-system-libffi +tables-next-to-code -dynamic -profiling -debug -threaded
  extra-include-dirs: $FFI_PREFIX/include
  extra-lib-dirs: $FFI_PREFIX/lib
  ghc-options: -j$GHC_JOBS -O0 -no-rts
  ghc-options: "-optc-DProjectVersion=\\"914\\""
  ghc-options: "-optc-DHostArch=\\"x86_64\\""
  ghc-options: "-optc-DHostOS=\\"mingw32\\""
  ghc-options: "-optc-DHostPlatform=\\"x86_64-w64-mingw32\\""
  ghc-options: "-optc-DHostVendor=\\"w64\\""
  ghc-options: "-optc-DBuildArch=\\"$BUILD_ARCH\\""
  ghc-options: "-optc-DBuildOS=\\"$BUILD_OS\\""
  ghc-options: "-optc-DBuildPlatform=\\"$BUILD_PLATFORM\\""
  ghc-options: "-optc-DBuildVendor=\\"$BUILD_VENDOR\\""
  ghc-options: "-optc-DGhcUnregisterised=\\"NO\\""
  ghc-options: "-optc-DTablesNextToCode=\\"YES\\""
  ghc-options: "-optc-DFS_NAMESPACE=rts"

package ghci
  flags: +internal-interpreter +use-system-libffi

package Win32
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
  build:any.Win32 installed,
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
  Win32 source,
  system-cxx-std-lib source
EOF

tdb="$DIST/lib/targets/x86_64-w64-mingw32/lib/package.conf.d"
export DERIVE_CONSTANTS GENAPPLY
DERIVE_CONSTANTS=$(cat "$CROSS_ROOT/deriveConstants.path")
GENAPPLY=$(cat "$CROSS_ROOT/genapply.path")
export NM="$LLVM_MINGW/bin/x86_64-w64-mingw32-nm"
export OBJDUMP="$LLVM_MINGW/bin/x86_64-w64-mingw32-objdump"
unset CC CXX AR RANLIB || true

if ! "$DIST/bin/x86_64-w64-mingw32-ghc-pkg" field base id >/dev/null 2>&1 \
  && [[ -z "${FORCE_STAGE3_REBUILD:-}" && -d "$STAGE3/exported-conf" ]] \
  && [[ -f "$STAGE3/sources.lock" ]] && cmp -s "$STAGE3/sources.lock" "$CROSS_ROOT/sources.lock"; then
  for conf in "$STAGE3/exported-conf"/*.conf; do
    [[ -e "$conf" ]] || continue
    "$DIST/bin/x86_64-w64-mingw32-ghc-pkg" --package-db="$tdb" update --force "$conf"
  done
  "$DIST/bin/x86_64-w64-mingw32-ghc-pkg" --package-db="$tdb" recache
  if "$DIST/bin/x86_64-w64-mingw32-ghc-pkg" --package-db="$tdb" check >/dev/null 2>&1 \
    && "$DIST/bin/x86_64-w64-mingw32-ghc-pkg" field base id >/dev/null 2>&1; then
    echo "Restored cached stage3-windows registrations from $STAGE3/exported-conf — skipping rebuild."
  else
    rm -f "$tdb"/*.conf
    "$DIST/bin/x86_64-w64-mingw32-ghc-pkg" --package-db="$tdb" recache
  fi
fi
if [[ -z "${FORCE_STAGE3_REBUILD:-}" ]] && "$DIST/bin/x86_64-w64-mingw32-ghc-pkg" field base id >/dev/null 2>&1; then
  echo "Windows target libraries already registered — skipping rebuild."
else
  cd "$STAGE3"
  # Complete the RTS way before compiling ghc-internal. This prevents its
  # interfaces from retaining placeholder RTS unit IDs during bootstrap.
  "$CABAL" --store-dir="$STAGE3/store" build -j"$BUILD_JOBS" \
    -w "$DIST/bin/x86_64-w64-mingw32-ghc" \
    -W "$DIST/bin/ghc" \
    --with-hc-pkg="$DIST/bin/x86_64-w64-mingw32-ghc-pkg" \
    --with-build-hc-pkg="$DIST/bin/ghc-pkg" \
    --with-gcc="$LLVM_MINGW/bin/x86_64-w64-mingw32-clang" \
    --with-hsc2hs="$DIST/bin/hsc2hs" \
    --with-build-hsc2hs="$DIST/bin/hsc2hs" \
    --hsc2hs-options=-x \
    --ghc-option="-ghcversion-file=$SRC/rts/include/ghcversion.h" \
    rts:lib:nonthreaded-nodebug
  "$CABAL" --store-dir="$STAGE3/store" build -j"$BUILD_JOBS" \
    -w "$DIST/bin/x86_64-w64-mingw32-ghc" \
    -W "$DIST/bin/ghc" \
    --with-hc-pkg="$DIST/bin/x86_64-w64-mingw32-ghc-pkg" \
    --with-build-hc-pkg="$DIST/bin/ghc-pkg" \
    --with-gcc="$LLVM_MINGW/bin/x86_64-w64-mingw32-clang" \
    --with-hsc2hs="$DIST/bin/hsc2hs" \
    --with-build-hsc2hs="$DIST/bin/hsc2hs" \
    --hsc2hs-options=-x \
    --ghc-option="-ghcversion-file=$SRC/rts/include/ghcversion.h" \
    all process:lib:process

  # Export only host library registrations, from both in-place and store DBs.
  python3 - "$STAGE3" "$tdb" "$DIST/bin/x86_64-w64-mingw32-ghc-pkg" <<'PY_REGISTER'
import json, re, subprocess, sys
from pathlib import Path
stage, dest, ghcpkg = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
plan = json.loads((stage / 'dist-newstyle/cache/plan.json').read_text())
assert plan['arch'] == 'x86_64', plan['arch']
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
fi
for package in base template-haskell ghci text containers Win32 process; do
  "$DIST/bin/x86_64-w64-mingw32-ghc-pkg" field "$package" id
done
""")

# ---- 7. iserv-proxy + wine TH smoke test ---------------------------------
md("win-proxy-md", r"""
## 7. `iserv-proxy` + Windows interpreter & Wine TH smoke test

Native `iserv-proxy` (native `ghc`) plus Windows `iserv-proxy-interpreter.exe` (target `ghc` + native `ghc` as `-W`), joined by `windows-runner --stdio` under Wine.
The wrapper owns the isolated `WINEPREFIX` and bridges GHC's POSIX pipe descriptors to the interpreter's stdio (`--no-load-call`).
UCRT `rtsExtraSyms()` entries are registered in `cbits/symbols.x86_64-windows.c` so CRT calls like `_fstat64i32` and `_errno` resolve against `ucrtbase.dll`, not `msvcrt.dll` under Wine.
Smoke test: an `Arch.hs` splice lifts target `(arch, os)`; `arch.exe` must print `("x86_64","mingw32")`.
Proxy and interpreter build from `iserv-proxy-src` with matching `network`/`hsc2hs` deps.
""")

code("win-proxy", r"""
%%bash
set -euo pipefail
ROOT=$(cat "$HOME/.cross-ghc-root-windows")
source "$ROOT/gate-windows.env"
cross_enter
export PATH="$LLVM_MINGW/bin:$PATH"

ISERV_SRC="$CROSS_ROOT/iserv-proxy-src"
if [[ ! -d "$ISERV_SRC/.git" ]]; then
  git clone --branch iserv-syms https://github.com/stable-haskell/iserv-proxy.git "$ISERV_SRC"
fi
git -C "$ISERV_SRC" checkout -- .
git -C "$ISERV_SRC" rev-parse HEAD

# Export all UCRT symbols referenced by $STAGE3 static archives in
# iserv-proxy-interpreter.exe's rtsExtraSyms() table (cbits/symbols.x86_64-windows.c).
# Under Wine, transitive Win32 DLL imports load msvcrt.dll ahead of ucrtbase.dll
# in opened_dlls, so any CRT symbol not in symhash (such as __imp__fstati64)
# would otherwise resolve to msvcrt.dll while __rts_swopen (in symhash) opens
# file descriptors in ucrtbase.dll.
python3 - "$ISERV_SRC/cbits/symbols.x86_64-windows.c" \
  "$LLVM_MINGW/bin/x86_64-w64-mingw32-nm" \
  "$LLVM_MINGW/x86_64-w64-mingw32/lib/libucrt.a" \
  "$STAGE3" \
  "$SRC/rts/RtsSymbols.c" <<'PY_ISERV_UCRT'
import pathlib, re, struct, subprocess, sys

c_file = pathlib.Path(sys.argv[1])
nm_bin = sys.argv[2]
libucrt = pathlib.Path(sys.argv[3])
stage3 = pathlib.Path(sys.argv[4])
rts_syms_file = pathlib.Path(sys.argv[5])

def read_ar_syms(ar_path: pathlib.Path) -> set[str]:
    data = ar_path.read_bytes()
    if not data.startswith(b"!<arch>\n") or data[8:10] != b"/ ":
        return set()
    size = int(data[56:66].decode("ascii", "replace").strip() or "0")
    body = data[68:68 + size]
    if len(body) < 4:
        return set()
    count = struct.unpack(">I", body[:4])[0]
    raw = body[4 + 4 * count:]
    return {
        s.decode("ascii", "replace")
        for s in raw.split(b"\x00")
        if s and re.match(rb"^[A-Za-z_][A-Za-z0-9_]*$", s)
    }

src = c_file.read_text()
existing = set(re.findall(r"\bSYM\(\s*([A-Za-z_][A-Za-z0-9_]*)\s*\)", src))
existing.update(re.findall(r'"([A-Za-z_][A-Za-z0-9_]*)"', src))
existing.update({"_assert", "__imp__assert", "__imp___msvcrt_assert"})
if rts_syms_file.exists():
    rts_src = rts_syms_file.read_text(errors="replace")
    existing.update(re.findall(r"\bSymI_[A-Za-z0-9_]*\(\s*([A-Za-z_][A-Za-z0-9_]*)", rts_src))

ucrt_defs = read_ar_syms(libucrt)
ucrt_types = {}
res = subprocess.run([nm_bin, "-s", str(libucrt)], capture_output=True, text=True, errors="replace")
for line in res.stdout.splitlines():
    m_map = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)\s+in\s+\S+", line)
    if m_map:
        ucrt_defs.add(m_map.group(1))
        continue
    m_sym = re.match(r"^[0-9a-fA-F]+\s+([A-TV-Z])\s+([A-Za-z_][A-Za-z0-9_]*)$", line)
    if m_sym:
        ucrt_types[m_sym.group(2)] = m_sym.group(1)
        ucrt_defs.add(m_sym.group(2))

ucrt_defs = {
    s for s in ucrt_defs
    if not s.startswith("_head_") and not s.endswith("_iname") and s != "__NULL_IMPORT_DESCRIPTOR"
}

archives = sorted(stage3.rglob("libHS*.a"))
assert archives, f"No libHS*.a archives found under {stage3}"
stage3_undefs = set()
stage3_defs = set()
for a in archives:
    stage3_defs.update(read_ar_syms(a))
for i in range(0, len(archives), 20):
    batch = [str(a) for a in archives[i:i + 20]]
    u_out = subprocess.check_output([nm_bin, "-u", *batch], text=True, errors="replace")
    for line in u_out.splitlines():
        m = re.search(r"\bU\s+([A-Za-z_][A-Za-z0-9_]*)$", line)
        if m:
            stage3_undefs.add(m.group(1))
    d_res = subprocess.run([nm_bin, "--defined-only", *batch], capture_output=True, text=True, errors="replace")
    for line in d_res.stdout.splitlines():
        m = re.match(r"^[0-9a-fA-F]+\s+[A-TV-Z]\s+([A-Za-z_][A-Za-z0-9_]*)$", line)
        if m:
            stage3_defs.add(m.group(1))

wanted = sorted((stage3_undefs - stage3_defs - existing) & ucrt_defs)
assert wanted, (
    f"Expected UCRT symbols in wanted (ucrt_defs={len(ucrt_defs)}, "
    f"stage3_undefs={len(stage3_undefs)})"
)

decls = []
entries = []
for idx, sym in enumerate(wanted):
    alias = f"iserv_ucrt_{idx}"
    if sym.startswith("__imp_"):
        decls.append(f'extern void *{alias} __asm__("{sym}");')
        entries.append(f'    {{ "{sym}", (void*)&{alias}, STRENGTH_WEAK, SYM_TYPE_INDIRECT_DATA }},')
    elif ucrt_types.get(sym) in ("D", "B", "R", "C"):
        decls.append(f'extern char {alias} __asm__("{sym}");')
        entries.append(f'    {{ "{sym}", (void*)&{alias}, STRENGTH_WEAK, SYM_TYPE_DATA }},')
    else:
        decls.append(f'extern void {alias}(void) __asm__("{sym}");')
        entries.append(f'    {{ "{sym}", (void*)&{alias}, STRENGTH_WEAK, SYM_TYPE_CODE }},')

table_hdr = "RtsSymbolVal my_iserv_syms[] = {\n"
sentinel = "    { 0, 0, STRENGTH_NORMAL, 1 } /* sentinel */"
assert table_hdr in src and sentinel in src, "Unexpected cbits/symbols.x86_64-windows.c layout"
src = src.replace(table_hdr, "\n".join(decls) + "\n\n" + table_hdr, 1)
src = src.replace(sentinel, "\n".join(entries) + "\n" + sentinel, 1)
c_file.write_text(src)
print(f"Registered {len(wanted)} UCRT symbols in {c_file.name}")
PY_ISERV_UCRT

mkdir -p "$CROSS_ROOT/proxy-native"
cat > "$CROSS_ROOT/proxy-native/cabal.project" << PROJECT
active-repositories: :none
allow-boot-library-installs: True
allow-newer: all
jobs: 2
packages:
  $ISERV_SRC
  https://hackage.haskell.org/package/hsc2hs-0.68.10/hsc2hs-0.68.10.tar.gz
  https://hackage.haskell.org/package/network-3.2.7.0/network-3.2.7.0.tar.gz
package *
  shared: False
  optimization: False
package iserv-proxy
  flags: -threaded
constraints:
  iserv-proxy -threaded,
  build:any.ghc-internal installed,
  any.ghc-internal installed,
  any.ghci installed,
  any.base installed
PROJECT
cd "$CROSS_ROOT/proxy-native"
"$CABAL" build -j"$BUILD_JOBS" -w "$DIST/bin/ghc" exe:iserv-proxy
"$CABAL" list-bin -w "$DIST/bin/ghc" exe:iserv-proxy > "$CROSS_ROOT/proxy-native.path"

mkdir -p "$CROSS_ROOT/proxy-windows"
cat > "$CROSS_ROOT/proxy-windows/cabal.project" << PROJECT
active-repositories: :none
allow-boot-library-installs: True
allow-newer: all
jobs: 2
packages:
  $ISERV_SRC
  https://hackage.haskell.org/package/hsc2hs-0.68.10/hsc2hs-0.68.10.tar.gz
  https://hackage.haskell.org/package/network-3.2.7.0/network-3.2.7.0.tar.gz
  https://hackage.haskell.org/package/temporary-1.3/temporary-1.3.tar.gz
  https://hackage.haskell.org/package/random-1.2.1.3/random-1.2.1.3.tar.gz
  https://hackage.haskell.org/package/splitmix-0.1.0.5/splitmix-0.1.0.5.tar.gz
package *
  shared: False
  executable-dynamic: False
  library-vanilla: True
  optimization: False
package iserv-proxy
  flags: -threaded
package network
  configure-options: --host=x86_64-w64-mingw32 --with-cc=$LLVM_MINGW/bin/x86_64-w64-mingw32-clang
constraints:
  iserv-proxy -threaded,
  build:any.base installed,
  build:any.Cabal installed,
  build:any.Cabal-syntax installed,
  build:any.ghc-internal installed,
  any.ghc-internal installed,
  any.ghci installed,
  any.base installed,
  any.bytestring installed,
  any.containers installed,
  any.deepseq installed,
  any.directory installed,
  any.exceptions installed,
  any.filepath installed,
  any.mtl installed,
  any.stm installed,
  any.transformers installed,
  any.Win32 installed
PROJECT
cd "$CROSS_ROOT/proxy-windows"
export NM="$LLVM_MINGW/bin/x86_64-w64-mingw32-nm"
export OBJDUMP="$LLVM_MINGW/bin/x86_64-w64-mingw32-objdump"
"$CABAL" build -j"$BUILD_JOBS" --hsc2hs-options=-x \
  --with-gcc="$LLVM_MINGW/bin/x86_64-w64-mingw32-clang" \
  --with-hsc2hs="$DIST/bin/hsc2hs" \
  --with-build-hsc2hs="$DIST/bin/hsc2hs" \
  -w "$DIST/bin/x86_64-w64-mingw32-ghc" -W "$DIST/bin/ghc" \
  --with-hc-pkg="$DIST/bin/x86_64-w64-mingw32-ghc-pkg" \
  --with-build-hc-pkg="$DIST/bin/ghc-pkg" \
  exe:iserv-proxy-interpreter
"$CABAL" list-bin -w "$DIST/bin/x86_64-w64-mingw32-ghc" -W "$DIST/bin/ghc" \
  --with-hc-pkg="$DIST/bin/x86_64-w64-mingw32-ghc-pkg" \
  --with-build-hc-pkg="$DIST/bin/ghc-pkg" \
  exe:iserv-proxy-interpreter > "$CROSS_ROOT/proxy-windows.path"
cat "$CROSS_ROOT/proxy-native.path" "$CROSS_ROOT/proxy-windows.path"

mkdir -p "$CROSS_ROOT/bin"
proxy=$(cat "$CROSS_ROOT/proxy-native.path")
interpreter=$(cat "$CROSS_ROOT/proxy-windows.path")
work="$CROSS_ROOT/wine-work"
cat > "$CROSS_ROOT/bin/windows-runner" << RUNNER
#!/bin/bash
set -euo pipefail
proxy=$(cat "$CROSS_ROOT/proxy-native.path")
interpreter=$(cat "$CROSS_ROOT/proxy-windows.path")
work="$CROSS_ROOT/wine-work"
mkdir -p "$work"
# The native proxy owns GHC's POSIX descriptors; the Windows interpreter
# communicates through stdin/stdout instead of inheriting those descriptors.
export WINEPREFIX="$CROSS_ROOT/wineprefix"
export WINEDLLOVERRIDES="mscoree,mshtml=;winemac.drv=d"
export WINEDEBUG="-all"
export MVK_CONFIG_LOG_LEVEL=0
exec "$proxy" "\$@" --pipe "$WINE_BIN" "$interpreter" "$work" --stdio --no-load-call
RUNNER
chmod +x "$CROSS_ROOT/bin/windows-runner"

mkdir -p "$CROSS_ROOT/smoke"
cat > "$CROSS_ROOT/smoke/Arch.hs" << 'EOF'
{-# LANGUAGE TemplateHaskell #-}
import Language.Haskell.TH.Syntax (lift)
import System.Info (arch, os)
main :: IO ()
main = print $(lift (arch, os))
EOF
"$DIST/bin/x86_64-w64-mingw32-ghc" \
  -fforce-recomp -O0 -fexternal-interpreter \
  -pgmi "$CROSS_ROOT/bin/windows-runner" \
  -o "$CROSS_ROOT/smoke/arch.exe" "$CROSS_ROOT/smoke/Arch.hs"
file "$CROSS_ROOT/smoke/arch.exe"
export WINEPREFIX="$CROSS_ROOT/wineprefix"
export WINEDLLOVERRIDES="mscoree,mshtml=;winemac.drv=d"
export WINEDEBUG="-all"
export MVK_CONFIG_LOG_LEVEL=0
out=$("$WINE_BIN" "$CROSS_ROOT/smoke/arch.exe" </dev/null | tr -d '\r')
"${WINE_BIN%/*}/wineserver" -k 2>/dev/null || wineserver -k 2>/dev/null || true
printf 'Splice evaluated for: %s\n' "$out"
[[ "$out" == '("x86_64","mingw32")' ]]
echo "Wine TH smoke test passed."
""")

# ---- 8. cross-report application ------------------------------------------
md("win-app-md", r"""
## 8. Building & running `cross-report.exe`

Native `Setup.hs` runs on the build machine (`BUILD_MACHINE_ARCH`/`OS` in the log, `git --version` into `build-tool.txt`).
`TH.hs` runs in the Windows interpreter under Wine (`-fexternal-interpreter`, `-pgmi windows-runner`); target C compiler stays in target settings (no cross `CC` export).
Splice validates labels/indices, rejects duplicates, tracks both files via `addDependentFile`, and embeds residues plus `SPLICE_ARCH`/`RUNTIME_ARCH`.
`--info` must show `SPLICE_ARCH=x86_64`, `SPLICE_OS=mingw32`, `RUNTIME_ARCH=x86_64`, `RUNTIME_OS=mingw32`, `BUILD_TOOL=git version ...`.
Expected catalogue output in map order: `century=9261915075`, `large=3428746875`, `small=55`.
Build log must contain `BUILD_MACHINE_ARCH`/`OS`; unknown labels fail with `Unknown catalogue entry: unknown`.
""")

code("win-app-build", r"""
%%bash
set -euo pipefail
ROOT=$(cat "$HOME/.cross-ghc-root-windows")
source "$ROOT/gate-windows.env"
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

cd "$FIBO"
# Ensure the log belongs to this build and Setup runs again.
"$CABAL" clean
PATH="$DIST/bin:$PATH" "$CABAL" build -j"$BUILD_JOBS" \
  -w x86_64-w64-mingw32-ghc -W ghc \
  --with-hc-pkg="$DIST/bin/x86_64-w64-mingw32-ghc-pkg" \
  --with-build-hc-pkg="$DIST/bin/ghc-pkg" \
  exe:cross-report \
  --ghc-option=-fexternal-interpreter \
  --ghc-option=-pgmi$CROSS_ROOT/bin/windows-runner 2>&1 | tee "$CROSS_ROOT/report-build.log"
grep -Fx "BUILD_MACHINE_ARCH=$BUILD_ARCH" "$CROSS_ROOT/report-build.log"
grep -Fx "BUILD_MACHINE_OS=$BUILD_OS" "$CROSS_ROOT/report-build.log"
bin=$(PATH="$DIST/bin:$PATH" "$CABAL" list-bin \
  -w x86_64-w64-mingw32-ghc -W ghc \
  --with-hc-pkg="$DIST/bin/x86_64-w64-mingw32-ghc-pkg" \
  --with-build-hc-pkg="$DIST/bin/ghc-pkg" exe:cross-report)
[[ -x "$bin" ]]
printf '%s\n' "$bin" > "$CROSS_ROOT/report-bin.path"
file "$bin"
WINEPREFIX="$CROSS_ROOT/wineprefix" "${WINE_BIN%/*}/wineserver" -k 2>/dev/null || wineserver -k 2>/dev/null || true
""")

code("win-app-run", r"""
%%bash
set -euo pipefail
ROOT=$(cat "$HOME/.cross-ghc-root-windows")
source "$ROOT/gate-windows.env"
cross_enter
export WINEPREFIX="$CROSS_ROOT/wineprefix"
export WINEDLLOVERRIDES="mscoree,mshtml=;winemac.drv=d"
export WINEDEBUG="-all"
export MVK_CONFIG_LOG_LEVEL=0

bin=$(cat "$CROSS_ROOT/report-bin.path")
run_report() {
  local out rc=0
  out=$("$WINE_BIN" "$bin" "$@" </dev/null) || rc=$?
  printf '%s\n' "${out//$'\r'/}"
  return $rc
}
run_report
info=$(run_report --info)
printf '%s\n' "$info" | tee "$CROSS_ROOT/report-info.txt"
for expected in SPLICE_ARCH=x86_64 SPLICE_OS=mingw32 RUNTIME_ARCH=x86_64 RUNTIME_OS=mingw32; do
  grep -Fx "$expected" <<< "$info"
done
grep -q '^BUILD_TOOL=git version ' <<< "$info"
[[ $(run_report small) == 55 ]]
[[ $(run_report century) == 9261915075 ]]
[[ $(run_report large) == 3428746875 ]]
[[ $(run_report --list) == $'century\nlarge\nsmall' ]]
grep -Fx "BUILD_MACHINE_ARCH=$BUILD_ARCH" "$CROSS_ROOT/report-build.log"
if run_report unknown > "$CROSS_ROOT/report-error.txt" 2>&1; then
  echo "Unknown catalogue label unexpectedly succeeded" >&2; exit 1
fi
tr -d '\r' < "$CROSS_ROOT/report-error.txt" | grep -Fx 'Unknown catalogue entry: unknown'
"${WINE_BIN%/*}/wineserver" -k 2>/dev/null || wineserver -k 2>/dev/null || true
echo "Cross compilation checks passed."
""")

# ---- 9. negative splice + single-compiler identity ------------------------
md("win-negative-md", r"""
## 9. Negative splice & single-compiler identity

A bad catalogue must fail compilation (`expected LABEL INDEX (0..100000)`) because the `addDependentFile` splice is revisited; the good catalogue is then restored and rebuilt.
`large` still reports `3428746875` after the rebuild.
`x86_64-w64-mingw32-ghc` is the same binary as `ghc`; identity reported as `ONE_GHC_SHA256`.
Wineserver is stopped after each Wine run to leave no server behind.
""")

code("win-negative", r"""
%%bash
set -euo pipefail
ROOT=$(cat "$HOME/.cross-ghc-root-windows")
source "$ROOT/gate-windows.env"
cross_enter
export WINEPREFIX="$CROSS_ROOT/wineprefix"
export WINEDLLOVERRIDES="mscoree,mshtml=;winemac.drv=d"
export WINEDEBUG="-all"
export MVK_CONFIG_LOG_LEVEL=0

cd "$FIBO"
cp catalogue.txt "$CROSS_ROOT/catalogue.txt.good"
trap 'cp "$CROSS_ROOT/catalogue.txt.good" catalogue.txt; "${WINE_BIN%/*}/wineserver" -k 2>/dev/null || wineserver -k 2>/dev/null || true' EXIT
echo 'invalid row contents' > catalogue.txt
sleep 1
touch catalogue.txt
if PATH="$DIST/bin:$PATH" "$CABAL" build -j"$BUILD_JOBS" \
  -w x86_64-w64-mingw32-ghc -W ghc \
  --with-hc-pkg="$DIST/bin/x86_64-w64-mingw32-ghc-pkg" \
  --with-build-hc-pkg="$DIST/bin/ghc-pkg" \
  exe:cross-report \
  --ghc-option=-fexternal-interpreter \
  --ghc-option=-pgmi$CROSS_ROOT/bin/windows-runner > "$CROSS_ROOT/negative-build.log" 2>&1; then
  echo "Invalid catalogue unexpectedly built" >&2; exit 1
fi
grep -q 'expected LABEL INDEX (0..100000)' "$CROSS_ROOT/negative-build.log"
echo "Invalid catalogue rejected during TH compilation."
cp "$CROSS_ROOT/catalogue.txt.good" catalogue.txt
sleep 1
touch catalogue.txt
trap ' "${WINE_BIN%/*}/wineserver" -k 2>/dev/null || wineserver -k 2>/dev/null || true' EXIT
PATH="$DIST/bin:$PATH" "$CABAL" build -j"$BUILD_JOBS" \
  -w x86_64-w64-mingw32-ghc -W ghc \
  --with-hc-pkg="$DIST/bin/x86_64-w64-mingw32-ghc-pkg" \
  --with-build-hc-pkg="$DIST/bin/ghc-pkg" \
  exe:cross-report \
  --ghc-option=-fexternal-interpreter \
  --ghc-option=-pgmi$CROSS_ROOT/bin/windows-runner
echo "Restored catalogue rebuilt."
bin=$(cat "$CROSS_ROOT/report-bin.path")
[[ $("$WINE_BIN" "$bin" large </dev/null | tr -d '\r') == 3428746875 ]]
"${WINE_BIN%/*}/wineserver" -k 2>/dev/null || wineserver -k 2>/dev/null || true

# One compiler throughout: the target name selects settings, not a new binary.
NATIVE_SHA=$(python3 -c "import hashlib;print(hashlib.file_digest(open('$DIST/bin/ghc','rb'),'sha256').hexdigest())")
TARGET_SHA=$(python3 -c "import hashlib;print(hashlib.file_digest(open('$DIST/bin/x86_64-w64-mingw32-ghc','rb'),'sha256').hexdigest())")
[[ "$NATIVE_SHA" == "$TARGET_SHA" ]]
echo "ONE_GHC_SHA256=$NATIVE_SHA"
echo "All Windows cross-compilation checks passed."
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

out = sys.argv[1] if len(sys.argv) > 1 else "Windows TH Cross.ipynb"
with open(out, "w") as f:
    json.dump(nb, f, indent=1, ensure_ascii=False)
    f.write("\n")
print(f"wrote {out} with {len(nb_cells)} cells")







