# Automate Intel compute-runtime updates

Status: initial plan, 2026-08-31

Inputs:

- Prior investigation: https://github.com/canonical/inference-snaps-dev/tasks/901f9d2c-a1b5-4cf2-b3f1-0e3efdd99661
- Upstream releases: https://github.com/intel/compute-runtime/releases
- Shared Renovate preset: `inference-snaps-dev/renovate/default.jsonc`

> The GitHub Tasks page is not available through the public or authenticated REST endpoints used in this workspace. Before implementation, compare any task-only conclusions with this plan.

## Goal

Open reviewable pull requests when Intel publishes a stable compute-runtime release, updating the complete compatible runtime stack without changing the intentionally pinned legacy stack. Prefer Renovate for release detection and PR lifecycle management. Do not automerge these updates until build and hardware tests are reliable.

## Current inventory

Five repositories currently download Intel release assets directly from `snap/snapcraft.yaml`:

| Repository | Part(s) | Rolling compute-runtime | Rolling IGC | Rolling gmmlib | Legacy compute-runtime |
| --- | --- | ---: | ---: | ---: | ---: |
| `gemma4-snap` | `cli-intel-opencl-icd`, `intel-compute-runtime` | `26.22.38646.4` | `v2.36.3` | `22.10.0` | `24.35.30872.36` |
| `gemma3-snap` | `cli-intel-opencl-icd`, `intel-compute-runtime` | `26.01.36711.4` | `v2.27.10` | `22.9.0` | `24.35.30872.36` |
| `deepseek-r1-snap` | `cli-intel-opencl-icd`, `intel-compute-runtime` | `26.01.36711.4` | `v2.27.10` | `22.9.0` | `24.35.30872.36` |
| `qwen-vl-snap` | `opencl-driver` | `24.52.32224.5` | `v2.5.6` | `22.5.5` | `24.35.30872.36` |
| `inference-snaps-cli` | `cli-intel-opencl-icd` | `26.01.36711.4` | not downloaded | `22.9.0` | `24.35.30872.36` |

As of 2026-08-31, the latest stable upstream release is `26.31.39395.13` (published 2026-08-21). Its declared compatible tuple is:

- compute-runtime: `26.31.39395.13`
- Intel Graphics Compiler: `v2.40.13`
- gmmlib: `22.10.0`

Intel is currently publishing approximately monthly.

All five repositories already extend `github>canonical/inference-snaps-dev//renovate/default.jsonc#v2`, so one shared Renovate rule can discover the dependency everywhere after each repository adopts a consistent marker.

### Reference packaging shape

`gemma4-snap` shows the intended two-part layout also used by `gemma3-snap` and `deepseek-r1-snap`:

| Part | Destination | Legacy packages | Rolling packages |
| --- | --- | --- | --- |
| `cli-intel-opencl-icd` | snap root | `intel-opencl-icd-legacy1` | `intel-opencl-icd`, `libigdgmm12` |
| `intel-compute-runtime` | `openvino-model-server` component | `intel-igc-core`, `intel-igc-opencl`, `intel-level-zero-gpu-legacy1` | `intel-igc-core-2`, `intel-igc-opencl-2`, `intel-ocloc`, `libze-intel-gpu1` |

`inference-snaps-cli` has only the CLI package set. `qwen-vl-snap` has an older combined `opencl-driver` part, so its migration needs package-role mapping rather than assuming the two-part layout.

## Important constraints

