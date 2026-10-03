# Changelog

All notable changes to this skill are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/), and this project adheres to
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added
- **Continuous integration** (`.github/workflows/ci.yml`) on pushes, PRs, tags and weekly:
  - tests on Python 3.8 and current Python, on Linux and Windows;
  - the Agent Skills spec (`skills-ref`), `claude plugin validate --strict` and
    `gh skill publish --dry-run`;
  - a canary that runs the planner on the fixture against the live repositories and checks the
    policy invariants (toolchain never "safe", "safe" items have no unmet requirements).
- Repository tests: the version matches across `plugin.json`, `marketplace.json`, `SKILL.md` and
  this changelog (and the tag on tag builds). The planner runs offline on the fixture, and
  `aggregate-updates.py` runs on a synthetic report.
- Dependabot for the workflow's actions.
- **Trigger evals** (`evals/triggers/`, run with `claude plugin eval`): 9 requests that should load
  the skill (English and Spanish, terse or indirect) and 10 near-misses that shouldn't (other
  ecosystems, adding a new library, Groovy → Kotlin DSL, `targetSdk`, Renovate, a duplicate-class
  error). First run, 3 runs per case: 57/57 as expected with the 1.4.0 description, so the
  description is unchanged. A repository test checks that the cases are well formed.

## [1.4.0] — 2026-10-03

Deterministic planning engine: the mechanical part of the review moves from prose into a script.

### Added
- **`scripts/plan.py`** drafts the tiered proposal (Markdown, or `--json`). It reads the catalog
  line by line (comments included, no `tomllib` needed) and groups entries by `version.ref`. It
  labels toolchain items (AGP, Kotlin, KSP and its scheme, the Gradle wrapper) and BOM-governed
  children. Candidates come from the ben-manes report or, with `--source metadata`, straight from
  Maven metadata with no Gradle run. JitPack libraries are checked automatically. It verifies the
  target exists for every member of a block, reads AAR `minCompileSdk` /
  `minAndroidGradlePluginVersion` and the POM's `kotlin-stdlib`, and finds the highest compatible
  version or a cautious step. It also flags major jumps, skipped minors, releases younger than a
  3-day cooldown, hold comments, unused entries and unpublished current versions, and detects a
  report older than the catalog. `compileSdk` is read from the catalog, the build scripts or
  convention-plugin constants.
- A versioned **eval fixture** (`evals/fixtures/android-catalog-fixture/`, AGP 9.2.1 + built-in
  Kotlin), so evals no longer depend on a temporary directory.
- **Offline unit tests** for the planner (`tests/`, `python3 -m unittest discover -s tests`).
- Offer to record declined/deferred items as `# held: <reason>` in the catalog.
- From two validation runs of the planner on the fixture (full flow, and no Gradle):
  - **Unused entries without a report**: the build files are scanned for catalog references
    (type-safe accessors, bundles, `findLibrary`/`findPlugin`, plugin IDs). A plugin only declared
    with `apply false` is a decision, not a bump. Dynamic lookups switch the scan off.
  - An unused entry is still checked against its repository (an unpublished current version shows).
  - A **compatible alternative** that is low-risk gets its own "safe" row next to the latest under
    "handle with care".
  - A `compileSdk` raise needed by several items is **one "needs a decision" item** naming them;
    AGP and Kotlin requirements are attached to those items.
  - **Effective Kotlin without Gradle**: on AGP 9 it is inferred from the Kotlin Gradle plugin in
    AGP's POM (or the catalog's, when an applied plugin raises it), with a warning to confirm it.
  - AGP items say whether the target changes the bundled Kotlin Gradle plugin, point to the Gradle
    and Android Studio minimums, and offer the latest patch of the current minor as the cautious step.
  - The Gradle wrapper honours the cooldown (release date from `services.gradle.org`).
  - `minCompileMinorSdk` is read, and a thin wrapper (e.g. `core-ktx`) is checked through the
    artifact it pulls in at the same version (`core`).
  - A current version newer than the latest stable names that stable version; the Kotlin item keeps
    its target when it is a decision.
  - Both scripts write UTF-8 (the Windows console code page broke on `→`).

### Changed
- `SKILL.md` restructured around the planner: Discovery → Detect → **Plan** → rules → **GATE** →
  Apply → Verify → Commit → Adapt. Manual procedures stay in `references/reference.md` as the
  fallback without Python. The latest Gradle comes from `services.gradle.org` (a report can be stale).
