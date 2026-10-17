# Hosted copy (Cloudflare Pages + Access)

Optional. The dashboard runs locally; this adds a copy you can open from any device
while the Mac is off, behind a login. A scheduled GitHub Actions job syncs Garmin,
builds the pages and deploys them to Cloudflare Pages; Cloudflare Access asks for a
**GitHub login** before any page is served, and lets in only your account.

**Why not GitHub Pages:** on a public repo GitHub Pages are public to everyone (private
Pages need GitHub Enterprise), and putting a login only in front of a custom domain
leaves `<user>.github.io` open. The deployment artifact would also be downloadable.

## What the hosted copy is

- Built with `build_dashboard.py --hosted`: no Sync button, no Coach popup and none of
  its data (prompt, caps, coach notes); `noindex` and `no-store` headers.
- Refreshed every 3 hours (`.github/workflows/hosted.yml`). Read-only: Sync and Coach
  updates happen on the Mac, then `./run.sh hosted-secrets` sends the config up.

## Privacy rules (the repo is public)

| Risk | Handling |
|---|---|
| Personal config, Garmin tokens | Repository **secrets**, written to the runner with `umask 077` |
| Garmin cache between runs (health data) | Stored in the Actions cache **encrypted** with `CACHE_KEY` (AES-256, PBKDF2); caches can be read by pull-request workflows |
| Public workflow logs | Sync output is discarded; only pass/fail is printed |
| Deployment | Straight to Cloudflare with wrangler; nothing uploaded to GitHub Pages or as a downloadable artifact |
| Search engines | `X-Robots-Tag: noindex` + `<meta name="robots">` |
| Garmin rate limits | The encrypted cache keeps each run at ~9 API calls (a cold run backfills ~250 calls once) |
| Garmin token expiry | The cache also carries the tokens as last refreshed; if they expire, `./run.sh login` + `./run.sh hosted-secrets` |

## Setup (once)

1. **Cloudflare Pages project.** Cloudflare dashboard → Workers & Pages → Create →
   Pages → *Direct upload*, name it (e.g. `training-dashboard`). Upload any file once
   to create it.
2. **Cloudflare API token.** My Profile → API Tokens → Create token → template
   *Edit Cloudflare Workers* or a custom token with **Account → Cloudflare Pages → Edit**.
   Note your **Account ID** (Workers & Pages overview).
3. **GitHub login (Cloudflare Access).** The only way in is your GitHub account.
   - Cloudflare Zero Trust → Settings: note your **team name** (`<team>.cloudflareaccess.com`).
   - GitHub → Settings → Developer settings → OAuth Apps → **New OAuth app**:
     homepage `https://<team>.cloudflareaccess.com`, callback
     `https://<team>.cloudflareaccess.com/cdn-cgi/access/callback`. Copy the Client ID,
     generate a client secret.
   - Zero Trust → Integrations → Identity providers → **Add → GitHub**: paste the
     Client ID (App ID) and secret, save, authorize, **Test**.
   - Pages project → Settings → **Enable access policy**. Then Zero Trust → Access →
     Applications → that application:
     - *Public hostname*: **remove the `*`** from the subdomain, so the production
       `<project>.pages.dev` is covered, not only previews;
     - *Login methods*: **GitHub only** (switch off One-time PIN), and turn on
       *Instant Auth* to skip the chooser;
     - *Policy*: Allow, Include → **Emails** → the primary email of your GitHub account.
   - Test in a private window: you must land on GitHub's login, not the dashboard.
   (Cloudflare docs: Pages → Known issues → "Enable Access on your `*.pages.dev`
   domain"; Zero Trust → Identity providers → GitHub.)
4. **GitHub secrets and variables** (repo → Settings → Secrets and variables → Actions):
   - secrets `CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_ACCOUNT_ID`;
   - variables `CF_PAGES_PROJECT` (the project name) and `HOSTED_ENABLED` = `true`;
   - then on the Mac: `./run.sh hosted-secrets` (config, Garmin tokens, and a random
     `CACHE_KEY` the first time).
5. **First run:** `gh workflow run hosted.yml`, then open `https://<project>.pages.dev`.

## Day to day

- After a Coach update or a config edit on the Mac: `./run.sh hosted-secrets`.
- To stop: set `HOSTED_ENABLED` to `false` (and delete the Pages project if wanted).
- GitHub pauses scheduled workflows after 60 days without repository activity; any
  commit or a manual run re-enables it.