1. **This is a compatibility tuple, not three independent dependencies.** The compute-runtime release notes identify the IGC and gmmlib revisions used to build that runtime. Updating each repository to the independently latest IGC/gmmlib can produce an unsupported combination.
2. **A Renovate regex match has one authoritative `currentValue`.** Each URL can contain both a release tag and one or more different filename versions. Capturing the release tag lets Renovate query the datasource, but default replacement does not derive or update a different filename version. `autoReplaceStringTemplate` can reconstruct runtime filenames that deterministically repeat `newValue`; it cannot discover gmmlib versions or IGC build suffixes.
3. **Asset names contain values Renovate cannot derive from release tags alone.** IGC assets include an extra build suffix, for example `v2.40.13` publishes `intel-igc-core-2_2.40.13+22418_amd64.deb`.
4. **gmmlib is hosted inside the compute-runtime release.** Its filename version can change independently of the compute-runtime tag.
5. **Release tag formats differ.** compute-runtime uses unprefixed four-part tags, modern IGC uses `v2.x`, and legacy IGC uses `igc-1.x`. One generic versioning/match expression is inappropriate.
6. **Package names can change.** Current releases provide `libze-intel-gpu1`; older `qwen-vl-snap` packaging expects `intel-level-zero-gpu`. Simple string replacement would generate a missing asset.
7. **Repeated tags must move atomically.** Matching only one "canonical" URL in a shell block would avoid duplicate Renovate dependency records but leave the remaining URLs stale. Matching every URL independently can create partial/conflicting edits. Refactoring to one rolling version marker avoids both failure modes.
8. **The legacy stack is intentional and follows a different release policy.** Intel's modern release notes direct legacy platforms to the latest `24.35` release, while GitHub marks `24.35.30872.36` itself as a prerelease. It and its matching `igc-1.0.17537.24` should remain explicitly pinned unless Intel publishes a newer legacy-specific release and it is separately reviewed. A generic "stable releases only" rule must govern only the rolling stack, not discover legacy updates.
9. **A successful URL update is insufficient validation.** `dpkg --force-all` can hide dependency/order issues, and current PR checks do not build or exercise these packages.
10. **Architecture is currently amd64-only.** Automation must not introduce these assets into arm64 builds.

## Options

### Option A — Renovate plus a release-asset resolver (recommended)

Make the rolling compute-runtime release the single source of truth in each `snapcraft.yaml`:

```text
# renovate: datasource=github-releases depName=intel/compute-runtime versioning=loose
intel_compute_runtime_version=26.31.39395.13
```

Add a shared, tested helper in `inference-snaps-dev` that accepts the compute-runtime version and logical package roles. It should:

1. Fetch metadata for that exact stable compute-runtime release.
2. Read the compatible IGC tag from the release notes.
3. Select assets by strict allowlisted patterns rather than constructing uncertain filenames:
   - `intel-opencl-icd_*_amd64.deb`
   - `intel-ocloc_*_amd64.deb`
   - `libze-intel-gpu1_*_amd64.deb`
   - `libigdgmm12_*_amd64.deb`
   - `intel-igc-core-2_*_amd64.deb`
   - `intel-igc-opencl-2_*_amd64.deb`
4. Reject missing, duplicate, debug-symbol, wrong-architecture, draft, or prerelease assets.
5. Download only the package roles requested by a part and verify upstream checksums where available.
6. Emit the resolved tuple and asset names in build logs for auditability.

Add a regex custom manager to the shared Renovate preset for the annotated variable. Use one dependency name so every occurrence in a repository is updated in one PR. Add a package rule that:

- names/groups the PR as an Intel compute-runtime stack update;
- applies a short stability delay (suggested: 7 days);
- excludes prereleases;
- assigns appropriate labels/reviewers;
- leaves automerge disabled.

Only annotate the rolling version marker; do not expose the legacy variables to the custom manager. If a broader URL manager is retained during migration, add a defensive disabled rule for `intel/compute-runtime` at `24.35.30872.36` and legacy IGC versions matching `igc-1.*`.

Do not add modern IGC as an independently updatable Renovate dependency in consumers. It is a separate upstream release stream, but for this packaging purpose its selected version is subordinate to the IGC revision declared by the chosen compute-runtime release.

**Advantages**

- Keeps Renovate's release discovery, dashboard, scheduling, and PR lifecycle.
- A PR changes one readable version rather than many duplicated URLs.
- Always resolves the exact IGC/gmmlib tuple declared by the selected runtime release.
- Handles asset build suffixes and package renames centrally.
- Reusable by newly onboarded inference snaps.

**Costs/risks**

