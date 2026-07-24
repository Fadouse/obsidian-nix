# obsidian-nix

A small, independently maintained Nix package for the official Obsidian x86_64 Linux release.

## Goals

- package the current official stable tar archive directly;
- use the exact Electron runtime shipped by Obsidian;
- avoid `nixpkgs#obsidian`, external Electron packages, AppImage/FHS wrappers, and `flake-utils`;
- reuse only the host nixpkgs input for ELF patching and required native libraries;
- support native Wayland and Fcitx5 while retaining an automatic X11 fallback;
- check Obsidian's official GitHub release daily and propose reviewed updates through GitHub Actions.

The package intentionally targets NixOS on `x86_64-linux`. On Wayland it enables text-input-v3 for Fcitx5. Set `OBSIDIAN_USE_X11=1` to force the X11 backend for troubleshooting.

## Use

```bash
nix run github:Fadouse/obsidian-nix
```

To share the parent system's nixpkgs input:

```nix
obsidian-nix = {
  url = "github:Fadouse/obsidian-nix";
  inputs.nixpkgs.follows = "nixpkgs";
};
```

## Update manually

```bash
python3 -m unittest discover -s tests -v
python3 scripts/update.py
nix flake check --print-build-logs
```

The scheduled workflow performs these checks and opens or refreshes an update pull request. It never publishes a Nix closure, application archive, or GitHub build artifact.

## License and security

The packaging code in this repository is MIT licensed. Obsidian is proprietary software governed by its own terms. This repository contains no Obsidian binaries; builds download the application from Obsidian's official GitHub release and mark it unfree.

Community plugins execute with the user's filesystem and network permissions and should be treated as trusted native code.
