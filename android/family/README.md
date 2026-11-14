# Daylight family app (Android)

The family dashboard as an Android app, for the person checking on a relative (Sara in the
demo). Package `com.junction.family`, label **Daylight**. It sits beside the glasses companion
app (`:app`, `com.junction.glasses`) in the same Gradle project.

## How it's built

The UI is the web dashboard from `../dashboard`, the single source for the browser, the
installable web app and this app. The `syncDashboard` task copies it into the APK's assets at
build time, so there is nothing to keep in sync by hand.

- **Instant start, works offline.** The page and its looks, fonts and artwork load from the APK
  through `WebViewAssetLoader` (`https://appassets.androidplatform.net/dashboard/`), never from
  the network. The example day works with no backend at all.
- **Data requests are native.** The page asks its own origin for `/timeline`, `/summary` and
  `/evidence/<id>` as it does in the browser. `ApiProxy` forwards only those three read-only routes
  to the configured backend with OkHttp: certificate checks, connection reuse and HTTP/2 stay in
  native code. Redirects are refused. The page's Content-Security-Policy stays `'self'` and the
  JavaScript is unchanged.
- **The token stays where it was.** It lives only in the page's memory, as in the browser,
  and travels in the Authorization header. The app stores only the backend address (not a secret).
- **Battery and smoothness.** WebView timers and animations pause when the app is in the
  background. The splash screen holds until the first frame is drawn (at most 1.5 s). The system
  bars follow the page's colour, which can change with the real sky. If the renderer process dies,
  the app reloads cleanly. Release builds are minified and resource-shrunk.
- **No JavaScript bridge.** The app only reads the page background colour and closes open
  dialogs on Back, through `evaluateJavascript`.

## Build and install

Java 17 and Android SDK 36 are needed, as for the glasses app. CI builds both apps and uploads
`daylight-family-apk` (debug, plus an unsigned release build).

```sh
cd android
./gradlew :family:assembleDebug :family:testDebugUnitTest :family:lintDebug
adb install -r family/build/outputs/apk/debug/family-debug.apk
```

On first launch the app asks for the home service address. Release builds accept only
`https://host[:port]`. Debug builds also accept `http://127.0.0.1:8000` (a phone on USB after
`adb reverse tcp:8000 tcp:8000`) and `http://10.0.2.2:8000` (the emulator). To change the address
later, touch and hold the app icon and choose **Change home address**. Then use **Connect** in the
app with the API token. **Example day** works without connecting.

## Meta glasses: where each app runs

Meta's glasses don't run third-party apps on the glasses themselves. The Wearables Device Access
Toolkit (`mwdat-*`, already used by `:app`) is a phone SDK: an Android or iOS app on the wearer's
phone uses the glasses' camera, microphone, speakers and, on Meta Ray-Ban Display, the in-lens
display. So:

| Person | App | Why |
| --- | --- | --- |
| The wearer (Anna) | `:app`, the glasses companion, on her phone | Asks questions by voice through the glasses and hears answers. Short answers can show on the Ray-Ban Display |
| The family (Sara) | `:family`, this app, on her own phone | The full day view needs a real screen. It never needs the glasses |

What belongs on the glasses display is glanceable and one line long: "You took your morning
pills at 08:00", a due/late medication prompt, or a yes/no with nod to confirm. It is sent from
`:app` through `mwdat-display`. The family dashboard should not be squeezed onto the glasses.
