#!/usr/bin/env bash
# purpose: Create and verify an immutable offline Tasca 0.1.29 rollback bundle.
# usage: build-viewer-auth-rollback-bundle.sh build|verify [declared immutable inputs]
# effects: Creates only selected local bundle and receipt paths; it never accesses GCP, credentials, or repository runtime state.
# requires: Python 3, git, and declared immutable wheelhouse inputs.
set -euo pipefail

readonly SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
export TASCA_ROLLBACK_BUNDLE_REPO_ROOT="$(cd -- "$SCRIPT_DIR/../.." && pwd)"
exec python3 - "$@" <<'PYTHON'
"""Create and verify an immutable offline Tasca 0.1.29 rollback bundle.

The input directory contains only immutable wheel bytes selected by the release
operator. The bundle records every installed wheel and its SHA-256 so the remote
producer can install it with ``uv --offline --no-index``.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import os
import re
import subprocess
import tarfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROLLBACK_VERSION = "0.1.29"
FORMAT = "tasca-rollback-bundle-v1"
ROOT = Path(os.environ["TASCA_ROLLBACK_BUNDLE_REPO_ROOT"])
REQUIRED_PRODUCERS = (
    ROOT / "scripts/gcp/viewer-auth-rollout.sh",
    ROOT / "scripts/gcp/viewer-auth-remote.sh",
    ROOT / "scripts/gcp/build-viewer-auth-rollback-bundle.sh",
)
_METADATA_NAME = re.compile(r"^Name:\s*(.+)$", re.MULTILINE | re.IGNORECASE)
_METADATA_VERSION = re.compile(r"^Version:\s*(.+)$", re.MULTILINE | re.IGNORECASE)
_METADATA_PYTHON = re.compile(r"^Requires-Python:\s*(.+)$", re.MULTILINE | re.IGNORECASE)
_METADATA_REQUIREMENT = re.compile(r"^Requires-Dist:\s*(.+)$", re.MULTILINE | re.IGNORECASE)
_REQUIREMENT = re.compile(r"^\s*([A-Za-z0-9_.-]+)(?:\[[^]]*\])?\s*(?:\(([^)]*)\)|(.+))?$")


@dataclass(frozen=True)
class Wheel:
    """A wheel selected for the offline rollback installation."""

    path: Path
    name: str
    version: str
    sha256: str
    requires: tuple[str, ...]


def sha256(path: Path) -> str:
    """Return the SHA-256 digest of one immutable input file."""
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def normalize(name: str) -> str:
    """Use the normalized package spelling used by Python package indexes."""
    return re.sub(r"[-_.]+", "-", name).lower()


def applies_to_target(requirement: str) -> bool:
    """Keep only runtime requirements for the declared Linux/CPython-3.13 target."""
    if ";" not in requirement:
        return True
    marker = requirement.split(";", 1)[1].lower()
    if "extra" in marker:
        return False
    if "sys_platform" in marker and any(value in marker for value in ("win32", "darwin")):
        return False
    if "platform_system" in marker and any(value in marker for value in ("windows", "darwin")):
        return False
    version_match = re.search(r"python_version\s*(==|!=|>=|<=|>|<)\s*['\"]([0-9.]+)['\"]", marker)
    if version_match is None:
        return True
    operator, required = version_match.groups()
    actual = version_key("3.13")
    expected = version_key(required)
    return {
        "==": actual == expected,
        "!=": actual != expected,
        ">=": actual >= expected,
        "<=": actual <= expected,
        ">": actual > expected,
        "<": actual < expected,
    }[operator]


def wheel_metadata(path: Path) -> tuple[str, str, str | None, tuple[str, ...]]:
    """Read distribution metadata from a wheel without installing it."""
    try:
        with zipfile.ZipFile(path) as archive:
            metadata_paths = [name for name in archive.namelist() if name.endswith(".dist-info/METADATA")]
            if len(metadata_paths) != 1:
                raise ValueError("wheel must contain exactly one dist-info METADATA file")
            metadata = archive.read(metadata_paths[0]).decode("utf-8")
    except (OSError, zipfile.BadZipFile, UnicodeDecodeError) as error:
        raise ValueError(f"invalid wheel {path.name}") from error
    name = _METADATA_NAME.search(metadata)
    version = _METADATA_VERSION.search(metadata)
    if name is None or version is None:
        raise ValueError(f"wheel {path.name} is missing Name or Version metadata")
    python_requirement = _METADATA_PYTHON.search(metadata)
    requirements = tuple(sorted({item for item in _METADATA_REQUIREMENT.findall(metadata) if applies_to_target(item)}))
    return name.group(1).strip(), version.group(1).strip(), (
        python_requirement.group(1).strip() if python_requirement else None
    ), requirements


def version_key(value: str) -> tuple[tuple[int, object], ...]:
    """Provide the bounded PEP-440 subset needed by this pinned wheelhouse."""
    key: list[tuple[int, object]] = []
    for part in re.split(r"[._+-]", value.lower()):
        if part.isdecimal():
            key.append((1, int(part)))
        elif part:
            key.append((0, part))
    return tuple(key)


def requirement_is_satisfied(requirement: str, candidate: Wheel) -> bool:
    """Reject a pinned wheelhouse whose declared version ranges do not agree."""
    expression = requirement.split(";", 1)[0].strip()
    parsed = _REQUIREMENT.fullmatch(expression)
    if parsed is None:
        raise ValueError(f"wheel has an unparseable requirement: {requirement}")
    name, parenthesized, bare = parsed.groups()
    if normalize(name) != candidate.name:
        return False
    specifiers = (parenthesized if parenthesized is not None else bare or "").replace(" ", "")
    if not specifiers:
        return True
    actual = version_key(candidate.version)
    for specifier in specifiers.split(","):
        matched = re.fullmatch(r"(===|==|!=|~=|>=|<=|>|<)(.+)", specifier)
        if matched is None:
            raise ValueError(f"wheel has an unparseable version specifier: {requirement}")
        operator, expected = matched.groups()
        expected_key = version_key(expected.rstrip(".*"))
        if operator == "==" and expected.endswith(".*"):
            valid = candidate.version.startswith(expected[:-1])
        elif operator == "==":
            valid = actual == expected_key
        elif operator == "!=":
            valid = actual != expected_key
        elif operator == ">=":
            valid = actual >= expected_key
        elif operator == "<=":
            valid = actual <= expected_key
        elif operator == ">":
            valid = actual > expected_key
        elif operator == "<":
            valid = actual < expected_key
        elif operator == "~=":
            compatible_prefix = expected.split(".")[:-1]
            valid = actual >= expected_key and candidate.version.split(".")[: len(compatible_prefix)] == compatible_prefix
        else:  # ===
            valid = candidate.version == expected
        if not valid:
            return False
    return True


def selected_wheels(wheelhouse: Path) -> tuple[Wheel, ...]:
    """Return a deterministic, complete, version-compatible wheelhouse input."""
    if not wheelhouse.is_dir():
        raise ValueError("wheelhouse must be a directory")
    paths = sorted(wheelhouse.glob("*.whl"))
    if not paths:
        raise ValueError("wheelhouse contains no wheels")
    wheels: list[Wheel] = []
    names: set[str] = set()
    for path in paths:
        name, version, _python_requirement, requires = wheel_metadata(path)
        normalized = normalize(name)
        if normalized in names:
            raise ValueError(f"wheelhouse contains multiple {normalized} wheels")
        names.add(normalized)
        wheels.append(Wheel(path=path, name=normalized, version=version, sha256=sha256(path), requires=requires))
    by_name = {wheel.name: wheel for wheel in wheels}
    missing: set[str] = set()
    incompatible: set[str] = set()
    for wheel in wheels:
        for requirement in wheel.requires:
            parsed = _REQUIREMENT.fullmatch(requirement.split(";", 1)[0].strip())
            if parsed is None:
                raise ValueError(f"wheel has an unparseable requirement: {requirement}")
            dependency = normalize(parsed.group(1))
            selected = by_name.get(dependency)
            if selected is None:
                missing.add(dependency)
            elif not requirement_is_satisfied(requirement, selected):
                incompatible.add(f"{wheel.name} requires {requirement}")
    if missing:
        raise ValueError(f"wheelhouse is missing declared dependencies: {', '.join(sorted(missing))}")
    if incompatible:
        raise ValueError(f"wheelhouse has incompatible declared dependencies: {', '.join(sorted(incompatible))}")
    return tuple(wheels)


def producer_identity() -> dict[str, Any]:
    """Bind the bundle to the exact tracked producer sources that consume it."""
    revision = subprocess.run(
        ["git", "-C", str(ROOT), "rev-parse", "--verify", "HEAD"],
        check=True,
        text=True,
        capture_output=True,
    ).stdout.strip()
    return {
        "revision": revision,
        "files": [
            {"path": str(path.relative_to(ROOT)), "sha256": sha256(path)}
            for path in REQUIRED_PRODUCERS
        ],
    }


def manifest_for(wheel: Path, wheel_sha256: str, wheelhouse: Path) -> tuple[dict[str, Any], dict[str, bytes]]:
    """Validate immutable inputs and return the manifest plus deterministic members."""
    if not re.fullmatch(r"[0-9a-f]{64}", wheel_sha256):
        raise ValueError("--wheel-sha256 must be a lowercase SHA-256 digest")
    if wheel.name != f"tasca-{ROLLBACK_VERSION}-py3-none-any.whl":
        raise ValueError(f"rollback wheel must be tasca-{ROLLBACK_VERSION}-py3-none-any.whl")
    if not wheel.is_file():
        raise ValueError("rollback wheel does not exist")
    if sha256(wheel) != wheel_sha256:
        raise ValueError("rollback wheel SHA-256 does not match --wheel-sha256")

    wheels = selected_wheels(wheelhouse)
    by_name = {item.name: item for item in wheels}
    root = by_name.get("tasca")
    if root is None or root.version != ROLLBACK_VERSION or root.sha256 != wheel_sha256:
        raise ValueError("wheelhouse must contain the exact selected Tasca 0.1.29 wheel")
    if "httpx" not in by_name:
        raise ValueError("wheelhouse must include the declared httpx dependency")
    _name, _version, python_requirement, _requires = wheel_metadata(wheel)
    if python_requirement != ">=3.13":
        raise ValueError("rollback Tasca wheel must require Python >=3.13")

    artifacts = [
        {
            "filename": item.path.name,
            "name": item.name,
            "sha256": item.sha256,
            "version": item.version,
        }
        for item in sorted(wheels, key=lambda item: (item.name, item.version, item.path.name))
    ]
    requirements = "".join(
        f"{item['name']}=={item['version']} --hash=sha256:{item['sha256']}\n" for item in artifacts
    ).encode()
    members = {
        "rollback/requirements.txt": requirements,
        **{f"rollback/wheelhouse/{item.path.name}": item.path.read_bytes() for item in wheels},
    }
    manifest = {
        "artifacts": artifacts,
        "format": FORMAT,
        "producer": producer_identity(),
        "rollback": {
            "python_requires": python_requirement,
            "version": ROLLBACK_VERSION,
            "wheel": wheel.name,
            "wheel_sha256": wheel_sha256,
        },
    }
    return manifest, members


def deterministic_tar(output: Path, manifest: dict[str, Any], members: dict[str, bytes]) -> None:
    """Write the content-addressable archive with fixed gzip/tar metadata."""
    encoded_manifest = (json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n").encode()
    content = {"rollback/manifest.json": encoded_manifest, **members}
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("wb") as target:
        with gzip.GzipFile(fileobj=target, mode="wb", mtime=0, filename="") as compressed:
            with tarfile.open(fileobj=compressed, mode="w") as archive:
                for name in sorted(content):
                    info = tarfile.TarInfo(name)
                    info.size = len(content[name])
                    info.mode = 0o644
                    info.mtime = 0
                    info.uid = 0
                    info.gid = 0
                    info.uname = ""
                    info.gname = ""
                    archive.addfile(info, io.BytesIO(content[name]))


def safe_members(bundle: Path) -> dict[str, bytes]:
    """Read a bundle only when every member stays in its fixed rollback prefix."""
    try:
        with tarfile.open(bundle, "r:gz") as archive:
            members = archive.getmembers()
            if not members or any(not member.isfile() or not member.name.startswith("rollback/") for member in members):
                raise ValueError("bundle has an invalid member path")
            return {member.name: archive.extractfile(member).read() for member in members if member.isfile()}
    except (OSError, tarfile.TarError) as error:
        raise ValueError("rollback bundle is not a readable gzip tar archive") from error


def verify_bundle(bundle: Path, expected_sha256: str) -> dict[str, Any]:
    """Verify archive digest, manifest shape, package bytes, and producer identity."""
    if not re.fullmatch(r"[0-9a-f]{64}", expected_sha256):
        raise ValueError("--sha256 must be a lowercase SHA-256 digest")
    if not bundle.is_file() or sha256(bundle) != expected_sha256:
        raise ValueError("rollback bundle SHA-256 mismatch")
    members = safe_members(bundle)
    try:
        manifest = json.loads(members["rollback/manifest.json"])
    except (KeyError, json.JSONDecodeError) as error:
        raise ValueError("rollback bundle has no valid manifest") from error
    if not isinstance(manifest, dict) or manifest.get("format") != FORMAT:
        raise ValueError("rollback bundle format is invalid")
    rollback = manifest.get("rollback")
    artifacts = manifest.get("artifacts")
    producer = manifest.get("producer")
    if (
        not isinstance(rollback, dict)
        or rollback.get("version") != ROLLBACK_VERSION
        or rollback.get("python_requires") != ">=3.13"
        or not isinstance(artifacts, list)
        or not isinstance(producer, dict)
        or producer != producer_identity()
    ):
        raise ValueError("rollback bundle does not match the current tracked producer")
    expected_requirements: list[str] = []
    names: set[str] = set()
    for artifact in artifacts:
        if not isinstance(artifact, dict):
            raise ValueError("rollback bundle artifact entry is invalid")
        filename = artifact.get("filename")
        name = artifact.get("name")
        version = artifact.get("version")
        digest = artifact.get("sha256")
        if (
            not isinstance(filename, str)
            or "/" in filename
            or not isinstance(name, str)
            or not isinstance(version, str)
            or not isinstance(digest, str)
            or not re.fullmatch(r"[0-9a-f]{64}", digest)
            or name in names
        ):
            raise ValueError("rollback bundle artifact metadata is invalid")
        names.add(name)
        payload = members.get(f"rollback/wheelhouse/{filename}")
        if payload is None or hashlib.sha256(payload).hexdigest() != digest:
            raise ValueError("rollback bundle artifact bytes do not match the manifest")
        expected_requirements.append(f"{name}=={version} --hash=sha256:{digest}\n")
    if "tasca" not in names or "httpx" not in names:
        raise ValueError("rollback bundle is missing Tasca or httpx")
    if rollback.get("wheel_sha256") != next(
        (item["sha256"] for item in artifacts if item.get("name") == "tasca"), None
    ):
        raise ValueError("rollback bundle Tasca wheel digest is invalid")
    if members.get("rollback/requirements.txt") != "".join(expected_requirements).encode():
        raise ValueError("rollback bundle requirements do not bind its artifacts")
    return manifest


def command_build(args: argparse.Namespace) -> int:
    """Create a deterministic bundle and a separate token-free receipt."""
    output = args.output.resolve()
    receipt = args.receipt.resolve()
    if output == receipt:
        raise ValueError("bundle and receipt paths must differ")
    manifest, members = manifest_for(args.wheel.resolve(), args.wheel_sha256, args.wheelhouse.resolve())
    deterministic_tar(output, manifest, members)
    bundle_sha256 = sha256(output)
    receipt_payload = {
        "bundle": {"filename": output.name, "sha256": bundle_sha256},
        "manifest": manifest,
    }
    receipt.parent.mkdir(parents=True, exist_ok=True)
    receipt.write_text(json.dumps(receipt_payload, sort_keys=True, separators=(",", ":")) + "\n")
    os.chmod(receipt, 0o600)
    print(json.dumps(receipt_payload, sort_keys=True))
    return 0


def command_verify(args: argparse.Namespace) -> int:
    """Verify one immutable bundle against the current tracked producer."""
    print(json.dumps(verify_bundle(args.bundle.resolve(), args.sha256), sort_keys=True))
    return 0


def main() -> int:
    """Parse the intentionally small build/verify interface."""
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    build = commands.add_parser("build")
    build.add_argument("--wheel", required=True, type=Path)
    build.add_argument("--wheel-sha256", required=True)
    build.add_argument("--wheelhouse", required=True, type=Path)
    build.add_argument("--output", required=True, type=Path)
    build.add_argument("--receipt", required=True, type=Path)
    build.set_defaults(handler=command_build)
    verify = commands.add_parser("verify")
    verify.add_argument("--bundle", required=True, type=Path)
    verify.add_argument("--sha256", required=True)
    verify.set_defaults(handler=command_verify)
    args = parser.parse_args()
    return args.handler(args)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, subprocess.CalledProcessError, ValueError) as error:
        print(f"tasca rollback bundle: {error}", file=os.sys.stderr)
        raise SystemExit(1) from error

PYTHON
