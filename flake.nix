{
  description = "stable-haskell notebooks — umbrella flake for CI, caching, and dev shells";

  inputs = {
    flake-utils.url = "github:numtide/flake-utils";
    nixpkgs.url = "github:nixos/nixpkgs/release-25.05";
    rt-series = {
      url = "github:zw3rk/rt-series";
    };
    hyper-linux = {
      url = "github:zw3rk/hyper-linux";
    };
  };

  outputs = { self, flake-utils, nixpkgs, rt-series, hyper-linux }:
    flake-utils.lib.eachSystem
    [
      flake-utils.lib.system.aarch64-darwin
      flake-utils.lib.system.x86_64-darwin
      flake-utils.lib.system.x86_64-linux
      flake-utils.lib.system.aarch64-linux
    ]
    (system:
      let
        pkgs = nixpkgs.legacyPackages.${system};
        lib = pkgs.lib;
        isLinux = pkgs.stdenv.isLinux;

        # The aarch64-unknown-linux-musl cross toolchain, the same
        # pkgsCross family rt-series uses for its own dev shell
        # (pkgsCross.aarch64-multiplatform-musl). Target libc, RTS
        # headers, and musl sysroot work comes from here when the
        # notebooks build aarch64 target libraries outside QEMU.
        # rt-series keeps its own locked nixpkgs (whose
        # pkgsCross.aarch64-multiplatform-musl toolchain is already
        # pre-built in /nix/store and in https://cache.zw3rk.com), so
        # take the toolchain from rt-series' nixpkgs, not the
        # umbrella's.
        rtPkgs = rt-series.inputs.nixpkgs.legacyPackages.${system};
        aarch64Musl = rtPkgs.pkgsCross.aarch64-multiplatform-musl;
      in {
        devShells = {
          # Everyday repo work: regenerate/strip/check notebooks and
          # lint the repo (`make notebook`, `make strip`, `make check`).
          default = pkgs.mkShell {
            name = "notebooks";
            packages = [
              pkgs.python3
              pkgs.gnumake
              pkgs.bash
              pkgs.git
              pkgs.jq
              pkgs.shellcheck
              pkgs.actionlint
            ];
            shellHook = ''
              echo "stable-haskell notebooks shell ready" >&2
              echo "  make notebook   regenerate notebooks from their notebook.py" >&2
              echo "  make strip      strip cell outputs in place" >&2
              echo "  make check      fail if any notebook carries outputs" >&2
            '';
          };

          # Unified host & cross-compilation environment for CI: builds
          # the shared GHC 9.14 stage2 + cross-aware Cabal toolchain
          # once (~/cross-ghc-current, "CROSS_ROOT") and reuses it
          # across all *-th-cross.nb notebooks.
          #
          #   nix develop .#ci-cross -c bash -c '...'
          #
          # Host build tools first, then the target pieces: the musl
          # cross toolchain (rt-series family) everywhere, patchelf on
          # Linux, and hyper-linux (`hl`) on aarch64-darwin, where it
          # runs aarch64-linux ELF target interpreters via
          # Hypervisor.framework instead of QEMU.
          ci-cross =
            let
              libDevs = [ pkgs.gmp.dev pkgs.libffi.dev pkgs.zlib.dev pkgs.ncurses.dev ];
              libOuts = [ pkgs.gmp.out pkgs.libffi.out pkgs.zlib.out pkgs.ncurses.out ];
              cpath = lib.makeIncludePath libDevs;
              libpath = lib.makeLibraryPath libOuts;
              pkgconfigpath = lib.makeSearchPathOutput "dev" "lib/pkgconfig" libDevs;
            in
            pkgs.mkShell {
              name = "ci-cross";
              packages = [
                pkgs.autoconf
                pkgs.automake
                pkgs.libtool
                pkgs.pkg-config
                pkgs.gnumake
                pkgs.python3
                pkgs.git
                pkgs.curl
                pkgs.cacert
                pkgs.xz
                pkgs.zstd
                pkgs.unzip
                pkgs.jq
                pkgs.file
                pkgs.gmp
                pkgs.libffi
                pkgs.zlib
                pkgs.ncurses
                pkgs.stdenv.cc
                aarch64Musl.stdenv.cc
              ]
              ++ lib.optionals isLinux [ pkgs.patchelf ]
              ++ lib.optionals (system == "aarch64-darwin")
                [ hyper-linux.packages.aarch64-darwin.default ];
              shellHook = ''
                export CURL_CA_BUNDLE="${pkgs.cacert}/etc/ssl/certs/ca-bundle.crt"
                export SSL_CERT_FILE="$CURL_CA_BUNDLE"
                export CPATH="${cpath}"
                export LIBRARY_PATH="${libpath}"
                export PKG_CONFIG_PATH="${pkgconfigpath}"
                echo "ci-cross shell ready (GHC 9.14 stage2 + cross-aware Cabal toolchain)" >&2
              '';
            };
        };

        checks = {
          # `nix flake check -L`: the same stripped-notebook guarantee
          # as `make check` in CI, inside a derivation.
          notebooks-stripped = pkgs.runCommand "notebooks-stripped"
            {
              nativeBuildInputs = [ pkgs.gnumake pkgs.bash pkgs.python3 ];
            } ''
            cd ${self}
            make check
            mkdir -p "$out"
            echo OK > "$out/status"
          '';
        };
      });
}
