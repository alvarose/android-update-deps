# android-update-deps

> An **[Agent Skill](https://agentskills.io)** for the **safe, gated review and update of
> dependencies** in an Android (Kotlin/Gradle) project that uses a Gradle version catalog. One
> skill folder that works with Claude Code, OpenAI Codex, Antigravity, Gemini CLI, Android Studio
> and any Agent-Skills-compatible tool.

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
  (`com.github.*`) libraries — the skill checks those **by hand**.
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
| **Detection** | ben-manes `dependencyUpdates` (the project's own, or injected with an init script), aggregated by a helper script |
| **JitPack** (`com.github.*`) | Checked manually via JitPack metadata / GitHub releases (plugin blind spot) |
| **BOMs** (Compose, Firebase, …) | Bumps only the BOM; ignores the governed child artifacts in the report |
| **Coupled blocks** | Kotlin ↔ Compose Compiler, KSP (old vs. 2.3+ scheme), AGP ↔ Gradle wrapper, Retrofit, OkHttp, Room, Hilt… treated as single items |
| **Risk** | Semver magnitude + hidden `compileSdk`/AGP/Kotlin requirements (read from AAR metadata) + known vulnerabilities + license changes |
| **Verification** | `:app:assembleDebug` (plus tests/linters when relevant); steps down or reverts any culprit bump |
| **Code adaptation** | Post-bump deprecation/migration pass, in a separate `refactor(deps)` commit |

## How it works

A fixed, repeatable procedure (the skill stops at step 5 for your approval):

1. **Discover** the project shape — catalog, detection path, JitPack libs, coupled blocks, SDK/JDK/wrapper, effective Kotlin/KSP.
2. **Detect** updates with the ben-manes plugin (+ a manual JitPack pass).
3. **Aggregate & dedupe** the report (helper script included).
4. **Filter noise** — drop BOM-governed children and AGP/Kotlin tooling; group coupled blocks.
5. **Classify by risk** and **propose** a table (safe vs. handle-with-care) — **⛔ GATE: waits for you.**
6. **Apply** only what you confirmed.
7. **Verify** with a build; step down or revert any culprit.
8. **Commit** locally on a branch, one thematic `chore(deps)` commit, no push.
9. **Adapt the code** to new APIs if needed — separate `refactor(deps)` commit.

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
- **Python scripts** (standard library only) that read the plugin's reports.
- **Network reads** of Maven metadata and artifacts (Google Maven, Maven Central, Gradle Plugin
  Portal, JitPack) and of changelogs / GitHub releases.
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
│           ├── aggregate-updates.py        # read/dedupe the ben-manes report (--json available)
│           └── versions.init.gradle.kts    # inject ben-manes without editing the build
├── .claude-plugin/                 # Claude Code plugin + marketplace manifests
├── evals/                          # evaluation prompts + assertions
├── CHANGELOG.md
├── LICENSE
└── README.md
```

## Contributing

Issues and PRs welcome. The skill is intentionally **concise** and **gated** — proposals that add
scope should preserve those principles (see the "handle with care" philosophy in `SKILL.md`).

## License

[MIT](LICENSE) © Alvaro Serrano
