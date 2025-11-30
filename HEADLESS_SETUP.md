# Headless Setup (Raspberry Pi OS Lite)

This guide covers preparing a minimal Raspberry Pi OS Lite environment for `rPi-station` with no desktop environment.

## 1. Flash Image

1. Download latest Raspberry Pi OS Lite (64-bit if Pi 3 or newer).
2. Flash with Raspberry Pi Imager (or `rpi-imager`, `dd`).
3. (Optional) Preconfigure Wi‑Fi & SSH:
   - Create empty file `ssh` in boot partition to enable SSH.
   - Create `wpa_supplicant.conf` in boot partition if Wi‑Fi is needed:

     ```conf
     country=US
     ctrl_interface=DIR=/var/run/wpa_supplicant GROUP=netdev
     update_config=1
     network={
       ssid="YOUR_SSID"
       psk="YOUR_PASSPHRASE"
     }
     ```

## 2. First Boot

```bash
# SSH into the Pi
ssh pi@<pi-ip>
# Change default password if not already
passwd
```

## 3. Enable Required Interfaces

Use `raspi-config` OR edit `/boot/config.txt` directly.

```bash
sudo raspi-config nonint do_spi 0      # Enable SPI
sudo raspi-config nonint do_i2c 0      # (Optional) Enable I2C
sudo raspi-config nonint do_serial 1   # Disable serial login if using UART
sudo raspi-config nonint do_boot_behaviour B1  # Console autologin (optional)
```

Manual alternative (append to `/boot/config.txt`):

```bash
sudo bash -c 'echo "dtparam=spi=on" >> /boot/config.txt'
sudo bash -c 'echo "dtparam=i2c_arm=on" >> /boot/config.txt'  # if needed
```

Reboot:

```bash
sudo reboot
```

## 4. Pre-Flight System Packages

```bash
sudo apt update
sudo apt install -y git python3-pip python3-venv curl hostapd dnsmasq libjpeg-dev \
  zlib1g-dev libpng-dev libfreetype6-dev liblcms2-dev libwebp-dev libtiff-dev \
  libopenjp2-7-dev libxcb1-dev libopenblas-dev libcairo2-dev libdbus-1-dev build-essential cmake
```

## 5. Clone & Install Project

```bash
git clone https://github.com/klept0/rPi-station.git
cd rPi-station
sudo make system-deps    # Will skip apt if already run; ensures uv
make python-packages     # Creates /opt/neondisplay/venv & installs deps
make config              # Interactive configuration
```

## 6. Display Driver (If Using Vendor LCD)

Installs vendor script & reboots:

```bash
sudo make setup-display   # WARNING: reboots
```

After reboot, continue:

```bash
sudo make setup-service   # systemd service
make sync-code            # sync latest source
```

## 7. Optional: DMA fbcp Driver (Improves SPI Tearing)

```bash
sudo make setup-fbcp
# Service fbcp-ili9341 should start; verify:
systemctl status fbcp-ili9341.service
```

## 8. Spotify Authentication (Headless)

From another machine on the network:

1. Open `http://<pi-ip>:5000`
2. Provide Spotify credentials in Advanced Config
3. Perform auth flow; tokens persist in `config.toml`

If you must authenticate via SSH only, temporarily port-forward or copy/paste redirect URL into terminal when prompted by interactive flow.

## 9. Overlay & Token Security

Enable encryption & HMAC in Advanced Config:

- Set overlay token
- (Optional) Enable encryption (creates `secrets/overlay_key.key`)
- For remote deployments prefer environment variable key source:

  ```bash
  export OVERLAY_SECRET_KEY=$(python - <<'PY'

from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())
PY)
  sudo systemctl restart neondisplay.service

  ```

## 10. Performance Tuning
- Lower tearing: ensure `display.st7789.spi_speed = 32000000`
- Adjust frame rate: `settings.max_fps` (default 25; reduce if CPU high)
- Install fbcp driver for smoother SPI double buffering

## 11. Basic Service Ops
```bash
make status
make logs
make tail
sudo systemctl restart neondisplay.service
```

## 12. Backup & Restore Config

Config file path: `/opt/neondisplay/config.toml`

```bash
sudo cp /opt/neondisplay/config.toml ~/config.toml.backup
# Restore
sudo cp ~/config.toml.backup /opt/neondisplay/config.toml && sudo systemctl restart neondisplay.service
```

## 13. Adding Tests Placeholder

```bash
mkdir -p tests
printf 'def test_placeholder():\n    assert 2 + 2 == 4\n' > tests/test_smoke.py
pytest -q || echo "(No further tests yet)"
```

## 14. Common Issues

| Symptom | Cause | Resolution |
|---------|-------|------------|
| SPI display blank | SPI not enabled | Enable SPI & reboot |
| Frame tearing | Too high SPI speed / fps | Lower `spi_speed`, reduce `max_fps`, install fbcp |
| Spotify 403 | Bad redirect URI | Match app redirect exactly |
| GPS fails | No device / service | Disable GPSD or set fallback city |
| Overlay 401 | Bad token / HMAC | Rotate token, verify header |

## 15. Clean Uninstall

```bash
make clean
sudo systemctl disable fbcp-ili9341.service 2>/dev/null || true
sudo rm -f /etc/systemd/system/fbcp-ili9341.service
sudo systemctl daemon-reload
```

---
Headless mode keeps RAM & CPU usage low and avoids X11/Wayland overhead. Use Desktop image only if you need a local browser or graphical tooling.
