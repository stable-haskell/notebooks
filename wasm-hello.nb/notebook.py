#!/usr/bin/env python3
"""Generator for "WASM Hello.ipynb" (bash kernel, nbformat 4.5).

The notebook is generated from this script so edits are reproducible and the
serialized form matches tools/strip-ipynb.py (clean cells, no outputs). Run via
`make notebook`, or: python3 wasm-hello.nb/notebook.py "wasm-hello.nb/WASM Hello.ipynb"

Targets the stable-haskell MULTI-TARGET GHC bindist (native + wasm + JS in one
ghcup install) — channel ghcup-multi-target-0.1.0.yaml, stable-haskell/ghc#184.
"""
import json
import sys

cells = []


def md(cid, text):
    cells.append(("markdown", cid, text))


def code(cid, text):
    cells.append(("code", cid, text))


# ---- 0. title -------------------------------------------------------------
md("wasm-title", r"""
# Hello, WebAssembly — a `stable-haskell` GHC walkthrough

Companion notebook to the landing page at <https://stable-haskell.github.io/ghc/>.

This walks end-to-end through the stable-haskell **multi-target GHC**: one ghcup
install ships native `ghc`, the `wasm32-unknown-wasi` cross-compiler, and the
`javascript-unknown-ghcjs` cross-compiler in a single bindist (same binary,
dispatched by `argv[0]`). We use it to build the shipped *hello* reactor template
into a `.wasm` and run it under Node.js and in the browser — all from one
`cabal build` that drives **two** of those compilers (native build side + wasm
target).

What you'll do:

1. Point `ghcup` at the stable-haskell multi-target release channel.
2. Install the multi-target GHC (native + wasm + JS in one) and the dual-compiler-aware `cabal`.
3. Install the **wasi C/LLVM toolchain** (`clang`, `wasm-ld`, …) the wasm target needs — it is *not* bundled.
4. Download the *hello* starter template.
5. Read the anatomy of a GHC **wasm reactor** (cabal flags, the foreign export, the JS bring-up sequence).
6. `make build` → a `myapp.wasm`.
7. `make run-node` → prints `Hello from the WASM reactor!`.
8. `make run-web` → the same module in a browser tab.

> **Platform note.** The multi-target bindist ships for **aarch64-darwin**,
> **x86_64-linux**, and **aarch64-linux**. This notebook drives your *system*
> `ghcup` (>= 0.2.5, for the multi-target channel's installer schema); the flake
> only pins the host-side helpers (Node 22+, make, curl, python). Install ghcup
> from <https://www.haskell.org/ghcup/> if you don't have it.

> **Prerelease.** The multi-target channel is tagged `LatestPrerelease`
> (stable-haskell/ghc#184). Pin `MULTI_VERSION` in §1 to a specific build.

Run the cells top to bottom. Re-running install cells is safe (ghcup is idempotent).
""")

# ---- references -----------------------------------------------------------
md("wasm-refs", r"""
## References

- Landing page & docs: <https://stable-haskell.github.io/ghc/>
- 90-second hello walkthrough: <https://stable-haskell.github.io/ghc/examples/hello/>
- Anatomy of the reactor template: <https://stable-haskell.github.io/ghc/anatomy/>
- Troubleshooting: <https://stable-haskell.github.io/ghc/troubleshooting/>
- ghcup multi-target release channel: <https://stable-haskell.github.io/ghc/ghcup-multi-target-0.1.0.yaml>
- Multi-target bindist work: <https://github.com/stable-haskell/ghc/pull/184>
- Starter template tarball: <https://stable-haskell.github.io/ghc/examples/stable-haskell-wasm-hello.tar.gz>
- GHC wasm backend (JavaScript FFI, reactor model): <https://downloads.haskell.org/ghc/latest/docs/users_guide/wasm.html>
- `@bjorn3/browser_wasi_shim` (browser WASI provider): <https://github.com/bjorn3/browser_wasi_shim>
""")

