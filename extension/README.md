# Kiln for Zen (and Firefox)

A toolbar button for your Kiln box. It shows what llama-server is doing and lets you switch
presets, stop or restart without opening the control panel. A badge on the button shows the
state at a glance, and notifications tell you when a tuning run finishes, a model is ready, the
server crashes or a download ends.

<table>
  <tr>
    <td><img src="../docs/screenshots/extension-popup.png" alt="The Kiln popup in the dark theme: the running model, Stop and Restart, and the preset list" width="300"></td>
    <td><img src="../docs/screenshots/extension-popup-light.png" alt="The same popup in the light theme" width="300"></td>
  </tr>
</table>

<sub>Demo instance with sample data.</sub>

It's a Manifest V3 WebExtension with no build step and no dependencies. It talks to Kiln's
control API ([../docs/API.md](../docs/API.md)), mostly `GET /api/quick` and
`POST /api/server/start {preset_id, replace: true}`. It needs a Kiln build that has those.

## What it does

| Where | What |
|---|---|
| **Popup** (toolbar button, or `Alt+Shift+K`) | Server state, model, preset, uptime and context; Stop and Restart; every preset, most recently used first, with **Start** (or **Switch**, which stops the running model first). Presets whose model file is gone are disabled. With more than six presets, a filter appears: type, then Enter starts the first match. VRAM, GPU load and power at the bottom, plus a link when downloads are running. |
| **Badge** | `ON` running · `…` loading or stopping · `!` crashed · `TUNE` tuning · `?` can't reach Kiln · nothing when stopped. Hover for details. |
| **Notifications** | Tuning finished (speeds, time) or failed · a model you started from the popup is ready · llama-server crashed or failed to load · a download finished or failed. Each can be turned off in settings. Clicking one opens the matching Kiln page. |
| **Settings** | Kiln URL, API token, notification switches. **Save and test** checks the connection and says what's wrong if it fails. |

The background script checks Kiln once a minute, and every ~6 seconds while a model is loading
or a start from the popup is pending. The popup itself refreshes every 1–4 seconds while open.

## Set up Kiln

If Kiln has no `KILN_UI_PASSWORD`, there's nothing to set up: the extension works with just the URL.

With a password, give the extension its own token instead of the password:

```bash
cd ~/kiln && echo "KILN_API_TOKEN=$(openssl rand -hex 24)" >> .env && docker compose up -d --build
grep KILN_API_TOKEN .env    # paste this value into the extension's settings
```

Kiln accepts cross-origin calls from browser extensions by default (`KILN_CORS_ORIGINS`), so no
other change is needed. See [API.md → CORS](../docs/API.md#cross-origin-access-cors) to pin it to
this one extension.

## Install in Zen

Zen, like Firefox, only installs signed add-ons permanently, and Zen does not honor
`xpinstall.signatures.required=false`. You have two options.

**Try it now (until Zen restarts):**

1. Open `about:debugging#/runtime/this-firefox`.
2. **Load Temporary Add-on…** and pick `extension/manifest.json`.
3. Settings open on first load: enter the Kiln URL (for example `http://ai-box:8090`) and the token,
   then **Save and test**. Allow the host permission when asked. It's optional (Kiln allows the
   extension through CORS anyway), but it lets the extension work even if you later restrict
   `KILN_CORS_ORIGINS`.
4. Pin the Kiln button: right-click the toolbar → **Customize Toolbar**, or open the extensions menu
   and pin it.

**Install permanently: sign it as an unlisted add-on.** Mozilla signs unlisted add-ons
automatically, without a store listing or review queue. You need a free addons.mozilla.org
account and API credentials from
[addons.mozilla.org/developers/addon/api/key](https://addons.mozilla.org/developers/addon/api/key/).

```bash
cd ~/kiln/extension
npx web-ext@8 sign --channel=unlisted --api-key="$AMO_JWT_ISSUER" --api-secret="$AMO_JWT_SECRET" -a dist
```

Then open the `.xpi` from `dist/` in Zen (drag it onto a window, or `about:addons` → gear →
**Install Add-on From File…**). The add-on ID is `kiln-quick@kiln.local` in `manifest.json`. AMO
needs IDs to be unique across all add-ons, so if signing says the ID is taken, change it to
something of your own (for example `kiln@yourname.dev`). Bump `version` in `manifest.json` before
each new signing.

## If something doesn't work

| Symptom | Fix |
|---|---|
| "Can't reach Kiln" but the URL opens in a tab | Zen's HTTPS-Only Mode upgrades `http://` requests. Add an exception for the Kiln host (padlock icon on the Kiln tab → turn HTTPS-Only off for this site), or put Kiln behind HTTPS. |
| "Kiln needs an API token" / "rejected the API token" | Kiln has `KILN_UI_PASSWORD` set. Use the `KILN_API_TOKEN` value, not the password. If the token is empty in `.env`, set one and restart Kiln. |
| "too old for the extension" | Rebuild the Kiln container so it has `/api/quick`. |
| Start/Switch is greyed out | The tuner is holding the server (the popup says so), or that preset's model file is missing. |
| No notifications | Check the switches in settings, and that Zen/your OS allow notifications from Zen. |

## Develop

```bash
npx web-ext@8 lint            # manifest and code checks (should be 0 errors, 0 warnings)
npx web-ext@8 run --firefox=/path/to/zen   # launches Zen with the extension loaded, reloads on save
npx web-ext@8 build -a dist   # zip for signing or sharing
./sync-ember.sh               # after regenerating Ember tokens in ../web
```

| File | Job |
|---|---|
| `manifest.json` | MV3 manifest. The CSP override drops Firefox's default `upgrade-insecure-requests`, so a plain-`http://` Kiln on the LAN works. |
| `lib/kiln.js` | Settings, the API client (timeouts, error kinds: `config`, `unreachable`, `auth`, `http`), formatting. |
| `popup.html/js` | The popup. Plain DOM; server text is always set as text, never parsed as HTML. |
| `options.html/js` | Settings and connection test. |
| `background.js` | Badge, polling (`alarms`), notifications. Event page: state lives in `storage.local`, not in variables. |
| `lib/icons.js`, `css/ext.css` | Ember icons (same paths as the web UI) and styles. |
| `css/tokens.css`, `fonts/` | Copies of the web UI's Ember tokens and fonts (`sync-ember.sh`). |
