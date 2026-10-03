# android-catalog-fixture

A small Android app (AGP 9.2.1, Gradle 9.6.1, built-in Kotlin) whose version catalog is
deliberately behind, so that each rule of the skill has something to trigger:

| Entry | Exercises |
|---|---|
| `agp` 9.2.1, Gradle wrapper 9.6.1 | toolchain items with their own confirmation |
| `ksp` `2.2.10-2.0.2` | old `<kotlin>-<ksp>` scheme pinning Kotlin on AGP 9 |
| `kotlin` 2.2.10 | a ref that feeds no applied plugin under built-in Kotlin |
| `composeBom` 2026.06.00 | BOM-governed children; newer BOMs need `compileSdk` 37 |
| `coreKtx` 1.16.0 | latest needs `compileSdk` 37; highest compatible is older |
| `gson` 2.8.8 | several minors behind, and a known vulnerability (GHSA-4jrv-ppp4-jm57, fixed in 2.8.9) |
| `cameraX` 1.3.0 (camera-core) | native libraries that are not 16 KB aligned; newer releases are |
| `android-pdfview` (JitPack) | unused entry pinned to a version that isn't published |

Before building, create `local.properties` with `sdk.dir=<path to your Android SDK>`. The
`org.gradle.java.home` line in `gradle.properties` points at Android Studio's JBR on Windows;
adjust or remove it for your machine.
