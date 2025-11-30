# rPi-station

Raspberry Pi-based media & status display with a Flask web UI, Spotify + Last.fm integration, secure overlay events (SSE), device/web service webhooks (Wyze, Konnected, Xbox), notifications, and small display support (Framebuffer TFT, ST7789, Waveshare e-paper). Includes performance-focused HUD with caching, process/thread offloading, and token / HMAC protection.

## 🚀 Highlights

- Multi-screen HUD: Weather, Spotify Now Playing, Clock (album / weather / solid backgrounds), E-Paper optimized layout
- Live Overlay: Server-Sent Events stream (`/event_stream`) fed by authenticated `/events` posts
- Notifications: Persisted (SQLite) + UI management (`/notifications/ui`)
- Spotify + Last.fm: Playback state, scrobbling, MusicBrainz cover fallback
- Xbox Presence: Optional Microsoft Graph OAuth-based polling
- Security: Token auth, optional HMAC validation, key rotation & token encryption (Fernet)
- Performance: Reduced SPI speed default (32MHz), frame throttle (`max_fps`), fbcp-ili9341 DMA driver target, LRU image caches, background generation in process pool

## 🖥️ Display & Performance Features

| Feature | Purpose | Config / Command |
|---------|---------|------------------|
| Lower SPI speed (32MHz) | Reduce tearing on ST7789 | `display.st7789.spi_speed` in `config.toml` |
| Frame throttle | Caps draw rate | `settings.max_fps` (default 25) |
| fbcp-ili9341 DMA | Hardware-accelerated buffer copy | `make setup-fbcp` (creates service) |
| MD5 frame dedup | Skip identical redraws | Automatic in HUD code |
| ProcessPoolExecutor | Heavy image ops off main thread | Auto init based on CPU |
| LRU caches | Avoid reprocessing album art | Internal (album bg, resize, dither) |

## 📦 Installation (Raspberry Pi)

Headless Raspberry Pi OS Lite is recommended (desktop not required). A detailed checklist is in `HEADLESS_SETUP.md`.

### Prerequisites

- Raspberry Pi OS Lite (64-bit recommended on Pi 3/4/5)
- Enable SPI (and I2C if you need it)
- Network access (Ethernet or Wi‑Fi)

Enable interfaces non-interactively:

```bash
sudo raspi-config nonint do_spi 0
sudo raspi-config nonint do_i2c 0   # optional
sudo reboot
```

```bash
git clone https://github.com/klept0/rPi-station.git
cd rPi-station
sudo make system-deps        # Packages + uv
make python-packages         # Virtualenv & Python deps
make config                  # Guided configuration
```

### Choose Display Path

Path A — GoodTFT 3.5" TFT (framebuffer driver, /dev/fb1)

Run vendor driver installer (reboots):

```bash
sudo make setup-display
```

Then set in `make config-display`: `display.type = framebuffer` and framebuffer `/dev/fb1`.

Path B — ST7789 HAT via Python library

- Skip `setup-display` (no vendor driver needed)
- In `make config-display`, set `display.type = st7789` and pins/rotation

Then enable the service:

```bash
sudo make setup-service      # Systemd service
make sync-code               # Sync source after changes
```

Optional (DMA fbcp driver for some SPI TFTs):

```bash
sudo make setup-fbcp
```

### Local Development (macOS / Linux Desktop)

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt  # if present; else install needed libs
python neondisplay.py
```

Open `http://127.0.0.1:5000`.

## 🔧 Make Targets (Core)

| Target | Description |
|--------|-------------|
| `system-deps` | Install OS packages + uv |
| `python-packages` | Create venv & install Python deps |
| `setup-display` | Install vendor LCD drivers (reboots) |
| `setup-fbcp` | Build & enable fbcp-ili9341 service |
| `setup-service` | Create & enable systemd service |
| `sync-code` | rsync source → `/opt/neondisplay` & restart |
| `config` | Full interactive configuration walkthrough |
| `update-packages` | Upgrade Python dependencies |
| `start` / `stop` / `status` / `logs` | Manage systemd service |

Configuration subtasks: `config-api`, `config-display`, `config-fonts`, `config-buttons`, `config-wifi`, `config-settings`.

## ⚙️ Configuration Overview

Primary file: `config.toml` (auto-created). Change via Advanced Config web page or make targets.

Key Sections:

