{
  description = "OpenWave - Linux control app for the Elgato Wave XLR";

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
          pythonEnv = pkgs.python3.withPackages (ps: [ ps.pygobject3 ]);
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
            # udev_installed() requires *every* product ID to be present. This
            # was hardcoded to 007d alone while UDEV_RULES also carries 0070
            # (Wave:3), so the check failed permanently: run_setup() called
            # install_udev() on every launch, pkexec-wrote the same file the
            # package already owns, and returned early on failure -- meaning the
            # WirePlumber and mix-sink configs after it never got installed
            # either. On NixOS that pkexec cannot succeed regardless, since
            # /etc/udev/rules.d/99-openwave.rules is a read-only store symlink.
            postInstall = ''
              mkdir -p $out/lib/udev/rules.d
              ${pythonEnv}/bin/python3 -c 'import ast,sys; t=ast.parse(open(sys.argv[1]).read()); v=next(n.value for n in t.body if isinstance(n,ast.Assign) and any(getattr(x,"id",None)=="UDEV_RULES" for x in n.targets)); sys.stdout.write("\n".join(ast.literal_eval(v))+"\n")' \
                "$out/${sitePkgs}/wavexlr/setup.py" \
                > $out/lib/udev/rules.d/99-openwave.rules

              # setup.py looks for the WirePlumber and mix-sink configs next to
              # the source tree, then under /usr/local and /usr. The Makefile put
              # them in $out/share/openwave, so on Nix all three candidates miss
              # and run_setup() dies with "WirePlumber rule source not found".
              # Retarget the FHS prefix at the real one; $out/share/openwave is
              # simply what PREFIX=/usr would have produced here.
              substituteInPlace "$out/${sitePkgs}/wavexlr/setup.py" \
                --replace-fail /usr/share/openwave "$out/share/openwave"

              # The desktop entry runs a bare `python3 -m wavexlr`, which only
              # works if wavexlr is already importable. It is not: the module
              # tree lives in $out and reaches the interpreter through the
              # wrapper's PYTHONPATH. So launching from a menu silently does
              # nothing while `openwave` in a shell works, because that is the
              # wrapper. Point Exec at it.
              substituteInPlace $out/share/applications/openwave.desktop \
                --replace-fail "python3 -m wavexlr" "$out/bin/openwave"

              # service.py builds ExecStart from shutil.which("python3"), and
              # the wrapper exports PYTHONPATH but never puts an interpreter on
              # PATH -- so which() finds nothing and the unit falls back to
              # /usr/bin/python3, which does not exist here. The service then
              # fails 203/EXEC and Restart=on-failure hides it in a loop, so the
              # app reports "Audio service not running" with no obvious cause.
              # $out/bin/openwave-daemon is the same entrypoint, already wrapped.
              substituteInPlace "$out/${sitePkgs}/wavexlr/service.py" \
                --replace-fail '{python} -c "from wavexlr.daemon import main; main()"' \
                               "$out/bin/openwave-daemon"
            '';

            # ctypes needs to find libusb; the module tree needs to be on
            # PYTHONPATH since it lives in $out, not inside the python env.
            dontWrapGApps = true;
            preFixup = ''
              wrapProgram $out/bin/openwave \
                --prefix PYTHONPATH : $out/${sitePkgs} \
                --prefix LD_LIBRARY_PATH : ${usbLibs} \
                "''${gappsWrapperArgs[@]}"

              # daemon entry point as a first-class binary, so a
              # declarative user service is trivial if ever wanted
              makeWrapper ${pythonEnv}/bin/python3 $out/bin/openwave-daemon \
                --add-flags "-m wavexlr.daemon" \
                --prefix PYTHONPATH : $out/${sitePkgs} \
                --prefix LD_LIBRARY_PATH : ${usbLibs}
            '';

            meta = {
              description = "Linux control application for the Elgato Wave XLR interface";
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
