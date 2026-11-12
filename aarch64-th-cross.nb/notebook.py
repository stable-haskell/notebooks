#!/usr/bin/env python3
"""Generator for "AArch64 TH Cross.ipynb" (python3 kernel, nbformat 4.5).

The notebook is generated from this script so edits are reproducible and the
serialized form matches tools/strip-ipynb.py (clean cells, no outputs). Run via:

  python3 aarch64-th-cross.nb/notebook.py "aarch64-th-cross.nb/AArch64 TH Cross.ipynb"

then verify with `python3 tools/strip-ipynb.py` (must be a no-op) and
`make check`.

Standalone AArch64-target walkthrough adapted from aarch64-th-cross.ipynb
(cells 0-28 and 46-47): source-build GHC 9.14 + cross-aware Cabal, target
libraries for aarch64 Linux, an aarch64 interpreter under a target runner,
and a Template Haskell report application. Unlike the Debian/x86_64-only
reference, every cell works on x86_64 Linux, aarch64 Linux, and Apple Silicon
macOS: the sibling flake provides the musl cross-toolchain, target libffi,
and the ELF runner (QEMU on Linux, `hl` / rt-series translators on macOS),
with an apt + .deb sysroot fallback on Debian/Ubuntu Linux.
"""
import json
import sys

cells = []


def md(cid, text):
    cells.append(("markdown", cid, text))


def code(cid, text):
    # python3 kernel: every executable cell is a %%bash cell so a nonzero
    # exit fails the cell, as in the reference notebook.
    cells.append(("code", cid, "%%bash\n" + text))


# ---- 0. title -------------------------------------------------------------
md("cross-00", r"""
# Cross Compiling Haskell with GHC 9.14 and Cabal

Build an aarch64 Linux report app from this machine (x86_64/aarch64 Linux or Apple Silicon macOS). Native `Setup.hs` runs on the build machine; splice code runs in an aarch64 interpreter under a target runner; the final binary is aarch64 Linux.

Two mechanisms do the work:

1. **One GHC, two targets.** `aarch64-unknown-linux-gnu-ghc` is a symlink to native `ghc`; its name selects `lib/targets/aarch64-unknown-linux-gnu/lib/{settings,package.conf.d}`.
2. **Dual-compiler Cabal.** `-w` picks the target compiler (`host:*` libraries and app); `-W` picks the native compiler (`build:*`) for `Setup.hs` and code generators.

| Part | Compiled for | Runs where |
|---|---|---|
| GHC & Cabal | build machine | build machine |
| `Setup.hs` & tools | build machine | build machine |
| Splice code & `iserv` | aarch64 Linux | target context on build machine |
| Report app & libraries | aarch64 Linux | target context; aarch64 Linux when deployed |

Cabal calls the app machine `host`; GHC calls it `target`.
Prerequisites: fresh absolute `CROSS_ROOT`; `GHC_REF`/`CABAL_REF` default to branch heads (pinned in `sources.lock`).
Run via `nix develop` in `aarch64-th-cross.nb/` for the musl toolchain, target libffi, and runner.
Run cells in order; config under `CROSS_ROOT` survives kernel restarts. Every executable cell is `%%bash` (Python 3 kernel); a nonzero exit fails the cell.
""")

# ---- 1. build root --------------------------------------------------------
md("cross-01", r"""
## A place to build

Save shared paths to `$CROSS_ROOT/gate-aarch64.env` (`DIST`, `GHC_SRC`, `CABAL_SRC`, `STAGE3`, `FIBO`, `TRIPLE`, `TARGET_CC`).
Native GHC sources (`ghc-src`) and target library sources (`ghc-target-src`) are separate trees; the stage 3 project lives at `stage3-aarch64`.
Detect the build platform (`BUILD_ARCH`/`BUILD_OS`) and pick the target triple plus cross `CC`/`CXX` here.
""")

