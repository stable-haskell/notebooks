{
  description = "Jupyter notebook walkthrough for the stable-haskell wasm GHC cross-compiler";

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

      # Tools the notebook's bash cells call. ghcup itself is intentionally NOT
      # here: this walkthrough drives the *user's* system ghcup (the standard
      # Haskell toolchain installer, the same one the landing page assumes), so
      # the notebook can install/manage real cross-compiler bindists. The flake
      # only pins the host-side helpers — most importantly Node.js 22+, which
      # the GHC wasm backend needs for Template Haskell, post-link, and running.
      runtimeTools = [
        pkgs.bash
        pkgs.coreutils      # realpath -m (used by the template Makefile), du, etc.
        pkgs.gnumake
        pkgs.curl
        pkgs.cacert         # TLS roots for curl over HTTPS
        pkgs.nodejs_22      # Node.js 22+ — required by the wasm backend + runner
        pkgs.python3        # `python3 -m http.server` for `make run-web`
        pkgs.gnutar
        pkgs.gzip
        pkgs.gnused
        pkgs.gnugrep
        pkgs.findutils
        pkgs.which
      ];
      runtimeBin = pkgs.lib.makeBinPath runtimeTools;
      caBundle = "${pkgs.cacert}/etc/ssl/certs/ca-bundle.crt";

      jupyterlab = jupyter.lib.makeJupyterLab {
        inherit pkgs;
        kernels = {
          "python3".ipykernel = {
            packages = pp: with pp; [
            ];
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
              # jupyter-lab` (devShells.default). The notebook's §1 setup cell
              # then prepends `~/.ghcup/bin` so the user's ghcup-managed ghc/cabal
              # win over anything here. System dirs are kept as a fallback.
              env = {
                PATH = "${runtimeBin}:/usr/bin:/bin:/usr/local/bin";
                CURL_CA_BUNDLE = caBundle;
                SSL_CERT_FILE = caBundle;
              };
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
          echo "wasm-hello.nb development shell ready" >&2
          echo "  jupyter-lab   - start the notebook server" >&2
          echo "  nix run       - same, via the flake app" >&2
          echo "" >&2
          echo "Note: this walkthrough uses your system ghcup (~/.ghcup/bin)." >&2
          echo "      Install it from https://www.haskell.org/ghcup/ if missing." >&2
        '';
      };
    });
}
