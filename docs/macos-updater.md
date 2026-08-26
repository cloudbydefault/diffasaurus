# Diffasaurus macOS updater (Preview channel)

Preview 5 is the bootstrap build: it embeds Sparkle 2 but Preview 4 users still install it manually. Later Preview builds update in place through the preview appcast.

## Pinned Sparkle version

Sparkle **2.9.6** is pinned in [`packaging/macos/sparkle.pin.json`](../packaging/macos/sparkle.pin.json).

The framework is fetched deterministically by [`scripts/fetch_sparkle_framework.sh`](../scripts/fetch_sparkle_framework.sh) using the pinned URL and SHA-256 checksum. The extracted framework is cached under `packaging/macos/vendor/` (gitignored).

## EdDSA key setup (one-time)

From the extracted Sparkle distribution:

```bash
./Sparkle/bin/generate_keys
```

- **Private key:** store in macOS Keychain / CI secret only. Never commit it.
- **Public key:** supply at build time as `DIFFASAURUS_SPARKLE_PUBLIC_ED_KEY`. It is written to `SUPublicEDKey` in the app Info.plist.

Per release, sign update archives with Sparkle tooling:

```bash
./Sparkle/bin/generate_appcast /path/to/release/folder
# or
./Sparkle/bin/sign_update Diffasaurus-0.2.0-preview.6-macOS-arm64.dmg
```

SHA256SUMS remains for human/release integrity only. Sparkle trust comes from EdDSA signatures.

## Preview appcast

Published URL:

`https://cloudbydefault.github.io/diffasaurus/appcast-preview.xml`

**Preview 5 release gate:** do not publish Preview 5 publicly until that URL serves a valid Sparkle appcast with a signed enclosure for the newest preview build. Until GitHub Pages is configured, Sparkle reports `SUSparkleErrorDomain Code=2001` (HTTP 404) when checking the production feed. That is expected and is not an updater defect.

After a successful in-app update, Sparkle relaunches the installed app without staging environment variables. A post-update **Check for Updates** against the production URL will therefore show the same 404 until the appcast is live. Do not suppress or hide those network errors.

Template: [`appcast/appcast-preview.xml`](../appcast/appcast-preview.xml)

Each item must include:

- `sparkle:version` matching `CFBundleVersion` (for example `0.2.0.6`)
- `sparkle:shortVersionString`
- release notes/title
- enclosure URL pointing at the Preview DMG
- `length`
- `sparkle:edSignature`

Do not enable `SURequireSignedFeed` in Preview 5 bootstrap.

## Release build requirements

Release macOS builds (`DIFFASAURUS_RELEASE_BUILD=1`, default in `scripts/build_macos.sh`) require:

```bash
export DIFFASAURUS_SPARKLE_PUBLIC_ED_KEY='...'
export DIFFASAURUS_SPARKLE_FEED_URL='https://cloudbydefault.github.io/diffasaurus/appcast-preview.xml'
sh scripts/build_macos.sh
```

Development/source mode (`python3 run.py`) does not self-update.

## Settings hooks

`config/settings.json` supports:

- `updates_enabled` (default `true`)
- `managed_updates` (default `false`) — disables Sparkle for Intune-managed deployments
- `update_channel` (fixed to `preview` internally for this slice)

## Session restore

Before Sparkle installs an update, Diffasaurus saves minimal navigation state to:

`~/Library/Application Support/Diffasaurus/config/session_state.json`

On the next launch after update, that state is restored once and then deleted.

## Test feed override

For staging only:

```bash
export DIFFASAURUS_UPDATER_TEST_MODE=1
export DIFFASAURUS_UPDATE_FEED_URL='https://localhost:8443/appcast-preview.xml'
```

Normal end-user settings cannot override the production feed.

## Staging end-to-end test

See [`docs/macos-updater-staging.md`](macos-updater-staging.md).

## Deferred to later releases

- automatic background checks
- custom updater UI
- delta updates / ZIP migration
- stable channel selector UI
- signed appcast feed enforcement (`SURequireSignedFeed`)
- GitHub Actions release automation
- full Managed App Configuration / MDM plist integration
