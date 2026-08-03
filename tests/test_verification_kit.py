"""Determinism, hostile-archive, package-export, and offline-kit checks."""

from __future__ import annotations

import ast
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import stat
import subprocess
import sys
import warnings
from zipfile import ZIP_STORED, ZipFile, ZipInfo

import pytest

from bulla.verification_kit import (
    ARCHIVE_NAME,
    export_verification_kit,
    verification_kit_bytes,
    verification_kit_sha256,
)


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "spec" / "build_release_bundle.py"
SPEC = importlib.util.spec_from_file_location("build_release_bundle", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
BUILDER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BUILDER)
PACKAGED = ROOT / "src" / "bulla" / "data" / ARCHIVE_NAME
CHECKER_PATH = ROOT / "spec" / "vectors" / "independent_check.py"
CHECKER_SPEC = importlib.util.spec_from_file_location("kit_independent_checker", CHECKER_PATH)
assert CHECKER_SPEC is not None and CHECKER_SPEC.loader is not None
CHECKER = importlib.util.module_from_spec(CHECKER_SPEC)
CHECKER_SPEC.loader.exec_module(CHECKER)
KIT_VERIFY_PATH = ROOT / "spec" / "verification-kit" / "verify.py"
KIT_VERIFY_SPEC = importlib.util.spec_from_file_location("kit_extracted_verifier", KIT_VERIFY_PATH)
assert KIT_VERIFY_SPEC is not None and KIT_VERIFY_SPEC.loader is not None
KIT_VERIFY = importlib.util.module_from_spec(KIT_VERIFY_SPEC)
KIT_VERIFY_SPEC.loader.exec_module(KIT_VERIFY)


def _info(name: str, *, mode: int = stat.S_IFREG | 0o644) -> ZipInfo:
    info = ZipInfo(name, date_time=BUILDER.FIXED_TIME)
    info.compress_type = ZIP_STORED
    info.create_system = 3
    info.external_attr = mode << 16
    return info


def _rewrite(
    source: Path,
    out: Path,
    *,
    drop: str | None = None,
    change: str | None = None,
    addition: tuple[str, bytes, int] | None = None,
) -> None:
    with ZipFile(source) as archive:
        rows = [(info.filename, archive.read(info), info.external_attr >> 16) for info in archive.infolist()]
    rewritten = []
    for name, payload, mode in rows:
        if name == drop:
            continue
        if name == change:
            payload += b"tamper"
        rewritten.append((name, payload, mode))
    if addition is not None:
        rewritten.append(addition)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        with ZipFile(out, "w", compression=ZIP_STORED) as archive:
            for name, payload, mode in sorted(rewritten, key=lambda row: row[0]):
                archive.writestr(_info(name, mode=mode), payload)


def test_two_clean_builds_are_byte_identical(tmp_path: Path) -> None:
    first = tmp_path / "first.zip"
    second = tmp_path / "second.zip"
    _, first_digest = BUILDER.build(first)
    _, second_digest = BUILDER.build(second)
    assert first.read_bytes() == second.read_bytes() == PACKAGED.read_bytes()
    assert first_digest == second_digest == hashlib.sha256(first.read_bytes()).hexdigest()


def test_kit_text_inputs_are_normalized_to_utf8_lf(tmp_path: Path) -> None:
    source = tmp_path / "portable.txt"
    source.write_bytes(b"first\r\nsecond\rthird\n")
    assert BUILDER._regular_file_bytes(source, "portable.txt") == (
        b"first\nsecond\nthird\n"
    )


def test_archive_contract_and_manifest_are_exact() -> None:
    digest = BUILDER.validate_archive(PACKAGED)
    assert digest == verification_kit_sha256()
    with ZipFile(PACKAGED) as archive:
        infos = archive.infolist()
        assert [item.filename for item in infos] == sorted(item.filename for item in infos)
        assert all(item.compress_type == ZIP_STORED for item in infos)
        assert all(item.date_time == BUILDER.FIXED_TIME for item in infos)
        assert all(stat.S_IFMT(item.external_attr >> 16) == stat.S_IFREG for item in infos)
        manifest = json.loads(archive.read("MANIFEST.json"))
        names = [row["path"] for row in manifest["members"]]
        assert names == sorted(names)
        assert "MANIFEST.json" not in names
        assert "MANIFEST.sha256" not in names
        assert archive.read("claims-file/receipt.json") == archive.read(
            "vectors/payment-authorization.json"
        )


