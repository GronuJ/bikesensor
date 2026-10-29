# kiel.earth iPhone app

Records a bike ride on an iPhone and sends it to [kiel.earth/surface](https://kiel.earth/surface) in the same CSV the bikesensor box writes. SwiftUI, iOS 17 or later, laid out for the iPhone 16 Pro. It follows the approved [design](https://claude.ai/artifact/4mpHdzmq9NQkzxC2qCirXv), cut down to what a ride needs.

- **start ride** starts GPS and the accelerometer. Recording carries on with the phone locked.
- **hold to stop** ends the ride after a one-second press. A ride also stops by itself after 3 minutes standing still.
- **The summary** shows the track, with everything within 100 m of the start and end cut on the phone (dashed). Choose send, later, or discard. The app shows no instructions; they are here.
- **rides** lists every ride with send and retry; swipe left to delete. A ride is deleted from the phone 7 days after kiel.earth confirms it.
- **map** is kiel.earth/surface inside the app, in the site's app mode (`/surface?app`: map and legend only). The app draws nothing of its own there, so it shows whatever the site shows. It reloads when the tab opens after five minutes away; a link out of the page opens in Safari. The app hands the page its upload token (`window.KIEL_TOKEN`, on kiel.earth only), so a tap on the line opens that ride's waveform.

## Install on your iPhone

You need the Mac, the iPhone 16 Pro, a USB-C cable and your Apple ID. A free Apple ID is enough.

### On the Mac, once

1. Install **Xcode** from the Mac App Store and open it. When it asks which platforms to add, tick **iOS**. The download is large, so allow an hour.
2. In **Xcode › Settings › Accounts**, click **+**, choose **Apple Account** and sign in.
3. Get the code in Terminal. The first `git` call offers to install the command line tools; accept.
   ```bash
   git clone https://github.com/GronuJ/bikesensor.git
   cd bikesensor
   git checkout claude/project-thread-2mus8v   # until the pull request is merged
   ```
4. Give the app its upload key:
   ```bash
   cp ios/Local.example.xcconfig ios/Local.xcconfig
   open -e ios/Local.xcconfig
   ```
   Replace the placeholder with the `DEVICE_TOKEN` that kiel.earth's ingest checks. It is the same value as `DEVICE_TOKEN` in `firmware/bikesensor/private_credentials.h`. Cloudflare never shows a secret again; if you no longer have it, set a new `DEVICE_TOKEN` in the Pages project's settings, redeploy, and put the new value here and in the firmware. `Local.xcconfig` is ignored by git.
5. Open the project:
   ```bash
   open ios/KielEarth.xcodeproj
   ```
6. In Xcode, click **KielEarth** at the top of the left sidebar, then the **KielEarth** target, then **Signing & Capabilities**. Under **Team**, pick **your name (Personal Team)**. If Xcode then says the bundle identifier is not available, uncomment `PRODUCT_BUNDLE_IDENTIFIER` in `Local.xcconfig`, give it your own suffix, and reopen the project.

### With the iPhone, once

7. Connect the iPhone with the cable, unlock it and tap **Trust**.
8. On the iPhone, open **Settings › Privacy & Security › Developer Mode** and switch it on. The phone restarts and asks you to confirm. The switch only appears after the phone has been connected to Xcode.
9. In Xcode's toolbar, choose your iPhone as the run destination and press **▶** (⌘R). The first time, Xcode spends a few minutes preparing the phone.
10. If the iPhone says **Untrusted Developer**, open **Settings › General › VPN & Device Management**, tap your Apple ID and **Trust**. Then press ▶ again.

The app stays on the phone and you can unplug. With a free Apple ID it stops opening after 7 days; connect the phone and press ▶ again. The paid Apple Developer Program ($99 a year) makes installs last a year.

## First ride

1. On first start, tick **share my rides** and allow location. Keep **Precise Location** on, or every fix is kilometres wide.
2. Clamp the phone firmly to the stem or bar. In a pocket or bag the body damps the road away.
3. Press **start ride**, lock the phone and ride. A blue location pill in the status bar shows it is still recording.
4. Hold **hold to stop**, or stand still for 3 minutes. Press **send** on the summary.
5. The ride shows up in `/admin#surface` as pending, with an id like `ip-261028_081500`. Process and publish it as you would a box ride.

Both files of every ride are in the Files app under **On My iPhone › kiel.earth › rides**: `<id>.csv` is the full recording, home included, which never leaves the phone, and `<id>.send.csv` is what was uploaded. AirDrop one to the Mac and run `node tools/check-ride.mjs <file>` in kiel-earth to see how `dsp.js` reads it.

## The CSV

The firmware contract (`../CLAUDE.md`), with these differences:

- **Columns:** a trailing `h_acc_m`, the fix's horizontal accuracy in metres. `dsp.js` reads columns by name and ignores it.
- **Rate:** one row per accelerometer sample. Apple promises 100 Hz on an iPhone; since 2026-10-29 the app asks for 200 Hz (`Recorder.sampleHz`), as a test of what the phone delivers. `millis` comes from the sensor's own timestamps, so `node tools/check-ride.mjs` in kiel-earth prints the rate a ride got, and `dsp.js` takes the rate from the file. The box writes 200 Hz. A dropped sample leaves a gap of two sample periods, which `dsp.js` splits on as it does on a firmware stall.
- **ax, ay, az:** acceleration in g × 8192 with gravity kept, so they read as MPU-6050 counts. The sign is flipped to the MPU's convention (+1 g on the axis pointing up). Values beyond ±4 g are not clipped.
- **millis:** time since the first sample, from the sensor's own timestamps.
- **GPS fields:** written on the row nearest the fix's own timestamp; rows are held back 2 s so a late fix still lands in the right place. `gps_time` is UTC with milliseconds; `speed_kmh` is empty when iOS has no valid speed.
- **Ride id:** `ip-` plus the start time in UTC, `ip-yymmdd_hhmmss`, next to the box's `r…`.

### The privacy cut

Before upload the phone cuts every row within 100 m, straight line, of the ride's first and last fix sharper than 50 m, and everything before the first or after the last of those fixes. Rows go in whole fix-to-fix intervals, so each kept stretch starts and ends on a fix, and `millis` keeps the gaps the cut leaves. On the synthetic ride from `tools/synth-ride.mjs` the cut keeps 1.5 of 1.7 km.

## Icon and background

The icon is Kiel's main streets in sand around the förde, with one ride along the Kiellinie drawn in white. The home screen has the same streets behind it, faint, as on the kiel.earth landing page; the other screens stay plain black. They were picked from this [proposal](https://claude.ai/artifact/EB2XpuUTs8yHmSgNJUx9gE). Both images are baked from kiel-earth's `public/bike/landing-streets.json` with Pillow, so re-run this when that file changes:

```bash
python3 ios/tools/bake-art.py ../kiel-earth/public/bike/landing-streets.json
```

## Building and checking without a Mac

`.github/workflows/ios.yml` builds the app unsigned on a GitHub macOS runner for every pull request that touches `ios/`. `RideCSV.swift` and `PrivacyTrim.swift` use only Foundation, so they also compile with `swiftc` on Linux to check their output against `dsp.js`.
