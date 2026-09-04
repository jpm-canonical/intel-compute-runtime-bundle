# Intel compute-runtime bundle

This repository packages one stable [Intel compute-runtime release](https://github.com/intel/compute-runtime/releases) and its compatible IGC packages into one verified amd64 archive for Canonical inference snaps. It does not build a snap or modify Intel's original `.deb` files.

The release artifact is `intel-compute-runtime-amd64.tar.gz`. It contains:

- `intel-opencl-icd` and gmmlib;
- IGC core and IGC OpenCL;
- ocloc and Level Zero;
- `manifest.json` with source releases, URLs, sizes, and SHA-256 hashes; and
- `SHA256SUMS` for all bundled packages.

Consumers extract the archive and split the packages between their Snapcraft parts at build time.

The resolver is verified against every complete stable release from `25.18.33578.6` onward. Earlier releases do not publish the required ocloc package and therefore fail closed.

## Automation

1. Hosted Renovate monitors stable `intel/compute-runtime` releases.
2. Renovate opens a PR changing only [`compute-runtime.version`](compute-runtime.version).
3. PR CI resolves the compatible package set, downloads and verifies every package, and builds the real archive.
4. GitHub auto-merges the PR after the required `Test` check passes.
5. The version change on `main` runs one workflow that builds the archive, creates a draft release, uploads it, and publishes it.

Release tags exactly match the Intel compute-runtime version, for example `26.31.39395.13`. The workflows use the standard `GITHUB_TOKEN`; this repository needs no custom Actions secrets. The hosted Renovate app, GitHub auto-merge, and branch protection requiring `Test / test` must be enabled in repository settings.

## Local development

Python 3.11 or newer is required; the tooling otherwise uses only the standard library.

```bash
make test
make resolve
make build
(cd dist && sha256sum --check SHA256SUMS)
make package
```

`make build` writes the unpacked bundle to `dist/`, where its package checksums can be verified directly. `make package` builds the release artifact at `dist/intel-compute-runtime-amd64.tar.gz` via a temporary staging directory.

## Consumer-side splitting

Both Snapcraft parts can use the same downloaded and extracted archive. The CLI ICD part installs the OpenCL ICD and gmmlib packages; the OpenVINO runtime part installs IGC, ocloc, and Level Zero. Package roles and exact filenames are available in `manifest.json`, so consumers do not need to embed Intel package versions.

Inference snaps must retain their amd64 guards, legacy-first installation order, and legacy ocloc symlink cleanup. The intentionally pinned legacy packages are not included in this modern bundle.
