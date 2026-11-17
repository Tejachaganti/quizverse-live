# QuizVerse Live — Android Packaging Guide

This guide explains how the Vanilla JavaScript web application is packaged for Android using Capacitor and modern Android WebViews.

---

## 1. Architecture & Security Approach

- **Web Assets**: The frontend (`quizverse/static/`) consists of high-performance Vanilla JavaScript, semantic HTML5, and responsive CSS3.
- **Packaging Framework**: Capacitor 6+ or a modern Android WebView wrapper.
- **HTTPS Enforcement**: The Android application communicates strictly over HTTPS and secure WebSockets (`wss://`). Cleartext traffic is disabled (`android:usesCleartextTraffic="false"`).
- **Zero Secrets on Client**: The mobile client bundle contains **ZERO** server secrets, database credentials, or AI provider keys. All credentials remain on the backend server.
- **Authentication Persistence**: The pseudonymous player credential token is stored securely in `localStorage` / Capacitor Storage and authenticated via HTTP headers and signed tickets.
- **Permissions**: Limited strictly to `INTERNET` and `ACCESS_NETWORK_STATE`. No sensitive or dangerous permissions (camera, location, contacts, etc.) are requested.

---

## 2. Prerequisites for Building the Android APK / AAB

1. Node.js 20+ and npm installed on the build machine.
2. Android Studio (version Iguana or newer) with:
   - Android SDK Platform 34
   - Android SDK Build-Tools 34.0.0
   - Android Command-line Tools
   - JDK 17 or newer

---

## 3. Build & Packaging Steps

1. **Install Capacitor CLI**:
   ```bash
   npm install --save-dev @capacitor/core @capacitor/cli @capacitor/android
   ```
2. **Initialize & Sync Assets**:
   ```bash
   # Sync static web assets into the Android native project
   npx cap copy android
   ```
3. **Open in Android Studio**:
   ```bash
   npx cap open android
   ```
4. **Build Release APK or Android App Bundle (AAB)**:
   ```bash
   cd android
   # Build release AAB for Google Play Store upload
   ./gradlew bundleRelease
   
   # Or build test APK
   ./gradlew assembleRelease
   ```
5. The generated bundle will be located at:
   `android/app/build/outputs/bundle/release/app-release.aab`

---

## 4. Google Play Release Checklist (Pending Post-Stage 12)

Before submitting to Google Play:
1. Finalize official branding: application display name, icon artwork (adaptive launcher icons), and splash screen graphics.
2. Sign the `.aab` file using an Android Keystore.
3. Configure Google Play Console listing (privacy policy URL, age rating questionnaire, data safety form).
4. Upload `.aab` to Closed Internal Testing track for testing before public release.
