# android-update-deps

> A Claude skill for the **safe, gated review and update of dependencies** in an Android
> (Kotlin/Gradle) project that uses a Gradle version catalog.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
![Claude Skill](https://img.shields.io/badge/Claude-Skill-8A2BE2)
![Platform](https://img.shields.io/badge/platform-Android%20%7C%20Kotlin%20%7C%20Gradle-3DDC84)

Upgrading dependencies is easy to get wrong: automated bumpers jump every library to its latest
version with **zero analysis**, silently breaking your build or your app. This skill does the
opposite — it turns "update my dependencies" into a **controlled, reviewable procedure** that
detects what's outdated, reasons about the risk, and **never changes anything without your explicit
confirmation**.

It's designed for real Android projects: version catalogs (`gradle/libs.versions.toml`), the Android
Gradle Plugin, Compose/Firebase BOMs, coupled version blocks (Kotlin ↔ KSP ↔ Compose Compiler),
JitPack libraries, and convention plugins (`build-logic/`).

## Why use it

- **Gated by design.** It detects, groups, classifies by risk, and **stops at a proposal** for you
  to approve — all / safe-only / a specific subset. Nothing is edited or committed until you say go.
- **Sees the whole picture, not just version numbers.** It groups coupled blocks and BOM-governed
  artifacts into single items, ignores transitive noise, and flags *hidden requirements* (a
  "safe-looking" minor that actually needs a higher `compileSdk`/AGP/Kotlin).
- **Covers the tool's blind spot.** The ben-manes `gradle-versions-plugin` can't see JitPack
  (`com.github.*`) libraries — the skill checks those **by hand**.
- **Verifies before it trusts.** Applied bumps are built with `:app:assembleDebug`; if something
  breaks, it isolates the culprit, reverts it, and returns to the proposal — never leaving your tree
  broken.
- **Clean git hygiene.** Commits locally on a feature branch (never the default branch, no push),
  one thematic commit per theme, with a **separate commit** for any code adaptation to new APIs.
- **Speaks your language.** User-facing prose mirrors the language you write in (English or Spanish).

## What it handles

| Concern | Behavior |
|---|---|
| **Version catalog** | Edits `gradle/libs.versions.toml` `version.ref`s — the single source of truth |
| **Detection** | `./gradlew dependencyUpdates --no-parallel` (ben-manes), aggregated across all modules |
| **JitPack** (`com.github.*`) | Checked manually via JitPack metadata / GitHub releases (plugin blind spot) |
| **BOMs** (Compose, Firebase, …) | Bumps only the BOM; ignores the governed child artifacts in the report |
| **Coupled blocks** | Kotlin ↔ KSP ↔ Compose Compiler, AGP ↔ Gradle wrapper, Retrofit, OkHttp, Room, Hilt… treated as single items |
| **Risk** | Semver magnitude + hidden `compileSdk`/AGP/Kotlin requirements + known vulnerabilities + license changes |
| **Verification** | `:app:assembleDebug` (plus tests/linters when relevant); isolates any culprit bump |
| **Code adaptation** | Post-bump deprecation/migration pass, in a separate `refactor(deps)` commit |

## How it works

A fixed, repeatable procedure (the skill stops at step 5 for your approval):

1. **Discover** the project shape — catalog, detection tool, JitPack libs, coupled blocks, SDK/JDK/wrapper.
2. **Detect** updates with the ben-manes plugin (+ a manual JitPack pass).
3. **Aggregate & dedupe** the per-module reports (helper script included).
4. **Filter noise** — drop BOM-governed children and transitive satellites; group coupled blocks.
5. **Classify by risk** and **propose** a table (safe vs. handle-with-care) — **⛔ GATE: waits for you.**
6. **Apply** only what you confirmed.
7. **Verify** with a build; isolate and revert any culprit.
8. **Commit** locally on a branch, one thematic `chore(deps)` commit, no push.
9. **Adapt the code** to new APIs if needed — separate `refactor(deps)` commit.

## Installation

### Option 1 — Claude Code plugin (recommended)

Add this repo as a plugin marketplace and install in one command:

```
/plugin marketplace add alvarose/android-update-deps
/plugin install android-update-deps@alvarose
```

Update later with `/plugin update android-update-deps@alvarose`.

### Option 2 — Manual skill install

Clone into a directory Claude Code scans for [skills](https://code.claude.com/docs/en/skills):

```bash
# Personal (all your projects)
git clone https://github.com/alvarose/android-update-deps.git ~/.claude/skills/android-update-deps

# or project-scoped (share it via your repo)
git clone https://github.com/alvarose/android-update-deps.git .claude/skills/android-update-deps
```

Or download the packaged `android-update-deps.skill` from the
[Releases](https://github.com/alvarose/android-update-deps/releases) page.

## Usage

Once installed, just ask Claude in an Android project — the skill triggers on phrases like:

- "review / update / bump the dependencies of my Android app"
- "what's outdated in my `libs.versions.toml`?"
- "actualiza las dependencias, solo las seguras"
- or run `/android-update-deps`

Claude will detect, analyze, and show you a risk-grouped proposal. You pick what to apply; it
verifies with a build and commits on a branch. **It won't touch anything without your OK.**

## Requirements

- An Android (Kotlin/Gradle) project, ideally with a version catalog (`gradle/libs.versions.toml`).
- The [ben-manes `gradle-versions-plugin`](https://github.com/ben-manes/gradle-versions-plugin)
  applied (the skill offers to add it temporarily if it's missing). On Gradle 9+ use `0.52.0+`.
- A working Android SDK (`sdk.dir` in the repo's `local.properties`).

## Repository layout

```
android-update-deps/
├── .claude-plugin/   # plugin + marketplace manifests (for /plugin install)
├── SKILL.md          # the skill: discovery, the 9-step gated procedure
├── reference.md      # coupled-versions table, JitPack procedure, report.json format
├── scripts/
│   └── aggregate-updates.py   # dedupe report.json across modules; list JitPack libs
├── evals/            # evaluation prompts + assertions used to test the skill
├── CHANGELOG.md
├── LICENSE
└── README.md
```

## Contributing

Issues and PRs welcome. The skill is intentionally **concise** and **gated** — proposals that add
scope should preserve those principles (see the "handle with care" philosophy in `SKILL.md`).

## License

[MIT](LICENSE) © Alvaro Serrano
