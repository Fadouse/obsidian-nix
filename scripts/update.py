#!/usr/bin/env python3
"""Update package.nix to the latest official stable Obsidian release."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import struct
import sys
import tarfile
import tempfile
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "package.nix"
RELEASE_API = "https://api.github.com/repos/obsidianmd/obsidian-releases/releases/latest"
USER_AGENT = "obsidian-nix-updater/1"
MAX_DOWNLOAD_BYTES = 512 * 1024 * 1024
MAX_UNPACKED_BYTES = 1024 * 1024 * 1024
MAX_ARCHIVE_MEMBERS = 5000
VERSION_RE = re.compile(r"\d+\.\d+\.\d+")


@dataclass(frozen=True)
class Release:
    version: str
    url: str
    sha256_hex: str


def request(url: str) -> urllib.request.Request:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": USER_AGENT,
        "X-GitHub-Api-Version": "2022-11-28",
    }
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return urllib.request.Request(url, headers=headers)


def fetch_release_payload() -> dict[str, Any]:
    with urllib.request.urlopen(request(RELEASE_API), timeout=30) as response:
        payload = json.load(response)
    if not isinstance(payload, dict):
        raise ValueError("GitHub returned a non-object release payload")
    return payload


def parse_release(payload: dict[str, Any]) -> Release:
    if payload.get("draft") is not False or payload.get("prerelease") is not False:
        raise ValueError("Latest GitHub release is not a stable published release")

    tag = payload.get("tag_name")
    if not isinstance(tag, str) or not tag.startswith("v"):
        raise ValueError("Release tag does not start with v")
    version = tag[1:]
    if VERSION_RE.fullmatch(version) is None:
        raise ValueError(f"Unsupported release version: {version!r}")

    expected_name = f"obsidian-{version}.tar.gz"
    expected_url = (
        "https://github.com/obsidianmd/obsidian-releases/releases/"
        f"download/v{version}/{expected_name}"
    )
    assets = payload.get("assets")
    if not isinstance(assets, list):
        raise ValueError("Release does not contain an asset list")

    matches = [asset for asset in assets if isinstance(asset, dict) and asset.get("name") == expected_name]
    if len(matches) != 1:
        raise ValueError(f"Expected exactly one {expected_name} asset")

    asset = matches[0]
    url = asset.get("browser_download_url")
    if url != expected_url:
        raise ValueError(f"Unexpected release asset URL: {url!r}")

    digest = asset.get("digest")
    if not isinstance(digest, str) or re.fullmatch(r"sha256:[0-9a-f]{64}", digest) is None:
        raise ValueError("Release asset is missing a valid GitHub SHA-256 digest")

    return Release(version=version, url=url, sha256_hex=digest.removeprefix("sha256:"))


def read_package_metadata(path: Path = PACKAGE) -> tuple[str, str, str]:
    text = path.read_text(encoding="utf-8")
    versions = re.findall(r'^  version = "([^"]+)";$', text, flags=re.MULTILINE)
    hashes = re.findall(r'^    hash = "(sha256-[^"]+)";$', text, flags=re.MULTILINE)
    if len(versions) != 1 or len(hashes) != 1:
        raise ValueError("package.nix must contain exactly one version and source hash")
    return text, versions[0], hashes[0]


def sri_to_hex(sri: str) -> str:
    if not sri.startswith("sha256-"):
        raise ValueError("Only SHA-256 SRI hashes are supported")
    try:
        raw = base64.b64decode(sri.removeprefix("sha256-"), validate=True)
    except (ValueError, base64.binascii.Error) as error:
        raise ValueError("Invalid SRI hash") from error
    if len(raw) != hashlib.sha256().digest_size:
        raise ValueError("Invalid SHA-256 digest length")
    return raw.hex()


def hex_to_sri(digest_hex: str) -> str:
    raw = bytes.fromhex(digest_hex)
    if len(raw) != hashlib.sha256().digest_size:
        raise ValueError("Invalid SHA-256 digest length")
    return "sha256-" + base64.b64encode(raw).decode("ascii")


def download_asset(release: Release, destination: Path) -> str:
    digest = hashlib.sha256()
    total = 0
    with urllib.request.urlopen(request(release.url), timeout=120) as response:
        length = response.headers.get("Content-Length")
        if length is not None and int(length) > MAX_DOWNLOAD_BYTES:
            raise ValueError("Release asset is larger than the configured limit")

        with destination.open("wb") as output:
            while chunk := response.read(1024 * 1024):
                total += len(chunk)
                if total > MAX_DOWNLOAD_BYTES:
                    raise ValueError("Release asset exceeded the configured limit")
                digest.update(chunk)
                output.write(chunk)

    if total == 0:
        raise ValueError("Downloaded release asset is empty")
    return digest.hexdigest()


def _unsafe_member_name(name: str) -> bool:
    if not name or name.startswith("/") or "\\" in name or "\x00" in name:
        return True
    # Reject spelling aliases such as a//b, a/./b and a/../b. Tar extraction
    # canonicalizes these, so accepting them would make duplicate detection
    # differ from the tree that is actually built.
    return any(part in ("", ".", "..") for part in name.split("/"))


def _validate_x86_64_elf(archive: tarfile.TarFile, member: tarfile.TarInfo, label: str) -> None:
    extracted = archive.extractfile(member)
    if extracted is None:
        raise ValueError(f"Could not read {label}")
    header = extracted.read(64)
    if len(header) != 64:
        raise ValueError(f"{label} has a truncated ELF header")

    ident = header[:16]
    if ident[:4] != b"\x7fELF" or ident[4:7] != bytes((2, 1, 1)):
        raise ValueError(f"{label} is not a little-endian ELF64 file")
    file_type, machine, elf_version = struct.unpack_from("<HHI", header, 16)
    header_size = struct.unpack_from("<H", header, 52)[0]
    if file_type not in (2, 3) or machine != 62 or elf_version != 1 or header_size != 64:
        raise ValueError(f"{label} is not a valid x86_64 executable")


def validate_archive(path: Path, version: str) -> None:
    root = f"obsidian-{version}"
    required = {
        f"{root}/obsidian",
        f"{root}/obsidian-cli",
        f"{root}/resources/app.asar",
        f"{root}/resources/obsidian.asar",
        f"{root}/resources/icon.png",
    }

    with tarfile.open(path, mode="r:gz") as archive:
        members = archive.getmembers()
        if not members or len(members) > MAX_ARCHIVE_MEMBERS:
            raise ValueError("Archive has an invalid member count")
        if sum(member.size for member in members) > MAX_UNPACKED_BYTES:
            raise ValueError("Archive expands beyond the configured limit")

        by_name: dict[str, tarfile.TarInfo] = {}
        for member in members:
            if _unsafe_member_name(member.name):
                raise ValueError(f"Unsafe archive member: {member.name!r}")
            if member.name != root and not member.name.startswith(f"{root}/"):
                raise ValueError(f"Archive member is outside {root}: {member.name!r}")
            # The official archive currently consists only of directories and
            # regular files. Reject links and special files rather than trying
            # to emulate every tar implementation's extraction semantics.
            if not (member.isdir() or member.isfile()):
                raise ValueError(f"Unsupported archive member type: {member.name!r}")
            if member.name in by_name:
                raise ValueError(f"Duplicate archive member: {member.name!r}")
            by_name[member.name] = member

        missing = required.difference(by_name)
        if missing:
            raise ValueError(f"Archive is missing required files: {sorted(missing)}")
        if not all(by_name[name].isfile() for name in required):
            raise ValueError("A required archive path is not a regular file")
        if not all(by_name[f"{root}/{name}"].mode & 0o111 for name in ("obsidian", "obsidian-cli")):
            raise ValueError("Obsidian executables are not marked executable")

        _validate_x86_64_elf(archive, by_name[f"{root}/obsidian"], "Main executable")
        _validate_x86_64_elf(archive, by_name[f"{root}/obsidian-cli"], "CLI executable")

        icon = archive.extractfile(by_name[f"{root}/resources/icon.png"])
        if icon is None:
            raise ValueError("Could not read the Obsidian icon")
        header = icon.read(24)
        if len(header) != 24 or header[:8] != b"\x89PNG\r\n\x1a\n" or header[12:16] != b"IHDR":
            raise ValueError("Obsidian icon is not a PNG file")
        if struct.unpack(">II", header[16:24]) != (512, 512):
            raise ValueError("Obsidian icon is not 512x512")


def update_package(path: Path, version: str, sri_hash: str) -> bool:
    original = path.read_text(encoding="utf-8")
    updated, version_count = re.subn(
        r'^  version = "[^"]+";$',
        f'  version = "{version}";',
        original,
        count=1,
        flags=re.MULTILINE,
    )
    updated, hash_count = re.subn(
        r'^    hash = "sha256-[^"]+";$',
        f'    hash = "{sri_hash}";',
        updated,
        count=1,
        flags=re.MULTILINE,
    )
    if version_count != 1 or hash_count != 1:
        raise ValueError("Could not update package.nix unambiguously")
    if updated == original:
        return False

    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(updated, encoding="utf-8")
    temporary.replace(path)
    return True


def version_tuple(version: str) -> tuple[int, int, int]:
    if VERSION_RE.fullmatch(version) is None:
        raise ValueError(f"Unsupported version: {version!r}")
    return tuple(int(part) for part in version.split("."))  # type: ignore[return-value]


def main() -> int:
    release = parse_release(fetch_release_payload())
    _, current_version, current_sri = read_package_metadata()
    current_hex = sri_to_hex(current_sri)

    if release.version == current_version:
        if release.sha256_hex != current_hex:
            raise ValueError(
                f"Official asset for {release.version} changed without a version bump; refusing to update"
            )
        print(f"Obsidian {current_version} is already current")
        return 0
    if version_tuple(release.version) < version_tuple(current_version):
        raise ValueError(f"GitHub returned older release {release.version} than packaged {current_version}")

    with tempfile.TemporaryDirectory(prefix="obsidian-nix-update-") as directory:
        archive = Path(directory) / f"obsidian-{release.version}.tar.gz"
        downloaded_hex = download_asset(release, archive)
        if downloaded_hex != release.sha256_hex:
            raise ValueError(
                f"Downloaded SHA-256 {downloaded_hex} does not match GitHub digest {release.sha256_hex}"
            )
        validate_archive(archive, release.version)

    sri_hash = hex_to_sri(release.sha256_hex)
    changed = update_package(PACKAGE, release.version, sri_hash)
    if not changed:
        raise ValueError("Release changed but package.nix did not")

    print(f"Updated Obsidian {current_version} -> {release.version}")
    print(sri_hash)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, json.JSONDecodeError, tarfile.TarError) as error:
        raise SystemExit(f"obsidian-nix updater: {error}") from error
