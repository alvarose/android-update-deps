# android-update-deps

**Update your Android dependencies without breaking the build.**

An [Agent Skill](https://agentskills.io) that reviews the Gradle version catalog of an Android
(Kotlin/Gradle) project the way a careful senior developer would. It checks:

- hidden `compileSdk` / AGP / Kotlin requirements;
- coupled versions;
- known vulnerabilities;
- Google Play's 16 KB page-size rule.

It proposes a plan grouped by risk and **changes nothing until you approve it**. It also reviews the
PRs that Dependabot and Renovate open.

One skill folder works in Claude Code, OpenAI Codex, Antigravity, Gemini CLI, Android Studio's
agent and any other Agent-Skills-compatible tool.

[![CI](https://github.com/alvarose/android-update-deps/actions/workflows/ci.yml/badge.svg)](https://github.com/alvarose/android-update-deps/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/alvarose/android-update-deps)](https://github.com/alvarose/android-update-deps/releases)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
![Agent Skill](https://img.shields.io/badge/Agent-Skill-6E56CF)
![Platform](https://img.shields.io/badge/platform-Android%20%7C%20Kotlin%20%7C%20Gradle-3DDC84)

## The problem

"Bump everything to latest" breaks Android builds in ways that version numbers don't show:

- `core-ktx` 1.16 → 1.19 looks like a minor bump, but the new AAR requires `compileSdk` 37.
- A newer Compose BOM brings Compose 1.12, which needs `compileSdk` 37 and AGP 9.1.
- Under AGP 9's built-in Kotlin, changing `kotlin` in the catalog may change nothing at all.
- An old-scheme KSP (`2.2.10-2.0.2`) pins your Kotlin version anyway.
- A library still ships native code aligned to 4 KB, while Google Play requires 16 KB page-size
  support from apps targeting Android 15+.
- The version you pinned two years ago has a published CVE.

A version bumper opens the PR and your CI finds out later. This skill checks **before** it proposes
anything.

## What you get

When asked to *"review the dependencies"* of the [sample app](evals/fixtures/android-catalog-fixture/)
in this repo (an AGP 9 project kept deliberately behind), the agent proposes this. Trimmed, from a
real run:

| Tier | Item | Proposed | Why |
|---|---|---|---|
| **safe** | `gson` | 2.8.8 → **2.8.9** | **security:** closes GHSA-4jrv-ppp4-jm57 (high, CVE-2022-25647) with the smallest step |
| **safe** | `camera-core` | 1.3.0 → **1.4.2** | first release whose native libraries are 16 KB aligned |
| **safe** | `core-ktx` | 1.16.0 → **1.18.0** | the highest version that fits `compileSdk` 36 |
| care | `core-ktx` | 1.16.0 → 1.19.1 | needs `compileSdk` 37: coupled to that change |
| care | `compose-bom` | 2026.06.00 → 2026.09.00 | Compose 1.12 needs `compileSdk` 37 |
| care | AGP, Gradle wrapper | 9.2.1 → 9.4.1, 9.6.1 → 9.8.0 | toolchain: each needs its own explicit yes |
| decision | `compileSdk` | 36 → 37 | needed by `core-ktx` and the BOM; a change of its own |
| decision | `ksp` | `2.2.10-2.0.2` | the old scheme pins Kotlin on AGP 9: migrate to 2.3.x first |
| decision | `android-pdfview` (JitPack) | `3.2.0-beta.1` | unused, and that version isn't published |

Every item links its release notes. Reply *"apply the safe ones"* and the agent:

1. edits `libs.versions.toml`;
2. builds `:app:assembleDebug`, and steps down any bump that breaks;
3. commits on a branch.

Kotlin, AGP and the wrapper only move when you name them.

## Quick start

```bash
npx skills add alvarose/android-update-deps     # detects your agents (Claude Code, Codex, …)
```

Then, in your Android project, ask your agent:

> Review the dependencies of this app and propose updates by risk. Don't change anything yet.

Other ways to install, per agent, are in [Installation](#installation).

## Why use it

- **Gated by design.** It stops at a proposal: you approve all of it, the safe items only, or a
  list. Nothing is edited or committed before that. Kotlin, AGP and the Gradle wrapper always need
  their own explicit yes.
- **Android-aware, not semver-aware.**
  - It groups coupled blocks and BOM-managed libraries into single items.
  - It reads each candidate AAR's `minCompileSdk` and minimum AGP, and the `kotlin-stdlib` in its
    POM.
  - When the latest doesn't fit, it finds the highest version that does.
  - It knows AGP 9's built-in Kotlin and both KSP version schemes.
- **Security and Google Play first.** These go to the top, each with the smallest version that fixes
  it:
  - known vulnerabilities (OSV);
  - native libraries that aren't 16 KB aligned;
  - Play SDK Index findings.
- **Reviews your bots' PRs.** It classifies each open Dependabot or Renovate PR as **merge
  candidate / review first / hold**, using the same checks plus:
  - the CI result;
  - merge conflicts;
  - files that aren't dependency files;
  - superseded PRs.
- **Doesn't touch your build to look.** It uses the
  [ben-manes plugin](https://github.com/ben-manes/gradle-versions-plugin) through an init script, or
  reads Maven metadata directly when Gradle can't run. JitPack libraries, which the plugin can't
  see, are checked too.
- **Verifies before it trusts.** Applied bumps are built. A bump that fails is stepped down to the
  highest version that compiles, or reverted. Your tree is never left broken.
- **Clean git history.** Local commits on a feature branch, one per theme, never pushed unless you
  ask. Code changes for new APIs go in a separate `refactor(deps)` commit.
- **Speaks your language.** It answers in the language you write in (English or Spanish).

## Works with Dependabot and Renovate

Keep your bots. When they open PRs, ask your agent *"review the open Dependabot PRs"* (or Renovate).
The bundled `review_prs.py` reads only the files each PR changes and judges the proposed version
like any other update. For each PR it reports:

- whether the CI failed, naming the checks;
- whether a check fails on *every* bot PR (then the base branch is the likely culprit);
- whether the author really is the bot;
- whether the PR touches anything besides dependencies.

It never reads PR descriptions and never merges, comments or closes. Each of those actions waits
for your explicit yes.

## How it works

A fixed, repeatable procedure. The skill stops at step 4 for your approval.

0. **Discover** the project: catalog, detection path, effective Kotlin and KSP, conventions.
1. **Detect** updates with the ben-manes plugin, or from Maven metadata.
2. **Plan:** `scripts/plan.py` drafts a tiered proposal. The agent reads the release notes and
   checks the draft.
3. **Apply the rules:** safe / handle with care / needs a decision. The toolchain is always separate.
4. **Propose** the table. **⛔ GATE: it waits for you.**
5. **Apply** only what you confirmed.
6. **Verify** with a build, plus an R8 release build for reflection-heavy libraries. It steps down
   or reverts any culprit.
7. **Commit** locally on a branch, with one thematic `chore(deps)` commit and no push.
8. **Adapt the code** to new APIs if needed, in a separate `refactor(deps)` commit.

## Installation

Agent Skills are an [open format](https://agentskills.io/specification). The skill lives in
[`skills/android-update-deps/`](skills/android-update-deps/), and the same folder works in every
compatible agent.

### Any agent, with an installer

```bash
# skills CLI (vercel-labs/skills): detects your agents; -g for a user-level install
npx skills add alvarose/android-update-deps

# GitHub CLI (gh skill, preview)
gh skill install alvarose/android-update-deps android-update-deps
```

### Claude Code

Install it as a plugin, then update it with `/plugin update android-update-deps@alvarose`:

```
/plugin marketplace add alvarose/android-update-deps
/plugin install android-update-deps@alvarose
```

### OpenAI Codex

Copy the skill folder to `~/.agents/skills/` (all projects) or to a repo's `.agents/skills/`, then
invoke it with `$android-update-deps`.

### Antigravity / Antigravity CLI

Copy the skill folder to the workspace's `.agents/skills/`. To install it globally, copy it to
`~/.gemini/antigravity-cli/skills/` (CLI) or `~/.gemini/config/skills/` (app). Invoke it with
`/android-update-deps`.

### Gemini CLI

```bash
gemini skills install https://github.com/alvarose/android-update-deps --path skills/android-update-deps --consent
```

### Android Studio (Gemini agent)

Copy the skill folder to the project's `.skills/` (or `.agent/skills/`), then invoke it with
`@android-update-deps`.

### Manual copy

```bash
git clone https://github.com/alvarose/android-update-deps.git
cp -r android-update-deps/skills/android-update-deps <your-agent-skills-dir>/
```

## Usage

Ask your agent in an Android project. The skill triggers on requests like these:

- "review / update / bump the dependencies of my Android app"
- "what's outdated in my `libs.versions.toml`?"
- "do any of our libraries have known CVEs?" / "Play says we're not 16 KB compatible"
- "which of the open Dependabot PRs can I merge?"
- "actualiza las dependencias, solo las seguras"
- or invoke it by name (see your agent above)

## Requirements

- An Android (Kotlin/Gradle) project, ideally with a version catalog (`gradle/libs.versions.toml`).
- A working Android SDK (`sdk.dir` in the repo's `local.properties`).
- Python 3.8+ (standard library only), and network access to the Maven repositories.
- The [ben-manes `gradle-versions-plugin`](https://github.com/ben-manes/gradle-versions-plugin):
  either already applied by the project (0.55+ recommended), or injected by the skill's init
  script. Without Gradle, the planner reads Maven metadata instead.
- For bot PR reviews: the [GitHub CLI](https://cli.github.com/) (`gh`), signed in.

## What the skill runs

So you can review it before installing:

- **Gradle tasks** in your project:
  - `dependencyUpdates`, optionally through `scripts/versions.init.gradle.kts`, which pulls the
    ben-manes plugin from the Gradle Plugin Portal;
  - `buildEnvironment`;
  - `:app:assembleDebug`, plus tests and lint;
  - the wrapper task, only when you approve a Gradle upgrade.
- **Python scripts** (standard library only): `plan.py`, `risk.py`, `review_prs.py` and
  `aggregate-updates.py`. They read your catalog, build files and reports, and never edit your
  project.
- **Vulnerability queries** to the [OSV API](https://osv.dev) (`api.osv.dev`). Only Maven
  coordinates and versions are sent.
- **Network reads** of:
  - Maven metadata, POMs and AARs (Google Maven, Maven Central, Gradle Plugin Portal, JitPack);
  - `services.gradle.org`, for the latest Gradle;
  - changelogs and GitHub releases.
- **git**: it creates a branch and local commits, and never pushes unless you ask.
- **GitHub CLI** (`gh`), only when reviewing bot PRs: read-only calls for the PRs, their files and
  their CI status. Merging, commenting or closing happens only after your explicit yes, one action
  at a time.

## Quality

- **CI** on every push and tag:
  - unit, repository and fixture tests on Python 3.8 and the current Python, on Linux and Windows;
  - the Agent Skills validator (`skills-ref`), `claude plugin validate --strict` and
    `gh skill publish --dry-run`.
- **A weekly canary** runs the planner against the live Maven repositories, OSV and Gradle services.
- **Trigger evals:** 26 realistic requests for
  [`claude plugin eval`](https://code.claude.com/docs/en/plugins/evals). 13 should load the skill;
  13 are near-misses that shouldn't. Latest run: 78/78 as expected.
- **Real projects:**
  - dogfooded on a production multi-module app;
  - the PR review was validated read-only against the open bot PRs of
    [element-x-android](https://github.com/element-hq/element-x-android) (Renovate) and
    [thunderbird-android](https://github.com/thunderbird/thunderbird-android) (Dependabot).

## Repository layout

```
android-update-deps/
├── skills/
│   └── android-update-deps/        # the skill (this folder is what agents install)
│       ├── SKILL.md                # discovery, the gated procedure, the bot-PR review flow
│       ├── references/
│       │   └── reference.md        # coupled versions, hidden requirements, security, bots, JitPack
│       └── scripts/
│           ├── plan.py                     # draft the tiered proposal (--json available)
│           ├── risk.py                     # vulnerabilities, 16 KB, lint, release notes
│           ├── review_prs.py               # review open Dependabot/Renovate PRs (gh, read-only)
│           ├── aggregate-updates.py        # raw view of the ben-manes report
│           └── versions.init.gradle.kts    # inject ben-manes without editing the build
├── .claude-plugin/                 # Claude Code plugin + marketplace manifests
├── .github/workflows/ci.yml        # tests, manifest validators, weekly live-repository canary
├── evals/                          # evaluation prompts, trigger cases and assertions
│   └── fixtures/android-catalog-fixture/   # an outdated AGP 9 app to test against
├── tests/                          # unit, repository and fixture tests (see Contributing)
├── CHANGELOG.md
├── LICENSE
└── README.md
```

## Contributing

Issues and PRs are welcome, especially a case from your own project that the skill got wrong. The
skill is intentionally **concise** and **gated**: proposals that add scope should keep those
principles (see the "handle with care" philosophy in `SKILL.md`).

Before a PR, run the tests (standard library only, no network):

```bash
python3 -m unittest discover -s tests -v
```

Setting `PLAN_ONLINE=1` adds the canary, which runs the planner on the fixture against the live
repositories.

When you change the skill's `description`, check that it still triggers when it should, and only
then. [`evals/triggers/`](evals/triggers/) holds the trigger cases. Running them makes real model
calls on your account:

```bash
claude plugin eval . --tag trigger --ablation none --threshold 0.6 --trust-plugin
```

A release bumps the version in `plugin.json`, `marketplace.json`, `SKILL.md` and `CHANGELOG.md`
together. CI checks that they match, and that a tag matches them.

If the skill saved you a broken build, a ⭐ helps other Android developers find it.

## License

[MIT](LICENSE) © Alvaro Serrano
