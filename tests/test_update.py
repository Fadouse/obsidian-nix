from __future__ import annotations

import hashlib
import importlib.util
import io
import struct
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "update.py"
SPEC = importlib.util.spec_from_file_location("obsidian_update", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
UPDATE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = UPDATE
SPEC.loader.exec_module(UPDATE)


def release_payload(version: str = "1.2.3") -> dict:
    name = f"obsidian-{version}.tar.gz"
    return {
        "draft": False,
        "prerelease": False,
        "tag_name": f"v{version}",
        "assets": [
            {
                "name": name,
                "browser_download_url": (
                    "https://github.com/obsidianmd/obsidian-releases/releases/"
                    f"download/v{version}/{name}"
                ),
                "digest": "sha256:" + "ab" * 32,
            }
        ],
    }


def png_header(width: int = 512, height: int = 512) -> bytes:
    return b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 13) + b"IHDR" + struct.pack(">II", width, height)


def elf_header(machine: int = 62) -> bytes:
    header = bytearray(64)
    header[:7] = b"\x7fELF\x02\x01\x01"
    struct.pack_into("<HHI", header, 16, 3, machine, 1)
    struct.pack_into("<H", header, 52, 64)
    return bytes(header)


def add_file(archive: tarfile.TarFile, name: str, content: bytes, mode: int = 0o644) -> None:
    member = tarfile.TarInfo(name)
    member.size = len(content)
    member.mode = mode
    archive.addfile(member, io.BytesIO(content))


def make_archive(
    path: Path,
    version: str = "1.2.3",
    unsafe: bool = False,
    machine: int = 62,
    truncated_cli: bool = False,
) -> None:
    root = f"obsidian-{version}"
    with tarfile.open(path, mode="w:gz") as archive:
        add_file(archive, f"{root}/obsidian", elf_header(machine), 0o755)
        add_file(
            archive,
            f"{root}/obsidian-cli",
            b"\x7fELF" if truncated_cli else elf_header(machine),
            0o755,
        )
        add_file(archive, f"{root}/resources/app.asar", b"app")
        add_file(archive, f"{root}/resources/obsidian.asar", b"obsidian")
        add_file(archive, f"{root}/resources/icon.png", png_header())
        if unsafe:
            add_file(archive, f"{root}/../escape", b"bad")


class ReleaseTests(unittest.TestCase):
    def test_parse_stable_release(self) -> None:
        release = UPDATE.parse_release(release_payload())
        self.assertEqual(release.version, "1.2.3")
        self.assertEqual(release.sha256_hex, "ab" * 32)

    def test_rejects_prerelease(self) -> None:
        payload = release_payload()
        payload["prerelease"] = True
        with self.assertRaisesRegex(ValueError, "stable"):
            UPDATE.parse_release(payload)

    def test_rejects_wrong_asset_url(self) -> None:
        payload = release_payload()
        payload["assets"][0]["browser_download_url"] = "https://example.invalid/obsidian.tar.gz"
        with self.assertRaisesRegex(ValueError, "asset URL"):
            UPDATE.parse_release(payload)

    def test_rejects_missing_digest(self) -> None:
        payload = release_payload()
        payload["assets"][0]["digest"] = None
        with self.assertRaisesRegex(ValueError, "digest"):
            UPDATE.parse_release(payload)


class HashTests(unittest.TestCase):
    def test_sri_round_trip(self) -> None:
        digest = hashlib.sha256(b"obsidian").hexdigest()
        self.assertEqual(UPDATE.sri_to_hex(UPDATE.hex_to_sri(digest)), digest)

    def test_rejects_short_digest(self) -> None:
        with self.assertRaisesRegex(ValueError, "length"):
            UPDATE.hex_to_sri("00")


