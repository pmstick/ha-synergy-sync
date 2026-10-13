# Synergy Sync — Home Assistant add-on

Imports your Synergy (WA) smart-meter data into Home Assistant's **Energy dashboard** as
hourly grid import and solar export, so you can see usage next to solar generation.

Built on [sniereffo/synergy-battery-analyser](https://github.com/sniereffo/synergy-battery-analyser),
which worked out how to log in to My Synergy and fetch the half-hourly data. The portal client
here is adapted from it under the Elastic License 2.0 (see [NOTICE.md](NOTICE.md)).

## How it works
Synergy's login needs MFA and reCAPTCHA, so it can't be fully automatic. The add-on runs a real
Chromium window on a virtual display and shows it in the **Synergy Sync** sidebar panel:

1. Open the panel → **Sync now**.
2. Log in to My Synergy in the embedded browser (works on your phone too). If the saved session
   is still valid, this step is skipped automatically.
3. The add-on fetches half-hourly data since the last sync (first run: `initial_days`), caches
   it under `/data`, and imports two statistics:
   - `synergy:grid_import` — **Synergy grid import** (kWh/hour)
   - `synergy:grid_export` — **Synergy grid export** (kWh/hour)

HA long-term statistics are hourly, so each pair of half-hours is summed. The full
half-hourly data is available from **Download half-hourly CSV** in the panel. Synergy data is
usually a day behind, so sync fetches up to yesterday. Re-syncing is safe; totals never double.

## Install
These steps assume Home Assistant OS (needed for add-ons, which HA 2026.2+ calls **apps**),
e.g. running as a VM on Unraid. The add-on image is built by GitHub Actions and published to
GitHub's container registry, so HA only downloads it; nothing is built on the HA box.
Supported architectures: amd64 (x86 VMs) and aarch64.

### 1. Check the VM has enough memory
Chromium needs a few hundred MB while a sync runs. On Unraid: **VMs** → click the HA VM →
**Edit** and make sure it has **at least 4 GB RAM** (the VM must be shut down to change it).

### 2. Add this repository to HA
1. **Settings → Apps → Install app** (the App Store). Older HA versions call this
   **Settings → Add-ons → Add-on Store**.
2. **⋮** (top right) → **Repositories**.
3. Paste `https://github.com/pmstick/ha-synergy-sync`, click **Add**, then close the dialog.

[![Add repository to Home Assistant](https://my.home-assistant.io/badges/supervisor_add_addon_repository.svg)](https://my.home-assistant.io/redirect/supervisor_add_addon_repository/?repository_url=https%3A%2F%2Fgithub.com%2Fpmstick%2Fha-synergy-sync)

### 3. Install the add-on
1. Still in the App Store, refresh the page. **Synergy Sync** appears in a
   **Synergy Sync add-on repository** section. Click it → **Install** (downloads ~1 GB, a few minutes).
2. On the **Info** tab turn on **Show in sidebar**, then **Start**.
3. The **Log** tab should show a line like `Running on http://0.0.0.0:8099`.

### 4. First sync
1. Click **Synergy Sync** in the sidebar → **Sync now**.
2. The Synergy login page appears in the panel. Log in, including the MFA code.
3. The log shows fetch progress, then "Importing…", then "Done". The first sync pulls
   `initial_days` (90 by default).

### 5. Add it to the Energy dashboard
1. **Settings → Dashboards → Energy**.
2. Under **Electricity grid**: **Add consumption** → **Synergy grid import**, and
   **Add return** → **Synergy grid export**.
3. Optionally enter your Synergy tariff prices.
4. Open the Energy dashboard on **yesterday** (Synergy data is a day behind). You should see
   hourly grid bars next to solar, and the Energy distribution card splits home usage between
   solar and grid.

### Troubleshooting
- **Synergy Sync doesn't appear in the store:** refresh, or **⋮ → Check for updates**. Then
  look at **Settings → System → Logs** → choose **Supervisor** in the dropdown.
- **Install fails:** the reason is in the same Supervisor log.
- **Login panel is blank or black:** **Cancel**, then **Sync now** again. If it persists, check
  the add-on's **Log** tab.
- **Updating:** new versions show up as an **Update** button on the add-on page.

## Options
| option | default | |
|---|---|---|
| `service_id` | *(blank)* | Synergy service to use; blank = first active service on the account |
| `initial_days` | `90` | How far back the first sync goes (max 730) |

## Reminder / trigger from an automation
The add-on accepts `sync` on stdin. That starts a sync and posts a persistent notification
asking you to log in via the panel:

```yaml
alias: Weekly Synergy sync reminder
triggers:
  - trigger: time
    at: "19:00:00"
conditions:
  - condition: time
    weekday: [sun]
actions:
  - action: hassio.addon_stdin
    data:
      addon: xxxxxxxx_synergy_sync  # copy the slug from the add-on page's URL
      input: sync
```

## Development
```bash
cd synergy_sync
uv run --with pytest --with aiohttp --with playwright python -m pytest tests
```

Pushing to `main` with a changed `synergy_sync/` runs the tests and publishes
`ghcr.io/pmstick/{amd64,aarch64}-ha-synergy-sync:<version>`. Bump `version` in
`synergy_sync/config.yaml` for every release, or HA won't offer the update.

## License
[Elastic License 2.0](LICENSE), inherited from
[sniereffo/synergy-battery-analyser](https://github.com/sniereffo/synergy-battery-analyser).
See [NOTICE.md](NOTICE.md) for which files are derived from it and how they were changed.
