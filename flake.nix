{
  description = "OpenWave - The audio mixing matrix for Linux";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";

  outputs =
    { self, nixpkgs }:
    let
      forAllSystems = nixpkgs.lib.genAttrs [ "x86_64-linux" "aarch64-linux" ];
    in
    {
      packages = forAllSystems (
        system:
        let
          pkgs = nixpkgs.legacyPackages.${system};
          pythonEnv = pkgs.python3.withPackages (ps: [ ps.pygobject3 ps.xlib ]);
          sitePkgs = pkgs.python3.sitePackages; # "lib/python3.X/site-packages"
          usbLibs = pkgs.lib.makeLibraryPath [ pkgs.libusb1 ];
        in
        rec {
          openwave = pkgs.stdenv.mkDerivation {
            pname = "openwave";
            version = "1.0.0";
            src = self;

            nativeBuildInputs = with pkgs; [
              makeWrapper
              wrapGAppsHook4
              gobject-introspection
            ];
            buildInputs = with pkgs; [
              gtk4
              libadwaita
            ];

            dontBuild = true;
            # The Makefile derives SITEPKG from the interpreter, which is
            # a read-only store path here -- override it onto $out and
            # point the generated launcher at the pygobject python.
            installFlags = [
              "PREFIX=${placeholder "out"}"
              "SITEPKG=${placeholder "out"}/${sitePkgs}"
              "PYTHON=${pythonEnv}/bin/python3"
            ];

            # Declarative version of the rule wavexlr/setup.py writes on
            # first run -- consume via services.udev.packages on NixOS
            # and the in-app permission check passes out of the box.
            #
            # Generated from setup.py's UDEV_RULES rather than restated, because
            # udev_installed() requires *every* product ID to be present, and
            # both it and the rules now derive from profiles.PROFILES — a new
            # device cannot be missing from either. (The old literal_eval AST
            # extraction is gone: UDEV_RULES is computed, so it is imported.)
            # On NixOS the in-app pkexec cannot write the rule regardless,
            # since /etc/udev/rules.d/99-openwave.rules would be a read-only
            # store symlink — consume this via services.udev.packages instead.
            postInstall = ''
              mkdir -p $out/lib/udev/rules.d
              PYTHONPATH=$out/${sitePkgs} ${pythonEnv}/bin/python3 -c \
                'from wavexlr.setup import UDEV_RULES; print("\n".join(UDEV_RULES))' \
                > $out/lib/udev/rules.d/99-openwave.rules

              # setup.py looks for the WirePlumber and mix-sink configs next to
              # the source tree, then under /usr/local and /usr. The Makefile put
              # them in $out/share/openwave, so on Nix all three candidates miss
              # and run_setup() dies with "WirePlumber rule source not found".
              # Retarget the FHS prefix at the real one; $out/share/openwave is
              # simply what PREFIX=/usr would have produced here.
            '';

            # ctypes needs to find libusb; the module tree needs to be on
            # PYTHONPATH since it lives in $out, not inside the python env.
            dontWrapGApps = true;
            preFixup = ''
              wrapProgram $out/bin/openwave \
                --prefix PYTHONPATH : $out/${sitePkgs} \
                --prefix LD_LIBRARY_PATH : ${usbLibs} \
                "''${gappsWrapperArgs[@]}"

              # The Makefile installs this launcher too; it just needs the same
              # import path as the GUI one. service.py points ExecStart at it.
              wrapProgram $out/bin/openwave-daemon \
                --prefix PYTHONPATH : $out/${sitePkgs} \
                --prefix LD_LIBRARY_PATH : ${usbLibs}
            '';

            meta = {
              description = "The audio mixing matrix for Linux — per-app mixes, per-mix outputs, Elgato Wave control";
              homepage = "https://github.com/rikkichy/openwave";
              license = pkgs.lib.licenses.mit;
              mainProgram = "openwave";
              platforms = pkgs.lib.platforms.linux;
            };
          };
          default = openwave;
        }
      );
    };
}
