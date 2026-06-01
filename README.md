# stable-haskell notebooks

Runnable Jupyter walkthroughs for the [stable-haskell](https://stable-haskell.github.io/)
toolchain.

Each notebook is a self-contained `*.nb/` directory with its own Nix flake that
provides JupyterLab, a Bash kernel, and exactly the host-side tools the notebook
needs. There is nothing to install globally — `nix develop -c jupyter-lab`
(or `nix run`) inside a notebook directory drops you into a ready-to-run
environment.

## Notebooks

| Notebook | What it does |
|---|---|
| [`wasm-hello.nb`](wasm-hello.nb/) | Install the stable-haskell **wasm32-wasi GHC** cross-compiler via `ghcup`, build the *hello* reactor template to a `.wasm`, and run it under Node.js and in the browser — from a single `cabal build` that drives two compilers (native build side + wasm target). Companion to <https://stable-haskell.github.io/ghc/>. |

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

## Working on the notebooks

Notebook **outputs are kept out of git**. Git filter config isn't cloned, so
after cloning activate the strip filter once:

```sh
make install-hooks
```

From then on cell outputs are stripped automatically on every commit (your
working copy keeps its rendered outputs). `make strip` cleans notebooks in
place; `make check` exits nonzero if any tracked notebook still carries outputs.

## License

Apache-2.0 — see [`LICENSE`](LICENSE). © Input Output Group.
