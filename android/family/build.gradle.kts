import javax.inject.Inject

plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

android {
    namespace = "com.junction.family"
    compileSdk = 36
    defaultConfig {
        applicationId = "com.junction.family"
        minSdk = 26
        targetSdk = 36
        versionCode = 1
        versionName = "0.1"
    }
    buildFeatures { buildConfig = true }
    buildTypes {
        release {
            isMinifyEnabled = true
            isShrinkResources = true
            proguardFiles(getDefaultProguardFile("proguard-android-optimize.txt"))
        }
    }
    testOptions { unitTests.isIncludeAndroidResources = true }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    packaging { resources.excludes += "/META-INF/{AL2.0,LGPL2.1}" }
}
kotlin { compilerOptions { jvmTarget = org.jetbrains.kotlin.gradle.dsl.JvmTarget.JVM_17 } }

// The web dashboard in ../dashboard is the single source of the UI. It is copied into the app's
// assets at build time (only what the page needs), so the app opens instantly and works offline.
abstract class SyncDashboard : DefaultTask() {
    @get:InputDirectory abstract val source: DirectoryProperty
    @get:OutputDirectory abstract val output: DirectoryProperty
    @get:Inject abstract val files: FileSystemOperations

    @TaskAction
    fun sync() {
        files.sync {
            from(source)
            into(output.dir("dashboard"))
            exclude("tests/**", "package.json", "**/*.md", "themes/*/source/**", "sw.js", "manifest.json")
        }
    }
}
val syncDashboard = tasks.register<SyncDashboard>("syncDashboard") {
    source.set(rootProject.layout.projectDirectory.dir("../dashboard"))
}
androidComponents {
    onVariants { variant -> variant.sources.assets?.addGeneratedSourceDirectory(syncDashboard, SyncDashboard::output) }
}

dependencies {
    implementation("androidx.activity:activity-ktx:1.13.0")
    implementation("androidx.core:core-ktx:1.16.0")
    implementation("androidx.core:core-splashscreen:1.0.1")
    implementation("androidx.webkit:webkit:1.14.0")
    implementation("com.squareup.okhttp3:okhttp:4.12.0")
    testImplementation("junit:junit:4.13.2")
    testImplementation("com.squareup.okhttp3:mockwebserver:4.12.0")
}