- `display`: Type (`framebuffer`, `st7789`, `waveshare_epd`, `dummy`), rotation, ST7789 pins & `spi_speed`
- `settings`: Start screen, GPSD, Google Geo, `max_fps`, sleep timeout
- `overlay`: Enabled, token / encrypted_token, key source (`file` or env)
- `lastfm`: API credentials, `enabled`, scrobble threshold, minimum seconds
- `buttons`, `fonts`, `wifi`, `clock`, `ui`

## 🔐 Security

| Mechanism | Endpoints / Use |
|-----------|-----------------|
| Overlay token (`X-Overlay-Token`) | `/events` authenticated event ingress |
| HMAC (sha256) optional | Overlay & webhook posts (configured secrets) |
| Token encryption (Fernet) | Stores `encrypted_token` + key rotation support |
| Rate limiting | Applied to `/events`, `/device_notify` to mitigate spam |

Token Encryption Steps:

1. Enable encryption in Advanced Config.
2. Choose key source (`file` auto-generates `secrets/overlay_key.key`, or environment variable).
3. Regenerate token → encrypted form saved; plaintext shown once.
4. Rotate key with `/rotate_overlay_key` UI action (re-encrypts existing token).

## 📡 Overlay & Events

- Stream consumer: `/event_stream` (SSE)
- Producer: POST `/events` with JSON body + header `X-Overlay-Token` (and HMAC if enabled)
- Device/webhook aggregator: `/device_notify` (multi-source normalized events)
- Key management actions: `/regenerate_overlay_token`, `/rotate_overlay_key`

## 🔔 Notifications

- Data: SQLite DB `neon_notifications.db`
- Endpoints: `/notifications`, `/notifications/filters`, `/notifications/clear`, DELETE `/notifications/<id>`
- UI: `/notifications/ui` (latest entries + filters)
- HUD: Latest notification rendered on clock screen

## 🎵 Spotify & Last.fm

Setup:

1. Spotify developer app → add redirect `http://127.0.0.1:5000`
2. Enter client id/secret in Advanced Config
3. Authenticate in UI (or interactive prompt if running headless)
4. Optional Last.fm: supply API key/secret + username/password → scrobble after threshold

Music Stats: `/music_stats` (top tracks/artists, counters)

Fallback art: MusicBrainz / Cover Art Archive attempted when Spotify image missing.

## 🎮 Xbox Presence

1. Register Microsoft app (scopes: `offline_access`, `User.Read`, `Presence.Read`)
2. Configure client id/secret in Services → Xbox panel
3. Launch connect flow → tokens saved in config
4. Presence updates appear in notifications & overlay (if enabled)

## ⚡ Performance Internals

- ThreadPoolExecutor (network / I/O), ProcessPoolExecutor (image transforms)
- LRU caches (album bg, resized images, dithered conversion)
- MD5 hashing for frame dedup (skip identical draws)
- Adaptive FPS: CPU load monitor adjusts animation/text scroll FPS
- Frame throttle: `max_fps` prevents excessive SPI writes

## 🧪 Testing & CI

GitHub Actions workflow `.github/workflows/ci.yml` runs `pytest` on pushes & PRs.

If no tests exist yet, add one:

```bash
mkdir -p tests
echo 'def test_placeholder():\n    assert 2 + 2 == 4' > tests/test_smoke.py
pytest -q
```

## 🛠️ Maintenance & Operations

```bash
make update-packages   # Upgrade deps
make view-config       # Show current config
make reset-config      # Restore defaults
make clean             # Remove service + files
```

## 🐛 Troubleshooting Quick Reference

| Symptom | Checks | Fix |
|---------|--------|-----|
| Display tearing (ST7789) | Verify `spi_speed` & `max_fps` | Lower `spi_speed` (32M) / reduce `max_fps` / install fbcp |
| No HUD updates | Service status | `make status` / check logs |
| Spotify auth fails | Redirect mismatch | Ensure URI matches developer dashboard |
| GPS unavailable | gpsd running? | Disable GPSD or set fallback city |
| Overlay rejected | Token/HMAC mismatch | Re-check header & secret, rotate token |

Logs & status:

```bash
make logs    # journalctl -f
make tail    # application log tail (if present)
make status  # systemd status
```

## 📄 License

See `LICENSE`.

## 📷 Screenshots

See `screenshots/` directory for examples.

---
Improvement ideas / issues welcome via GitHub. For additional integrations (e.g., multi-account Xbox, expanded webhook sources), open an Issue or PR.
