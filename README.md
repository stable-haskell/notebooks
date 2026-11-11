# stable-haskell notebooks

Runnable Jupyter walkthroughs for the [stable-haskell](https://stable-haskell.github.io/)
toolchain.

Each notebook is a self-contained `*.nb/` directory with its own Nix flake that
provides JupyterLab, a Bash kernel, and exactly the host-side tools the notebook
needs. There is nothing to install globally — `nix develop -c jupyter-lab`
(or `nix run`) inside a notebook directory drops you into a ready-to-run
environment.

The repository root also carries an umbrella [`flake.nix`](flake.nix) (CI,
caching, and repo-wide dev shells — no notebook of its own).

## Notebooks

| Notebook | What it does |
|---|---|
| [`wasm-hello.nb`](wasm-hello.nb/) | Prebuilt `stable-haskell` multi-target GHC bindist via `ghcup` (`WASM Hello.ipynb`). Installs the multi-target GHC (native + wasm + JS in one bindist) and the dual-compiler-aware `cabal`, builds the *hello* starter template to `.wasm`, and runs it under Node.js and in the browser. Companion to <https://stable-haskell.github.io/ghc/>. |
| [`aarch64-th-cross.nb`](aarch64-th-cross.nb/) | Source-built GHC 9.14 + cross-aware Cabal targeting `aarch64` Linux (`AArch64 TH Cross.ipynb`), with Template Haskell run through a target `iserv`. QEMU user-mode on Linux; `zw3rk/rt-series` + `zw3rk/hyper-linux` (`hl`) on macOS (`aarch64-darwin`). |
| [`js-th-cross.nb`](js-th-cross.nb/) | Source-built GHC 9.14 + cross-aware Cabal targeting `javascript-unknown-ghcjs` (`JS TH Cross.ipynb`) with Template Haskell in Node.js. |
| [`wasm-th-cross.nb`](wasm-th-cross.nb/) | Source-built GHC 9.14 + cross-aware Cabal targeting `wasm32-unknown-wasi` (`WASM TH Cross.ipynb`) with Template Haskell via `dyld.mjs` in Node.js and WASI execution via `wasmtime`. |
| [`windows-th-cross.nb`](windows-th-cross.nb/) | Source-built GHC 9.14 + cross-aware Cabal targeting `x86_64-w64-mingw32` (`Windows TH Cross.ipynb`) with LLVM-MinGW UCRT, `iserv-proxy`, and Wine. |

## The shared `CROSS_ROOT`

The four `*-th-cross.nb` notebooks all build on **one** shared compiler tree,
`CROSS_ROOT` (default `~/cross-ghc-current`): a source-built GHC 9.14 stage2
plus the cross-aware Cabal from
[`andreabedini/cabal`](https://github.com/andreabedini/cabal)
(`andrea/feature/cross-compilation-2`).

The tree is built **once** and reused across all target notebooks:

1. The `cross-02` step fixes the layout (`gate-aarch64.env`, `gate-js.env`,
   `gate-wasm.env`, `gate-windows.env`, `ghc-src`, `cabal-src`,
   `cabal-dist`, `stage3`, `sources.lock`, …) under `CROSS_ROOT`.
2. The `cross-07` step checks out
   [`stable-haskell/ghc`](https://github.com/stable-haskell/ghc)
   (`stable-ghc-9.14`) and the Cabal branch, recording the exact commits in
   `sources.lock`.
3. The `cross-08` step builds the cross-aware `cabal` and the stage2 GHC.
4. The `cross-12` step records the native tool paths (`deriveConstants.path`,
   `genapply.path`, `ghc-toolchain-bin.path`).

Each target notebook then adds only its own target libraries, settings file,
and package database on top of that compiler — no second GHC build. Starting
from different source commits requires a fresh `CROSS_ROOT`: compiler
artifacts and package databases from different revisions must not be mixed
(the build refuses when `sources.lock` drifts from `build-sources.lock`).

CI runs a 4-job pipeline (see [`.github/workflows/th-cross-notebooks.yml`](.github/workflows/th-cross-notebooks.yml)):
`plan` computes the per-OS validation matrices and resolves the exact GHC/Cabal
commits plus the flake hash once, `base-toolchain-linux` /
`base-toolchain-macos` build the shared base tree once per platform (including
the per-target `gate-aarch64.env`, `gate-js.env`, `gate-wasm.env`,
`gate-windows.env` files under the shared `CROSS_ROOT`) and share it through
the GitHub Actions cache, and `validate-linux` / `validate-macos` restore the
base tree plus a per-target cache and execute each notebook end to end.
Splitting Stage 1 and Stage 2 by OS keeps a macOS issue from blocking Linux
validation.

## Cross-compiling from macOS

The `aarch64` target (and the shared base toolchain) also builds on
`aarch64-darwin`. The root flake's `ci-cross` dev shell
(`nix develop .#ci-cross`) provides the unified host + cross environment:
autotools, the `aarch64-unknown-linux-musl` cross toolchain (same `pkgsCross`
family [`github:zw3rk/rt-series`](https://github.com/zw3rk/rt-series) uses),
and [`github:zw3rk/hyper-linux`](https://github.com/zw3rk/hyper-linux) (`hl`)
on `aarch64-darwin`, which executes `aarch64`-Linux ELF target interpreters
through Hypervisor.framework where Linux would use QEMU user-mode.

## Running a notebook

```sh
cd wasm-hello.nb
nix develop -c jupyter-lab     # or: nix run
```

Then open the `.ipynb` and run the cells top to bottom.

## Requirements

- [Nix](https://nixos.org/download) with flakes enabled (`experimental-features = nix-command flakes`).
- Notebooks that drive `ghcup` (e.g. `wasm-hello.nb`) assume a system
  [ghcup](https://www.haskell.org/ghcup/); each notebook's intro says so.
- The `*-th-cross.nb` notebooks build GHC from source: allow ~32 GiB of free
  disk and several GiB of RAM (see each notebook's `cross-02` prerequisites
  cell), plus the platform pieces the notebook installs itself (QEMU/`hl`,
  Emscripten, WASI SDK, LLVM-MinGW, Wine).

## Working on the notebooks

Notebook **outputs are kept out of git**. Git filter config isn't cloned, so
after cloning activate the strip filter once:

```sh
make install-hooks
```

From then on cell outputs are stripped automatically on every commit (your
working copy keeps its rendered outputs). `make strip` cleans notebooks in
place; `make check` exits nonzero if any tracked notebook still carries outputs
or if a `notebook.py` generator no longer reproduces its sibling `.ipynb`
(see `make check-generators`); `make notebook` regenerates the notebooks;
`make flake-lock` updates `flake.lock` in the root and every `*.nb/` flake.

## License

Apache-2.0 — see [`LICENSE`](LICENSE). © Input Output Group.
