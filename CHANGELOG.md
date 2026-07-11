# Changelog

All notable changes to this skill are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/), and this project adheres to
[Semantic Versioning](https://semver.org/).

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
