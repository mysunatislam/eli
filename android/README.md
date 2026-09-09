# Eli Companion (Android)

A minimal Kotlin app that wraps the phone page served by the Eli backend
(`http://<pc-ip>:8790/mobile?token=…`) in a WebView, so Eli lives on the home screen and can
use notifications and the microphone through the browser engine.

Not compiled in the environment where this MVP was built; the project is a standard Android
Studio (Gradle Kotlin DSL) layout and should build with Android Studio Koala or newer:

1. Open the `android/` folder in Android Studio and let it sync.
2. Run on a phone that is on the same Wi-Fi as the PC.
3. On first launch, paste the URL shown by the **Phone** button in Eli's desktop panel.

Notes
- `network_security_config.xml` allows cleartext HTTP on the LAN (the backend has no TLS).
- The WebView grants microphone permission to the page when the app has `RECORD_AUDIO`.
- Everything the page can do (live screen view, commands, approvals) works here unchanged.
