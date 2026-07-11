# Builds the frontend with buildNpmPackage, then the backend as a Python app
# that serves the built SPA (HLIDSKJALF_STATIC_DIR baked into the wrapper).
{ lib
, buildNpmPackage
, python312Packages
, makeWrapper
}:

let
  frontend = buildNpmPackage {
    pname = "hlidskjalf-frontend";
    version = "0.1.0";
    src = ../frontend;

    # Refresh after every package-lock.json change:
    #   nix build .#hlidskjalf 2>&1 | grep 'got:'  (or prefetch-npm-deps)
    npmDepsHash = lib.fakeHash;

    installPhase = ''
      runHook preInstall
      cp -r dist $out
      runHook postInstall
    '';
  };
in
python312Packages.buildPythonApplication {
  pname = "hlidskjalf";
  version = "0.1.0";
  pyproject = true;
  src = ../backend;

  nativeBuildInputs = [ makeWrapper ];
  build-system = [ python312Packages.hatchling ];

  dependencies = with python312Packages; [
    fastapi
    uvicorn
    httpx
    websockets
    aiosqlite
    argon2-cffi
    pydantic-settings
    itsdangerous
  ];

  postFixup = ''
    wrapProgram $out/bin/hlidskjalf \
      --set-default HLIDSKJALF_STATIC_DIR ${frontend}
  '';

  passthru = { inherit frontend; };

  meta = {
    description = "Self-hosted Proxmox VPS panel — Odin's high seat over hella";
    mainProgram = "hlidskjalf";
  };
}
