{
  description = "Development environment for Clair Edge";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";

  outputs = { nixpkgs, ... }:
    let
      systems = [ "x86_64-linux" "aarch64-linux" "x86_64-darwin" "aarch64-darwin" ];
    in {
      devShells = nixpkgs.lib.genAttrs systems (system: {
        default = let pkgs = import nixpkgs { inherit system; }; in
          pkgs.mkShell {
            packages = with pkgs; [ python313 uv sqlite curl ];
          };
      });
    };
}