class ArchiveTests(unittest.TestCase):
    def test_accepts_expected_archive(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "obsidian.tar.gz"
            make_archive(path)
            UPDATE.validate_archive(path, "1.2.3")

    def test_rejects_path_traversal(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "obsidian.tar.gz"
            make_archive(path, unsafe=True)
            with self.assertRaisesRegex(ValueError, "Unsafe archive member"):
                UPDATE.validate_archive(path, "1.2.3")

    def test_rejects_wrong_icon_size(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "obsidian.tar.gz"
            root = "obsidian-1.2.3"
            with tarfile.open(path, mode="w:gz") as archive:
                add_file(archive, f"{root}/obsidian", elf_header(), 0o755)
                add_file(archive, f"{root}/obsidian-cli", elf_header(), 0o755)
                add_file(archive, f"{root}/resources/app.asar", b"app")
                add_file(archive, f"{root}/resources/obsidian.asar", b"obsidian")
                add_file(archive, f"{root}/resources/icon.png", png_header(256, 256))
            with self.assertRaisesRegex(ValueError, "512x512"):
                UPDATE.validate_archive(path, "1.2.3")

    def test_rejects_normalized_path_alias(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = "obsidian-1.2.3"
            alias_path = Path(directory) / "alias.tar.gz"
            with tarfile.open(alias_path, mode="w:gz") as archive:
                add_file(archive, f"{root}/obsidian", elf_header(), 0o755)
                add_file(archive, f"{root}/./obsidian", elf_header(), 0o755)
                add_file(archive, f"{root}/obsidian-cli", elf_header(), 0o755)
                add_file(archive, f"{root}/resources/app.asar", b"app")
                add_file(archive, f"{root}/resources/obsidian.asar", b"obsidian")
                add_file(archive, f"{root}/resources/icon.png", png_header())
            with self.assertRaisesRegex(ValueError, "Unsafe archive member"):
                UPDATE.validate_archive(alias_path, "1.2.3")

    def test_rejects_hard_links(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "obsidian.tar.gz"
            root = "obsidian-1.2.3"
            with tarfile.open(path, mode="w:gz") as archive:
                add_file(archive, f"{root}/obsidian", elf_header(), 0o755)
                add_file(archive, f"{root}/obsidian-cli", elf_header(), 0o755)
                add_file(archive, f"{root}/resources/app.asar", b"app")
                add_file(archive, f"{root}/resources/obsidian.asar", b"obsidian")
                add_file(archive, f"{root}/resources/icon.png", png_header())
                link = tarfile.TarInfo(f"{root}/hard-link")
                link.type = tarfile.LNKTYPE
                link.linkname = "outside"
                archive.addfile(link)
            with self.assertRaisesRegex(ValueError, "Unsupported archive member type"):
                UPDATE.validate_archive(path, "1.2.3")

    def test_rejects_wrong_elf_architecture(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "obsidian.tar.gz"
            make_archive(path, machine=183)
            with self.assertRaisesRegex(ValueError, "x86_64"):
                UPDATE.validate_archive(path, "1.2.3")

    def test_rejects_truncated_cli(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "obsidian.tar.gz"
            make_archive(path, truncated_cli=True)
            with self.assertRaisesRegex(ValueError, "truncated ELF"):
                UPDATE.validate_archive(path, "1.2.3")


class PackageUpdateTests(unittest.TestCase):
    def test_updates_exact_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            package = Path(directory) / "package.nix"
            package.write_text(
                '{\n  version = "1.0.0";\n  src = fetchurl {\n'
                '    hash = "sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=";\n'
                "  };\n}\n",
                encoding="utf-8",
            )
            changed = UPDATE.update_package(
                package,
                "1.2.3",
                "sha256-q6urq6urq6urq6urq6urq6urq6urq6urq6urq6urq6s=",
            )
            self.assertTrue(changed)
            text = package.read_text(encoding="utf-8")
            self.assertIn('version = "1.2.3";', text)
            self.assertIn("sha256-q6urq6ur", text)

    def test_current_metadata_is_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            package = Path(directory) / "package.nix"
            text = (
                '{\n  version = "1.2.3";\n  src = fetchurl {\n'
                '    hash = "sha256-q6urq6urq6urq6urq6urq6urq6urq6urq6urq6urq6s=";\n'
                "  };\n}\n"
            )
            package.write_text(text, encoding="utf-8")
            self.assertFalse(
                UPDATE.update_package(
                    package,
                    "1.2.3",
                    "sha256-q6urq6urq6urq6urq6urq6urq6urq6urq6urq6urq6s=",
                )
            )
            self.assertEqual(package.read_text(encoding="utf-8"), text)


if __name__ == "__main__":
    unittest.main()
