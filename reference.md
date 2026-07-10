# Reference — android-update-deps

Support detail for `SKILL.md`. Loaded on demand; no need to read it whole except for the steps that
reference it.

## Coupled versions

Build this table by **reading the concrete repo's catalog**. Common patterns in Android/Kotlin
projects (adjust to what actually exists):

| Block | Typical refs / artifacts | Rule |
|---|---|---|
| Kotlin | ref `kotlin` (kotlin-android, kotlin-jvm, kotlin-compose, kotlin-serialization) | Move together; a single `kotlin` ref bumps them all. Bumping Kotlin usually forces its satellites below (KSP, Compose Compiler, other compiler plugins) to move in lockstep. |
| KSP (plugin) | ref `ksp` | **Tied to the Kotlin version — two schemes, check which the repo uses.** **KSP1** `<kotlin>-<rev>` (e.g. Kotlin `2.0.21` → `2.0.21-1.0.28`): the prefix must equal the *exact* Kotlin version. **KSP2** `<kotlin-major.minor>.<patch>` (e.g. Kotlin `2.3.21` → `ksp 2.3.9`): tracks the Kotlin `major.minor` line — a Kotlin `2.3.x` → `2.4.0` bump needs a `2.4.x` KSP, while a Kotlin patch-only move may not. Either way, move `ksp` together with `kotlin`. The KSP *processors* you consume (Room compiler, etc., declared as `ksp(...)` deps) are independent and API-stable — usually no bump needed. |
| Compose Compiler | Kotlin 2.0+: `org.jetbrains.kotlin.plugin.compose` (ref `kotlin`); Kotlin <2.0: `kotlinCompilerExtensionVersion` | **Tied to the Kotlin version.** Kotlin 2.0+: the Compose Compiler Gradle plugin uses the *same* version as Kotlin — bump together. Kotlin <2.0: set `kotlinCompilerExtensionVersion` to the Compose-compiler release compatible with that Kotlin. Distinct from the Compose BOM (runtime libs). |
| AGP ↔ Gradle | ref `agp` + wrapper | A new AGP usually **requires** a newer Gradle (bump the wrapper), a compatible Android Studio, and sometimes a higher `compileSdk`/JDK — check the compatibility matrix. An AGP **major** is the highest-impact bump; treat it + wrapper as one deferred item. |
| Compose BOM | `composeBom` | **Governs all `androidx.compose.*` without a `version.ref`.** The report will list `compose-ui 1.x → 1.y`, etc.: **IGNORE them**, bump only `composeBom`. |
| Hilt | ref `hilt` (hilt-core/android/compiler) | Together. |
| Koin | ref `koin` (core/android/compose) | Together. |
| Retrofit | ref `retrofit` (retrofit + converter) | Together. |
| OkHttp / Ktor | ref `okhttp` / `ktor` (client + plugins/logging) | Together. |
| Coroutines | ref `coroutines` (core + test) | Together. |
| Room | ref `room` (ktx/runtime + compiler) | Together. |
| Other BOMs (Firebase, etc.) | the BOM governs its artifacts without their own ref | Bump only the BOM; ignore the child artifacts in the report. |
| JUnit5 | ref `junitJupiter` (jupiter + engine); `junit-platform-launcher` sometimes without a ref | Jumping 5.x → 6.x is a **major**: validate the platform and `junit-platform-launcher` together. |
| Library → compileSdk / minSdk / Kotlin | any lib carrying a minimum requirement (common with AndroidX) | A *seemingly safe* minor lib bump can require a higher `compileSdk`/AGP or Kotlin. Then it's **coupled** to that bump, not safe — the verify build exposes it (e.g. "requires compileSdk 37"); reclassify and defer it with the major it needs. |

> General rule: any set of artifacts sharing a `version.ref` or governed by a BOM is a **single
> item** in the proposal.

## JitPack

Manual procedure for `com.github.*` libs (the gradle-versions-plugin does not track them):

1. For each catalog entry with `group = "com.github.…"`, build the metadata URL by replacing the
   dots in the group with slashes:
   `https://jitpack.io/<group/with/slashes>/<name>/maven-metadata.xml`
   (e.g. `com.github.owner` + `lib-android` → `https://jitpack.io/com/github/owner/lib-android/maven-metadata.xml`).
2. `WebFetch` that URL and read `<release>` (the latest stable published). Verify its build is `ok`
   at `https://jitpack.io/api/builds/<group>/<name>` if in doubt.
3. Compare it with the catalog version. If there's a jump, **treat it as one more item** in the
   proposal (SKILL step 5), classified by risk (step 4), ignoring junk "versions" (hashes,
   `*-SNAPSHOT`, `master`, loose numeric tags).
4. The report's "linked changelog" doesn't exist for these libs: use the GitHub releases
   (`gh api repos/<owner>/<repo>/releases`) and, if you need to confirm API signatures, the source
   at the tag (`gh api …/contents/<path>?ref=<tag>`).

## report.json format (ben-manes)

Relevant structure produced by `dependencyUpdates`:

```json
{
  "outdated":   { "dependencies": [ { "group": "…", "name": "…", "version": "…", "available": { "release": "…", "milestone": "…", "integration": "…" } } ] },
  "exceeded":   { "dependencies": [ … ] },
  "unresolved": { "dependencies": [ { "group": "com.github.…", "name": "…" } ] },
  "current":    { "dependencies": [ … ] }
}
```

- `outdated` → candidates to bump (use `available.release`; if absent, `milestone`).
- `unresolved` → JitPack libs land here; review them with the procedure above.