# ---- 1. environment -------------------------------------------------------
md("wasm-env-md", r"""
## 1. Environment

This notebook calls your **system `ghcup`** (>= 0.2.5, for the multi-target
channel's installer schema) plus a few host-side tools the flake provides (Node
22+, make, curl, python3, GNU coreutils). The cell below makes ghcup's tool
symlinks visible to the kernel by prepending `~/.ghcup/bin` to `PATH` — the same
trick the other `*.nb` notebooks use so it doesn't matter how Jupyter was
launched.

Edit `MULTI_VERSION` if you install a different build.
""")

code("wasm-env", r"""
# Multi-target GHC + cabal versions this walkthrough targets. One bindist ships
# native + wasm + JS; see the multi-target release channel added in §2.
export MULTI_VERSION=multi-9.14.0.stable.1
export CABAL_VERSION=3.17.0.0.stable.0

# The template Makefile locates post-link.mjs under
#   ~/.ghcup/ghc/$WASM_VERSION/lib/targets/wasm32-unknown-wasi/lib
# which, for the multi bindist, is the multi GHC dir. Point WASM_VERSION there.
export WASM_VERSION="$MULTI_VERSION"

# `ghcup set` symlinks every target binary (ghc, wasm32-unknown-wasi-ghc,
# javascript-unknown-ghcjs-ghc, plus the -pkg variants) into ~/.ghcup/bin, so
# that single dir is all we need on PATH for the compilers.
export PATH="$HOME/.ghcup/bin:$PATH"

# Some Jupyter kernels launch with a minimal PATH. Make sure the native
# build-side C toolchain (gcc/cc/ld in /usr/bin) and the wasi-sdk toolchain
# (installed in §2.4, if already present) are reachable. Append these so the
# nix-provided node/make/curl stay ahead of any system copies.
for d in /usr/bin /bin /usr/local/bin "$HOME/.ghc-wasm/wasi-sdk/bin"; do
  [ -d "$d" ] || continue
  case ":$PATH:" in *":$d:"*) ;; *) PATH="$PATH:$d" ;; esac
done
export PATH

echo "MULTI_VERSION = $MULTI_VERSION"
echo "CABAL_VERSION = $CABAL_VERSION"
if command -v ghcup >/dev/null; then
  echo "ghcup         = $(ghcup --version 2>/dev/null | head -1)"
else
  echo "ghcup         = MISSING — install from https://www.haskell.org/ghcup/"
fi
""")

md("wasm-node-md", r"""
### Node.js 22+ is mandatory

The GHC wasm backend uses Node for **Template Haskell** evaluation (a wasm-iserv
host), for the **post-link** step that emits the JavaScript FFI glue, and for
running the module. Ubuntu's `apt` ships Node 18 — too old; use
[NodeSource](https://github.com/nodesource/distributions) (or this flake's
pinned `nodejs_22`).
""")

code("wasm-node-check", r"""
if command -v node >/dev/null; then
  echo "node $(node --version)"
  MAJ=$(node --version | sed 's/^v//; s/\..*$//')
  if [ "$MAJ" -ge 22 ]; then echo "OK: Node.js >= 22"; else echo "WARN: need Node.js 22+, found $(node --version)"; fi
else
  echo "node MISSING — provided by this flake (nodejs_22), or install Node 22+ from NodeSource."
fi
""")

# ---- 2. install toolchain -------------------------------------------------
md("wasm-install-md", r"""
## 2. Install the toolchain

Four pieces:

1. **Add the multi-target release channel** — teaches ghcup about the
   stable-haskell **multi-target** bindist (one GHC, three targets).
2. **Install the multi-target GHC** — a single `ghcup install` gives you native
   `ghc`, `wasm32-unknown-wasi-ghc`, *and* `javascript-unknown-ghcjs-ghc` (same
   binary, dispatched by `argv[0]`). No separate native compiler needed.
3. **Install + select the stable cabal** — `cabal-3.17.0.0.stable.0` carries the
   *target-prefix-aware* patch that lets a single `cabal build` drive a native
   build-side GHC **and** the wasm host-side GHC.
4. **Install the wasi C/LLVM toolchain** — the cross-compiler shells out to
   `wasm32-unknown-wasi-clang` + `wasm-ld` to compile/link C and emit the final
   `.wasm`. **These are not bundled** (the channel's own pre-install notice says
   so — §2.4), so even `wasm32-unknown-wasi-ghc hello.hs` fails until they're on
   `PATH`.

These are network downloads (~700 MB for the multi bindist, a few hundred MB for
the wasi-sdk) and are idempotent — re-running is fine.
""")