- Adds a build-time GitHub API dependency and release-note parsing. Cache/download behavior and unauthenticated API limits need consideration.
- The resolver must fail closed if Intel changes release-note or asset conventions.
- Every consuming repository must first be refactored to use the helper.
- Availability of the `dev` submodule during Snapcraft part execution must be proven in the prototype.

**Mitigation:** keep release parsing in a small library with fixture tests from several old/current releases, and optionally allow a checked-in resolved manifest as a fallback cache.

### Option B — Renovate plus a checked-in compatibility manifest

Maintain a machine-readable manifest in `inference-snaps-dev`, keyed by compute-runtime release, containing the exact IGC tag and all asset URLs/checksums. Renovate updates a single version marker in each consumer; build logic reads the matching manifest entry.

A small workflow or maintainer command generates and validates a new manifest entry from Intel's release API before consumer PRs are allowed.

**Advantages**

- Reproducible builds do not depend on parsing live release notes.
- Changes to the complete tuple and URLs are visible in review.
- Central place to encode package renames and exceptions.

**Costs/risks**

- Renovate alone cannot create the new manifest entry from related release metadata.
- Requires a two-stage process: publish/merge the manifest entry, then update consumers.
- The shared `dev` submodule revision and selected runtime version must remain coordinated.

This is a good fallback if build-time API access is unacceptable. It can also complement Option A as a cache/audit record.

### Option C — Scheduled GitHub Actions updater instead of Renovate

Add a scheduled/manual workflow in `inference-snaps-dev` or `inference-snaps-admin` that:

1. Queries the latest stable compute-runtime release.
2. Resolves and validates its complete dependency tuple and assets.
3. Enumerates onboarded repositories.
4. Runs an updater script for each affected repository.
5. Opens one PR per repository, updating all relevant parts together.

Use a GitHub App or narrowly scoped token for cross-repository pull requests.

**Advantages**

- Full control over release-note parsing, asset selection, package transitions, PR bodies, and cross-repository coordination.
- Can include generated compatibility details and test evidence in each PR.
- Does not require Renovate `postUpgradeTasks`, which hosted Renovate commonly restricts for security.

**Costs/risks**

- More credentials, workflow code, monitoring, retries, and ownership than Renovate.
- Reimplements dependency scheduling and PR deduplication.
- Cross-repository partial failures need explicit handling.

Choose this if Renovate cannot support the required transformation safely.

### Option D — Renovate-only regex replacements of current URLs

Add regex managers for every compute-runtime, IGC, gmmlib, and filename occurrence, then group their PRs.

It is straightforward to *detect* newer non-legacy compute-runtime tags by anchoring matches to package names such as `intel-opencl-icd_` or `intel-ocloc_`, and newer modern IGC tags by anchoring to `intel-igc-core-2_`. Legacy package names can be omitted from those expressions or disabled with `matchCurrentVersion` rules.

**Advantages:** smallest initial infrastructure change and useful as release notification.

**Not recommended for source modification:** tag detection does not make the complete URL replacement safe. Renovate cannot reliably derive gmmlib versions or IGC build suffixes from the compute-runtime release, cannot enforce Intel's compatibility tuple, and will not automatically handle package-name transitions. Selecting one canonical occurrence leaves sibling URLs unchanged, while treating all occurrences as independent dependencies permits partial updates. It may open plausible-looking but unbuildable or unsupported PRs.

This option is acceptable only as a release notification (for example, a dependency-dashboard item), not unattended source modification.

### Option E — Central Intel runtime release bundles (prototype implemented)

Publish the validated Intel userspace packages as GitHub release artifacts, following the model used by `canonical/llama.cpp-builds`; do not build a full or content snap. The prototype is on the `investigate-release-bundles` branch of `jpm-canonical/intel-compute-runtime-bundle`.

Each release contains one reproducible `intel-compute-runtime-amd64.tar.gz` archive with all six modern package roles used by the two parts in `gemma4-snap`: OpenCL ICD, gmmlib, IGC core, IGC OpenCL, ocloc, and Level Zero. The original `.deb` files remain intact inside the archive, accompanied by a machine-readable manifest and SHA-256 sums. Consuming snaps split the packages between their CLI ICD and OpenVINO parts at build time while retaining their existing `dpkg --root` installation, amd64 guards, legacy-first ordering, and symlink cleanup.

