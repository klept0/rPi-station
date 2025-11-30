# NeonDisplay

A comprehensive Raspberry Pi-based display system with weather information, Spotify integration, and web-based control interface.
For 3.5" TFT, Display Hat Mini, and Waveshare 2.13" e-ink screens.

## 🌟 Features

![Screenshots](screenshots)

### Display Modes

- **Weather Display**: Current weather conditions with forecasts
- **Spotify Integration**: Now playing display with progress bars
- **Clock Display**: Digital and analog clocks with customizable backgrounds
- Waveshare_epd has a different design with all three at once

### Music Integration

- **Spotify Control**: Play, pause, skip tracks, and control volume
- **Music Statistics**: Track play counts and artist statistics
- **Search & Queue**: Search Spotify and manage playback queue
- **Current Track Display**: Real-time now playing information

### Web Interface

- **Responsive Design**: Works on desktop and mobile devices
- **Dark/Light Theme**: Toggle between themes
- **Real-time Updates**: Live track information and system status
- **Configuration Management**: Web-based configuration interface

### Hardware Support

- **Supported Display Types**:
  - Framebuffer (TFT 3.5")
  - ST7789 (DisplayHatMini)
  - Waveshare E-Paper
- **GPIO Button Control**: Configurable physical buttons for st7789
- **Touch Control**: Touch support for 3.5" tft
- **GPS Integration**: Location services with GPSD support

## 🛠️ Hardware Requirements

- Raspberry Pi
- Supported display (3.5" TFT, ST7789, or E-Paper)(optional)
- Internet connection (WiFi or Ethernet)
- Optional: GPS module for location services

## 📦 Installation

### Quick Install

```bash
# Clone the repository
https://github.com/NeonLightning/NeonDisplay.git
cd Hud35

# Run complete installation (excluding display drivers)
sudo make
```

### Step-by-Step Installation

1. **System Dependencies**

   ```bash
   make system-deps
   ```

2. **Python Environment & Packages**

   ```bash
   make python-packages
   ```

3. **Display Setup** (⚠️ **Will reboot system**)

   ```bash
   make setup-display
   ```

4. **System Service Setup**

   ```bash
   make setup-service
   ```

5. **Configuration**

   ```bash
   make config
   ```

## ⚙️ Configuration

### API Keys Setup

You'll need to configure the following API keys:

- **OpenWeatherMap**: Free weather API key
- **Spotify**: Client ID and Secret for music integration
- **Google Geolocation**: Optional, for precise location

### Configuration Methods

**Web Interface** (Recommended and needed to authenticate):

- Access the web UI after installation
- Navigate to "Advanced Configuration"
- Fill in API keys and settings

**Command Line**:

```bash
# Interactive configuration walk-through
make config

# Individual configuration sections
make config-api
make config-display
make config-fonts
make config-buttons
make config-wifi
make config-settings
```

### Key Configuration Sections

- **API Configuration**: Weather and Spotify API keys
- **Display Settings**: Screen type, rotation, timeout
- **Font Configuration**: Custom fonts for different display elements
- **Button Mapping**: GPIO pins for physical buttons
- **WiFi Settings**: Access point configuration
- **Clock Appearance**: Digital/analog with background options

## 🚀 Usage

### Starting the System

```bash
# Start the service
make start

# Check status
make status

# View logs
make logs
```

### Web Interface

After starting, access the web interface at:

```
http://[raspberry-pi-ip]:5000
```

### Running the Test Suite

Install the development/test dependencies and run pytest:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pytest -q
```

For a full guide on tests and a detailed end-to-end testing scenario, see `TESTING.md` in the project root.

```

### Service Management

```bash
# Start/stop/restart service
make start
make stop
make restart

# View real-time logs
make tail
```

## 🎵 Spotify Integration

### Authentication

1. Create a Spotify Developer application
2. Set redirect URI to `http://127.0.0.1:5000`
3. Enter Client ID and Secret in configuration
4. Authenticate through the web interface

### Features

- Now playing display with progress bars
- Playback controls (play, pause, skip, volume)
- Search and add to queue
- Music statistics and play history

## 📊 Music Statistics

Access detailed music statistics at:

```
http://[raspberry-pi-ip]:5000/music_stats
```

Features:

- Most played songs and artists
- Total play counts
- Interactive bar charts

## 🔧 Advanced Features

### Display Types

- **Framebuffer**: Standard TFT displays
- **ST7789**: For DisplayHatMini with SPI configuration
- **Waveshare E-Paper 2.13"**: E-ink displays
- **Dummy Display**: No screen enabled

### Location Services

- **GPSD**: Hardware GPS with gpsd service
- **Google Geolocation**: Network-based location
- **Fallback City**: Manual location setting

## 🛠️ Maintenance

### Updating Packages

```bash
make update-packages
```

### Viewing Configuration

```bash
make view-config
```

### Resetting Configuration

```bash
make reset-config
```

### Complete Cleanup

```bash
make clean
```

## 🐛 Troubleshooting

### Common Issues

**Display Not Working**:

**Spotify Authentication Fails**:

**Service Won't Start**:

**No Internet Connection**:

### Logs and Debugging

```bash
# Service logs (systemd)
make logs

# Application logs
make tail

# Service status
make status
```

**Note**: The display driver installation (`make setup-display`) will reboot your system. Ensure you save any work before proceeding.

## 🔔 Recent Features & Improvements

This project has added several new performance and integration features since the base release. Here are the highlights:

- Performance improvements: The HUD now uses a ThreadPoolExecutor for non-blocking network and I/O tasks, and a ProcessPoolExecutor (lazily initialized) for CPU-heavy operations such as image resizing, dithering, and background generation. This reduces main-thread CPU and stops UI stutter.
- Smart caching: LRU caches for album backgrounds, resized images, and dithered images reduce repeated processing. The display update process includes frame deduplication (MD5) to avoid unnecessary writes.
- Last.fm support: Optional now-playing reporting and scrobbling with a configurable scrobble threshold and minimum time before scrobbling.
- MusicBrainz fallback: Attempts to fetch release cover images from MusicBrainz / Cover Art Archive when Spotify art is missing.
- Overlay & Event Streaming: A secure overlay endpoint (`/events`) streams events via SSE to `overlay.html`. You can configure an overlay token and pick which events are shown.
- Device webhooks: Wyze snapshots (saved to `static/wyze_last.jpg`), Konnected messages, and Xbox presence/achievement events are normalized and streamed through the overlay and persisted as notifications.
- Notifications & UI: The HUD shows latest notifications on the clock screen; a new Notifications page is available in the web UI to see and manage past events.
- IP display: Optionally show the device local IP on the main clock page.
- Security enhancements: Overlay uses token-based authentication and local-only checks; webhooks can use service-specific tokens.

## 🔔 Notifications UI

The web UI exposes a Notifications page that lists recent events received via overlay or webhooks. Access it here:

```
http://[raspberry-pi-ip]:5000/notifications/ui
```

From the Notifications page, you can clear or delete individual notifications, which are persisted in a SQLite database `neon_notifications.db` located in the application directory.

## ⚙️ Additional Notes

- To enable Xbox presence with Microsoft Graph API, add your Xbox client id and secret in `Advanced Configuration`, then use the new "Connect Xbox" flow to authorize and obtain tokens.

# Xbox Graph OAuth Quick Steps

1. Register an app in Microsoft Azure or the Microsoft Application Portal, request scopes: `offline_access`, `User.Read`, `Presence.Read`.
2. Enter the Client ID and Client Secret under Services -> Xbox in Advanced Configuration.
3. Click "Connect Xbox" to be redirected to the Microsoft OAuth consent screen and authorize the app.
4. After the callback, the access token and refresh token will be saved to `config.toml` and the launcher will poll Microsoft Graph for presence updates.
Note: You may need to expose the redirect URI in your Microsoft app registration (default is `http://127.0.0.1:5000/xbox_callback`).

- Overlay tokens and webhook tokens are sensitive; consider enabling token encryption at rest.

## 🔐 Overlay Token Encryption

Overlay tokens and webhook tokens are stored in `config.toml` by default. If you'd prefer to encrypt the overlay token at rest:

1. Open `Advanced Configuration` and check "Encrypt overlay token at rest".
2. Regenerate or set an overlay token. When encryption is enabled the launcher will store an encrypted token in `overlay.encrypted_token` and create a key file at `secrets/overlay_key.key` with restricted file permissions.
3. To rotate the encryption key, use the `Rotate Key` button next to the token controls (this will re-encrypt the token with a new key and return the plaintext token in the UI once so you can update places relying on the token).

Important: both the HUD and the neondisplay server must have permission to read the key file so that HUD can post overlay events locally; rotate keys carefully and keep backups if needed.

If you'd like, I can now implement the Xbox Microsoft Graph OAuth integration (OAuth + refresh handling), add a Notifications management UI backed by a DB, and add an overlay token encryption feature (Fernet-based) — tell me if you'd like me to proceed with those in that order.