code("wasm-add-channel", r"""
ghcup config add-release-channel \
  https://stable-haskell.github.io/ghc/ghcup-multi-target-0.1.0.yaml
""")

code("wasm-install-ghc", r"""
# One bindist, three targets. `set` activates the unversioned symlinks
# (ghc, wasm32-unknown-wasi-ghc, javascript-unknown-ghcjs-ghc, …) in ~/.ghcup/bin.
ghcup install ghc "$MULTI_VERSION"
ghcup set     ghc "$MULTI_VERSION"
""")

code("wasm-install-cabal", r"""
ghcup install cabal "$CABAL_VERSION"
ghcup set     cabal "$CABAL_VERSION"
cabal --version
""")

md("wasm-wasi-md", r"""
### 2.4 The wasi C/LLVM toolchain (required — not bundled)

The multi-target bindist ships the GHCs but not the wasm C/LLVM toolchain. Its
`settings` shell out to `wasm32-unknown-wasi-clang(++)`, `wasm-ld`,
`wasm32-unknown-wasi-ar`/`-ranlib`, `llc`/`opt`/`llvm-as` — and the channel's own
pre-install notice tells you to install them separately. Without it, even
`wasm32-unknown-wasi-ghc hello.hs` fails with
`gcc: could not execute: 'wasm32-unknown-wasi-clang'`.

The cell below installs it the way stable-haskell CI does: `ghc-wasm-meta`'s
`bootstrap.sh` fetches a pinned **wasi-sdk** into `~/.ghc-wasm`, then we bridge
the tool names GHC expects (`wasm32-wasi-clang` → `wasm32-unknown-wasi-clang`,
`llvm-ar` → `wasm32-unknown-wasi-ar`, …) and put the toolchain on `PATH`.

> `bootstrap.sh` needs `jq` and `unzip` on `PATH` (this notebook's flake provides
> them). It also drops an unused `wasm32-wasi-ghc` of its own flavour in
> `~/.ghc-wasm/bin`, which we deliberately keep **off** `PATH` so the multi-target
> compiler stays in charge.
""")

code("wasm-wasi-install", r"""
export GHC_WASM_PREFIX="$HOME/.ghc-wasm"
WASI_BIN="$GHC_WASM_PREFIX/wasi-sdk/bin"

for t in jq unzip; do
  command -v "$t" >/dev/null || echo "WARN: '$t' not on PATH — bootstrap.sh needs it (the flake's 'nix develop' provides it)."
done

if [ -x "$WASI_BIN/clang" ] || [ -x "$WASI_BIN/wasm32-wasi-clang" ]; then
  echo "wasi-sdk already present in $WASI_BIN — skipping download."
else
  echo "Installing wasi-sdk toolchain into $GHC_WASM_PREFIX (a few hundred MB)…"
  curl -fsSL https://gitlab.haskell.org/ghc/ghc-wasm-meta/-/raw/master/bootstrap.sh | \
    FLAVOUR=9.12 PREFIX="$GHC_WASM_PREFIX" sh
fi

# GHC's settings call the tools wasm32-unknown-wasi-*; bootstrap installs clang
# as wasm32-wasi-* and binutils as llvm-*. Bridge the names (matches CI).
for t in ar nm ranlib strip; do ln -sf "$WASI_BIN/llvm-$t"       "$WASI_BIN/wasm32-unknown-wasi-$t"; done
for t in clang clang++;     do ln -sf "$WASI_BIN/wasm32-wasi-$t" "$WASI_BIN/wasm32-unknown-wasi-$t"; done

# Put ONLY the toolchain bin on PATH (append, so the native cc/clang stay the
# host compiler; note we do NOT add ~/.ghc-wasm/bin, to keep the multi ghc).
case ":$PATH:" in *":$WASI_BIN:"*) ;; *) export PATH="$PATH:$WASI_BIN" ;; esac
echo "wasi-sdk bin on PATH: $WASI_BIN"
command -v wasm32-unknown-wasi-clang && command -v wasm-ld
""")

