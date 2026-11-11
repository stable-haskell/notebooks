{
  description = "Jupyter notebook walkthrough: Windows x86_64 cross-compilation with Template Haskell via Wine & iserv-proxy";

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

      # Wine carries the Windows execution environment for the TH
      # interpreter and the compiled Windows application. On macOS Wine
      # runs via Rosetta 2 from /Applications/Wine Stable.app or the
      # Homebrew cask (see the win-sdk cell); only Linux carries a Nix
      # Wine package here.
      wineTools = pkgs.lib.optionals (system == "x86_64-linux") [
        pkgs.wineWowPackages.minimal
      ];

      # Tools the notebook's bash cells call. The walkthrough builds GHC
      # 9.14 and a cross-aware Cabal from source, so the flake pins the
      # host-side helpers: the LLVM-MinGW UCRT SDK and libffi are fetched
      # and built by the notebook itself (pinned tags + hashes there).
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
        pkgs.file # `file arch.exe` smoke checks
        pkgs.xz # LLVM-MinGW + libffi tarballs unpack here
        pkgs.zstd
        pkgs.unzip
        pkgs.jq
        pkgs.gnutar
        pkgs.gzip
        pkgs.gnused
        pkgs.gnugrep
        pkgs.findutils
        pkgs.which
        # C libraries the native compiler build links against; the
        # Windows target libraries use a UCRT libffi built by the
        # notebook instead of this one.
        pkgs.gmp
        pkgs.libffi
        pkgs.zlib
        pkgs.ncurses
      ] ++ pkgs.lib.optionals pkgs.stdenv.isLinux [
        pkgs.patchelf # GHC's distribution step needs it on Linux
      ] ++ wineTools;
      runtimeBin = pkgs.lib.makeBinPath runtimeTools;
      caBundle = "${pkgs.cacert}/etc/ssl/certs/ca-bundle.crt";
      libDevs = [ pkgs.gmp.dev pkgs.libffi.dev pkgs.zlib.dev pkgs.ncurses.dev ];
      libOuts = [ pkgs.gmp.out pkgs.libffi.out pkgs.zlib.out pkgs.ncurses.out ];
      cpath = pkgs.lib.makeIncludePath libDevs;
      libpath = pkgs.lib.makeLibraryPath libOuts;
      pkgconfigpath = pkgs.lib.makeSearchPathOutput "dev" "lib/pkgconfig" libDevs;

      pythonEnv = {
        PATH = "${runtimeBin}:/usr/bin:/bin:/usr/sbin:/sbin:/usr/local/bin";
        CURL_CA_BUNDLE = caBundle;
        SSL_CERT_FILE = caBundle;
        CPATH = cpath;
        LIBRARY_PATH = libpath;
        PKG_CONFIG_PATH = pkgconfigpath;
      };

      jupyterlab = jupyter.lib.makeJupyterLab {
        inherit pkgs;
        kernels = {
          "python3".ipykernel = {
            packages = pp: with pp; [
            ];
            spec.env = pythonEnv;
          };
          "bash".kernelspec = let
            pythonWithBashKernel = pkgs.python3.withPackages (pp: [ pp.bash-kernel ]);
          in {
            spec = {
              argv = [ "${pythonWithBashKernel}/bin/python" "-m" "bash_kernel" "-f" "{connection_file}" ];
              display_name = "Bash";
              language = "bash";
              # Bake the runtime tools onto the kernel PATH so the notebook works
              # whether launched via `nix run` (apps.default) or `nix develop -c
              # jupyter-lab` (devShells.default). System dirs stay as a fallback.
              env = pythonEnv;
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
          echo "windows-th-cross.nb development shell ready" >&2
          echo "  jupyter-lab   - start the notebook server" >&2
          echo "  nix run       - same, via the flake app" >&2
          echo "" >&2
          echo "This walkthrough builds GHC 9.14 + a cross-aware Cabal from source." >&2
          echo "Allow 32 GiB of free disk (4 GiB to re-run against an existing build)." >&2
        '';
      };
    });
}