@pytest.mark.parametrize(
    ("kind", "kwargs", "match"),
    [
        ("missing", {"drop": "README.md"}, "member set differs|payload is absent"),
        ("changed", {"change": "README.md"}, "payload differs"),
        (
            "extra",
            {"addition": ("EXTRA.txt", b"extra", stat.S_IFREG | 0o644)},
            "member set differs",
        ),
        (
            "traversal",
            {"addition": ("../escape", b"escape", stat.S_IFREG | 0o644)},
            "unsafe archive member",
        ),
        (
            "windows-drive-absolute",
            {"addition": ("C:/escape", b"escape", stat.S_IFREG | 0o644)},
            "unsafe archive member",
        ),
        (
            "windows-drive-relative",
            {"addition": ("C:escape", b"escape", stat.S_IFREG | 0o644)},
            "unsafe archive member",
        ),
        (
            "symlink",
            {"addition": ("link", b"README.md", stat.S_IFLNK | 0o777)},
            "non-regular",
        ),
        (
            "case-collision",
            {"addition": ("readme.md", b"collision", stat.S_IFREG | 0o644)},
            "case-folding",
        ),
    ],
)
def test_hostile_archives_fail_closed(
    tmp_path: Path, kind: str, kwargs: dict, match: str
) -> None:
    hostile = tmp_path / f"{kind}.zip"
    _rewrite(PACKAGED, hostile, **kwargs)
    with pytest.raises(BUILDER.KitBuildError, match=match):
        BUILDER.validate_archive(hostile)


def test_duplicate_member_fails_closed(tmp_path: Path) -> None:
    hostile = tmp_path / "duplicate.zip"
    _rewrite(
        PACKAGED,
        hostile,
        addition=("README.md", b"duplicate", stat.S_IFREG | 0o644),
    )
    with pytest.raises(BUILDER.KitBuildError, match="duplicate archive member"):
        BUILDER.validate_archive(hostile)


@pytest.mark.parametrize("name", ["C:/escape", "C:escape", "C:\\escape"])
def test_extracted_checker_rejects_windows_drive_paths(name: str) -> None:
    assert not KIT_VERIFY.safe_relative(name)


def test_independent_checker_rejects_unhashed_v02_claims() -> None:
    receipt = json.loads(
        (ROOT / "spec" / "vectors" / "payment-authorization.json").read_text(
            encoding="utf-8"
        )
    )
    injected = copy.deepcopy(receipt)
    injected["underlying_payment"] = "SETTLED"
    assert not CHECKER.verify_action_receipt(injected)["ok"]
    occupied_stake = copy.deepcopy(receipt)
    occupied_stake["stake"] = {"underlying_payment": "SETTLED"}
    assert not CHECKER.verify_action_receipt(occupied_stake)["ok"]


def test_standalone_checker_runs_without_bulla_or_network_imports(tmp_path: Path) -> None:
    with ZipFile(PACKAGED) as archive:
        archive.extractall(tmp_path)
    for source_path in (tmp_path / "verify.py", tmp_path / "vectors" / "independent_check.py"):
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
        imports = {
            alias.name.split(".", 1)[0]
            for node in ast.walk(tree)
            if isinstance(node, (ast.Import, ast.ImportFrom))
            for alias in (
                node.names if isinstance(node, ast.Import) else [ast.alias(node.module or "")]
            )
        }
        assert imports.isdisjoint({"bulla", "requests", "socket", "urllib", "http"})
    result = subprocess.run(
        [sys.executable, "-I", str(tmp_path / "verify.py")],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "manifest authenticates" in result.stdout
    assert "zero bulla imports" in result.stdout
    assert "CONSTRUCTED CLAIMS FILE" in result.stdout
    assert "record integrity       VERIFIED" in result.stdout
    assert "declared bounds        CONFORMS" in result.stdout
    assert "authority              UNAUTHENTICATED" in result.stdout
    assert "NOT ESTABLISHED BY THIS CHECK" in result.stdout
    assert "whether funds moved" in result.stdout
    assert "No issuer connection was used" in result.stdout
    assert "ok=True" not in result.stdout


def test_independent_checker_stdout_is_ascii_portable() -> None:
    result = subprocess.run(
        [sys.executable, "-I", str(CHECKER_PATH)],
        cwd=ROOT,
        capture_output=True,
        timeout=30,
    )
    assert result.stdout.isascii()
    assert result.stderr.isascii()
    output = result.stdout.decode("ascii") + result.stderr.decode("ascii")
    assert result.returncode == 0, output
    assert "PASS" in output


def test_package_export_is_exact_and_refuses_replacement(tmp_path: Path) -> None:
    out = tmp_path / ARCHIVE_NAME
    path, digest = export_verification_kit(out)
    assert path == out
    assert out.read_bytes() == verification_kit_bytes() == PACKAGED.read_bytes()
    assert digest == verification_kit_sha256()
    export_verification_kit(out)
    out.write_bytes(b"different")
    with pytest.raises(FileExistsError, match="refusing to replace"):
        export_verification_kit(out)


def test_receipt_kit_cli_exports_installed_bytes(tmp_path: Path) -> None:
    out = tmp_path / ARCHIVE_NAME
    result = subprocess.run(
        [sys.executable, "-m", "bulla", "receipt", "kit", "--out", str(out)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert out.read_bytes() == PACKAGED.read_bytes()
    assert f"sha256:{verification_kit_sha256()}" in result.stdout