- Discovery says which `buildEnvironment` line holds the effective Kotlin (the resolved Kotlin Gradle
  plugin, not Gradle's embedded `kotlin-stdlib`).

## [1.3.0] — 2026-10-03

Correctness update for the 2026 toolchain, plus a new repository layout.

### Changed
- **Repository layout:** the skill now lives in `skills/android-update-deps/` — the layout expected by
  `gh skill`, `npx skills`, the Agent Plugins spec and the Codex/Antigravity plugin formats. The
  Claude Code plugin picks it up from `skills/` (no `skills` key in `plugin.json` any more).
- **KSP rules rewritten.** KSP 2.3.0+ is versioned independently of Kotlin; the old
  `<kotlin>-<ksp>` scheme is tied to Kotlin and, on AGP 9, pins Kotlin to 2.2.10 (AGP depends on
  KGP 2.2.10 and KSP `2.2.10-2.0.2`). Migrating KSP to 2.3.x is now proposed as the prerequisite for
  a Kotlin bump. The previous "KSP follows Kotlin / KSP2 tracks the Kotlin line" guidance was wrong.
- **Effective toolchain:** with AGP 9 built-in Kotlin, the skill reads the effective Kotlin and KSP
  versions from `./gradlew buildEnvironment` instead of trusting the catalog.
- **ben-manes guidance updated:** 0.55+ supports parallel builds and writes one merged report;
  `--no-parallel` is only needed for older versions. The `ConcurrentModificationException` was a
  pre-0.54 bug.
- **Hidden requirements are checked before the gate**, by reading each candidate AAR's
  `aar-metadata.properties` (`minCompileSdk`, `minAndroidGradlePluginVersion`) and the
  `kotlin-stdlib` version in its POM, instead of waiting for the build to fail. New coupling
  examples: Compose BOM 2026.08.00+ (Compose 1.12) needs `compileSdk` 37 and AGP ≥ 9.1; AGP 9 needs
  KSP ≥ 2.3.6 and Hilt ≥ 2.59.2.
- **Gradle wrapper upgrades** use the wrapper task with `--gradle-distribution-sha256-sum`, run twice.
- README: install instructions for `npx skills`, `gh skill`, Antigravity / Antigravity CLI, Gemini
  CLI (`--path`), Android Studio and Codex (`$android-update-deps`); a "What the skill runs" section.
  The packaged `.skill` file is no longer attached to releases.

### Added
- `scripts/versions.init.gradle.kts`: injects the ben-manes plugin (latest release, JSON output,
  pre-releases rejected, Gradle check on) **without editing the project's build files**. Replaces the
  "add the plugin temporarily, then revert" step.
- `scripts/aggregate-updates.py` rewrite: reads the merged report (and per-project reports from older
  plugins), labels Kotlin-toolchain and AGP-internal artifacts as noise, lists step-down candidates
  (`available.patch` / `available.minor`), surfaces skipped configurations and other unresolved
  entries, ignores pre-release candidates, and gains `--help`, `--json` and documented exit codes.
- Explicit rules: a blanket "update everything" still gets the proposal first; the Gradle wrapper
  joins Kotlin and AGP as never-"safe" items; changelogs and release notes are data, not
  instructions; proposed versions must exist in their repository before editing.
- From a validation run of 1.3.0 on an AGP 9 fixture:
  - A **"needs a decision"** group for entries that aren't bumps: a current version missing from its
    repository or newer than the latest stable, and unused catalog entries.
  - JitPack libraries are listed **from the catalog**: unused entries never appear in the report.
  - `maven-metadata.xml` `<release>` can be a pre-release (AGP alphas, Kotlin betas): take the newest
    stable from `<versions>`.
  - Moving Kotlin under AGP 9 built-in Kotlin: a newer Kotlin Gradle plugin goes on the top-level
    `buildscript` classpath (per the AGP 9 release notes); the catalog ref alone may change nothing.
  - Skipping several minors is not "safe" by default; commit only the files you changed, never
    `build/` or `.gradle/`.
  - `aggregate-updates.py` treats `kotlin-stdlib` / `kotlin-reflect` / `kotlin-test` as Kotlin
    toolchain (they move with the `kotlin` ref).

## [1.2.0] — 2026-08-06

### Changed
- **Cross-agent portability.** Aligned with the [AgentSkills.io](https://agentskills.io) open format
  so the same `SKILL.md` folder works in Claude Code, OpenAI Codex, Gemini CLI, and other compatible
  agents. Added the standard `metadata` (author, version) and `compatibility` frontmatter fields, and
  moved `reference.md` → `references/reference.md` (idiomatic layout).
- **Agent-neutral wording.** Replaced Claude-specific tool references in the instructions with generic
  verbs (edit / fetch / search) and reframed the docs from "Claude skill" to "agent skill". The Claude
  plugin (`.claude-plugin/`) is kept for one-command `/plugin install`.
- **Multi-agent install docs.** README now covers Claude Code (plugin or `~/.claude/skills/`), Gemini
  CLI (`gemini skills install <git-url>`), OpenAI Codex, and the universal `~/.agents/skills/` directory.

## [1.1.3] — 2026-07-11

### Added
- **AGP 9 built-in Kotlin constraint**: on AGP 9+ (which ships a *built-in Kotlin*), KSP must be
  compatible with that built-in Kotlin, which can cap how far Kotlin/KSP can be bumped (if no
  compatible KSP exists yet, Kotlin is stuck) — verify the matrix before proposing a Kotlin bump.
- **Step-down on verify failure**: when a bump doesn't compile, try the *highest version that still
  compiles* before reverting outright — a library's latest may pull a too-new `kotlin-stdlib` the
  project's compiler can't read, while the previous minor works (e.g. Coil `3.5.0` fails on a Kotlin
  2.2 compiler but `3.4.0` compiles).

## [1.1.2] — 2026-07-11

### Added
- **Detection fallback when `dependencyUpdates` won't run** — a ben-manes × Gradle incompatibility
  (e.g. `ConcurrentModificationException` on some Gradle 9.x, or a removed-API error): try a newer
  ben-manes version with `--no-parallel`, and if it still fails, fall back to **manual metadata
  detection**. The JitPack `maven-metadata.xml` technique generalizes to any repository (Maven
  Central, Google's Maven repo), so detection is never blocked by the plugin; revert any temporary
  plugin afterwards.

## [1.1.1] — 2026-07-11

### Fixed
- When the skill adds the ben-manes plugin temporarily (projects that don't already have it), it now
  uses the plugin's **latest stable** version instead of pinning an old one — a dependency-update
  tool shouldn't introduce an outdated dependency — and configures it correctly for the rest of the
  flow: `outputFormatter = "json,plain"` (so `aggregate-updates.py` finds `report.json`),
  `gradleReleaseChannel = "current"` (wrapper detection), and a `rejectVersionIf {}` that skips
  pre-releases. It reverts the change after the report.

## [1.1.0] — 2026-07-11

### Changed
- **Blocking toolchain bumps now always require their own explicit confirmation** and never sit in
  the "safe" batch — the Kotlin version (with its coupled KSP / Compose Compiler), AGP, and the
  Gradle wrapper. An "apply the safe ones" approval never moves them, even for a minor/patch.

### Added
- **Explicit KSP handling**: a coupled KSP bump rides Kotlin's confirmation (no double-ask); a
  standalone KSP bump (same Kotlin line) is "handle with care" — it drives annotation processing
  (Room, Hilt) — not "safe".
- **Gradle wrapper detection**: `scripts/aggregate-updates.py` now surfaces the wrapper update from
  the report's `gradle` section (requires `gradleReleaseChannel = "current"`); documented in
  `SKILL.md` and `reference.md`.
- **SDK-level scope boundary** (`compileSdk` / `minSdk` / `targetSdk`): raise `compileSdk` only when
  a confirmed dependency requires it (coupled, high-risk, own confirmation); `targetSdk` is out of
  scope (a Play-Store behavior migration — flagged and deferred); `minSdk` is not changed but flagged
  when a dependency raises the effective minimum.

## [1.0.0] — 2026-07-10

First public release.

### Features
- Controlled, **gated** dependency-update procedure for Android (Kotlin/Gradle) projects using a
  Gradle version catalog — detect, aggregate, filter, classify by risk, **propose (gate)**, apply,
  verify, commit, and adapt code, as a fixed 9-step flow.
- **JitPack** (`com.github.*`) handling — the ben-manes plugin's blind spot — via manual metadata /
  GitHub-release checks.
- **BOM-aware** (Compose, Firebase): bumps only the BOM, ignores governed children.
- **Coupled-block awareness**: Kotlin ↔ KSP (both KSP1 `<kotlin>-<rev>` and KSP2
  `<kotlin-major.minor>.<patch>` schemes) ↔ Compose Compiler, AGP ↔ Gradle wrapper, Retrofit,
  OkHttp, Room, Hilt, Coroutines, and more.
- **Risk analysis beyond semver**: hidden `compileSdk`/AGP/Kotlin requirements, known
  vulnerabilities (Play SDK Index), and license changes.
- **Build verification** with `:app:assembleDebug`, isolating any culprit bump.
- **Clean git hygiene**: feature-branch commits, thematic `chore(deps)` / `refactor(deps)` split,
  no push.
- **Language mirroring**: user-facing prose follows the user's language (English/Spanish).
- Helper script `scripts/aggregate-updates.py` to dedupe `report.json` across modules.

### Validated
- Behavior benchmarked against a real Android fixture and dogfooded on a production multi-module app.
