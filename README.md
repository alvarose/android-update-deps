# android-update-deps

> An **[Agent Skill](https://agentskills.io)** for the **safe, gated review and update of
> dependencies** in an Android (Kotlin/Gradle) project that uses a Gradle version catalog. One
> skill folder that works with Claude Code, OpenAI Codex, Antigravity, Gemini CLI, Android Studio
> and any Agent-Skills-compatible tool.

[![CI](https://github.com/alvarose/android-update-deps/actions/workflows/ci.yml/badge.svg)](https://github.com/alvarose/android-update-deps/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
![Agent Skill](https://img.shields.io/badge/Agent-Skill-6E56CF)
![Platform](https://img.shields.io/badge/platform-Android%20%7C%20Kotlin%20%7C%20Gradle-3DDC84)

Upgrading dependencies is easy to get wrong: automated bumpers jump every library to its latest
version with **zero analysis**, silently breaking your build or your app. This skill does the
opposite — it turns "update my dependencies" into a **controlled, reviewable procedure** that
detects what's outdated, reasons about the risk, and **never changes anything without your explicit
confirmation**.

It's designed for real Android projects: version catalogs (`gradle/libs.versions.toml`), the Android
Gradle Plugin (including AGP 9 built-in Kotlin), Compose/Firebase BOMs, coupled version blocks
(Kotlin ↔ Compose Compiler, KSP, AGP ↔ Gradle wrapper), JitPack libraries, and convention plugins
(`build-logic/`).

## Why use it

- **Gated by design.** It detects, groups, classifies by risk, and **stops at a proposal** for you
  to approve — all / safe-only / a specific subset. Nothing is edited or committed until you say go.
  Kotlin, AGP and the Gradle wrapper always need their own explicit yes.
- **Sees the whole picture, not just version numbers.** It groups coupled blocks and BOM-governed
  artifacts into single items, separates AGP/Kotlin tooling noise, and checks *hidden requirements*
  **before** proposing (a "safe-looking" minor whose AAR actually needs a higher `compileSdk` or AGP).
- **Doesn't touch your build to look.** If the project doesn't use the ben-manes plugin, the skill
  injects it with a Gradle init script instead of editing `build.gradle.kts`.
- **Covers the tool's blind spot.** The ben-manes `gradle-versions-plugin` can't see JitPack
  (`com.github.*`) libraries — the skill checks those against JitPack's own metadata.
- **Verifies before it trusts.** Applied bumps are built with `:app:assembleDebug`; if something
  breaks, it steps down to the highest version that compiles or reverts it — never leaving your tree
  broken.
- **Clean git hygiene.** Commits locally on a feature branch (never the default branch, no push),
  one thematic commit per theme, with a **separate commit** for any code adaptation to new APIs.
- **Speaks your language.** User-facing prose mirrors the language you write in (English or Spanish).

## What it handles

| Concern | Behavior |
|---|---|
| **Version catalog** | Edits `gradle/libs.versions.toml` `version.ref`s — the single source of truth |
| **Detection** | ben-manes `dependencyUpdates` (the project's own, or injected with an init script), or straight from Maven metadata when Gradle can't run |
| **Planning** | `scripts/plan.py` drafts the proposal deterministically: coupled blocks, toolchain items, hidden requirements, version existence, release-age cooldown, hold comments, unused entries, cautious alternatives |
| **JitPack** (`com.github.*`) | Checked against JitPack metadata, used or not (the plugin's blind spot) |
| **BOMs** (Compose, Firebase, …) | Bumps only the BOM; ignores the governed child artifacts in the report |
| **Coupled blocks** | Kotlin ↔ Compose Compiler, KSP (old vs. 2.3+ scheme), AGP ↔ Gradle wrapper, Retrofit, OkHttp, Room, Hilt… treated as single items |
| **Risk** | Semver magnitude + hidden `compileSdk`/AGP/Kotlin requirements (read from AAR metadata) + known vulnerabilities + license changes |
| **Verification** | `:app:assembleDebug` (plus tests/linters when relevant); steps down or reverts any culprit bump |
| **Code adaptation** | Post-bump deprecation/migration pass, in a separate `refactor(deps)` commit |

## How it works

A fixed, repeatable procedure (the skill stops at step 4 for your approval):

0. **Discover** the project shape — catalog, detection path, effective Kotlin/KSP, conventions.
1. **Detect** updates with the ben-manes plugin.
2. **Plan**: `scripts/plan.py` drafts a tiered proposal; the agent reads release notes and checks it.
3. **Apply the rules** — safe vs. handle-with-care vs. needs-a-decision, toolchain always separate.
4. **Propose** the table — **⛔ GATE: waits for you.**
5. **Apply** only what you confirmed.
6. **Verify** with a build; step down or revert any culprit.
7. **Commit** locally on a branch, one thematic `chore(deps)` commit, no push.
8. **Adapt the code** to new APIs if needed — separate `refactor(deps)` commit.

## Installation

Agent Skills are an [open format](https://agentskills.io/specification): the skill lives in
[`skills/android-update-deps/`](skills/android-update-deps/) and the same folder works in every
compatible agent.

### Any agent — with an installer

```bash
# skills CLI (vercel-labs/skills): detects your agents; -g for a user-level install
npx skills add alvarose/android-update-deps

# GitHub CLI (gh skill, preview)
gh skill install alvarose/android-update-deps android-update-deps
```

### Claude Code

Install as a plugin, then update with `/plugin update android-update-deps@alvarose`:

```
/plugin marketplace add alvarose/android-update-deps
/plugin install android-update-deps@alvarose
```

### OpenAI Codex

Copy the skill folder to `~/.agents/skills/` (all projects) or a repo's `.agents/skills/`, then
invoke it with `$android-update-deps`.

### Antigravity / Antigravity CLI

Copy the skill folder to the workspace's `.agents/skills/`, or globally to
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

Once installed, just ask your agent in an Android project — the skill triggers on phrases like:

- "review / update / bump the dependencies of my Android app"
- "what's outdated in my `libs.versions.toml`?"
- "actualiza las dependencias, solo las seguras"
- or invoke it by name (see your agent above)

The agent will detect, analyze, and show you a risk-grouped proposal. You pick what to apply; it
verifies with a build and commits on a branch. **It won't touch anything without your OK.**

## Requirements

- An Android (Kotlin/Gradle) project, ideally with a version catalog (`gradle/libs.versions.toml`).
- A working Android SDK (`sdk.dir` in the repo's `local.properties`).
- Python 3 (standard library only) and network access to Maven repositories.
- The [ben-manes `gradle-versions-plugin`](https://github.com/ben-manes/gradle-versions-plugin):
  either already applied by the project (0.55+ recommended), or injected by the skill's init script.

## What the skill runs

So you can review it before installing:

- **Gradle tasks** in your project: `dependencyUpdates` (optionally through
  `scripts/versions.init.gradle.kts`, which pulls the ben-manes plugin from the Gradle Plugin Portal),
  `buildEnvironment`, `:app:assembleDebug`, tests/lint, and the wrapper task when you approve a
  Gradle upgrade.
- **Python scripts** (standard library only) that read the catalog and the plugin's reports
  (`plan.py`, `aggregate-updates.py`). They never edit your project.
- **Network reads** of Maven metadata, POMs and AARs (Google Maven, Maven Central, Gradle Plugin
  Portal, JitPack), of `services.gradle.org` (latest Gradle), and of changelogs / GitHub releases.
- **git**: creates a branch and local commits. It never pushes unless you ask.

## Repository layout

```
android-update-deps/
├── skills/
│   └── android-update-deps/        # the skill (this folder is what agents install)
│       ├── SKILL.md                # discovery + the 9-step gated procedure
│       ├── references/
│       │   └── reference.md        # coupled versions, hidden requirements, JitPack, report format
│       └── scripts/
│           ├── plan.py                     # draft the tiered proposal (--json available)
│           ├── aggregate-updates.py        # raw view of the ben-manes report
│           └── versions.init.gradle.kts    # inject ben-manes without editing the build
├── .claude-plugin/                 # Claude Code plugin + marketplace manifests
├── .github/workflows/ci.yml        # tests, manifest validators, weekly live-repository canary
├── evals/                          # evaluation prompts + assertions
│   └── fixtures/android-catalog-fixture/   # an outdated AGP 9 app to test against
├── tests/                          # unit, repository and fixture tests (see Contributing)
├── CHANGELOG.md
├── LICENSE
└── README.md
```

## Contributing

Issues and PRs welcome. The skill is intentionally **concise** and **gated** — proposals that add
scope should preserve those principles (see the "handle with care" philosophy in `SKILL.md`).

Before a PR, run the tests (standard library only, no network):

```bash
python3 -m unittest discover -s tests -v
```

`PLAN_ONLINE=1` adds the canary that runs the planner on the fixture against the live
repositories.

When you change the skill's `description`, check that it still triggers when it should, and only
then. [`evals/triggers/`](evals/triggers/) holds 19 realistic requests for
[`claude plugin eval`](https://code.claude.com/docs/en/plugins/evals): 9 should load the skill;
10 are near-misses that shouldn't (other ecosystems, adding a new library, `targetSdk`, Renovate,
build errors). These are real model calls on your account:

```bash
claude plugin eval . --tag trigger --ablation none --threshold 0.6 --trust-plugin
``` A release bumps the version in `plugin.json`, `marketplace.json`, `SKILL.md`
and `CHANGELOG.md` together; CI checks that they match, and that a tag matches them.

## License

[MIT](LICENSE) © Alvaro Serrano