code("cross-02", r"""
set -euo pipefail
export PATH="$HOME/.nix-profile/bin:$HOME/.local/state/nix/profiles/profile/bin:$PATH:/usr/sbin:/sbin"
fail() { echo "$*" >&2; exit 1; }
UNAME_S=$(uname -s)
UNAME_M=$(uname -m)
case "$UNAME_S-$UNAME_M" in
  Linux-x86_64)   BUILD_ARCH=x86_64;  BUILD_OS=linux;  BUILD_VENDOR=unknown ;;
  Linux-aarch64)  BUILD_ARCH=aarch64; BUILD_OS=linux;  BUILD_VENDOR=unknown ;;
  Darwin-arm64|Darwin-aarch64) BUILD_ARCH=aarch64; BUILD_OS=darwin; BUILD_VENDOR=apple ;;
  *) fail "Unsupported build platform: $UNAME_S-$UNAME_M (need x86_64/aarch64 Linux or Apple Silicon macOS)." ;;
esac
BUILD_PLATFORM="${BUILD_ARCH}-${BUILD_VENDOR}-${BUILD_OS}"

if [[ "$BUILD_OS" == linux ]]; then
  if [[ -f /etc/os-release ]]; then
    source /etc/os-release
  fi
  glibc=$(getconf GNU_LIBC_VERSION 2>/dev/null | awk '{print $2}')
  glibc=${glibc:-unknown}
  if [[ $(id -u) == 0 ]]; then SUDO=; else SUDO=sudo; fi
  command -v sudo >/dev/null 2>&1 || SUDO=
  sysinfo="${PRETTY_NAME:-Linux} (glibc $glibc)"
else
  SUDO=
  sysinfo="$UNAME_S $UNAME_M"
fi

# Select the AArch64 Linux target triple. Prefer the Nix cross-toolchain
# (AARCH64_CC, or aarch64-unknown-linux-musl-gcc from the rt-series flake);
# otherwise use the Debian/Ubuntu cross gcc on Linux.
if [[ -n "${AARCH64_CC:-}" ]] && [[ -x "$AARCH64_CC" ]]; then
  TARGET_CC="$AARCH64_CC"
  TARGET_CXX="${AARCH64_CXX:-${AARCH64_CC%gcc}g++}"
  TARGET_PREFIX="${AARCH64_TARGET_PREFIX:-aarch64-unknown-linux-musl-}"
  TRIPLE=aarch64-unknown-linux-musl
elif command -v aarch64-unknown-linux-musl-gcc >/dev/null 2>&1; then
  TARGET_CC=$(command -v aarch64-unknown-linux-musl-gcc)
  if command -v aarch64-unknown-linux-musl-g++ >/dev/null 2>&1; then
    TARGET_CXX=$(command -v aarch64-unknown-linux-musl-g++)
  else
    TARGET_CXX="${AARCH64_CXX:-aarch64-unknown-linux-musl-g++}"
  fi
  TARGET_PREFIX="${AARCH64_TARGET_PREFIX:-aarch64-unknown-linux-musl-}"
  TRIPLE=aarch64-unknown-linux-musl
elif [[ "$BUILD_OS" == linux ]] && command -v aarch64-linux-gnu-gcc >/dev/null 2>&1; then
  TRIPLE=aarch64-unknown-linux-gnu
  TARGET_PREFIX=aarch64-linux-gnu-
  TARGET_CC=$(command -v aarch64-linux-gnu-gcc)
  TARGET_CXX=$(command -v aarch64-linux-gnu-g++)
else
  fail "No AArch64 cross compiler found. Enter 'nix develop' in aarch64-th-cross.nb/ (provides \$AARCH64_CC), or install gcc-aarch64-linux-gnu on Debian/Ubuntu Linux."
fi

CROSS_ROOT=${CROSS_ROOT:-$HOME/cross-ghc-current}
[[ "$CROSS_ROOT" == /* && "$CROSS_ROOT" != *[[:space:]]* ]] || fail "CROSS_ROOT must be absolute and contain no whitespace."
mkdir -p "$CROSS_ROOT"
CROSS_ROOT=$(cd "$CROSS_ROOT" && pwd -P)
# Fresh checkouts need room for a full source build; cached trees and CI
# runners only need headroom for the application rebuild.
if [[ -z "${MIN_FREE_GB:-}" ]]; then
  if [[ "${CI:-false}" == true ]] || [[ -x "$CROSS_ROOT/ghc-src/_build/dist/bin/ghc" ]]; then MIN_FREE_GB=4; else MIN_FREE_GB=32; fi
fi
avail=$(df -Pk "$CROSS_ROOT" | awk 'NR==2 {print $4}')
(( avail >= MIN_FREE_GB*1024*1024 )) || fail "Allow at least ${MIN_FREE_GB} GiB free under $CROSS_ROOT."
if [[ "$BUILD_OS" == linux ]]; then
  mem=$(awk '/MemTotal/ {print $2}' /proc/meminfo)
else
  mem=$(( $(sysctl -n hw.memsize) / 1024 ))
fi
if (( mem < 16*1024*1024 )); then
  echo "Less than 16 GiB RAM: the full compiler build may need more memory or swap." >&2
fi
printf '%s\n' "$CROSS_ROOT" > "$HOME/.cross-ghc-aarch64-root"
printf '%s\n' "$CROSS_ROOT" > "$HOME/.cross-ghc-root"
{
  printf 'CROSS_ROOT=%q\n' "$CROSS_ROOT"
  printf 'DIST=%q\n' "$CROSS_ROOT/ghc-src/_build/dist"
  printf 'GHC_SRC=%q\n' "$CROSS_ROOT/ghc-src"
  printf 'CABAL_SRC=%q\n' "$CROSS_ROOT/cabal-src"
  printf 'BOOT_GHC=%q\n' "${BOOT_GHC:-ghc-9.8.4}"
  printf 'BOOT_CABAL=%q\n' "${BOOT_CABAL:-cabal}"
  printf 'MAKE_BIN=%q\n' "${MAKE_BIN:-make}"
  printf 'SRC=%q\n' "$CROSS_ROOT/ghc-target-src"
  printf 'CABAL=%q\n' "$CROSS_ROOT/cabal-dist/cabal"
  printf 'TOOLS=%q\n' "$CROSS_ROOT/tool-build"
  printf 'STAGE3=%q\n' "$CROSS_ROOT/stage3-aarch64"
  printf 'FIBO=%q\n' "$CROSS_ROOT/cross-report"
  printf 'BUILD_JOBS=%q\n' "${BUILD_JOBS:-4}"
  printf 'GHC_JOBS=%q\n' "${GHC_JOBS:-2}"
  printf 'BUILD_ARCH=%q\n' "$BUILD_ARCH"
  printf 'BUILD_OS=%q\n' "$BUILD_OS"
  printf 'BUILD_VENDOR=%q\n' "$BUILD_VENDOR"
  printf 'BUILD_PLATFORM=%q\n' "$BUILD_PLATFORM"
  printf 'TRIPLE=%q\n' "$TRIPLE"
  printf 'TARGET_PREFIX=%q\n' "$TARGET_PREFIX"
  printf 'TARGET_CC=%q\n' "$TARGET_CC"
  printf 'TARGET_CXX=%q\n' "$TARGET_CXX"
  printf 'SUDO=%q\n' "$SUDO"
  printf 'AARCH64_SYSROOT=%q\n' "${AARCH64_SYSROOT:-}"
  printf 'AARCH64_LIBFFI_INCLUDE=%q\n' "${AARCH64_LIBFFI_INCLUDE:-}"
  printf 'AARCH64_LIBFFI_LIB=%q\n' "${AARCH64_LIBFFI_LIB:-}"
  printf 'AARCH64_NM=%q\n' "${AARCH64_NM:-}"
  printf 'AARCH64_OBJDUMP=%q\n' "${AARCH64_OBJDUMP:-}"
  printf 'AARCH64_READELF=%q\n' "${AARCH64_READELF:-}"
  printf 'AARCH64_LINUX_RUNNER=%q\n' "${AARCH64_LINUX_RUNNER:-}"
  printf 'AARCH64_LINUX_VM=%q\n' "${AARCH64_LINUX_VM:-}"
  printf 'export CABAL_DIR=%q\n' "$CROSS_ROOT/cabal-home"
  cat <<'EOF'
cross_enter() {
  set -euo pipefail
  cd "$CROSS_ROOT"
  unset CC CXX AR RANLIB
  export PATH="$HOME/.nix-profile/bin:$HOME/.local/state/nix/profiles/profile/bin:$PATH:/usr/sbin:/sbin"
  if [[ -d "$HOME/.ghcup/bin" && ":$PATH:" != *":$HOME/.ghcup/bin:"* ]]; then
    export PATH="$HOME/.ghcup/bin:$PATH"
  fi
}
EOF
} > "$CROSS_ROOT/gate-aarch64.env"
cp "$CROSS_ROOT/gate-aarch64.env" "$CROSS_ROOT/gate.env"
printf 'Build tree: %s\nSystem: %s; RAM %s MiB\n' "$CROSS_ROOT" "$sysinfo" "$((mem/1024))"
printf 'Build platform: %s; target triple: %s (%s)\n' "$BUILD_PLATFORM" "$TRIPLE" "$TARGET_CC"
""")

# ---- 2. C toolchain -----------------------------------------------------
md("cross-03", r"""
## The C side

Compile and run an aarch64 Linux C program first: cross `aarch64-unknown-linux-{musl,gnu}-gcc`, a target libc sysroot, and `aarch64-linux-run` to execute it.
The runner is `qemu-aarch64` on Linux, `hl` (or Lima `AARCH64_LINUX_VM`) on macOS, and direct exec on aarch64 Linux.
Inside the flake `$TARGET_CC` plus `$AARCH64_LIBFFI_INCLUDE`/`$AARCH64_LIBFFI_LIB` are preset; outside it, Debian/Ubuntu falls back to `apt-get` plus pinned `libffi` `.deb`s.
Target libs use `bignum-native` with system libffi; native `libgmp-dev` serves only the native compiler. The smoke test must print `qemu-and-libffi-ok`.
""")

code("cross-04", r"""
set -euo pipefail
ROOT=$(cat "$HOME/.cross-ghc-aarch64-root" 2>/dev/null || cat "$HOME/.cross-ghc-root")
if [[ -f "$ROOT/gate-aarch64.env" ]]; then source "$ROOT/gate-aarch64.env"; else source "$ROOT/gate.env"; fi
cross_enter

# Inside the flake the cross-toolchain and target libffi are already present;
# skip the system setup in that case.
if command -v "$TARGET_CC" >/dev/null 2>&1 \
  && [[ -n "${AARCH64_LIBFFI_LIB:-}" && -d "$AARCH64_LIBFFI_LIB" ]] \
  && [[ -n "${AARCH64_LIBFFI_INCLUDE:-}" && -f "$AARCH64_LIBFFI_INCLUDE/ffi.h" ]]; then
  echo "Nix cross-toolchain present: $TARGET_CC"
  echo "libffi headers: $AARCH64_LIBFFI_INCLUDE; libs: $AARCH64_LIBFFI_LIB"
  "$TARGET_CC" --version | head -1
  command -v "${TARGET_PREFIX}gcc"
else
  # Debian/Ubuntu fallback: install the cross toolchain and QEMU with apt.
  [[ "$BUILD_OS" == linux ]] || fail "Without the flake toolchain, this walkthrough needs Debian/Ubuntu Linux. Enter 'nix develop' in aarch64-th-cross.nb/ instead."
  source /etc/os-release
  case "${ID:-}:${VERSION_ID:-}" in
    debian:*|ubuntu:*) ;;
    *) fail "Without the flake toolchain, use Debian or Ubuntu for the apt-get sysroot instructions below." ;;
  esac
  if [[ $(id -u) == 0 ]]; then SUDO=; else SUDO=sudo; fi
  $SUDO apt-get update
  $SUDO env DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    build-essential curl ca-certificates git autoconf automake python3 pkg-config \
    file patchelf xz-utils zstd gcc-aarch64-linux-gnu g++-aarch64-linux-gnu libc6-dev-arm64-cross \
    qemu-user-static qemu-user libffi-dev zlib1g-dev libncurses-dev libgmp-dev dpkg-dev
  # The apt fallback only provides the gnu toolchain: re-point a musl
  # selection at it so later cells use the compiler that is actually here.
  if ! command -v "$TARGET_CC" >/dev/null 2>&1 && command -v aarch64-linux-gnu-gcc >/dev/null 2>&1; then
    TRIPLE=aarch64-unknown-linux-gnu
    TARGET_PREFIX=aarch64-linux-gnu-
    TARGET_CC=$(command -v aarch64-linux-gnu-gcc)
    TARGET_CXX=$(command -v aarch64-linux-gnu-g++)
    python3 - "$CROSS_ROOT/gate-aarch64.env" "$TRIPLE" "$TARGET_PREFIX" "$TARGET_CC" "$TARGET_CXX" <<'PY_TRIPLE'
import sys
from pathlib import Path
p, triple, prefix, cc, cxx = Path(sys.argv[1]), sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5]
lines = []
for line in p.read_text().splitlines():
    if line.startswith(('TRIPLE=', 'TARGET_PREFIX=', 'TARGET_CC=', 'TARGET_CXX=')):
        continue
    lines.append(line)
lines += [f'TRIPLE={triple}', f'TARGET_PREFIX={prefix}', f'TARGET_CC={cc}', f'TARGET_CXX={cxx}']
p.write_text('\n'.join(lines) + '\n')
print(f'Re-pointed at available toolchain: {triple}')
PY_TRIPLE
    cp "$CROSS_ROOT/gate-aarch64.env" "$CROSS_ROOT/gate.env"
  fi
  command -v "$TARGET_CC"
  command -v qemu-aarch64-static || command -v qemu-aarch64
fi
""")

