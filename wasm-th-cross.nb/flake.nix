{
  description = "Jupyter notebook walkthrough: cross-compiling Haskell with Template Haskell to WebAssembly/WASI";

  inputs.flake-utils.url = "github:numtide/flake-utils";
  inputs.nixpkgs.url = "github:nixos/nixpkgs/release-25.05";
  inputs.jupyter.url = "github:kirelagin/jupyter.nix";

  outputs = { self, flake-utils, nixpkgs, jupyter }:
    flake-utils.lib.eachSystem
    [
      flake-utils.lib.system.aarch64-darwin
      flake-utils.lib.system.x86_64-darwin
      flake-utils.lib.system.x86_64-linux
      flake-utils.lib.system.aarch64-linux
    ]
    (system: let
      pkgs = nixpkgs.legacyPackages.${system};
      stdenv = pkgs.stdenv;

      # Tools the notebook's bash cells call. The compilers themselves are
      # built from source inside the walkthrough (stable-haskell GHC 9.14 +
      # cross-aware Cabal); the wasi-sdk C toolchain is fetched by the
      # notebook. The flake pins the host-side helpers — most importantly
      # Node.js 22+, which the wasm backend needs for Template Haskell
      # (dyld.mjs), post-link, and running, plus wasmtime for executing the
      # final WASI modules and the LLVM tools GHC shells out to.
      runtimeTools = [
        pkgs.stdenv.cc
        pkgs.bash
        pkgs.coreutils # realpath -m, du, etc.
        pkgs.gnumake
        pkgs.curl
        pkgs.cacert # TLS roots for curl over HTTPS
        pkgs.git
        pkgs.autoconf
        pkgs.automake
        pkgs.libtool
        pkgs.pkg-config
        pkgs.python3 # venv/pip fallback for the wasmtime runner helper
        pkgs.file # `file cross-report.wasm` smoke checks
        pkgs.xz # toolchain tarball extraction
        pkgs.zstd
        pkgs.unzip # ghc-wasm-meta bootstrap.sh needs it
        pkgs.jq # ghc-wasm-meta bootstrap.sh needs it
        pkgs.gnutar
        pkgs.gzip
        pkgs.gnused
        pkgs.gnugrep
        pkgs.findutils
        pkgs.which
        pkgs.nodejs_22 # Node.js 22+ — required by the wasm backend + dyld.mjs
        pkgs.wasmtime # runs the compiled WASI `cross-report.wasm` directly
        pkgs.llvmPackages.llvm # llc, opt, llvm-as, llvm-nm, llvm-ar, llvm-objdump
        pkgs.llvmPackages.lld # wasm-ld
        pkgs.gmp
        pkgs.libffi
        pkgs.zlib
        pkgs.ncurses
      ] ++ pkgs.lib.optionals stdenv.isLinux [
        pkgs.patchelf # GHC's distribution step needs it on Linux
      ];
      runtimeBin = pkgs.lib.makeBinPath runtimeTools;
      caBundle = "${pkgs.cacert}/etc/ssl/certs/ca-bundle.crt";

      # Native C dependencies above live in the Nix store, which a system
      # `cc` cannot see on its own. Export the search paths both kernels and
      # the dev shell need so GHC's configure finds gmp/libffi/zlib/ncurses.
      libDevs = [ pkgs.gmp.dev pkgs.libffi.dev pkgs.zlib.dev pkgs.ncurses.dev ];
      libOuts = [ pkgs.gmp.out pkgs.libffi.out pkgs.zlib.out pkgs.ncurses.out ];
      cpath = pkgs.lib.makeIncludePath libDevs;
      libpath = pkgs.lib.makeLibraryPath libOuts;
      pkgconfigpath = pkgs.lib.makeSearchPathOutput "dev" "lib/pkgconfig" libDevs;

      kernelEnv = {
        PATH = "${runtimeBin}:/usr/bin:/bin:/usr/sbin:/sbin:/usr/local/bin";
        CURL_CA_BUNDLE = caBundle;
        SSL_CERT_FILE = caBundle;
        CPATH = cpath;
        LIBRARY_PATH = libpath;
        PKG_CONFIG_PATH = pkgconfigpath;
      };

      pythonWithBashKernel = pkgs.python3.withPackages (pp: [ pp.bash-kernel ]);

      jupyterlab = jupyter.lib.makeJupyterLab {
        inherit pkgs;
        kernels = {
          # The walkthrough runs on this kernel: every executable cell starts
          # with `%%bash`, and IPython raises a cell failure on nonzero exit.
          "python3".ipykernel = {
            packages = pp: with pp; [
            ];
            spec.env = kernelEnv;
          };
          "bash".kernelspec = {
            spec = {
              argv = [ "${pythonWithBashKernel}/bin/python" "-m" "bash_kernel" "-f" "{connection_file}" ];
              display_name = "Bash";
              language = "bash";
              # Bake the runtime tools onto the kernel PATH so the notebook
              # works whether launched via `nix run` (apps.default) or
              # `nix develop -c jupyter-lab` (devShells.default).
              env = kernelEnv;
            };
          };
        };
      };
    in {
      packages.default = jupyterlab;
      apps.default = {
        type = "app";
        program = "${jupyterlab}/bin/jupyter-lab";
      };

      # `direnv allow` / `nix develop` yields a usable ad-hoc shell with the
      # same tools the notebook uses, and `nix develop -c jupyter-lab` starts
      # the server with everything on PATH.
      devShells.default = pkgs.mkShell {
        packages = [ jupyterlab ] ++ runtimeTools;
        shellHook = ''
          export CURL_CA_BUNDLE="${caBundle}"
          export SSL_CERT_FILE="${caBundle}"
          export CPATH="${cpath}"
          export LIBRARY_PATH="${libpath}"
          export PKG_CONFIG_PATH="${pkgconfigpath}"
          echo "wasm-th-cross.nb development shell ready" >&2
          echo "  jupyter-lab   - start the notebook server" >&2
          echo "  nix run       - same, via the flake app" >&2
          echo "" >&2
          echo "This walkthrough builds GHC 9.14 + cross-aware Cabal from source." >&2
          echo "Allow 32 GiB free disk (4 GiB to re-run once built)." >&2
        '';
      };
    });
}