Hosted Renovate monitors stable `intel/compute-runtime` releases and updates one version file. The PR workflow resolves the compatible IGC and package assets, downloads and verifies them, and builds the actual archive. GitHub auto-merges only after this test passes. No generated lock needs to be committed: a failed or changed upstream layout blocks the PR before merge.

The version change on `main` runs one workflow that repeats the verified build, creates a draft GitHub release, uploads the archive and its checksum, and publishes it. The release tag exactly matches the compute-runtime release, for example `26.31.39395.13`. This uses the standard `GITHUB_TOKEN` and requires no self-hosted Renovate or custom repository secrets.

**Advantages**

- Removes duplicated Intel package discovery and per-package downloads from model snaps.
- Makes the exact compatibility tuple, source URLs, sizes, and checksums reviewable in one repository.
- Gives downstream snaps one stable artifact that Renovate can update atomically.
- Avoids content-interface, store-review, ABI, and lifecycle complexity of a shared content snap.
- Fails before publication if package roles are missing, ambiguous, wrong-architecture, or unverifiable.

**Costs/risks**

- Resolves and downloads the same upstream package set once in PR CI and once in the release workflow.
- Requires the hosted Renovate app, GitHub auto-merge, and a required PR test to be enabled for the repository.
- Downstream snaps still need hardware validation and retain responsibility for legacy packages.
- GitHub release assets become a build dependency; provenance/attestation and downstream archive checksum pinning still need policy decisions.
- The first consumer migration must prove that consumer-side package splitting reproduces the existing `gemma4-snap` filesystem and store-review behavior.

This is now the preferred prototype to evaluate before implementing the build-time resolver in every consumer. Keep Option A as the fallback if central artifact ownership or release credentials are unacceptable.

## Reconciliation with the prior feasibility analysis

The supplied analysis is consistent with this plan on the essential facts:

- legacy and rolling package sets need different policy;
- the current recommended legacy tag is marked as a prerelease and therefore cannot share the rolling stable-release filter;
- release tags and package filename versions are not always the same;
- gmmlib and IGC asset build suffixes prevent complete default regex replacement;
- modern and legacy IGC tag formats differ; and
- filename-prefix anchoring can distinguish many rolling packages from legacy packages.

This plan deliberately goes further in four areas:

1. The two-part package table in that analysis describes the current `gemma4-snap` reference implementation, not the whole workspace. Five repositories are affected, including CLI-only and older combined-part variants.
2. Although compute-runtime and IGC are separate upstream repositories, consumers should not independently select their latest releases. The compute-runtime release declares the IGC revision used to build the compatible stack.
3. `libigdgmm12` does not need to remain manual-only: a resolver can select its uniquely matching release asset, or a generated compatibility manifest can record it.
4. Updating only the first/canonical URL occurrence is not a complete automation strategy. A single annotated variable plus resolver/helper is what makes every occurrence move atomically.

## Recommended phased implementation

### Phase 0 — Confirm policy and baseline

- [ ] Compare this plan with any details only visible in the referenced GitHub Task.
- [ ] Confirm `24.35.30872.36` is the legacy support policy, not part of rolling updates.
- [ ] Decide whether build-time GitHub API access is acceptable; if not, select Option B.
- [ ] Agree on a rollout policy: stable releases only, 7-day minimum age, manual merge.
- [ ] Bring all rolling stacks to one reviewed baseline (currently `26.31.39395.13` / `v2.40.13` / `22.10.0`) before judging future automated diffs.

### Phase 1 — Build and test the resolver

