{
  description = "Minimal package for the official Obsidian Linux release";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-26.05";

  outputs =
    { self, nixpkgs, ... }:
    let
      system = "x86_64-linux";
      pkgs = import nixpkgs {
        inherit system;
        config.allowUnfree = true;
      };
      obsidian = pkgs.callPackage ./package.nix { };
    in
    {
      packages.${system} = {
        inherit obsidian;
        default = obsidian;
      };

      checks.${system} = {
        package = obsidian;

        updater-tests =
          pkgs.runCommand "obsidian-updater-tests"
            {
              nativeBuildInputs = [ pkgs.python3 ];
            }
            ''
              cp -r ${self} source
              chmod -R u+w source
              cd source
              python3 -m unittest discover -s tests -v
              touch "$out"
            '';
      };

      formatter.${system} = pkgs.nixfmt;
    };
}
