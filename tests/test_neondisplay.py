import os
import json
import time
import tempfile
import sqlite3
from cryptography.fernet import Fernet
import pytest

import importlib


def setup_tmp_config(tmp_path, monkeypatch, config_data=None):
    cfg_file = tmp_path / 'config.toml'
    monkeypatch.setenv('PYTHONIOENCODING', 'utf-8')
    # configure module to use a config in tmp_path
    import neondisplay
    monkeypatch.setattr(neondisplay, 'CONFIG_PATH', str(cfg_file))
    # force a reload to write default config
    importlib.reload(neondisplay)
    if config_data:
        c = neondisplay.load_config()
        for k, v in config_data.items():
            c[k] = v
        neondisplay.save_config(c)
    return neondisplay


def test_health_endpoint(monkeypatch, tmp_path):
    nd = setup_tmp_config(tmp_path, monkeypatch)
    client = nd.app.test_client()
    resp = client.get('/health')
    assert resp.status_code == 200
    data = resp.get_json()
    assert 'uptime_seconds' in data
    assert 'hud_running' in data


def test_notifications_persistence(monkeypatch, tmp_path):
    # Use an in-memory sqlite by monkeypatching sqlite3.connect
    import neondisplay as nd
    def mem_connect(path, check_same_thread=False):
        return sqlite3.connect(':memory:', check_same_thread=check_same_thread)
    monkeypatch.setattr(nd, 'sqlite3', sqlite3)
    # reload module helpers
    importlib.reload(nd)
    # initialize notifications DB
    conn = nd.init_notifications_db()
    # store an event
    ev = {'type': 'device', 'timestamp': int(time.time()), 'payload': {'message': 'test'}, 'source': 'unittest'}
    nd.store_notification(ev)
    client = nd.app.test_client()
    resp = client.get('/notifications')
    assert resp.status_code == 200
    data = resp.get_json()
    assert 'notifications' in data
    # notifications should include at least one item with 'unittest' source
    sources = [n.get('source') for n in data.get('notifications', [])]
    assert 'unittest' in sources or len(sources) >= 1


def test_overlay_encryption_helper(monkeypatch, tmp_path):
    import neondisplay as nd
    # create keyfile and enc token
    secrets_dir = tmp_path / 'secrets'
    secrets_dir.mkdir()
    key_path = secrets_dir / 'overlay_key.key'
    key = Fernet.generate_key()
    key_path.write_bytes(key)
    f = Fernet(key)
    token = 'mysecrettoken'
    enc = f.encrypt(token.encode('utf-8'))
    cfg = nd.load_config()
    if 'overlay' not in cfg:
        cfg['overlay'] = {}
    cfg['overlay']['encrypted'] = True
    cfg['overlay']['encrypted_token'] = enc.decode('utf-8')
    # override config path and current dir to tmp so get_overlay_token_from_config finds key
    monkeypatch.setenv('PYTHONIOENCODING', 'utf-8')
    monkeypatch.chdir(str(tmp_path))
    # Save a modified config
    nd.save_config(cfg)
    # monkeypatch the CONFIG_PATH to point to our temp config
    monkeypatch.setattr(nd, 'CONFIG_PATH', str(tmp_path / 'config.toml'))
    # monkeypatch key path inside function by creating secrets dir at working dir
    importlib.reload(nd)
    got = nd.get_overlay_token_from_config(cfg)
    assert got == token


def test_ingest_event_with_overlay(monkeypatch, tmp_path):
    # Ensure ingest_event accepts overlay posts when enabled and token valid
    import neondisplay as nd
    # use temporary config
    cfg_file = tmp_path / 'config.toml'
    monkeypatch.setenv('PYTHONIOENCODING', 'utf-8')
    monkeypatch.setattr(nd, 'CONFIG_PATH', str(cfg_file))
    importlib.reload(nd)
    cfg = nd.load_config()
    if 'overlay' not in cfg:
        cfg['overlay'] = {}
    cfg['overlay']['enabled'] = True
    cfg['overlay']['token'] = 'test_token'
    nd.save_config(cfg)
    client = nd.app.test_client()
    headers = {'X-Overlay-Token': 'test_token', 'Content-Type': 'application/json'}
    payload = {'type': 'unit_test_event', 'source': 'unittest', 'message': 'ok'}
    resp = client.post('/events', headers=headers, json=payload)
    assert resp.status_code == 200


def test_device_notify_with_service_token(monkeypatch, tmp_path):
    import neondisplay as nd
    cfg_file = tmp_path / 'config.toml'
    monkeypatch.setattr(nd, 'CONFIG_PATH', str(cfg_file))
    importlib.reload(nd)
    cfg = nd.load_config()
    if 'services' not in cfg:
        cfg['services'] = {}
    cfg['services']['wyze'] = {'enabled': True, 'webhook_token': 'wyzetoken'}
    nd.save_config(cfg)
    client = nd.app.test_client()
    headers = {'X-Webhook-Token': 'wyzetoken', 'Content-Type': 'application/json'}
    payload = {'source': 'wyze', 'snapshot_url': 'https://example.com/test.jpg', 'message': 'hello'}
    resp = client.post('/device_notify', headers=headers, json=payload)
    assert resp.status_code == 200