code("cross-05", r"""
set -euo pipefail
ROOT=$(cat "$HOME/.cross-ghc-aarch64-root" 2>/dev/null || cat "$HOME/.cross-ghc-root")
# shellcheck disable=SC1091
if [[ -f "$ROOT/gate-aarch64.env" ]]; then source "$ROOT/gate-aarch64.env"; else source "$ROOT/gate.env"; fi
cross_enter

# Target libffi: skip when the flake already provides it; otherwise unpack the
# two pinned Debian arm64 packages into the cross sysroot.
if [[ -n "${AARCH64_LIBFFI_LIB:-}" && -d "$AARCH64_LIBFFI_LIB" ]] \
  && [[ -n "${AARCH64_LIBFFI_INCLUDE:-}" && -f "$AARCH64_LIBFFI_INCLUDE/ffi.h" ]]; then
  echo "Using target libffi from the flake: $AARCH64_LIBFFI_LIB"
else
  [[ "$BUILD_OS" == linux ]] || fail "Target libffi needs the flake on this platform. Enter 'nix develop' in aarch64-th-cross.nb/."
  debdir="$CROSS_ROOT/debs"
  mkdir -p "$debdir"
  curl -fL --retry 3 -o "$debdir/libffi8.deb" \
    https://deb.debian.org/debian/pool/main/libf/libffi/libffi8_3.4.4-1_arm64.deb
  curl -fL --retry 3 -o "$debdir/libffi-dev.deb" \
    https://deb.debian.org/debian/pool/main/libf/libffi/libffi-dev_3.4.4-1_arm64.deb
  if command -v sha256sum >/dev/null 2>&1; then
    echo "80b5c36177dc0e29d531c7eddbed3cc7355cb490e49f8cfa5959572d161f27b3  $debdir/libffi8.deb" | sha256sum -c -
    echo "8ff6335f76752ef8659762433d0d88129ef0090a587520853238809bd10ef392  $debdir/libffi-dev.deb" | sha256sum -c -
  else
    echo "80b5c36177dc0e29d531c7eddbed3cc7355cb490e49f8cfa5959572d161f27b3  $debdir/libffi8.deb" | shasum -a 256 -c -
    echo "8ff6335f76752ef8659762433d0d88129ef0090a587520853238809bd10ef392  $debdir/libffi-dev.deb" | shasum -a 256 -c -
  fi
  root="$CROSS_ROOT/ffi-root"
  mkdir -p "$root"
  dpkg-deb -x "$debdir/libffi8.deb" "$root"
  dpkg-deb -x "$debdir/libffi-dev.deb" "$root"
  if [[ $(id -u) == 0 ]]; then SUDO=; else SUDO=sudo; fi
  $SUDO mkdir -p /usr/aarch64-linux-gnu/lib /usr/aarch64-linux-gnu/include /usr/lib/aarch64-linux-gnu
  # Libraries. Copy the real objects, then recreate the soname links locally so we
  # do not depend on the deb's relative links surviving the copy.
  find "$root" -name 'libffi.so*' -type f -exec $SUDO cp -a {} /usr/aarch64-linux-gnu/lib/ \;
  find "$root" -name 'libffi.so*' -type f -exec $SUDO cp -a {} /usr/lib/aarch64-linux-gnu/ \;
  $SUDO ln -sfn libffi.so.8.1.2 /usr/aarch64-linux-gnu/lib/libffi.so.8
  $SUDO ln -sfn libffi.so.8 /usr/aarch64-linux-gnu/lib/libffi.so
  $SUDO ln -sfn libffi.so.8.1.2 /usr/lib/aarch64-linux-gnu/libffi.so.8
  $SUDO ln -sfn libffi.so.8 /usr/lib/aarch64-linux-gnu/libffi.so
  # Headers. The cross gcc searches <sysroot>/usr/include and <triplet>/include.
  find "$root" -name 'ffi.h' -o -name 'ffitarget.h' | while read -r h; do
    $SUDO cp -a "$h" /usr/aarch64-linux-gnu/include/
  done
  ls -l /usr/aarch64-linux-gnu/lib/libffi.so /usr/aarch64-linux-gnu/include/ffi.h
fi

# Target runner helper: one command that executes an AArch64 Linux binary on
# this machine, whatever the build platform is.
mkdir -p "$CROSS_ROOT/bin"
cat > "$CROSS_ROOT/bin/aarch64-linux-run" <<'RUNNER_EOF'
#!/bin/bash
# Execute an AArch64 Linux binary from the build machine.
set -euo pipefail
export PATH="$HOME/.nix-profile/bin:$HOME/.local/state/nix/profiles/profile/bin:/nix/var/nix/profiles/default/bin:$PATH:/usr/sbin:/sbin"
ROOT=$(cat "$HOME/.cross-ghc-aarch64-root" 2>/dev/null || cat "$HOME/.cross-ghc-root")
# shellcheck disable=SC1091
if [[ -f "$ROOT/gate-aarch64.env" ]]; then source "$ROOT/gate-aarch64.env"; else source "$ROOT/gate.env"; fi
if [[ -n "${AARCH64_LINUX_RUNNER:-}" ]]; then
  # Explicit override, e.g. a docker invocation on a macOS CI VM.
  # shellcheck disable=SC2086
  exec ${AARCH64_LINUX_RUNNER} "$@"
fi
case "$(uname -s)-$(uname -m)" in
  Darwin-arm64|Darwin-aarch64)
    if command -v hl >/dev/null 2>&1 && [[ "$(sysctl -n kern.hv_support 2>/dev/null || echo 0)" == "1" ]]; then
      if [[ -n "${AARCH64_SYSROOT:-}" ]] && file "$1" 2>/dev/null | grep -q 'dynamically linked'; then
        exec hl --fs-mode=legacy --audio-backend null --sysroot "$AARCH64_SYSROOT" "$@"
      else
        exec hl --fs-mode=legacy --audio-backend null "$@"
      fi
    fi
    # No HVF entitlement (e.g. CI VMs): run inside a Linux VM instead.
    if [[ -n "${AARCH64_LINUX_VM:-}" ]] && command -v limactl >/dev/null 2>&1; then
      if [[ -n "${AARCH64_SYSROOT:-}" ]] && ! limactl shell "$AARCH64_LINUX_VM" -- test -d "$AARCH64_SYSROOT" </dev/null >/dev/null 2>&1; then
        tar -C / -cf - "${AARCH64_SYSROOT#/}" ${AARCH64_LIBFFI_LIB:+"${AARCH64_LIBFFI_LIB#/}"} \
          | limactl shell "$AARCH64_LINUX_VM" -- sudo tar -C / -xf - >&2
      fi
      vm_ld="${AARCH64_LIBFFI_LIB:-}${AARCH64_SYSROOT:+:$AARCH64_SYSROOT/lib}"
      if [[ " $* " == *" --stdio "* ]]; then
        exec limactl shell --workdir "$PWD" "$AARCH64_LINUX_VM" -- env LD_LIBRARY_PATH="${vm_ld#:}" "$@"
      else
        exec limactl shell --workdir "$PWD" "$AARCH64_LINUX_VM" -- env LD_LIBRARY_PATH="${vm_ld#:}" "$@" </dev/null
      fi
    fi
    echo "No AArch64 Linux runner: 'hl' is missing and AARCH64_LINUX_VM is unset." >&2
    echo "On Apple Silicon with HVF, enter the flake dev shell (provides hl);" >&2
    echo "on a VM without HVF, point AARCH64_LINUX_VM at a lima Linux instance" >&2
    echo "or set AARCH64_LINUX_RUNNER (e.g. a docker run invocation)." >&2
    exit 1
    ;;
  Linux-aarch64|Linux-arm64)
    if [[ -n "${AARCH64_LIBFFI_LIB:-}" ]]; then
      export LD_LIBRARY_PATH="${AARCH64_LIBFFI_LIB}${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
    fi
    exec "$@"
    ;;
  *)
    qemu=""
    for q in qemu-aarch64-static qemu-aarch64; do
      if command -v "$q" >/dev/null 2>&1; then qemu=$(command -v "$q"); break; fi
    done
    if [[ -z "$qemu" ]]; then
      echo "No qemu-aarch64 found. Enter the flake dev shell or install qemu-user." >&2
      exit 1
    fi
    sysroot="${AARCH64_SYSROOT:-/usr/aarch64-linux-gnu}"
    if [[ -d "$sysroot" ]]; then exec "$qemu" -L "$sysroot" "$@"; else exec "$qemu" "$@"; fi
    ;;
esac
RUNNER_EOF
chmod +x "$CROSS_ROOT/bin/aarch64-linux-run"

cat > "$CROSS_ROOT/hello.c" << 'EOF'
#include <stdio.h>
#include <ffi.h>
int main(void) {
  ffi_cif cif;
  if (ffi_prep_cif(&cif, FFI_DEFAULT_ABI, 0, &ffi_type_void, NULL) != FFI_OK) return 1;
  puts("qemu-and-libffi-ok");
  return 0;
}
EOF
FFI_CFLAGS=""
FFI_LDFLAGS=""
STATIC_FLAG=""
if [[ -n "${AARCH64_LIBFFI_INCLUDE:-}" ]]; then FFI_CFLAGS="-I$AARCH64_LIBFFI_INCLUDE"; fi
if [[ -n "${AARCH64_LIBFFI_LIB:-}" ]]; then FFI_LDFLAGS="-L$AARCH64_LIBFFI_LIB"; fi
if [[ "$TRIPLE" == *-musl* ]] && [[ -n "${AARCH64_LIBFFI_LIB:-}" ]] && [[ -f "$AARCH64_LIBFFI_LIB/libffi.a" ]]; then
  STATIC_FLAG="-static"
fi
# shellcheck disable=SC2086
"$TARGET_CC" -o "$CROSS_ROOT/hello" "$CROSS_ROOT/hello.c" $FFI_CFLAGS -lffi $FFI_LDFLAGS $STATIC_FLAG
out=$("$CROSS_ROOT/bin/aarch64-linux-run" "$CROSS_ROOT/hello")
echo "target said: $out"
[[ "$out" == "qemu-and-libffi-ok" ]]
echo "target smoke test ok"
""")

