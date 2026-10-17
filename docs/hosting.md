# Hosted dashboard (GitHub Pages + private access)

This project can stay local, but you can also publish a **read-only** dashboard and
open it from phone/Mac anywhere.

## 1) Turn on hosted read-only mode

In `private/config.toml`:

```toml
[app]
hosted_read_only = true
```

This hides the **Sync** and **Coach Apply** actions in hosted pages. Sync/build is
done by GitHub Actions instead.

## 2) Add repository secrets

Set these in **Settings → Secrets and variables → Actions**:

- `DASHBOARD_CONFIG_TOML`  
  Full contents of your `private/config.toml`.

- `GARMIN_TOKENSTORE_TGZ_B64`  
  Base64 tarball of your `~/.garminconnect` tokenstore (from your Mac):
  ```bash
  tar -czf - -C "$HOME" .garminconnect | base64 | pbcopy
  ```
  Then paste clipboard into the secret value.

## 3) Enable GitHub Pages (Actions source)

The workflow `.github/workflows/publish-dashboard.yml`:

- runs every 3 hours (and manually with **Run workflow**),
- syncs Garmin + rebuilds HTML,
- deploys only `dashboard.html`, `season.html`, `health.html` to Pages.

In **Settings → Pages**, set source to **GitHub Actions**.

## 4) Protect access (recommended)

Use **Cloudflare Access** (free tier works well):

1. Add a custom domain in Pages.
2. Put that domain behind Cloudflare.
3. Create an Access policy that only allows your email(s).

Result: the dashboard is reachable from anywhere, but protected by login before any
page content is shown.

## Security notes

- Never commit `private/` or Garmin tokens.
- This workflow publishes only rendered HTML, not `data/cache/`.
- If Garmin tokens expire, run `./run.sh login` locally and refresh
  `GARMIN_TOKENSTORE_TGZ_B64`.
