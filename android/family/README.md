# Daylight family app (Android)

The family dashboard as an Android app (`com.junction.family`, label **Daylight**) for the person
checking on a relative. It sits beside the wearer's app (`:app`) in the same Gradle project.

- **UI:** the `syncDashboard` task copies `../dashboard` into the APK, so the page, looks and
  example day load offline through `WebViewAssetLoader`, with no copy to keep in sync.
- **Data:** the page asks its own origin for `/timeline`, `/summary` and `/evidence/<id>`;
  `ApiProxy` forwards only those read-only routes to the backend with OkHttp and refuses redirects.
- **Token:** only in the page's memory, as in the browser. The app stores just the backend address.
- No JavaScript bridge; timers pause in the background; release builds are minified.

```sh
cd android
./gradlew :family:assembleDebug :family:testDebugUnitTest :family:lintDebug
adb install -r family/build/outputs/apk/debug/family-debug.apk
```

First launch asks for the backend address: `https://host[:port]`, or in debug builds
`http://127.0.0.1:8000` (USB with `adb reverse tcp:8000 tcp:8000`) or `http://10.0.2.2:8000`
(emulator). Change it later from the icon's long-press menu, **Change home address**.

**Live 3D tab:** the laptop relay's caretaker page (`/care`): the room as the Gaussian splat trained
on the GPU server, the wearer moving in it, the named places as labels, and what they are doing (where and
since when, what changed in view, reminders, today's timeline). Its first use asks for the relay's
address, access key and room; touch and hold **Live 3D** (or use the icon's **Live 3D settings**) to
change them. `RelayProxy` forwards only the page's read-only routes under `/relay/` and adds the key
as an `X-Key` header, so the key never appears in a page address; it is kept in the app's private
preferences. Debug builds may use `http://127.0.0.1:8090` (`adb reverse tcp:8090 tcp:8090`) or the
laptop's private LAN address; release builds need `https`.

**Meta glasses:** they run no third-party apps. The Wearables toolkit is a phone SDK, so the
wearer's phone runs `:app`, which drives the glasses; the family uses this app on their own phone.
Only one-glance cards belong on the glasses display, never the dashboard.