md("wasm-verify-md", r"""
### 2.5 Verify the toolchain end to end

One multi install, three compilers. Check the native and wasm drivers resolve,
then compile a one-liner — this exercises the full wasm toolchain
(Haskell → C → `wasm-ld`) and is the fastest way to confirm §2.4 worked. Expect
output containing `WebAssembly (wasm) binary module`.
""")

code("wasm-verify", r"""
ghc --version
wasm32-unknown-wasi-ghc --version
printf 'main = putStrLn "hello, wasm"\n' > /tmp/hello.hs
wasm32-unknown-wasi-ghc /tmp/hello.hs -o /tmp/hello.wasm && file /tmp/hello.wasm
""")

# ---- 3. template ----------------------------------------------------------
md("wasm-template-md", r"""
## 3. Get the starter template

A small tarball with the minimal meaningful wasm **reactor**: a `myapp.cabal`, a
`cabal.project`, an `app/Main.hs`, a Node runner (`run.mjs`), a browser launcher
(`public/`), and a self-documenting `Makefile`. We extract it next to this
notebook so everything stays self-contained (it's git-ignored).
""")

code("wasm-get-template", r"""
curl -L -o hello.tar.gz \
  https://stable-haskell.github.io/ghc/examples/stable-haskell-wasm-hello.tar.gz
tar xf hello.tar.gz
cd stable-haskell-wasm-hello
export APP="$PWD"
echo "APP=$APP"
find . -type f | sort
""")

# ---- 4. anatomy -----------------------------------------------------------
md("wasm-anatomy-md", r"""
## 4. Anatomy of the reactor template

A *reactor* is a long-lived wasm module with no `main`/`_start`: it exposes
exported functions the JavaScript host calls on demand. GHC builds one when you
pass `-no-hs-main -optl-mexec-model=reactor` and export an entry point. Four
files make it work — let's read each.
""")

md("wasm-cabalproj-md", r"""
### 4.1 `cabal.project` — one build, two compilers

`with-build-compiler` / `with-build-hc-pkg` name the **native** GHC (build side:
`Setup.hs`, build-tools, the Template Haskell host). `with-compiler` /
`with-hc-pkg` name the **wasm** GHC (host side: your package's code, compiled to
wasm32). With the multi-target bindist both `ghc` and `wasm32-unknown-wasi-ghc`
come from the *same* install. `shared: True` under `if arch(wasm32)` is required
for the wasm dynamic-linker / JSFFI story.
""")
code("wasm-cat-cabalproj", "cat cabal.project")

md("wasm-cabal-md", r"""
### 4.2 `myapp.cabal` — the reactor flags

The `if arch(wasm32)` block is the whole trick:

- `-no-hs-main` — don't synthesize a `_start` from Haskell's `main`.
- `-optl-mexec-model=reactor` — emit a WASI **reactor** (host-driven), not a command.
- `-optl-Wl,--export=hs_start` — have the linker export our entry symbol.
- `-DWASM` — a CPP flag the source uses to guard the `foreign export`.
""")
code("wasm-cat-cabal", "cat myapp.cabal")

md("wasm-main-md", r"""
### 4.3 `app/Main.hs` — the JavaScript-callable entry point

`foreign export javascript "hs_start" main :: IO ()` exposes `main` to JS under
the name `hs_start`. It's guarded by `#ifdef WASM` so the file still builds with
a native GHC too.
""")
code("wasm-cat-main", "cat app/Main.hs")

md("wasm-runmjs-md", r"""
### 4.4 `run.mjs` — the 3-step bring-up sequence

The host must call **three** things, in order, after instantiating the module:

1. `wasi.initialize(instance)` — runs WASI static constructors (`_initialize`).
2. `instance.exports.__ghc_wasm_jsffi_init()` — initializes GHC's JSFFI runtime **and the RTS**.
3. `instance.exports.hs_start()` — your foreign-exported entry point.

**Skipping step 2 is the classic mistake** — `hs_start()` then throws
`RTS is not initialised; call hs_init() first`. The browser launcher in
`public/index.js` is the same pattern with `@bjorn3/browser_wasi_shim` swapped in
for `node:wasi`.
""")
code("wasm-cat-runmjs", "cat run.mjs")

