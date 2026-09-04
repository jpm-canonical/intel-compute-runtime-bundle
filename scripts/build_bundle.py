#!/usr/bin/env python3
"""Resolve and bundle the packages for one stable Intel compute-runtime release."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import os
import re
import shutil
import sys
import tarfile
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

API_ROOT = "https://api.github.com"
RUNTIME_REPO = "intel/compute-runtime"
IGC_REPO = "intel/intel-graphics-compiler"
ARCHIVE_NAME = "intel-compute-runtime-amd64.tar.gz"
VERSION_RE = re.compile(r"^\d{2}\.\d{2}\.\d+\.\d+$")
ROLE_PATTERNS = {
    "opencl_icd": re.compile(r"^intel-opencl-icd_[^/]+_amd64\.deb$"),
    "gmmlib": re.compile(r"^libigdgmm\d+_[^/]+_amd64\.deb$"),
    "igc_core": re.compile(r"^intel-igc-core-2_[^/]+_amd64\.deb$"),
    "igc_opencl": re.compile(r"^intel-igc-opencl-2_[^/]+_amd64\.deb$"),
    "ocloc": re.compile(r"^intel-ocloc_[^/]+_amd64\.deb$"),
    "level_zero": re.compile(
        r"^(?:libze-intel-gpu\d+|intel-level-zero-gpu)_[^/]+_amd64\.deb$"
    ),
}


class BundleError(RuntimeError):
    """Raised when upstream metadata or a downloaded package is invalid."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def fetch_release(repo: str, tag: str, token: str | None = None) -> dict[str, Any]:
    """Fetch the GitHub release metadata for the given repository and tag.

    Args:
        repo: The GitHub repository in the form "owner/repo".
        tag: The release tag.
        token: Optional GitHub API token for authentication.

    Returns:
        The release metadata as a dictionary.

    Raises:
        BundleError: If the release metadata could not be fetched or is invalid.
    """
    request = Request(
        f"{API_ROOT}/repos/{repo}/releases/tags/{tag}",
        headers={
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "intel-compute-runtime-bundle",
            **({"Authorization": f"Bearer {token}"} if token else {}),
        },
    )
    try:
        with urlopen(request, timeout=30) as response:
            value = json.load(response)
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as error:
        raise BundleError(f"failed to fetch {repo} release {tag}: {error}") from error
    if not isinstance(value, dict):
        raise BundleError(f"unexpected response for {repo} release {tag}")
    return value


def linked_igc_tag(body: str) -> str:
    pattern = re.compile(
        r"https://github\.com/intel/intel-graphics-compiler/releases/tag/([^\s/)]+)"
    )
    tags = sorted(set(pattern.findall(body)))
    if len(tags) != 1:
        raise BundleError(f"expected exactly one linked IGC release; found {tags}")
    return tags[0]


def checksum(release: dict[str, Any], asset: dict[str, Any]) -> str:
    """
    Return the SHA-256 checksum for the given asset in the release.
    Looks at the API "digest" field or at the release description.

    Args:
        release: The GitHub release metadata.
        asset: The asset metadata within the release.

    Returns:
        The SHA-256 checksum as a lowercase hexadecimal string.

    Raises:
        BundleError: If no trustworthy SHA-256 checksum is found.
    """
    digest = asset.get("digest")
    if isinstance(digest, str) and re.fullmatch(r"sha256:[0-9a-fA-F]{64}", digest):
        return digest.split(":", 1)[1].lower()

    # Look at the release description for a SHA-256 checksum of the given file
    name = re.escape(str(asset.get("name")))
    match = re.search(rf"(?m)^\s*([0-9a-fA-F]{{64}})\s+[*]?{name}\s*$", str(release.get("body") or ""))
    if not match:
        raise BundleError(f"no trustworthy SHA-256 found for {asset.get('name')}")
    
    return match.group(1).lower()


def package_for_role(
    release: dict[str, Any], repo: str, tag: str, role: str
) -> dict[str, Any]:
    assets = release.get("assets")
    if not isinstance(assets, list):
        raise BundleError(f"release {tag} has no asset list")
    matches = [
        asset
        for asset in assets
        if isinstance(asset, dict)
        and isinstance(asset.get("name"), str)
        and ROLE_PATTERNS[role].fullmatch(asset["name"])
    ]
    if len(matches) != 1:
        raise BundleError(
            f"expected exactly one amd64 .deb for {role} in {repo}@{tag}; "
            f"found {[asset.get('name') for asset in matches]}"
        )
    asset = matches[0]
    if not isinstance(asset.get("size"), int) or asset["size"] <= 0:
        raise BundleError(f"invalid size for {asset['name']}")
    if not isinstance(asset.get("browser_download_url"), str):
        raise BundleError(f"invalid URL for {asset['name']}")
    return {
        "role": role,
        "source": repo,
        "source_tag": tag,
        "name": asset["name"],
        "url": asset["browser_download_url"],
        "size": asset["size"],
        "sha256": checksum(release, asset),
    }


