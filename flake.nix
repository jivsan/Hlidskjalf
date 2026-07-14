{
  description = "Hlidskjalf — self-hosted, multi-user Proxmox VE control panel";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-24.11";
    flake-utils.url = "github:numtide/flake-utils";
  };

  outputs = { self, nixpkgs, flake-utils }:
    flake-utils.lib.eachDefaultSystem (system:
      let
        pkgs = nixpkgs.legacyPackages.${system};
      in
      {
        packages = rec {
          hlidskjalf = pkgs.callPackage ./nix/package.nix { };
          default = hlidskjalf;
        };

        devShells.default = pkgs.mkShell {
          packages = with pkgs; [
            nodejs_22
            python312
            python312Packages.venvShellHook
            uv
          ];
          shellHook = ''
            echo "hlidskjalf dev shell"
            echo "  backend:  cd backend && uv venv && uv pip install -e . && source ../dev/dev.env"
            echo "  mock pve: cd dev && uvicorn mock_pve:app --port 18006"
            echo "  frontend: cd frontend && npm ci && npm run dev"
          '';
        };
      })
    // {
      nixosModules.hlidskjalf = import ./nix/module.nix self;
      nixosModules.default = self.nixosModules.hlidskjalf;
    };
}