md("cross-06", r"""
## Fetching the two branches

Fetch `stable-haskell/ghc:stable-ghc-9.14` (stage 2 native GHC) and `andreabedini/cabal:andrea/feature/cross-compilation-2` (cross-aware Cabal); pin heads in `sources.lock`.
Build tutorial Cabal with bootstrap GHC 9.8.4/Cabal, then stage 2 GHC with its own `cabal.project.stage0` builder — never Andrea's Cabal for the compiler itself.
`GHC_REF`/`CABAL_REF` select other refs for reproduction; use a fresh `CROSS_ROOT` when they change.
With `allow-boot-library-installs: True`, the local `Dependency.hs` patch lets only *missing* wired-in units build from an empty target DB; installed units keep exact-unit pins.
""")

code("cross-07", r"""
set -euo pipefail
ROOT=$(cat "$HOME/.cross-ghc-aarch64-root" 2>/dev/null || cat "$HOME/.cross-ghc-root")
if [[ -f "$ROOT/gate-aarch64.env" ]]; then source "$ROOT/gate-aarch64.env"; else source "$ROOT/gate.env"; fi
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
  if ! head -n 2 "$CROSS_ROOT/build-sources.lock" | cmp -s "$CROSS_ROOT/sources.lock" -; then
    echo "Sources changed. Start with a fresh CROSS_ROOT for this toolchain." >&2; exit 1;
  fi
fi
""")

code("cross-08", r'''
set -euo pipefail
ROOT=$(cat "$HOME/.cross-ghc-aarch64-root" 2>/dev/null || cat "$HOME/.cross-ghc-root")
if [[ -f "$ROOT/gate-aarch64.env" ]]; then source "$ROOT/gate-aarch64.env"; else source "$ROOT/gate.env"; fi
cross_enter

sha256_file() {
  if command -v sha256sum >/dev/null 2>&1; then sha256sum "$1" | awk '{print $1}';
  elif command -v shasum >/dev/null 2>&1; then shasum -a 256 "$1" | awk '{print $1}';
  else echo "Need sha256sum or shasum for checksums." >&2; return 1; fi
}

BOOT_GHC=$(command -v "$BOOT_GHC")
BOOT_CABAL=$(command -v "$BOOT_CABAL")
MAKE_BIN=$(command -v "$MAKE_BIN")
[[ $("$BOOT_GHC" --numeric-version) == 9.8.4 ]]
make_version=$("$MAKE_BIN" --version | awk 'NR==1 {print $3}')
python3 - "$make_version" <<'PY_MAKE'
import sys
found = tuple(int(p) for p in sys.argv[1].split('.')[:2])
assert found >= (4, 4), f"Need GNU Make >= 4.4, found {sys.argv[1]}"
print(f"GNU Make {sys.argv[1]} ok")
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
new = """      | (pkgName, unitId) <- wiredInUnitIds
      -- Explicit target bootstrap: do not require a missing installed unit.
      , not (asBool (depResolverAllowBootLibInstalls params))
          || isJust (InstalledPackageIndex.lookupUnitId
               (onMachine (depResolverInstalledPkgIndex params) Host) unitId)
      ]"""
if old in s:
    p.write_text(s.replace(old, new, 1))
else:
    assert 'onMachine (depResolverInstalledPkgIndex params) Host) unitId)' in s, 'Review the Cabal boot patch against this revision'
PY_CABAL_BOOT
  git -C "$CABAL_SRC" diff -- cabal-install/src/Distribution/Client/Dependency.hs > "$CROSS_ROOT/cabal-boot.patch"
  sha256_file "$CROSS_ROOT/cabal-boot.patch" >> "$CROSS_ROOT/build-sources.lock"
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
ONE_GHC_SHA256=$(sha256_file "$DIST/bin/ghc")
python3 - "$CROSS_ROOT/gate-aarch64.env" "$ONE_GHC_SHA256" <<'PY_GHC_HASH'
import sys
from pathlib import Path
p = Path(sys.argv[1])
lines = [l for l in p.read_text().splitlines() if not l.startswith('ONE_GHC_SHA256=')]
lines.append(f'ONE_GHC_SHA256={sys.argv[2]}')
p.write_text('\n'.join(lines) + '\n')
PY_GHC_HASH
cp "$CROSS_ROOT/gate-aarch64.env" "$CROSS_ROOT/gate.env"
echo "ONE_GHC_SHA256=$ONE_GHC_SHA256"
''')

