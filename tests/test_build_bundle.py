from __future__ import annotations

import hashlib
import io
import json
import tarfile
import tempfile
import unittest
from pathlib import Path

from scripts import build_bundle

VERSION = "26.31.39395.13"
IGC_TAG = "v2.40.13"


def asset(name: str, content: bytes) -> dict[str, object]:
    return {
        "name": name,
        "size": len(content),
        "digest": f"sha256:{hashlib.sha256(content).hexdigest()}",
        "browser_download_url": f"https://github.com/example/releases/download/v1/{name}",
    }


def releases(old_level_zero: bool = False):
    contents = {
        "opencl_icd": b"opencl",
        "gmmlib": b"gmmlib",
        "igc_core": b"igc-core",
        "igc_opencl": b"igc-opencl",
        "ocloc": b"ocloc",
        "level_zero": b"level-zero",
    }
    level_zero = (
        "intel-level-zero-gpu_1.6.39395.13_amd64.deb"
        if old_level_zero
        else f"libze-intel-gpu1_{VERSION}-0_amd64.deb"
    )
    runtime = {
        "tag_name": VERSION,
        "draft": False,
        "prerelease": False,
        "body": (
            f"https://github.com/intel/intel-graphics-compiler/releases/tag/{IGC_TAG}\n"
        ),
        "assets": [
            asset(f"intel-opencl-icd_{VERSION}-0_amd64.deb", contents["opencl_icd"]),
            asset("libigdgmm12_22.10.0_amd64.deb", contents["gmmlib"]),
            asset(f"intel-ocloc_{VERSION}-0_amd64.deb", contents["ocloc"]),
            asset(level_zero, contents["level_zero"]),
            asset(f"intel-opencl-icd-dbgsym_{VERSION}-0_amd64.ddeb", b"debug"),
        ],
    }
    igc = {
        "tag_name": IGC_TAG,
        "draft": False,
        "prerelease": False,
        "body": "",
        "assets": [
            asset("intel-igc-core-2_2.40.13+22418_amd64.deb", contents["igc_core"]),
            asset("intel-igc-opencl-2_2.40.13+22418_amd64.deb", contents["igc_opencl"]),
            asset("intel-igc-core-devel_2.40.13+22418_amd64.deb", b"development"),
        ],
    }
    return contents, {
        (build_bundle.RUNTIME_REPO, VERSION): runtime,
        (build_bundle.IGC_REPO, IGC_TAG): igc,
    }


def fetcher(values):
    def fetch(repo: str, tag: str, token: str | None = None):
        del token
        return values[(repo, tag)]

    return fetch


class ResolveTests(unittest.TestCase):
    def test_parses_historical_igc_link_variants(self) -> None:
        cases = {
            "legacy markdown": (
                (
                    "[intel/intel-graphics-compiler@igc-1.0.17537.20]"
                    "(https://github.com/intel/intel-graphics-compiler/releases/tag/"
                    "igc-1.0.17537.20)"
                ),
                "igc-1.0.17537.20",
            ),
            "modern bare URL": (
                "https://github.com/intel/intel-graphics-compiler/releases/tag/v2.5.6",
                "v2.5.6",
            ),
            "repeated modern reference": (
                (
                    "[IGC](https://github.com/intel/intel-graphics-compiler/releases/tag/"
                    "v2.40.13)\nFor sums see https://github.com/intel/"
                    "intel-graphics-compiler/releases/tag/v2.40.13"
                ),
                "v2.40.13",
            ),
        }
        for name, (body, expected) in cases.items():
            with self.subTest(name=name):
                self.assertEqual(build_bundle.linked_igc_tag(body), expected)

    def test_rejects_ambiguous_igc_links(self) -> None:
        body = (
            "https://github.com/intel/intel-graphics-compiler/releases/tag/v2.5.6\n"
            "https://github.com/intel/intel-graphics-compiler/releases/tag/v2.6.0"
        )
        with self.assertRaisesRegex(build_bundle.BundleError, "exactly one linked IGC"):
            build_bundle.linked_igc_tag(body)

    def test_resolves_exactly_six_package_roles(self) -> None:
        _, values = releases()
        manifest = build_bundle.resolve(VERSION, fetcher(values))

        self.assertEqual(manifest["compute_runtime"], VERSION)
        self.assertEqual(manifest["igc"], IGC_TAG)
        self.assertEqual(
            [package["role"] for package in manifest["packages"]],
            ["opencl_icd", "gmmlib", "igc_core", "igc_opencl", "ocloc", "level_zero"],
        )

    def test_accepts_older_level_zero_package_name(self) -> None:
        _, values = releases(old_level_zero=True)
        manifest = build_bundle.resolve(VERSION, fetcher(values))
        level_zero = next(
            package for package in manifest["packages"] if package["role"] == "level_zero"
        )
        self.assertTrue(level_zero["name"].startswith("intel-level-zero-gpu_"))

    def test_rejects_prerelease(self) -> None:
        _, values = releases()
        values[(build_bundle.RUNTIME_REPO, VERSION)]["prerelease"] = True
        with self.assertRaisesRegex(build_bundle.BundleError, "not a stable"):
            build_bundle.resolve(VERSION, fetcher(values))

    def test_rejects_missing_role(self) -> None:
        _, values = releases()
        runtime = values[(build_bundle.RUNTIME_REPO, VERSION)]
        runtime["assets"] = [
            item for item in runtime["assets"] if not item["name"].startswith("intel-ocloc_")
        ]
        with self.assertRaisesRegex(build_bundle.BundleError, "exactly one.*ocloc"):
            build_bundle.resolve(VERSION, fetcher(values))

    def test_uses_release_note_checksum_without_api_digest(self) -> None:
        _, values = releases()
        runtime = values[(build_bundle.RUNTIME_REPO, VERSION)]
        selected = runtime["assets"][0]
        selected["digest"] = None
        expected = hashlib.sha256(b"release-note-value").hexdigest()
        runtime["body"] += f"{expected}  {selected['name']}\n"

        manifest = build_bundle.resolve(VERSION, fetcher(values))

        self.assertEqual(manifest["packages"][0]["sha256"], expected)

    def test_resolves_when_both_repositories_only_publish_note_checksums(self) -> None:
        _, values = releases()
        for release in values.values():
            checksum_lines = []
            for selected in release["assets"]:
                selected["digest"] = None
                checksum_lines.append(
                    f"{hashlib.sha256(selected['name'].encode()).hexdigest()}  {selected['name']}"
                )
            release["body"] += "\n" + "\n".join(checksum_lines)

        manifest = build_bundle.resolve(VERSION, fetcher(values))

        self.assertEqual(len(manifest["packages"]), 6)
        self.assertTrue(all(len(package["sha256"]) == 64 for package in manifest["packages"]))

if __name__ == "__main__":
    unittest.main()
