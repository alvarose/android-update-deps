---
name: android-update-deps
description: Reviews and safely updates the dependencies of an Android project (Android Studio; Kotlin/Gradle) that uses a Gradle version catalog. Detects available updates with the ben-manes gradle-versions-plugin, checks JitPack libraries by hand (the plugin's blind spot), assesses risk, and — only after explicit confirmation — edits libs.versions.toml, verifies with an Android build (:app:assembleDebug), commits locally, and in a separate commit adapts the existing code to the new APIs. Use this whenever the user wants to update, review, or bump dependencies in an Android app, asks "what's outdated", mentions libs.versions.toml / version catalog / AGP / Compose BOM upgrades, or runs /android-update-deps — in English or Spanish ("actualiza/revisa las dependencias", "qué hay desactualizado").
license: MIT
---

# android-update-deps — controlled dependency review & update

A fixed, repeatable procedure to keep an Android (Kotlin/Gradle) project's dependencies current.

**Communicate in the language the user is writing in** (default English; mirror Spanish or any
other language they use). **Never apply a change without explicit confirmation** — this workflow
is gated on purpose (step 5). Bumping dependencies silently is how a working build breaks.

> **Commands:** examples use `./gradlew` (Unix/macOS/Git Bash). On **Windows PowerShell** use
> `.\gradlew.bat`. If the Bash tool is available, run them there so `./gradlew` works on any OS.

## Discovery (do this first in a new repo)

This skill is generic; every repo differs. Before touching anything, learn the project's shape:

1. **Locate the version catalog** — usually `gradle/libs.versions.toml`. If it doesn't exist,
   versions may be inline in the `build.gradle.kts` files; say so — those files become the edit target.
2. **Confirm the detection tool.** Check whether `ben-manes/gradle-versions-plugin` is applied
   (grep for `dependencyUpdates` / `com.github.ben-manes.versions` in the `build.gradle.kts`). If
   it is **not** applied, offer to add it temporarily; don't assume it's there.
3. **List JitPack dependencies** (`com.github.*` in the catalog) up front — they are the plugin's
   blind spot and must be checked by hand (step 1b, details in [reference.md](reference.md#jitpack)).
4. **Map coupled version blocks.** Inspect `[versions]` and `[libraries]` for shared `version.ref`s
   and BOMs, and build the project's coupling table — see [reference.md](reference.md#coupled-versions).
   General rule: **any set of artifacts sharing a `version.ref` or governed by a BOM is a single item.**
5. **Find the Android SDK, the JDK/compileSdk baseline, and the wrapper.** The Android SDK path
   comes from the repo's own `local.properties` (`sdk.dir=…`) — it is per-machine and git-ignored,
   not `ANDROID_HOME`. If `local.properties` is missing, the build can't run until it exists (see
   step 1). The JDK/compileSdk baseline lives in the convention plugins (`build-logic/`, wired via
   `includeBuild("build-logic")` in `settings.gradle.kts`) or in the module `build.gradle.kts`; the
   Gradle wrapper is in `gradle/wrapper/gradle-wrapper.properties`.

> If the repo has a `CLAUDE.md`, `AGENTS.md`, or `.docs/` with build conventions, read it: it may
> pin where versions live and which libs are coupled.

Key facts for a catalog-based project:
- With convention plugins, module `build.gradle.kts` files declare **no** versions — the catalog is
  the single edit target.
- On **Gradle 9+**, `dependencyUpdates` **must** run without parallelism or it only scans the root
  module (`--no-parallel`). Each project writes `build/dependencyUpdates/report.json`.
- The **Gradle wrapper is not in the catalog** — bump it in `gradle/wrapper/gradle-wrapper.properties`,
  preferably via `./gradlew wrapper --gradle-version=X`. Its *available* version isn't in the report's
  `outdated` bucket either: ben-manes reports it in a separate `gradle` section (only when
  `gradleReleaseChannel = "current"` is set), which `scripts/aggregate-updates.py` surfaces for you.
  Treat it as its own item with its own confirmation (bump it alongside AGP when an AGP upgrade
  requires it).

## Procedure

### 1. Detect
Run `./gradlew dependencyUpdates --no-parallel` (resolves network dependencies: may be slow, use a
wide timeout). If it fails for missing local files, flag it — that's environment setup, not a
dependency problem. The most common case is **`SDK location not found`**: the repo just needs a
`local.properties` with `sdk.dir=<path-to-Android-SDK>` (git-ignored, per-machine). Others:
`keystore.properties`, `google-services.json`.

Prefer the plugin to reject pre-releases (alpha/beta/RC/SNAPSHOT) via `rejectVersionIf`; if it
doesn't, filter unstable candidates yourself in step 3.

### 1b. Detect sources the plugin doesn't track (JitPack)
The step-1 report **never** includes JitPack libs (`com.github.*`) — they land in `unresolved`.
Check them by hand following [reference.md](reference.md#jitpack) (build the metadata URL, read
`<release>`, compare with the catalog, classify by risk).

### 2. Aggregate and deduplicate
Use the helper script for the mechanical part:
```
python scripts/aggregate-updates.py <repo-path>
```
It walks every `*/build/dependencyUpdates/report.json`, deduplicates by `group:name`, and also
lists the JitPack libs (`com.github.*`) left `unresolved` for step 1b. If you can't run it, do it
by hand from the `report.json` files. Map each dependency to its **key in `libs.versions.toml`**.

### 3. Filter noise
- **Drop** any artifact governed by a BOM without its own `version.ref` (`androidx.compose.*` under
  `composeBom`, Firebase artifacts under their BOM, etc.). Only consider the BOM bump.
- Drop pre-release candidates if the plugin didn't already.
- Group coupled blocks (see [reference.md](reference.md#coupled-versions)) into a single item
  (e.g. "Kotlin X → Y ⇒ move KSP to Z").

### 4. Classify by risk
Judge each item on more than the version number:
- **Semver magnitude:** patch/minor (same major) = low risk; **major** (first number changes) or
  large multi-version jumps = high risk — flag it and link the changelog.
- **Hidden requirements:** a *seemingly safe* bump can demand a higher `compileSdk`, AGP, or Kotlin
  (common with AndroidX). Then it's **coupled** to that bump, not safe — the verify build (step 7)
  exposes it ("requires compileSdk N"); reclassify and defer it with the major it depends on.
- **Security & license:** treat a current version with known vulnerabilities as a reason to
  prioritize the bump (Android Studio flags vulnerable libraries via the Play SDK Index). On majors,
  watch for a changed license.
- **Blocking toolchain bumps always get their own explicit yes — never the "safe" bucket.** The
  **Kotlin version** is never "safe", not even for a minor/patch: it's a cascading compiler change
  that drags `ksp`, the Compose Compiler, and every Kotlin compiler plugin with it, and can break
  annotation processing or Compose. Always list `kotlin` (with its coupled block) separately under
  "handle with care" and require a **distinct confirmation** — an "apply the safe ones" approval must
  never move it. The same applies to **AGP** and the **Gradle wrapper** (an AGP major ⇒ Gradle +
  Studio, often `compileSdk`/JDK too): their own items, their own yes.
- **KSP follows Kotlin.** When Kotlin moves, `ksp` moves under the *same* confirmation — don't ask
  twice. A **standalone** `ksp` bump (same Kotlin line, e.g. a KSP-only patch) isn't "safe" either:
  it drives annotation processing (Room, Hilt), so put it under "handle with care" with its own note
  — the verify build (step 7) catches codegen breakage. The Compose Compiler shares the `kotlin` ref,
  so it always rides Kotlin's confirmation automatically.

### 5. Propose (GATE)
Present a table: `catalog key | current → proposed | risk | note/changelog`, grouped into "safe"
vs "handle with care". **Stop and wait for explicit confirmation.** Let the user pick a subset
(all / safe only / a specific list). Do not edit anything until they say go.
- The **Kotlin version** (and **AGP**) must **never** sit in the "safe" group: an "apply the safe
  ones" / "solo las seguras" approval must exclude them. They only move on their own explicit yes,
  called out as separate line items — even for a minor/patch.

### 6. Apply (only what was confirmed)
- Edit the `version.ref`s in `gradle/libs.versions.toml` (with `Edit`).
- Respect coupled blocks: if you bump `kotlin`, move `ksp` to the matching version in the same change.
- The wrapper, if included, via `./gradlew wrapper --gradle-version=<X>`.

### 7. Verify
Build to confirm nothing breaks. Default:
```
./gradlew :app:assembleDebug
```
If the changes touch testing/Kotlin/coroutines, add tests + linters for the affected modules
(`./gradlew :<module>:testDebugUnitTest detekt`/`lint`). Full validation: `./gradlew build`.
- **If it fails:** isolate the culprit bump (revert that `version.ref`), report the real error, and
  return to the gate with an adjusted proposal. Never leave the tree in a broken state.

### 8. Commit (local, thematic)
Before committing: confirm it's a git repo and that you are **not** on the default branch
(`main`/`master`). If you are, create a branch first (e.g. `chore/deps-update`). One local commit
per theme, **no push** unless explicitly asked:
```
chore(deps): bump <short list of what went up>
```
If there were distinct blocks (Kotlin/KSP on one side, test libs on the other), consider separate
commits per theme.

### 9. Adapt the code to the new versions (post-bump)
After committing the bump, check whether the **existing** code needs adjustments. This is
**independent** of the bump and goes in a **separate commit** — never mixed with `chore(deps):`.
The step-7 build only guarantees it *compiles*, not that the code is *up to date* (deprecations
still compile).

**Scope:** only libs whose API the production or test code consumes. Ignore pure tooling (detekt,
secrets, google-services). Prioritize majors and minors; patches only if the changelog warns of
deprecations.

1. **Collect real deprecations.** Compile with warnings visible and run the linters:
   ```
   ./gradlew :app:assembleDebug --warning-mode all
   ./gradlew lint detekt
   ```
   List the new deprecation warnings attributable to the bumps (as file:line).
2. **Read the migration guide** for each relevant major/minor: `WebFetch` the changelog URL the
   plugin's report **already provides**. Extract deprecated/renamed APIs and behavior changes.
3. **Find usages in the code.** `Grep` the old symbols across production and tests; map occurrences
   to file:line.
4. **Propose (GATE).** For each finding: what changes, why (cite the changelog), affected files, and
   the suggested refactor. Wait for confirmation. **If there's nothing to adapt, say so explicitly.**
5. **Apply and re-verify** what was confirmed (`Edit` + build/tests/linters for what you touched).
6. **Separate thematic commit:** `refactor(deps): adapt <X> to <lib> <version> API`. One lib or
   theme per commit.

## Notes
- Don't invent versions: the source of "what's new" is the plugin report for Maven Central/Google,
  **plus** the manual JitPack check (step 1b). To confirm a major's compatibility, read the
  changelog linked in the report (or the GitHub releases for JitPack libs).
- **Optional automation:** littlerobots' `version-catalog-update-plugin` can write the TOML
  automatically from the report. This skill deliberately keeps the **manual gated** flow; if the
  user prefers the automated one, apply it only after the same GATE in step 5.
- Adapt the "coupled versions" table ([reference.md](reference.md)) to the concrete repo's catalog;
  don't assume every block exists.
- **Supply chain:** if the project doesn't already use Gradle
  [dependency verification](https://developer.android.com/build/dependency-verification), suggest
  enabling it before a large update so downloaded artifacts are checksum/signature-verified.
- **Transitive fan-out:** a catalog bump also moves *transitive* dependencies. For higher-risk
  bumps, diff the resolved graph (`./gradlew :app:dependencies`, or a snapshot tool) to catch
  version conflicts (search for `->`) and new AAR contributions (permissions/components added via
  manifest merge).
- **SDK levels (`compileSdk` / `minSdk` / `targetSdk`) are not catalog dependencies.** They live in
  the module `android {}` block or the convention plugins (`build-logic/`), sometimes surfaced as a
  `[versions]` value, and ben-manes does not track them. Scope for this skill:
  - **`compileSdk`** — raise it *only* when a confirmed dependency requires it (a "hidden
    requirement", step 4). Treat that as a coupled, high-risk item with its own confirmation; never
    bump it speculatively to chase new APIs.
  - **`targetSdk`** — **out of scope.** Raising it is a Play-Store-driven *behavior* migration (new
    permissions, behavior changes, its own testing). Flag it and defer to Android Studio's SDK
    Upgrade Assistant / a dedicated pass; don't move it as part of a dependency update.
  - **`minSdk`** — don't change it (a product decision that drops device support), but **flag** when
    a dependency bump raises the *effective* `minSdk`.
