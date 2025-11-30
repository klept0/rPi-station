# Testing rPi-station (NeonDisplay)

This guide explains how to run basic unit tests and a simple end-to-end scenario for the NeonDisplay project.

## Prerequisites

- Python 3.10+ (or whichever Python version you use for the project)
- Virtualenv or venv
- Development dependencies installed:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

If you are enabling overlay token encryption tests or rotation, you must have `cryptography` installed (included in `requirements.txt`).

## Run Tests (pytest)

From the project root:

```bash
pytest -q
```

This will run the test suite included in `tests/`.

## End-to-End Scenario (Manual Steps)

1. Start the web UI (neondisplay):

```bash
python3 neondisplay.py
```

2. In the web UI (`http://[raspberry-pi-ip]:5000`), go to `Advanced Configuration` and do the following:
 - Set `Overlay` enabled and `Overlay token` (or click `Regenerate`).
 - Optionally enable `Encrypt overlay token at rest` and hit `Save`.

- (Optional) Wire Wyze or Konnected webhooks to `http://[device-ip]:5000/device_notify` with a configured `X-Webhook-Token`.

3. Start the HUD (HUD should auto-start if enabled):

```bash
python3 hud.py
```

4. Test overlay posting from the HUD or simulate posting from a local curl command:

```bash
# Replace <token> with your overlay token and <device-ip> with your neondisplay IP
curl -X POST http://127.0.0.1:5000/events -H "X-Overlay-Token: <token>" -H "Content-Type: application/json" -d '{"type":"test_event","source":"unit-test","payload":{"message":"hello overlay"}}'

# Confirm notification is present via the list endpoint
curl http://127.0.0.1:5000/notifications
```

5. To simulate a Wyze snapshot post (saved to `static/wyze_last.jpg`):

```bash
curl -X POST http://[device-ip]:5000/device_notify -H "X-Webhook-Token: <wyze_token>" -H "Content-Type: application/json" -d '{"source":"wyze","snapshot_url":"https://example.com/some-image.jpg","message":"test"}'
```

6. Use `Notifications` from the web UI to view and clear events.

## Notes & Troubleshooting

- Tests manipulate local config files and may create `config.toml` in the working directory during tests — run tests in a separate environment or a temporary directory if you want to avoid touching your real configuration.
- If you enable encryption for overlay tokens, ensure `secrets/overlay_key.key` is protected with correct permissions (the code attempts to set `0600`).
- For Graph API / Xbox integration, register a Microsoft application with redirect URI `http://127.0.0.1:5000/xbox_callback` (or configure a different redirect and set it in `Advanced Configuration`).

## Suggested CI steps (basic)

- Install Python & venv
- pip install -r requirements.txt
- pytest

Running the test suite in a clean environment will validate basic behavior and endpoints.
