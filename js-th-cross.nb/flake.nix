{
  description = "Jupyter notebook walkthrough: cross-compiling Haskell with Template Haskell to JavaScript (javascript-unknown-ghcjs)";

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

      # Tools the notebook's bash cells call. The walkthrough builds GHC 9.14
      # and a cross-aware Cabal from source, then targets
      # javascript-unknown-ghcjs: emscripten supplies the C toolchain (the JS
      # backend links through emcc/emar), Node.js 22+ hosts GHC's JS
      # interpreter (ghc-interp.js) while Template Haskell splices evaluate,
      # and runs the final `.jsexe/all.js` bundle. llvm-objdump/llvm-nm back
      # the target configure step; gmp/libffi/zlib/ncurses are the native
      # compiler's C dependencies (patchelf only exists on Linux).
      # Nixpkgs' emscripten wrapper exposes emcc/em++/emar/emranlib in bin/
      # but leaves emnm in share/emscripten/emnm; wrap it with the same
      # EM_CACHE-Exporting launcher as emcc.
      emnmWrapper = pkgs.runCommand "emnm-wrapper" {} ''
        mkdir -p "$out/bin"
        sed 's|/share/emscripten/emcc|/share/emscripten/emnm|' "${pkgs.emscripten}/bin/emcc" > "$out/bin/emnm"
        chmod +x "$out/bin/emnm"
      '';

      runtimeTools = [
        pkgs.stdenv.cc
        pkgs.bash
        pkgs.coreutils
        pkgs.gnumake
        pkgs.curl
        pkgs.cacert # TLS roots for curl over HTTPS
        pkgs.git
        pkgs.autoconf
        pkgs.automake
        pkgs.libtool
        pkgs.pkg-config
        pkgs.python3
        pkgs.file # `file` smoke checks on built artifacts
        pkgs.xz
        pkgs.zstd
        pkgs.unzip
        pkgs.jq
        pkgs.gnutar
        pkgs.gzip
        pkgs.gnused
        pkgs.gnugrep
        pkgs.findutils
        pkgs.which
        pkgs.nodejs_22 # Node.js 22+ — required by the JS backend + runner
        pkgs.emscripten # emcc, em++, emar, emranlib
        emnmWrapper # emnm
        pkgs.llvmPackages.llvm # llvm-objdump / llvm-nm for the target configure step
        pkgs.gmp
        pkgs.libffi
        pkgs.zlib
        pkgs.ncurses
      ] ++ pkgs.lib.optionals pkgs.stdenv.isLinux [
        pkgs.patchelf # GHC's distribution step (Linux only)
      ];
      runtimeBin = pkgs.lib.makeBinPath runtimeTools;
      caBundle = "${pkgs.cacert}/etc/ssl/certs/ca-bundle.crt";
      # Emscripten wants a writable cache directory. The notebook's gate.env
      # overrides this per build with $CROSS_ROOT/em-cache; this default only
      # covers emcc invocations before gate.env is loaded.
      emCacheDefault = "/tmp/js-th-cross-em-cache";

      libDevs = [ pkgs.gmp.dev pkgs.libffi.dev pkgs.zlib.dev pkgs.ncurses.dev ];
      libOuts = [ pkgs.gmp.out pkgs.libffi.out pkgs.zlib.out pkgs.ncurses.out ];
      cpath = pkgs.lib.makeIncludePath libDevs;
      libpath = pkgs.lib.makeLibraryPath libOuts;
      pkgconfigpath = pkgs.lib.makeSearchPathOutput "dev" "lib/pkgconfig" libDevs;

      kernelEnv = {
        PATH = "${runtimeBin}:/usr/bin:/bin:/usr/sbin:/sbin:/usr/local/bin";
        CURL_CA_BUNDLE = caBundle;
        SSL_CERT_FILE = caBundle;
        EM_CACHE = emCacheDefault;
        CPATH = cpath;
        LIBRARY_PATH = libpath;
        PKG_CONFIG_PATH = pkgconfigpath;
      };

      pythonWithBashKernel = pkgs.python3.withPackages (pp: [ pp.bash-kernel ]);

      jupyterlab = jupyter.lib.makeJupyterLab {
        inherit pkgs;
        kernels = {
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

      # `direnv allow` / `nix develop` yields a usable ad-hoc shell with the same
      # tools the notebook uses, and `nix develop -c jupyter-lab` starts the
      # server with everything on PATH.
      devShells.default = pkgs.mkShell {
        packages = [ jupyterlab ] ++ runtimeTools;
        shellHook = ''
          export CURL_CA_BUNDLE="${caBundle}"
          export SSL_CERT_FILE="${caBundle}"
          export CPATH="${cpath}"
          export LIBRARY_PATH="${libpath}"
          export PKG_CONFIG_PATH="${pkgconfigpath}"
          mkdir -p "${emCacheDefault}"
          export EM_CACHE="${emCacheDefault}"
          echo "js-th-cross.nb development shell ready" >&2
          echo "  jupyter-lab   - start the notebook server" >&2
          echo "  nix run       - same, via the flake app" >&2
          echo "" >&2
          echo "This walkthrough builds GHC 9.14 + a cross-aware Cabal from source." >&2
          echo "Allow 32 GiB free disk (4 GiB suffices once the compiler is built)." >&2
        '';
      };
    });
}