md("wasm-makefile-md", r"""
### 4.5 The `Makefile`

Self-documenting; `make` with no target prints help. It wires up `build` (cabal),
the `post-link` step (turns the wasm's `ghc_wasm_jsffi` custom section into a
paired `jsffi.mjs` ESM module — the wasm and `jsffi.mjs` must sit side by side),
and the `run-node` / `run-web` runners. It finds `post-link.mjs` under
`~/.ghcup/ghc/$WASM_VERSION/lib/...`; §1 sets `WASM_VERSION` to the multi version
so it resolves inside the multi-target install.
""")
code("wasm-make-help", "make help")

# ---- 5. build -------------------------------------------------------------
md("wasm-build-md", r"""
## 5. Build the wasm module

`make build` is just `cabal build myapp`. Cabal uses the native GHC for the build
side and `wasm32-unknown-wasi-ghc` for the target, producing a reactor `.wasm`
under `dist-newstyle/store/host/wasm32-unknown-wasi/bin/`. The first build is
slower — it compiles the wasm `base`/`rts` dependencies into the store.
""")
code("wasm-build", "make build")
code("wasm-file", "file dist-newstyle/store/host/wasm32-unknown-wasi/bin/myapp.wasm")

# ---- 6. run node ----------------------------------------------------------
md("wasm-runnode-md", r"""
## 6. Run under Node.js

`make run-node` copies the wasm out, runs `post-link.mjs` to emit `jsffi.mjs`,
then `node run.mjs`. Expected output: `Hello from the WASM reactor!`
""")
code("wasm-run-node", "make run-node")

# ---- 7. run web -----------------------------------------------------------
md("wasm-runweb-md", r"""
## 7. Run in the browser

`make run-web` post-links into `public/` and serves it with
`python3 -m http.server 8000` in the **foreground** (Ctrl-C to stop) — use that
from a terminal. Because a foreground server would block this kernel, the cell
below does the non-blocking pieces: post-link into `public/`, then start the
server in the **background**. Open <http://localhost:8000> and check the browser
console for `[hs stdout] Hello from the WASM reactor!`.
""")
code("wasm-run-web", r"""
make post-link-web
# Serve in the background so the kernel stays interactive.
( cd public && python3 -m http.server 8000 >/tmp/wasm-http.log 2>&1 & echo $! >/tmp/wasm-http.pid )
sleep 1
echo "Serving public/ at http://localhost:8000 (pid $(cat /tmp/wasm-http.pid))."
echo "Open it, then check the browser console. Stop it with the next cell."
""")
code("wasm-stop-web", r"""
# Stop the background web server started above.
if [ -f /tmp/wasm-http.pid ]; then
  kill "$(cat /tmp/wasm-http.pid)" 2>/dev/null && echo "stopped server (pid $(cat /tmp/wasm-http.pid))" || echo "server not running"
  rm -f /tmp/wasm-http.pid
else
  echo "no server pid file; nothing to stop"
fi
""")

# ---- 8. going further -----------------------------------------------------
md("wasm-further-md", r"""
## 8. Going further — real apps (FFI, miso) and the JS target

The hello reactor only uses `base`. Real browser apps add JavaScript FFI and a UI
framework. Two gotchas learned shipping a full [miso](https://haskell-miso.org/)
demo to wasm:

- **JSFFI needs extra deps.** Add, under `if arch(wasm32)`:
  ```cabal
  build-depends: ghc-experimental, jsaddle-wasm
  ```
  (`ghc-experimental` provides `GHC.Wasm.Prim`; without it you get
  `Could not load module 'GHC.Wasm.Prim'`.)
- **Use miso 1.11+.** The entry point is `startApp defaultEvents app`; older
  tutorials' `initialAction` / bare `startApp` won't typecheck.

Anything pulling in Template Haskell (most non-trivial packages, including miso)
needs the wasm libraries built with `shared: True` — which is exactly why the
template's `cabal.project` sets it under `if arch(wasm32)`.

The same multi-target install also ships `javascript-unknown-ghcjs-ghc`, so you
can target the browser's JS backend from the same project by pointing
`with-compiler` at it (and `with-hc-pkg` at `javascript-unknown-ghcjs-ghc-pkg`)
— no extra install. The JS target needs **emscripten** on `PATH` (see the
channel's pre-install notice), the JS analogue of §2.4's wasi-sdk.
""")

