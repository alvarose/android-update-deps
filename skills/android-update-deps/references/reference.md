# Reference — android-update-deps

Support detail for `SKILL.md`. Loaded on demand; no need to read it whole except for the steps that
reference it.

## Coupled versions

Build this table by **reading the concrete repo's catalog**. Common patterns in Android/Kotlin
projects (adjust to what actually exists):

| Block | Typical refs / artifacts | Rule |
|---|---|---|
| Kotlin | ref `kotlin` (kotlin-android, kotlin-jvm, kotlin-compose, kotlin-serialization) | Move together; a single `kotlin` ref bumps them all, plus the Compose Compiler and other Kotlin compiler plugins. **Always high-risk: propose it separately and bump it only under its own explicit confirmation — never inside a "safe" batch.** With AGP 9 built-in Kotlin, the version that compiles comes from the build classpath (check `./gradlew buildEnvironment`): AGP brings KGP 2.2.10, and a catalog ref that only feeds an unapplied `kotlin-android` alias changes nothing. Per the AGP 9 release notes, a higher KGP (or KSP) goes on the top-level build's classpath: `buildscript { dependencies { classpath("org.jetbrains.kotlin:kotlin-gradle-plugin:<version>") } }` (KSP: `com.google.devtools.ksp:symbol-processing-gradle-plugin`). |
| KSP (plugin) | ref `ksp` | **Check the scheme.** **2.3.0+** (e.g. `2.3.12`): independent of Kotlin — its own item, checked against its minimum AGP and its release notes. **Old scheme** `<kotlin>-<ksp>` (e.g. `2.2.10-2.0.2`): tied to that exact Kotlin version, and on AGP 9 it pins Kotlin (AGP depends on KGP 2.2.10 and KSP `2.2.10-2.0.2`); migrating KSP to 2.3.x is the prerequisite for any Kotlin bump. The KSP *processors* you consume (Room compiler, etc., declared as `ksp(...)`) are versioned by their own libraries. |
| Compose Compiler | Kotlin 2.0+: `org.jetbrains.kotlin.plugin.compose` (ref `kotlin`); Kotlin <2.0: `kotlinCompilerExtensionVersion` | **Tied to the Kotlin version.** Kotlin 2.0+: the Compose Compiler Gradle plugin uses the *same* version as Kotlin — bump together. Kotlin <2.0: set `kotlinCompilerExtensionVersion` to the Compose-compiler release compatible with that Kotlin. Distinct from the Compose BOM (runtime libs). |
| AGP ↔ Gradle | ref `agp` + wrapper | A new AGP usually **requires** a newer Gradle (bump the wrapper), a compatible Android Studio, and sometimes a higher `compileSdk`/JDK — check the compatibility matrix. An AGP **major** is the highest-impact bump; treat it + wrapper as one deferred item. **AGP 9** also needs KSP ≥ 2.3.6 and Hilt ≥ 2.59.2 when a module applies them (a KSP plugin only declared with `apply false` isn't exercised). |
| Compose BOM | `composeBom` | **Governs all `androidx.compose.*` without a `version.ref`.** The report lists those children as current or outdated: **ignore them**, bump only `composeBom`. Check the hidden requirements of the components it moves: e.g. BOM `2026.08.00`+ brings Compose 1.12, whose AARs require `compileSdk` 37 and AGP ≥ 9.1 (the release notes recommend 9.2). |
| Hilt | ref `hilt` (hilt-core/android/compiler) | Together. |
| Koin | ref `koin` (core/android/compose) | Together. |
| Retrofit | ref `retrofit` (retrofit + converter) | Together. |
| OkHttp / Ktor | ref `okhttp` / `ktor` (client + plugins/logging) | Together. |
| Coroutines | ref `coroutines` (core + test) | Together. |
| Room | ref `room` (ktx/runtime + compiler) | Together. |
| Other BOMs (Firebase, etc.) | the BOM governs its artifacts without their own ref | Bump only the BOM; ignore the child artifacts in the report. |
| JUnit5 | ref `junitJupiter` (jupiter + engine); `junit-platform-launcher` sometimes without a ref | Jumping 5.x → 6.x is a **major**: validate the platform and `junit-platform-launcher` together. |
| Library → compileSdk / AGP / Kotlin | any lib carrying a minimum requirement (common with AndroidX) | A *seemingly safe* minor lib bump can require a higher `compileSdk`/AGP or Kotlin. Detect it before the gate ([Hidden requirements](#hidden-requirements)); then it's **coupled** to that bump, not safe — defer it with the item it needs. |

> General rule: any set of artifacts sharing a `version.ref` or governed by a BOM is a **single
> item** in the proposal.

## Hidden requirements

`scripts/plan.py` does this automatically; this is the manual procedure (no Python, or to double-check
an item). Check them before proposing (SKILL steps 3–4), instead of discovering them when the build fails.

1. **Find the artifact.** Google artifacts (`androidx.*`, `com.google.android.*`,
   `com.android.*`) live in Google's Maven repo, the rest usually in Maven Central:
   - `https://dl.google.com/android/maven2/<group/with/slashes>/<name>/<version>/<name>-<version>.aar`
   - `https://repo1.maven.org/maven2/<group/with/slashes>/<name>/<version>/<name>-<version>.aar`
   Multiplatform libraries publish the Android variant as `<name>-android` (e.g. Compose
   `ui` → `ui-android`); the `<name>-<version>.module` file lists the variants if unsure.
2. **Read the AAR metadata.** An AAR is a zip; its
   `META-INF/com/android/build/gradle/aar-metadata.properties` declares `minCompileSdk`
   (plus `minCompileSdkExtension`) and `minAndroidGradlePluginVersion`:
   ```
   python3 - <<'EOF'
   import io, urllib.request, zipfile
   url = "https://dl.google.com/android/maven2/androidx/compose/ui/ui-android/1.12.0/ui-android-1.12.0.aar"
   z = zipfile.ZipFile(io.BytesIO(urllib.request.urlopen(url).read()))
   print(z.read("META-INF/com/android/build/gradle/aar-metadata.properties").decode())
   EOF
   ```
3. **For a BOM**, read the new BOM's POM (`<name>-<version>.pom`), take the versions of a few key
   components (e.g. `ui`, `foundation`, `material3`) and check their AARs.
4. **Kotlin**: in the candidate's POM, look at the `org.jetbrains.kotlin:kotlin-stdlib` version. If
   it's more than one minor ahead of the project's **effective** Kotlin (`./gradlew
   buildEnvironment`), the project's compiler may not read it: the item is blocked by Kotlin.
5. **Compare** with the project's `compileSdk`, the catalog's `agp`, and the effective Kotlin. Any
   gap makes the item coupled to that bump.

## JitPack

Manual procedure for `com.github.*` libs (the gradle-versions-plugin does not track them):

1. For each catalog entry with `group = "com.github.…"`, build the metadata URL by replacing the
   dots in the group with slashes:
   `https://jitpack.io/<group/with/slashes>/<name>/maven-metadata.xml`
   (e.g. `com.github.owner` + `lib-android` → `https://jitpack.io/com/github/owner/lib-android/maven-metadata.xml`).
2. Fetch that URL and take the newest **stable** entry of `<versions>`: `<release>` / `<latest>`
   can point to a pre-release, so skip `alpha`, `beta`, `rc`, `dev`, `eap`, `M<n>`, `SNAPSHOT`. If a
   web-fetch tool is blocked (JitPack can answer 403), use `curl` from the shell. Verify the build is
   `ok` at `https://jitpack.io/api/builds/<group>/<name>` if in doubt. If the catalog's current
   version isn't in `<versions>`, or is newer than the latest stable, it's a "needs a decision" item
   (SKILL step 3), not a bump.
3. Compare it with the catalog version. If there's a jump, **treat it as one more item** in the
   proposal (SKILL step 4), classified by risk (step 3), ignoring junk "versions" (hashes,
   `*-SNAPSHOT`, `master`, loose numeric tags).
4. The report's "linked changelog" doesn't exist for these libs: use the GitHub releases
   (`gh api repos/<owner>/<repo>/releases`) and, if you need to confirm API signatures, the source
   at the tag (`gh api …/contents/<path>?ref=<tag>`).

> **General fallback:** the same `maven-metadata.xml` technique works for any repository — use it
> when `dependencyUpdates` won't run at all, and to confirm that a proposed version exists. As above,
> pick the newest stable from `<versions>`: on Google Maven and Maven Central `<release>` is often a
> pre-release (AGP alphas, Kotlin betas).
> - Google Maven: `https://dl.google.com/android/maven2/<group/with/slashes>/<name>/maven-metadata.xml`
> - Maven Central: `https://repo1.maven.org/maven2/<group/with/slashes>/<name>/maven-metadata.xml`
> - Gradle Plugin Portal (plugins): `https://plugins.gradle.org/m2/<plugin/id/with/slashes>/<plugin.id>.gradle.plugin/maven-metadata.xml`

## report.json format (ben-manes)

ben-manes 0.55+ writes one merged report to `<root>/build/dependencyUpdates/report.json` (partial
results go to `partials/`); older versions write one `report.json` per project. Relevant structure:

```json
{
  "outdated":   { "dependencies": [ { "group": "…", "name": "…", "version": "…",
                  "available": { "release": null, "milestone": "…", "integration": null,
                                 "preRelease": null, "patch": "…", "minor": "…" } } ] },
  "current":    { "dependencies": [ … ] },
  "exceeded":   { "dependencies": [ … ] },
  "unresolved": { "dependencies": [ { "group": "com.github.…", "name": "…", "reason": "…" } ] },
  "gradle":     { "running": { "version": "…" }, "current": { "version": "…", "isUpdateAvailable": true } },
  "skipped":    { "count": 0, "configurations": [] }
}
```

- `outdated` → candidates. The accepted newer version is in `release`, `milestone` or `integration`
  depending on the task's `revision` (`milestone` by default). Ignore `preRelease`. `patch` and
  `minor` (0.63+) are the latest patch / minor versions: step-down candidates (SKILL step 6).
- `unresolved` → JitPack libs land here; review them with the procedure above.
- `gradle` → the Gradle wrapper, **not** in `outdated`. Present when the task checks Gradle
  (`gradleReleaseChannel`, or `rejectPreReleases = true` as in the bundled init script).
- `skipped` (0.61+) → configurations the plugin could not inspect; mention them in the report.
- `scripts/aggregate-updates.py` reads all of this; `--json` exposes it to other tools.