def resolve(
    version: str,
    fetcher: Callable[[str, str, str | None], dict[str, Any]] = fetch_release,
    token: str | None = None,
) -> dict[str, Any]:
    if not VERSION_RE.fullmatch(version):
        raise BundleError(f"invalid compute-runtime version: {version!r}")
    runtime = fetcher(RUNTIME_REPO, version, token)
    if runtime.get("tag_name") != version or runtime.get("draft") or runtime.get("prerelease"):
        raise BundleError(f"{RUNTIME_REPO}@{version} is not a stable published release")
    body = str(runtime.get("body") or "")
    igc_tag = linked_igc_tag(body)
    igc = fetcher(IGC_REPO, igc_tag, token)
    if igc.get("tag_name") != igc_tag or igc.get("draft") or igc.get("prerelease"):
        raise BundleError(f"{IGC_REPO}@{igc_tag} is not a stable published release")

    sources = {
        "opencl_icd": (runtime, RUNTIME_REPO, version),
        "gmmlib": (runtime, RUNTIME_REPO, version),
        "igc_core": (igc, IGC_REPO, igc_tag),
        "igc_opencl": (igc, IGC_REPO, igc_tag),
        "ocloc": (runtime, RUNTIME_REPO, version),
        "level_zero": (runtime, RUNTIME_REPO, version),
    }
    packages = [
        package_for_role(release, repo, tag, role)
        for role, (release, repo, tag) in sources.items()
    ]
    return {
        "schema": 1,
        "architecture": "amd64",
        "compute_runtime": version,
        "igc": igc_tag,
        "packages": packages,
    }


def download(package: dict[str, Any], cache: Path, token: str | None) -> Path:
    target = cache / package["name"]
    if target.exists() and target.stat().st_size == package["size"] and sha256_file(target) == package["sha256"]:
        return target
    target.unlink(missing_ok=True)
    temporary = target.with_suffix(target.suffix + ".part")
    request = Request(
        package["url"],
        headers={
            "User-Agent": "intel-compute-runtime-bundle",
            **({"Authorization": f"Bearer {token}"} if token else {}),
        },
    )
    try:
        with urlopen(request, timeout=60) as response, temporary.open("wb") as output:
            shutil.copyfileobj(response, output, length=1024 * 1024)
        temporary.replace(target)
    except (HTTPError, URLError, TimeoutError, OSError) as error:
        temporary.unlink(missing_ok=True)
        raise BundleError(f"failed to download {package['url']}: {error}") from error
    if target.stat().st_size != package["size"] or sha256_file(target) != package["sha256"]:
        target.unlink(missing_ok=True)
        raise BundleError(f"checksum or size mismatch for {package['name']}")
    return target


def add_bytes(archive: tarfile.TarFile, name: str, content: bytes) -> None:
    info = tarfile.TarInfo(name)
    info.size = len(content)
    info.mode = 0o644
    info.uid = info.gid = 0
    info.uname = info.gname = "root"
    info.mtime = 0
    archive.addfile(info, io.BytesIO(content))


def build_archive(manifest: dict[str, Any], paths: dict[str, Path], output: Path) -> None:
    manifest_bytes = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode()
    sums = "".join(
        f"{package['sha256']}  packages/{package['name']}\n"
        for package in manifest["packages"]
    ).encode()
    with (
        output.open("wb") as raw,
        gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as compressed,
        tarfile.open(fileobj=compressed, mode="w", format=tarfile.PAX_FORMAT) as archive,
    ):
        add_bytes(archive, "SHA256SUMS", sums)
        add_bytes(archive, "manifest.json", manifest_bytes)
        for package in manifest["packages"]:
            path = paths[package["name"]]
            info = archive.gettarinfo(str(path), arcname=f"packages/{package['name']}")
            info.mode = 0o644
            info.uid = info.gid = 0
            info.uname = info.gname = "root"
            info.mtime = 0
            with path.open("rb") as source:
                archive.addfile(info, source)


def read_version(path: Path) -> str:
    lines = [line.strip() for line in path.read_text().splitlines() if line.strip()]
    if len(lines) != 1:
        raise BundleError(f"{path} must contain exactly one version")
    return lines[0]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version-file", type=Path, default=Path("compute-runtime.version"))
    parser.add_argument("--cache-dir", type=Path, default=Path(".cache/packages"))
    parser.add_argument("--output-dir", type=Path, default=Path("dist"))
    parser.add_argument("--resolve-only", action="store_true")
    args = parser.parse_args()
    try:
        token = os.environ.get("GITHUB_TOKEN")
        manifest = resolve(read_version(args.version_file), token=token)
        if args.resolve_only:
            print(json.dumps(manifest, indent=2, sort_keys=True))
            return 0
        args.cache_dir.mkdir(parents=True, exist_ok=True)
        args.output_dir.mkdir(parents=True, exist_ok=True)
        paths = {
            package["name"]: download(package, args.cache_dir, token)
            for package in manifest["packages"]
        }
        archive = args.output_dir / ARCHIVE_NAME
        build_archive(manifest, paths, archive)
        (args.output_dir / "SHA256SUMS").write_text(f"{sha256_file(archive)}  {archive.name}\n")
        print(archive)
    except (BundleError, OSError, json.JSONDecodeError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