- [ ] Implement the helper in `inference-snaps-dev` with no dependency on `gh`; use a runtime available in Snapcraft builds and authenticated API access only when optionally supplied.
- [ ] Keep logical package sets for CLI-only and full Intel GPU runtime consumers.
- [ ] Add fixture tests for at least `24.52.32224.5`, `26.01.36711.4`, `26.22.38646.4`, and `26.31.39395.13`.
- [ ] Test missing assets, duplicate matches, prereleases, malformed release notes, wrong architecture, checksum failure, API rate-limit/error responses, and the Level Zero package rename.
- [ ] Document the intentional legacy pins next to their variables.

### Phase 2 — Renovate proof of concept in `gemma4-snap`

`gemma4-snap` is the preferred prototype because it has both CLI-only and full runtime parts and is closest to the latest tuple.

- [ ] Refactor duplicated modern URLs to one annotated compute-runtime variable and helper calls.
- [ ] Add the shared regex manager and non-automerge package rule in `inference-snaps-dev/renovate/default.jsonc` on the `v2` branch.
- [ ] Validate the Renovate preset and run a dry run against the prototype.
- [ ] Confirm one new stable release produces one PR and updates both modern parts to the same version.
- [ ] Confirm legacy URLs are not modified.

### Phase 3 — CI and hardware gates

Add a lightweight PR check that resolves metadata and verifies every selected asset/checksum. Then require an amd64 Snapcraft build for dependency/package-layout validation.

Before merge, test on representative hardware:

- [ ] CLI `clinfo`/GPU detection on a modern Intel GPU.
- [ ] OpenVINO/Level Zero inference on a modern Intel GPU.
- [ ] Legacy OpenCL path on at least one supported legacy Intel GPU.
- [ ] Non-Intel and arm64 behavior remains unchanged.
- [ ] Snap review passes (especially no escaping `/etc/alternatives/ocloc` symlinks).
- [ ] Installed package/library inventory contains the expected modern and legacy files and no debug packages.

Initially record hardware validation manually in the PR. Automate it with the existing test infrastructure when suitable runners are available.

### Phase 4 — Roll out

- [ ] Apply the same pattern to `gemma3-snap`.
- [ ] Apply the same pattern to `deepseek-r1-snap`.
- [ ] Apply it to `qwen-vl-snap`, explicitly migrating the modern Level Zero package role.
- [ ] Apply the CLI-only package set to `inference-snaps-cli`.
- [ ] Scan the authoritative onboarded repository list during CI so newly added direct Intel release URLs are flagged.
- [ ] Document onboarding guidance: use the shared helper and Renovate marker; do not copy fixed Intel asset URLs.

### Phase 5 — Operate and reassess

- [ ] Track failed update PRs and upstream format changes.
- [ ] Pin/close a release update centrally if hardware regression is found.
- [ ] Consider automerge only after several releases pass build and hardware gates consistently; hardware-driver updates should probably remain manual-merge.
- [ ] Reassess Option E if duplicated build/download cost remains significant.

## Acceptance criteria

Automation is ready for broad rollout when:

1. A synthetic/new stable compute-runtime release causes exactly one Renovate PR per opted-in repository after the stability delay.
2. Every modern occurrence in that repository moves to the same compute-runtime release.
3. Matching IGC/gmmlib assets are resolved from that release's declared tuple without hand-edited build suffixes.
4. Legacy pins remain unchanged.
5. Missing/renamed/ambiguous assets fail before Snapcraft packaging rather than silently selecting another file.
6. Resolver unit/fixture tests, amd64 snap build, store review, and required hardware smoke tests pass.
7. The PR clearly lists the old/new runtime tuple, selected assets, upstream release link, and rollback instructions.

## Open questions

- Can Snapcraft builds rely on the checked-out `dev` submodule path at the point these parts run, or should the helper be copied into each repository by the shared tooling?
- Is unauthenticated GitHub API access acceptable and reliable in Launchpad/remote builds, or is a checked-in manifest required?
- Should CLI-only consumers ship gmmlib from the selected compute-runtime release, or can that dependency be sourced consistently another way?
- Which modern and legacy Intel GPU models are mandatory release gates, and where can those tests run?
- Should updates be synchronized across repositories or allowed to roll out independently after the shared checks pass?
- Does the existing Renovate deployment support custom stability/package rules from the shared `v2` preset as expected?
