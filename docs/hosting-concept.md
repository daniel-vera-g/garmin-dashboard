# Hosting concept: cloud-generated static dashboard + private access

This document explains the architecture choice for accessing the dashboard from
any device (phone, Mac, etc.) while keeping personal data protected.

## Goal

- Reach the dashboard from anywhere.
- Keep costs near zero.
- Avoid running a personal always-on server.
- Keep Garmin credentials and raw data private.

## Concept

Use a **two-layer setup**:

1. **Generation layer (private):** GitHub Actions runs sync + build on a schedule.
2. **Delivery layer (public endpoint, private access):** GitHub Pages serves only
   generated HTML pages, protected by Cloudflare Access login.

## Why this concept

- GitHub Actions + Pages is free-tier friendly and simple to maintain.
- Pages hosting is reliable for static content and device agnostic.
- Cloudflare Access adds authentication in front of the site.
- No inbound connection to a personal machine is required.

## Data and security boundaries

### Stays private

- Garmin tokenstore (in GitHub Actions secret).
- Personal config (`private/config.toml`, in GitHub Actions secret).
- Raw cache (`data/cache/`), readiness/load snapshots, and all local files.

### Published

- `dashboard.html`
- `season.html`
- `health.html`

No API endpoints are exposed in hosted mode.

## Operational model

- Workflow schedule: every 3 hours (plus manual trigger).
- Each run:
  1. Restores secrets into runtime files.
  2. Runs `garmin_sync.py`.
  3. Runs `build_dashboard.py`.
  4. Publishes only the three rendered HTML files.
- Hosted pages are **read-only** (`[app] hosted_read_only = true`).

## Responsibilities

### Repository automation

- Keep workflow healthy.
- Keep docs and setup instructions current.

### User setup

- Configure GitHub Pages to deploy from Actions.
- Add required repository secrets.
- Configure Cloudflare + Access policy.
- Refresh tokenstore secret after re-authentication when needed.

## Fallback option

If strict private-network-only access is preferred, keep local hosting and use
Tailscale to reach your Mac from other devices instead of public Pages hosting.