# ---- 9. troubleshooting ---------------------------------------------------
md("wasm-troubleshooting-md", r"""
## 9. Troubleshooting

**Install / setup**

| Symptom | Cause | Fix |
|---|---|---|
| `wasm32-unknown-wasi-ghc: command not found` | multi GHC installed but not activated, or `~/.ghcup/bin` not on `PATH` | `ghcup set ghc $MULTI_VERSION` (re-run §2) to create the symlinks; ensure `~/.ghcup/bin` is on `PATH` (§1) |
| `ghc` / `ghc-pkg` version mismatch | Upstream cabal older than `3.17.0.0.stable.0` (no target-prefix-aware patch) | `ghcup install cabal 3.17.0.0.stable.0 && ghcup set cabal 3.17.0.0.stable.0` |

**Build time**

| Symptom | Cause | Fix |
|---|---|---|
| `could not execute: wasm32-unknown-wasi-clang`, or cabal `[Cabal-6666] gcc … not found` | wasi C/LLVM toolchain not installed — it is **not** bundled in the bindist | Run §2.4 (`ghc-wasm-meta` bootstrap + the `wasm32-unknown-wasi-*` symlinks), then add `~/.ghc-wasm/wasi-sdk/bin` to `PATH` |
| `Template Haskell evaluation on the wasm32 backend requires node` | Node 22+ missing / not on `PATH` | Install Node 22+ (NodeSource, not apt — apt ships 18) |
| `External interpreter terminated (127)` | wasm-iserv host failed to start (almost always missing node) | Ensure `node` is on `PATH` |
| `Could not load module 'GHC.Wasm.Prim'` | Missing `ghc-experimental` dep | Add `if arch(wasm32) build-depends: ghc-experimental, jsaddle-wasm` |
| miso: `Not in scope: 'initialAction'` | Pre-1.11 miso API | Use miso 1.11+; `startApp defaultEvents app` |

**Post-link / runtime**

| Symptom | Cause | Fix |
|---|---|---|
| `post-link.mjs` runs but emits nothing | Node 18 / pre-20.11 (no `import.meta.filename`) | Install Node 22 |
| `RTS is not initialised; call hs_init() first` | Host skipped `__ghc_wasm_jsffi_init()` | Order: `wasi.initialize()` → `__ghc_wasm_jsffi_init()` → `hs_start()` |
| Browser 404 on `myapp.wasm` / `jsffi.mjs` | Files not in the same dir as `index.html` | Keep all three together; `make run-web` does this |

Still stuck? File an issue at <https://github.com/stable-haskell/ghc/issues>
with your ghc / cabal / node versions, platform, and the full error message.
""")

# ---- 10. cleanup ----------------------------------------------------------
md("wasm-cleanup-md", r"""
## 10. Cleanup

Everything lives under `stable-haskell-wasm-hello/` next to this notebook
(git-ignored). Delete it to start over. The installed multi-target compiler stays
in `~/.ghcup` for next time; uncomment the `ghcup rm` line to remove it too.
""")
code("wasm-cleanup", r"""
# cd back out of the template dir (the bash kernel keeps cwd across cells).
cd "$(dirname "$APP")" 2>/dev/null || true

# Uncomment to wipe build artifacts + the extracted template:
# rm -rf "$APP" hello.tar.gz

# Uncomment to uninstall the multi-target compiler:
# ghcup rm ghc "$MULTI_VERSION"

echo "APP=$APP"
[ -d "$APP" ] && du -sh "$APP" 2>/dev/null || echo "already removed"
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
        "kernelspec": {"display_name": "Bash", "language": "bash", "name": "bash"},
        "language_info": {
            "codemirror_mode": "shell",
            "file_extension": ".sh",
            "mimetype": "text/x-sh",
            "name": "bash",
        },
    },
    "nbformat": 4,
    "nbformat_minor": 5,
}

out = sys.argv[1] if len(sys.argv) > 1 else "WASM Hello.ipynb"
with open(out, "w") as f:
    json.dump(nb, f, indent=1, ensure_ascii=False)
    f.write("\n")
print(f"wrote {out} with {len(nb_cells)} cells")
