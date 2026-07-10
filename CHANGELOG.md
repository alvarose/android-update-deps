# Changelog

All notable changes to this skill are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/), and this project adheres to
[Semantic Versioning](https://semver.org/).

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