# ---- 3. target tree + toolchain -----------------------------------------
md("cross-09", r"""
## One compiler, and a separate target tree

Check out the same GHC commit as a worktree at `ghc-target-src` for target libraries; apply the `rts/rts.cabal` edit here only (vanilla `v` way stays buildable).
Supply the missing `ghc-boot-th-next` configure input locally; it is not part of the target build.
Native `DIST` stays untouched: `ghc`, `ghc-toolchain-bin`, `deriveConstants`, and `genapply` there are the inputs for what follows.
""")

code("cross-10", r"""
set -euo pipefail
ROOT=$(cat "$HOME/.cross-ghc-aarch64-root" 2>/dev/null || cat "$HOME/.cross-ghc-root")
# shellcheck disable=SC1091
if [[ -f "$ROOT/gate-aarch64.env" ]]; then source "$ROOT/gate-aarch64.env"; else source "$ROOT/gate.env"; fi
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
  rm -f ghc-boot-th-next.cabal.in.bak ghc-boot-th.cabal
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

md("cross-11", r"""
## Native tools for target configuration

Record `$DIST/bin/{deriveConstants,genapply,ghc-toolchain-bin}` paths for the target build.
`deriveConstants` inspects target objects via `$TARGET_PREFIX{nm,objdump}`; it reads but never executes them.
""")

code("cross-12", r"""
set -euo pipefail
ROOT=$(cat "$HOME/.cross-ghc-aarch64-root" 2>/dev/null || cat "$HOME/.cross-ghc-root")
if [[ -f "$ROOT/gate-aarch64.env" ]]; then source "$ROOT/gate-aarch64.env"; else source "$ROOT/gate.env"; fi
cross_enter

for name in deriveConstants genapply ghc-toolchain-bin; do
  [[ -x "$DIST/bin/$name" ]]
  printf '%s\n' "$DIST/bin/$name" > "$CROSS_ROOT/$name.path"
done
""")

md("cross-13", r"""
## Teaching GHC about aarch64 Linux

Run `ghc-toolchain-bin --triple $TRIPLE --target-prefix $TARGET_PREFIX --cc $TARGET_CC --cxx $TARGET_CXX` from the source tree to write `lib/targets/$TRIPLE/lib/settings`.
The `$TRIPLE` directory name is the lookup key for both `$TRIPLE-ghc` and `ghc -target=$TRIPLE`; pass the tool prefix explicitly so the aarch64 linker is found.
Symlink `$TRIPLE-ghc`/`$TRIPLE-ghc-pkg` to the native binaries, `init` the target `package.conf.d`, and force static/unprofiled target properties (`NO`).
""")

code("cross-14", r"""
set -euo pipefail
ROOT=$(cat "$HOME/.cross-ghc-aarch64-root" 2>/dev/null || cat "$HOME/.cross-ghc-root")
# shellcheck disable=SC1091
if [[ -f "$ROOT/gate-aarch64.env" ]]; then source "$ROOT/gate-aarch64.env"; else source "$ROOT/gate.env"; fi
cross_enter
toolbin=$(cat "$CROSS_ROOT/ghc-toolchain-bin.path")
target_lib="$DIST/lib/targets/$TRIPLE/lib"
mkdir -p "$target_lib"
cd "$SRC"
CC_OPT_FLAG=""
CC_LINK_OPT_FLAG=""
if [[ -n "${AARCH64_LIBFFI_INCLUDE:-}" ]]; then CC_OPT_FLAG="--cc-opt=-I$AARCH64_LIBFFI_INCLUDE"; fi
if [[ -n "${AARCH64_LIBFFI_LIB:-}" ]]; then CC_LINK_OPT_FLAG="--cc-link-opt=-L$AARCH64_LIBFFI_LIB"; fi
# shellcheck disable=SC2086
"$toolbin" \
  --triple "$TRIPLE" \
  --target-prefix "$TARGET_PREFIX" \
  --cc "$TARGET_CC" \
  --cxx "$TARGET_CXX" \
  $CC_OPT_FLAG $CC_LINK_OPT_FLAG \
  --disable-ld-override \
  --output-settings \
  -o "$target_lib/settings"
python3 - "$target_lib/settings" <<'PY_SETTINGS'
import ast, json, sys
from pathlib import Path
p = Path(sys.argv[1])
settings = dict(ast.literal_eval(p.read_text()))
for key in ('target is dynamic', 'target ships dynamic libraries',
            'target is profiled', 'target ships profiling libraries'):
    settings[key] = 'NO'
p.write_text('[' + ',\n'.join('(' + json.dumps(k) + ',' + json.dumps(v) + ')'
                              for k, v in settings.items()) + ']\n')
PY_SETTINGS
if [[ ! -d "$target_lib/package.conf.d" ]]; then
  "$DIST/bin/ghc-pkg" init "$target_lib/package.conf.d"
fi
ln -sfn ghc "$DIST/bin/$TRIPLE-ghc"
ln -sfn ghc-pkg "$DIST/bin/$TRIPLE-ghc-pkg"

echo "---- platform string (gnu stripped) and RTS ways ----"
python3 - << PY
import ast
s = ast.literal_eval(open("$target_lib/settings").read())
d = dict(s)
for k in ("target platform string", "target arch", "cross compiling", "RTS ways", "Use interpreter", "Tables next to code", "Use LibFFI"):
    print(f"{k}: {d.get(k)}")
PY

# unlit runs on the build machine, even when processing target sources.
mkdir -p "$DIST/lib/targets/$TRIPLE/bin"
ln -sfn "$DIST/bin/unlit" "$DIST/lib/targets/$TRIPLE/bin/unlit"
echo "---- selected target ----"
echo -n "symlink:   "; "$DIST/bin/$TRIPLE-ghc" --print-libdir
echo -n "-target=:  "; "$DIST/bin/ghc" -target="$TRIPLE" --print-libdir
echo -n "native:    "; "$DIST/bin/ghc" --print-libdir
linkdir=$("$DIST/bin/$TRIPLE-ghc" --print-libdir)
natdir=$("$DIST/bin/ghc" --print-libdir)
[[ "$linkdir" == "$target_lib" ]]
[[ "$natdir" != "$target_lib" ]]
[[ $("$DIST/bin/ghc" -target="$TRIPLE" --print-libdir) == "$target_lib" ]]
echo "list-targets:"
"$DIST/bin/ghc" -list-targets
echo "settings ok"
""")

md("cross-15", r"""
## Building the target libraries

Dual-compiler configure: `-w $TRIPLE-ghc` (target) plus `-W ghc` (native `Setup.hs`/generators), with matching `--with-hc-pkg`/`--with-build-hc-pkg` and `--with-gcc=$TARGET_CC`.
`cabal.project`: `allow-boot-library-installs: True`, `build:any.<pkg> installed` pins for native boot deps, unprefixed `source` for target boot libs, `package host:*` for static/vanilla flags.
Target RTS uses `+use-system-libffi +tables-next-to-code` with native bignum; early RTS packages pass `-no-rts`; `Host*` defines are always aarch64 Linux.
Build vanilla `rts:lib:nonthreaded-nodebug` first, then `all` (incl. `process:lib:process`); register `"machine": "host"` units from `plan.json` into the target package DB.
""")

code("cross-16", r"""
set -euo pipefail
ROOT=$(cat "$HOME/.cross-ghc-aarch64-root" 2>/dev/null || cat "$HOME/.cross-ghc-root")
# shellcheck disable=SC1091
if [[ -f "$ROOT/gate-aarch64.env" ]]; then source "$ROOT/gate-aarch64.env"; else source "$ROOT/gate.env"; fi
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
  ghc-options: -j$GHC_JOBS -O0 -fPIC -optc-fPIC

package ghc-internal
  configure-options: --with-compiler=$DIST/bin/$TRIPLE-ghc
  flags: +bignum-native
  ghc-options: -no-rts

package rts-fs
  ghc-options: -no-rts

package rts-headers
  ghc-options: -no-rts

package rts
  flags: +use-system-libffi +tables-next-to-code -dynamic -profiling -debug -threaded
  ghc-options: -j$GHC_JOBS -O0 -fPIC -optc-fPIC -no-rts
  ghc-options: "-optc-DProjectVersion=\\"914\\""
  ghc-options: "-optc-DHostArch=\\"aarch64\\""
  ghc-options: "-optc-DHostOS=\\"linux\\""
  ghc-options: "-optc-DHostPlatform=\\"aarch64-unknown-linux\\""
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
if [[ -n "${AARCH64_LIBFFI_INCLUDE:-}" ]]; then
  cat >> "$STAGE3/cabal.project" <<EOF

package rts
  extra-include-dirs: $AARCH64_LIBFFI_INCLUDE
package ghci
  extra-include-dirs: $AARCH64_LIBFFI_INCLUDE
EOF
fi
if [[ -n "${AARCH64_LIBFFI_LIB:-}" ]]; then
  cat >> "$STAGE3/cabal.project" <<EOF

package rts
  extra-lib-dirs: $AARCH64_LIBFFI_LIB
package ghci
  extra-lib-dirs: $AARCH64_LIBFFI_LIB
EOF
fi

# Skip the expensive library build when the target database already has the
# units this walkthrough needs (set FORCE_STAGE3_REBUILD=1 to force it).
tdb="$DIST/lib/targets/$TRIPLE/lib/package.conf.d"
need_stage3=0
if [[ -n "${FORCE_STAGE3_REBUILD:-}" ]]; then
  need_stage3=1
  echo "FORCE_STAGE3_REBUILD set — rebuilding target libraries."
else
  for package in base template-haskell ghci text containers directory process; do
    if ! "$DIST/bin/$TRIPLE-ghc-pkg" field "$package" id >/dev/null 2>&1; then need_stage3=1; break; fi
  done
fi
if (( need_stage3 == 1 )) && [[ -z "${FORCE_STAGE3_REBUILD:-}" && -d "$STAGE3/exported-conf" ]] && [[ -f "$STAGE3/sources.lock" ]] && cmp -s "$STAGE3/sources.lock" "$CROSS_ROOT/sources.lock"; then
  for conf in "$STAGE3/exported-conf"/*.conf; do
    [[ -e "$conf" ]] || continue
    "$DIST/bin/$TRIPLE-ghc-pkg" --package-db="$tdb" update --force "$conf"
  done
  "$DIST/bin/$TRIPLE-ghc-pkg" --package-db="$tdb" recache
  if "$DIST/bin/$TRIPLE-ghc-pkg" --package-db="$tdb" check >/dev/null 2>&1; then
    need_stage3=0
    for package in base template-haskell ghci text containers directory process; do
      if ! "$DIST/bin/$TRIPLE-ghc-pkg" field "$package" id >/dev/null 2>&1; then need_stage3=1; break; fi
    done
  fi
  if (( need_stage3 == 0 )); then
    echo "Restored cached stage3-aarch64 registrations from $STAGE3/exported-conf — skipping rebuild."
  else
    rm -f "$tdb"/*.conf
    "$DIST/bin/$TRIPLE-ghc-pkg" --package-db="$tdb" recache
  fi
fi
export DERIVE_CONSTANTS GENAPPLY
DERIVE_CONSTANTS=$(cat "$CROSS_ROOT/deriveConstants.path")
GENAPPLY=$(cat "$CROSS_ROOT/genapply.path")
export NM="${AARCH64_NM:-${TARGET_PREFIX}nm}"
export OBJDUMP="${AARCH64_OBJDUMP:-${TARGET_PREFIX}objdump}"
case "${AARCH64_SYSROOT:-/usr/aarch64-linux-gnu}" in
  "") ;;
  *) if [[ -d "${AARCH64_SYSROOT:-/usr/aarch64-linux-gnu}" ]]; then export QEMU_LD_PREFIX="${AARCH64_SYSROOT:-/usr/aarch64-linux-gnu}"; fi ;;
esac
unset CC CXX AR RANLIB || true

if (( need_stage3 == 0 )); then
  echo "Target libraries already registered — skipping stage3 build."
else
  cd "$STAGE3"
  "$CABAL" --store-dir="$STAGE3/store" build -j"$BUILD_JOBS" \
    -w "$DIST/bin/$TRIPLE-ghc" \
    -W "$DIST/bin/ghc" \
    --with-hc-pkg="$DIST/bin/$TRIPLE-ghc-pkg" \
    --with-build-hc-pkg="$DIST/bin/ghc-pkg" \
    --with-gcc="$TARGET_CC" \
    --with-hsc2hs="$DIST/bin/hsc2hs" \
    --with-build-hsc2hs="$DIST/bin/hsc2hs" \
    --hsc2hs-options=-x \
    --ghc-option="-ghcversion-file=$SRC/rts/include/ghcversion.h" \
    rts:lib:nonthreaded-nodebug
  "$CABAL" --store-dir="$STAGE3/store" build -j"$BUILD_JOBS" \
    -w "$DIST/bin/$TRIPLE-ghc" \
    -W "$DIST/bin/ghc" \
    --with-hc-pkg="$DIST/bin/$TRIPLE-ghc-pkg" \
    --with-build-hc-pkg="$DIST/bin/ghc-pkg" \
    --with-gcc="$TARGET_CC" \
    --with-hsc2hs="$DIST/bin/hsc2hs" \
    --with-build-hsc2hs="$DIST/bin/hsc2hs" \
    --hsc2hs-options=-x \
    --ghc-option="-ghcversion-file=$SRC/rts/include/ghcversion.h" \
    all process:lib:process

  # Export only host library registrations, from both in-place and store DBs.
  python3 - "$STAGE3" "$tdb" "$DIST/bin/$TRIPLE-ghc-pkg" <<'PY_REGISTER'
import json, re, subprocess, sys
from pathlib import Path
stage, dest, ghcpkg = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
plan = json.loads((stage / 'dist-newstyle/cache/plan.json').read_text())
assert plan['arch'] == 'aarch64'
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
for package in base template-haskell ghci text containers directory process; do
  "$DIST/bin/$TRIPLE-ghc-pkg" field "$package" id
done
""")

# ---- 4. interpreter -----------------------------------------------------
md("cross-17", r"""
## An interpreter for the target

Splices are aarch64 Linux code but GHC is a build-machine process, so build a target `iserv` from `GHCi.Server.defaultServer` with the target `ghci` library.
It has no splices itself, so `$TRIPLE-ghc` builds it directly; the native wrapper `aarch64-iserv` launches `aarch64-iserv.bin --stdio` via `aarch64-linux-run` with GHC's pipe fds.
Small RTS defaults (`-xr64m -H16m -M192m`) limit address-space reservation under emulation.
Smoke test: `-fexternal-interpreter -pgmi aarch64-iserv` must embed `aarch64` from `$(lift arch)`.
""")

md("target-context", r"""
## Target context: where splices run

GHC stays native; `aarch64-iserv.bin` (target) plus the `aarch64-iserv` wrapper (native shell → `aarch64-linux-run` → pipe fds) form the target context.
`runIO (readFile ...)` executes in the interpreter: relative `catalogue.txt`/`build-tool.txt` resolve from its working directory, which shares the build filesystem here.
QEMU `-L` is a loader prefix (not a chroot) and `hl` maps the build filesystem in; remote runners must instead stage or mount inputs.
`addDependentFile` only records rebuild deps, never transfers files or translates paths.
Keep build-machine process IO (`git --version`) in native `Setup.hs` (built with `-W`, run directly), which writes `build-tool.txt` for the target splice to read.
The target context needs file IO and computation only; it needs no `git`, shell, or child-process support.
""")

code("cross-18", r"""
set -euo pipefail
ROOT=$(cat "$HOME/.cross-ghc-aarch64-root" 2>/dev/null || cat "$HOME/.cross-ghc-root")
if [[ -f "$ROOT/gate-aarch64.env" ]]; then source "$ROOT/gate-aarch64.env"; else source "$ROOT/gate.env"; fi
cross_enter

mkdir -p "$CROSS_ROOT/bin" "$CROSS_ROOT/iserv-build"
cat > "$CROSS_ROOT/iserv-build/Main.hs" <<'EOF'
module Main where
import GHCi.Server (defaultServer)
import System.Environment (getArgs, withArgs)
import System.Posix.IO (dup, dupTo, stdInput, stdOutput, stdError)

main :: IO ()
main = do
  args <- getArgs
  case args of
    "--stdio":rest -> do
      rfd <- dup stdInput
      wfd <- dup stdOutput
      _ <- dupTo stdError stdOutput
      withArgs (show wfd : show rfd : rest) defaultServer
    _ -> defaultServer
EOF
"$DIST/bin/$TRIPLE-ghc" \
  -O0 -static -fkeep-cafs -optl-Wl,--export-dynamic \
  -rtsopts '-with-rtsopts=-xr64m -H16m -M192m' \
  -package ghci -package unix -outputdir "$CROSS_ROOT/iserv-build" \
  -o "$CROSS_ROOT/bin/aarch64-iserv.bin" "$CROSS_ROOT/iserv-build/Main.hs"
file "$CROSS_ROOT/bin/aarch64-iserv.bin"
cat > "$CROSS_ROOT/bin/aarch64-iserv" <<EOF
#!/bin/bash
set -euo pipefail
if (( \$# >= 2 )) && [[ "\$1" =~ ^[0-9]+$ && "\$2" =~ ^[0-9]+$ ]]; then
  wfd=\$1; rfd=\$2; shift 2
  exec "$CROSS_ROOT/bin/aarch64-linux-run" "$CROSS_ROOT/bin/aarch64-iserv.bin" --stdio "\$@" <&\$rfd >&\$wfd
else
  exec "$CROSS_ROOT/bin/aarch64-linux-run" "$CROSS_ROOT/bin/aarch64-iserv.bin" "\$@"
fi
EOF
chmod +x "$CROSS_ROOT/bin/aarch64-iserv"
set +e
"$CROSS_ROOT/bin/aarch64-iserv" </dev/null > "$CROSS_ROOT/iserv-usage.log" 2>&1
rc=$?
set -e
cat "$CROSS_ROOT/iserv-usage.log"
[[ $rc == 1 ]]
grep -q 'usage: iserv' "$CROSS_ROOT/iserv-usage.log"
# A protocol smoke test is stronger than merely reaching the usage message.
cat > "$CROSS_ROOT/iserv-build/Arch.hs" <<'EOF'
{-# LANGUAGE TemplateHaskell #-}
import Language.Haskell.TH.Syntax (lift)
import System.Info (arch)
main :: IO ()
main = putStrLn $(lift arch)
EOF
STATIC_GHC_OPT=""
if [[ "$TRIPLE" == *-musl* ]]; then STATIC_GHC_OPT="-static"; fi
# shellcheck disable=SC2086
"$DIST/bin/$TRIPLE-ghc" \
  $STATIC_GHC_OPT -fforce-recomp -fexternal-interpreter -pgmi "$CROSS_ROOT/bin/aarch64-iserv" \
  -rtsopts '-with-rtsopts=-xr64m -H16m -M192m' \
  -o "$CROSS_ROOT/iserv-build/arch" "$CROSS_ROOT/iserv-build/Arch.hs"
out=$("$CROSS_ROOT/bin/aarch64-linux-run" "$CROSS_ROOT/iserv-build/arch")
printf 'Splice evaluated for: %s\n' "$out"
[[ "$out" == aarch64 ]]
""")

md("cross-19", r"""
## A report prepared at compile time

`TH.hs:catalogueTH` validates `catalogue.txt` (labels, `0..100000`, unique), `addDependentFile`s it plus `build-tool.txt`, and `lift`s `(rows, arch, os, gitVersion)`.
`Main.hs` embeds that tuple into a `Map Text Integer` (`--list`, `--info` with `SPLICE_ARCH=`/`RUNTIME_ARCH=`, `LABEL`); it never reads files or runs `git` at runtime.
`Setup.hs` (native, `-W`) records `git --version` into `build-tool.txt` before `defaultMain`; keep the generator in a separate module for the TH stage restriction.
""")

code("cross-20", r"""
set -euo pipefail
ROOT=$(cat "$HOME/.cross-ghc-aarch64-root" 2>/dev/null || cat "$HOME/.cross-ghc-root")
if [[ -f "$ROOT/gate-aarch64.env" ]]; then source "$ROOT/gate-aarch64.env"; else source "$ROOT/gate.env"; fi
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
STATIC_GHC_OPT=""
if [[ "$TRIPLE" == *-musl* ]]; then STATIC_GHC_OPT="-static"; fi
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
  ghc-options:
      -fexternal-interpreter
      -pgmi $CROSS_ROOT/bin/aarch64-iserv
      "-with-rtsopts=-xr64m -H16m -M192m"
      $STATIC_GHC_OPT
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
""")

md("cross-21", r"""
## Giving Cabal both compilers

Build with `-w $TRIPLE-ghc -W ghc` plus matching `--with-hc-pkg`/`--with-build-hc-pkg`; never export a cross `CC` globally (target CC lives in target settings).
`Setup.hs` compiles with the build compiler and runs `git` natively; `TH.hs` compiles against target `template-haskell` and runs in the interpreter.
Check `BUILD_MACHINE_ARCH=$BUILD_ARCH` in the build log and `"machine": "host"`/`"build"` plus `arch == aarch64` in `plan.json`.
""")

code("cross-22", r"""
set -euo pipefail
ROOT=$(cat "$HOME/.cross-ghc-aarch64-root" 2>/dev/null || cat "$HOME/.cross-ghc-root")
if [[ -f "$ROOT/gate-aarch64.env" ]]; then source "$ROOT/gate-aarch64.env"; else source "$ROOT/gate.env"; fi
cross_enter

cd "$FIBO"
# Ensure the log belongs to this build and Setup runs again.
"$CABAL" clean
"$CABAL" build -j"$BUILD_JOBS" \
  -w "$DIST/bin/$TRIPLE-ghc" \
  -W "$DIST/bin/ghc" \
  --with-hc-pkg="$DIST/bin/$TRIPLE-ghc-pkg" \
  --with-build-hc-pkg="$DIST/bin/ghc-pkg" \
  exe:cross-report 2>&1 | tee "$CROSS_ROOT/report-build.log"
grep -Fx "BUILD_MACHINE_ARCH=$BUILD_ARCH" "$CROSS_ROOT/report-build.log"
grep -Fx "BUILD_MACHINE_OS=$BUILD_OS" "$CROSS_ROOT/report-build.log"
python3 - "$FIBO/dist-newstyle/cache/plan.json" <<'PY_PLAN'
import json, sys
plan = json.load(open(sys.argv[1]))
units = plan['install-plan']
assert plan['arch'] == 'aarch64', 'Wrong application target platform'
for unit in units:
    if unit.get('pkg-name') in {'cross-report', 'base', 'Cabal', 'template-haskell', 'text', 'process'}:
        print(unit.get('machine'), unit.get('pkg-name'), unit.get('component-name', ''), unit['type'])
app = [u for u in units if u.get('pkg-name') == 'cross-report' and (u.get('component-name') == 'exe:cross-report' or 'exe:cross-report' in u.get('components', {}))]
assert app and all(u.get('machine') == 'host' for u in app), 'Expected host application unit'
assert any(u.get('machine') == 'build' and u.get('pkg-name') == 'Cabal' for u in units), 'Expected build-side Cabal'
PY_PLAN
bin=$("$CABAL" list-bin \
  -w "$DIST/bin/$TRIPLE-ghc" -W "$DIST/bin/ghc" \
  --with-hc-pkg="$DIST/bin/$TRIPLE-ghc-pkg" \
  --with-build-hc-pkg="$DIST/bin/ghc-pkg" exe:cross-report)
[[ -x "$bin" ]]
printf '%s\n' "$bin" > "$CROSS_ROOT/report-bin.path"
""")

# ---- 5. run + verify ----------------------------------------------------
md("cross-23", r"""
## Running the report

The binary must be an AArch64 ELF (`readelf -h` → `Machine: AArch64`) run via `aarch64-linux-run`.
`--info` must show `SPLICE_ARCH=aarch64`, `RUNTIME_ARCH=aarch64`, and `BUILD_TOOL=git version ...`; lookups return `55`, `9261915075`, `3428746875`.
Single-compiler check pins `ONE_GHC_SHA256=` to the toolchain cell's hash.
""")

code("cross-24", r"""
set -euo pipefail
ROOT=$(cat "$HOME/.cross-ghc-aarch64-root" 2>/dev/null || cat "$HOME/.cross-ghc-root")
if [[ -f "$ROOT/gate-aarch64.env" ]]; then source "$ROOT/gate-aarch64.env"; else source "$ROOT/gate.env"; fi
cross_enter

sha256_file() {
  if command -v sha256sum >/dev/null 2>&1; then sha256sum "$1" | awk '{print $1}';
  elif command -v shasum >/dev/null 2>&1; then shasum -a 256 "$1" | awk '{print $1}';
  else echo "Need sha256sum or shasum for checksums." >&2; return 1; fi
}

bin=$(cat "$CROSS_ROOT/report-bin.path")
"${AARCH64_READELF:-${TARGET_PREFIX}readelf}" -h "$bin" | tee "$CROSS_ROOT/report-elf.txt"
grep -Eq 'Machine:[[:space:]]+AArch64' "$CROSS_ROOT/report-elf.txt"
run_report() { "$CROSS_ROOT/bin/aarch64-linux-run" "$bin" "$@"; }
run_report
info=$(run_report --info)
printf '%s\n' "$info" | tee "$CROSS_ROOT/report-info.txt"
for expected in SPLICE_ARCH=aarch64 SPLICE_OS=linux RUNTIME_ARCH=aarch64 RUNTIME_OS=linux; do
  grep -Fx "$expected" <<< "$info"
done
grep -q '^BUILD_TOOL=git version ' <<< "$info"
[[ $(run_report small) == 55 ]]
[[ $(run_report century) == 9261915075 ]]
[[ $(run_report large) == 3428746875 ]]
[[ $(run_report --list) == $'century\nlarge\nsmall' ]]
grep -Fx "BUILD_MACHINE_ARCH=$BUILD_ARCH" "$CROSS_ROOT/report-build.log"
grep -Fx "BUILD_MACHINE_OS=$BUILD_OS" "$CROSS_ROOT/report-build.log"
if run_report unknown > "$CROSS_ROOT/report-error.txt" 2>&1; then
  echo "Unknown catalogue label unexpectedly succeeded" >&2; exit 1
fi
grep -Fx 'Unknown catalogue entry: unknown' "$CROSS_ROOT/report-error.txt"
# Single-compiler identity: the native and target commands resolve to one file,
# and its hash is unchanged since the toolchain build cell recorded it.
[[ "$DIST/bin/ghc" -ef "$DIST/bin/$TRIPLE-ghc" ]]
[[ "$DIST/bin/ghc-pkg" -ef "$DIST/bin/$TRIPLE-ghc-pkg" ]]
cur=$(sha256_file "$DIST/bin/ghc")
[[ "$cur" == "${ONE_GHC_SHA256:?ONE_GHC_SHA256 not recorded; rerun the toolchain build cell.}" ]]
echo "ONE_GHC_SHA256=$cur"
echo "Cross compilation checks passed."
""")

md("cross-25", r"""
## Expected output, then a negative check

Expect `century=9261915075`, `large=3428746875`, `small=55` in map order: native Setup, target interpreter, and target binary each did their part.
Next, break the catalogue (`broken -1`): the rebuild must fail with `expected LABEL INDEX (0..100000)` via `addDependentFile`, then restore, rebuild, and re-check `large`.
""")

code("cross-26", r"""
set -euo pipefail
ROOT=$(cat "$HOME/.cross-ghc-aarch64-root" 2>/dev/null || cat "$HOME/.cross-ghc-root")
if [[ -f "$ROOT/gate-aarch64.env" ]]; then source "$ROOT/gate-aarch64.env"; else source "$ROOT/gate.env"; fi
cross_enter

cd "$FIBO"
cp catalogue.txt catalogue.txt.saved
trap 'mv -f catalogue.txt.saved catalogue.txt' EXIT
printf 'broken -1\n' > catalogue.txt
# Ensure the timestamp advances even on coarse timestamp filesystems.
sleep 1
touch catalogue.txt
build_report() {
  "$CABAL" build -j"$BUILD_JOBS" \
    -w "$DIST/bin/$TRIPLE-ghc" -W "$DIST/bin/ghc" \
    --with-hc-pkg="$DIST/bin/$TRIPLE-ghc-pkg" \
    --with-build-hc-pkg="$DIST/bin/ghc-pkg" exe:cross-report
}
if build_report > "$CROSS_ROOT/report-invalid-build.log" 2>&1; then
  echo "The invalid catalogue unexpectedly compiled" >&2; exit 1
fi
grep -F 'expected LABEL INDEX (0..100000)' "$CROSS_ROOT/report-invalid-build.log"
mv -f catalogue.txt.saved catalogue.txt
trap - EXIT
sleep 1
touch catalogue.txt
build_report
bin=$(cat "$CROSS_ROOT/report-bin.path")
[[ $("$CROSS_ROOT/bin/aarch64-linux-run" "$bin" large) == 3428746875 ]]
echo "Compile-time validation and restored build checked."
""")

# ---- 6. one compiler ------------------------------------------------------
md("extra-c6e3b984", r"""
### One compiler throughout

The target names select settings and package databases. The executables below must resolve to the same native file, and its hash must remain unchanged since the toolchain build cell recorded it. Adding the aarch64 target required its libraries and auxiliary tools; it did not require rebuilding GHC.

The recipes above use `BUILD_JOBS` Cabal jobs and `GHC_JOBS` GHC workers per library project. Increase them to suit the memory available. Keep target stores separate if building several targets concurrently: this notebook's project lives at `stage3-aarch64` precisely so it does not collide with other target notebooks sharing `CROSS_ROOT`.
""")

code("extra-53a40afd", r"""
set -euo pipefail
ROOT=$(cat "$HOME/.cross-ghc-aarch64-root" 2>/dev/null || cat "$HOME/.cross-ghc-root")
if [[ -f "$ROOT/gate-aarch64.env" ]]; then source "$ROOT/gate-aarch64.env"; else source "$ROOT/gate.env"; fi
cross_enter
export DIST
[[ "$DIST/bin/ghc" -ef "$DIST/bin/$TRIPLE-ghc" ]]
[[ "$DIST/bin/ghc-pkg" -ef "$DIST/bin/$TRIPLE-ghc-pkg" ]]
if command -v sha256sum >/dev/null 2>&1; then
  cur=$(sha256sum "$DIST/bin/ghc" | awk '{print $1}')
else
  cur=$(shasum -a 256 "$DIST/bin/ghc" | awk '{print $1}')
fi
[[ "$cur" == "${ONE_GHC_SHA256:?ONE_GHC_SHA256 not recorded; rerun the toolchain build cell.}" ]]
echo "ONE_GHC_SHA256=$cur"
echo "Single compiler, $TRIPLE target ok."
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
            "codemirror_mode": "ipython",
            "file_extension": ".py",
            "mimetype": "text/x-python",
            "name": "python",
            "pygments_lexer": "ipython3",
            "version": "3.11",
        },
    },
    "nbformat": 4,
    "nbformat_minor": 5,
}

out = sys.argv[1] if len(sys.argv) > 1 else "AArch64 TH Cross.ipynb"
with open(out, "w") as f:
    json.dump(nb, f, indent=1, ensure_ascii=False)
    f.write("\n")
print(f"wrote {out} with {len(nb_cells)} cells")




