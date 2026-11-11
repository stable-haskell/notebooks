{
  description = "Jupyter notebook walkthrough: AArch64 Linux cross-compilation with Template Haskell (GHC 9.14 + Cabal)";

  inputs.flake-utils.url = "github:numtide/flake-utils";
  inputs.nixpkgs.url = "github:nixos/nixpkgs/release-25.05";
  inputs.jupyter.url = "github:kirelagin/jupyter.nix";
  inputs.rt-series.url = "github:zw3rk/rt-series";
  inputs.hyper-linux.url = "github:zw3rk/hyper-linux";

  outputs = { self, flake-utils, nixpkgs, jupyter, rt-series, hyper-linux }:
    flake-utils.lib.eachSystem
    [
      flake-utils.lib.system.aarch64-darwin
      flake-utils.lib.system.x86_64-darwin
      flake-utils.lib.system.x86_64-linux
      flake-utils.lib.system.aarch64-linux
    ]
    (system: let
      pkgs = nixpkgs.legacyPackages.${system};
      isLinux = pkgs.stdenv.hostPlatform.isLinux;

      # Host tools the notebook's bash cells call.
      hostTools = with pkgs; [
        pkgs.stdenv.cc
        bash
        coreutils # df, sha256sum, realpath, etc.
        gnumake
        curl
        cacert # TLS roots for curl over HTTPS
        git
        autoconf
        automake
        libtool
        pkg-config
        python3
        file
        xz
        zstd
        gnutar
        gzip
        gnused
        gnugrep
        findutils
        which
        gmp
        libffi
        zlib
        ncurses
      ] ++ pkgs.lib.optionals isLinux [ pkgs.patchelf ];

      # Cross-compilation toolchain for AArch64 Linux (rt-series pattern).
      # rt-series keeps its own locked nixpkgs (whose
      # pkgsCross.aarch64-multiplatform-musl toolchain is already
      # pre-built in /nix/store and in https://cache.zw3rk.com), so take
      # the toolchain from rt-series' nixpkgs, not the umbrella's.
      rtPkgs = rt-series.inputs.nixpkgs.legacyPackages.${system};
      a64Musl = rtPkgs.pkgsCross.aarch64-multiplatform-musl;
      crossTools = [
        a64Musl.stdenv.cc # aarch64-unknown-linux-musl-{gcc,g++,nm,objdump,readelf,ar,ranlib}
      ];

      # Target ELF runner: QEMU user emulation on Linux, hl from
      # hyper-linux on Apple Silicon macOS.
      linuxRunner =
        if isLinux
        then [ pkgs.qemu-user ]
        else [ ];
      darwinRunner =
        if system == "aarch64-darwin"
        then [ hyper-linux.packages.aarch64-darwin.default ]
        else [ ];

      runtimeTools = hostTools ++ crossTools ++ linuxRunner ++ darwinRunner;
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

      # Cross-toolchain locations exported to the notebook kernels and shell.
      aarch64Sysroot = "${a64Musl.musl}";
      aarch64LibffiInclude = "${a64Musl.libffi.dev}/include";
      aarch64LibffiLib = "${a64Musl.libffi.out}/lib";
      aarch64Prefix = "aarch64-unknown-linux-musl-";
      aarch64Cc = "${a64Musl.stdenv.cc}/bin/${aarch64Prefix}gcc";
      aarch64Cxx = "${a64Musl.stdenv.cc}/bin/${aarch64Prefix}g++";
      aarch64Nm = "${a64Musl.stdenv.cc}/bin/${aarch64Prefix}nm";
      aarch64Objdump = "${a64Musl.stdenv.cc}/bin/${aarch64Prefix}objdump";
      aarch64Readelf = "${a64Musl.stdenv.cc}/bin/${aarch64Prefix}readelf";

      kernelEnv = {
        PATH = "${runtimeBin}:/usr/bin:/bin:/usr/local/bin";
        CURL_CA_BUNDLE = caBundle;
        SSL_CERT_FILE = caBundle;
        CPATH = cpath;
        LIBRARY_PATH = libpath;
        PKG_CONFIG_PATH = pkgconfigpath;
        AARCH64_SYSROOT = aarch64Sysroot;
        AARCH64_LIBFFI_INCLUDE = aarch64LibffiInclude;
        AARCH64_LIBFFI_LIB = aarch64LibffiLib;
        AARCH64_CC = aarch64Cc;
        AARCH64_CXX = aarch64Cxx;
        AARCH64_NM = aarch64Nm;
        AARCH64_OBJDUMP = aarch64Objdump;
        AARCH64_READELF = aarch64Readelf;
        AARCH64_TARGET_PREFIX = aarch64Prefix;
      };

      jupyterlab = jupyter.lib.makeJupyterLab {
        inherit pkgs;
        kernels = {
          "python3".ipykernel = {
            packages = pp: with pp; [
            ];
            spec.env = kernelEnv;
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
              # jupyter-lab` (devShells.default).
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
          export PATH="${runtimeBin}:/usr/bin:/bin:/usr/local/bin:$PATH"
          export CURL_CA_BUNDLE="${caBundle}"
          export SSL_CERT_FILE="${caBundle}"
          export CPATH="${cpath}"
          export LIBRARY_PATH="${libpath}"
          export PKG_CONFIG_PATH="${pkgconfigpath}"
          export AARCH64_SYSROOT="${aarch64Sysroot}"
          export AARCH64_LIBFFI_INCLUDE="${aarch64LibffiInclude}"
          export AARCH64_LIBFFI_LIB="${aarch64LibffiLib}"
          export AARCH64_CC="${aarch64Cc}"
          export AARCH64_CXX="${aarch64Cxx}"
          export AARCH64_NM="${aarch64Nm}"
          export AARCH64_OBJDUMP="${aarch64Objdump}"
          export AARCH64_READELF="${aarch64Readelf}"
          export AARCH64_TARGET_PREFIX="${aarch64Prefix}"
          echo "aarch64-th-cross.nb development shell ready" >&2
          echo "  jupyter-lab   - start the notebook server" >&2
          echo "  nix run       - same, via the flake app" >&2
          echo "  TARGET_CC     - \$AARCH64_CC ($aarch64Prefix gcc)" >&2
        '';
      };
    });
}
