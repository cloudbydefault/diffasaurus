# macOS updater staging procedure

Use this before calling Preview 5 updater production-ready. Do not publish a fake public release.

## Goal

Verify Build A (older `CFBundleVersion`) can update to Build B (newer version) through a staging HTTPS appcast with EdDSA-signed DMG enclosure, including session restore.

## Prerequisites

- Same EdDSA key pair used for both builds
- Local HTTPS server (for example `python3 -m http.server` behind `mkcert` / `nginx` / `caddy`)
- Two local build outputs installed under distinct names for testing, or sequential installs to `/Applications/Diffasaurus.app`

## Build A (installed baseline)

1. Check out the Preview 5 bootstrap commit/branch.
2. Set version to an older preview build, for example `0.2.0rc5` / `CFBundleVersion=0.2.0.5`.
3. Build with Sparkle embedded:

```bash
export DIFFASAURUS_SPARKLE_PUBLIC_ED_KEY='YOUR_PUBLIC_KEY'
export DIFFASAURUS_RELEASE_BUILD=1
sh scripts/build_macos.sh
```

4. Copy `dist/Diffasaurus.app` to `/Applications/Diffasaurus.app`.
5. Launch from `/Applications` and confirm **Diffasaurus → Check for Updates…** appears.

## Build B (update target)

1. Bump version to a newer preview build, for example `0.2.0rc6` / `CFBundleVersion=0.2.0.6`.
2. Rebuild with the same public EdDSA key.
3. Sign the release DMG:

```bash
/path/to/Sparkle/bin/sign_update release/Diffasaurus-0.2.0-preview.6-macOS-arm64.dmg
```

4. Serve the DMG over HTTPS from your staging host.
5. Generate a staging appcast item with `generate_appcast` or hand-assemble using the `sign_update` output for `sparkle:edSignature` and the exact byte `length`.

Example staging appcast item:

```xml
<item>
  <title>Diffasaurus 0.2.0 Preview 6 (staging)</title>
  <sparkle:version>0.2.0.6</sparkle:version>
  <sparkle:shortVersionString>0.2.0</sparkle:shortVersionString>
  <enclosure url="https://staging.example.test/Diffasaurus-0.2.0-preview.6-macOS-arm64.dmg"
             length="12345678"
             type="application/octet-stream"
             sparkle:edSignature="..." />
</item>
```

## Run the test from Build A

1. Open Snapshot Explorer, choose a report family/snapshot, switch to Dashboard if desired.
2. Launch Build A with staging feed override:

```bash
export DIFFASAURUS_UPDATER_TEST_MODE=1
export DIFFASAURUS_UPDATE_FEED_URL='https://staging.example.test/appcast-preview.xml'
open /Applications/Diffasaurus.app
```

3. Choose **Check for Updates…**
4. Confirm Build B is detected.
5. Accept install; verify:
   - EdDSA verification succeeds
   - app quits
   - bundle in `/Applications` is replaced
   - app relaunches
   - navigation page, report family, snapshot (if still present), and table/dashboard view restore

## Negative checks

- Staging feed with no newer item → Sparkle reports up to date
- Tampered DMG or wrong signature → install rejected
- App launched from mounted DMG → update menu disabled with guidance to move to Applications

## Result logging

Record:

- Sparkle version
- Build A/B `CFBundleVersion`
- appcast URL used
- whether session restore succeeded
- codesign `--verify --deep --strict` output for final installed app

This staging run was **not executed as part of automated CI** in the Preview 5 bootstrap PR; perform it manually before publishing Preview 5 publicly.
