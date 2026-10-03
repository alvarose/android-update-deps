// Injects the ben-manes gradle-versions-plugin into a build WITHOUT editing it.
//
// Usage (from the Android project root):
//   ./gradlew --init-script <this-skill>/scripts/versions.init.gradle.kts dependencyUpdates
//
// Use it only when the project does NOT already apply ben-manes (neither the
// settings plugin `io.github.ben-manes.versions.settings` nor the project plugin
// `io.github.ben-manes.versions` / legacy `com.github.ben-manes.versions`):
// mixing two plugin versions on one build classpath can fail.
//
// Based on the official "Initialization script" recipe of
// https://github.com/ben-manes/gradle-versions-plugin (settings plugin, 0.56+).
// Works with parallel execution, the configuration cache and isolated projects;
// the merged report is written to <root>/build/dependencyUpdates/report.json.

import com.github.benmanes.gradle.versions.VersionsSettingsPlugin
import com.github.benmanes.gradle.versions.updates.DependencyUpdatesTask

initscript {
  repositories {
    gradlePluginPortal()
  }

  dependencies {
    // Latest stable release: a dependency-update tool shouldn't run an outdated
    // version of itself. Pin an explicit version here if you need reproducibility.
    classpath("io.github.ben-manes:gradle-versions-plugin:latest.release")
  }
}

gradle.settingsEvaluated(Action<Settings> {
  if (!pluginManager.hasPlugin("io.github.ben-manes.versions.settings")) {
    pluginManager.apply(VersionsSettingsPlugin::class.java)
  }
})

gradle.rootProject(Action<Project> {
  tasks.withType(DependencyUpdatesTask::class.java).configureEach {
    // JSON for scripts/aggregate-updates.py, plain text for humans.
    outputFormatter = "json,plain"
    // Drop the pre-release step; also makes the Gradle check use the `current` channel.
    rejectPreReleases = true
  }
})
