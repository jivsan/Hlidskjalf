# Builds the frontend with buildNpmPackage, then the backend as a Python app
# that serves the built SPA (HLIDSKJALF_STATIC_DIR baked into the wrapper).
#
# `dependencies` below must stay in step with backend/pyproject.toml. It did not,
# once: `cryptography` was missing, and since Nix gives a Python app ONLY the
# packages listed here, the panel would have built cleanly and then died on its
# first import of secretbox.py. `pythonImportsCheck` now imports the app at build
# time so that failure happens in the builder, not in production —
# backend/tests/test_nix_package.py catches the same drift without needing Nix.
{ lib
, buildNpmPackage
, python3Packages
, makeWrapper
}:

let
  version = "0.5.5-alpha";

  frontend = buildNpmPackage {
    pname = "hlidskjalf-frontend";
    inherit version;
    src = ../frontend;

    # Refresh after every package-lock.json change:
    #   nix build .#hlidskjalf 2>&1 | grep 'got:'
    # or, without a full build:
    #   nix run nixpkgs#prefetch-npm-deps -- frontend/package-lock.json
    npmDepsHash = "sha256-2Y9r+QDqZEWieQLoTIDTQpZs8nZn7yoSDT3sT6WyejA=";

    installPhase = ''
      runHook preInstall
      cp -r dist $out
      runHook postInstall
    '';
  };
in
python3Packages.buildPythonApplication {
  pname = "hlidskjalf";
  inherit version;
  pyproject = true;
  src = ../backend;

  nativeBuildInputs = [ makeWrapper ];
  build-system = [ python3Packages.hatchling ];

  # Keep in step with backend/pyproject.toml [project].dependencies.
  dependencies = with python3Packages; [
    fastapi
    uvicorn
    httpx
    websockets
    aiosqlite
    argon2-cffi
    pydantic-settings
    itsdangerous
    cryptography        # secretbox.py — encryption at rest for the PVE token
  ];

  # The backend's own tests need a live mock PVE on a socket; that is a poor fit
  # for a sandboxed builder. Prove the app imports instead — that is what catches
  # a dependency missing from the list above.
  doCheck = false;
  pythonImportsCheck = [ "hlidskjalf" "hlidskjalf.main" "hlidskjalf.secretbox" ];

  postFixup = ''
    wrapProgram $out/bin/hlidskjalf \
      --set-default HLIDSKJALF_STATIC_DIR ${frontend}
  '';

  passthru = { inherit frontend; };

  meta = {
    description = "Self-hosted, multi-user Proxmox VE control panel";
    mainProgram = "hlidskjalf";
  };
}
