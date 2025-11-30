#!/usr/bin/env python3
from flask import Flask, request, redirect, url_for, flash, Response, render_template, send_file
from spotipy.oauth2 import SpotifyOAuth
from datetime import datetime
from collections import Counter
from functools import wraps
from logging.handlers import RotatingFileHandler
import os, toml, time, requests, subprocess, sys, signal, urllib.parse, socket, logging, threading, json, hashlib, spotipy, io, sqlite3, shutil
try:
    from cryptography.fernet import Fernet
    HAS_CRYPTO = True
except Exception:
    Fernet = None
    HAS_CRYPTO = False
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from collections import OrderedDict

app = Flask(__name__)
# event overlay support
recent_events = []
event_condition = threading.Condition()
LAUNCHER_START_TIME = time.time()
@app.after_request
def add_header(response):
    response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    return response
app.secret_key = 'hud-launcher-secret-key'

CONFIG_PATH = "config.toml"
DEFAULT_CONFIG = {
    "display": {
        "type": "framebuffer",
        "framebuffer": "/dev/fb1",
        "rotation": 0,
        "st7789": {
            "spi_port": 0,
            "spi_cs": 1,
            "dc_pin": 9,
            "backlight_pin": 13,
            "rotation": 0,
            "spi_speed": 60000000
        }
    },
    "fonts": {
        "large_font_path": "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "large_font_size": 36,
        "medium_font_path": "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "medium_font_size": 24,
        "small_font_path": "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "small_font_size": 16,
        "spot_large_font_path": "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "spot_large_font_size": 26,
        "spot_medium_font_path": "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "spot_medium_font_size": 18,
        "spot_small_font_path": "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "spot_small_font_size": 12
    },
    "api_keys": {
        "openweather": "",
        "google_geo": "",
        "client_id": "",
        "client_secret": "",
        "redirect_uri": "http://127.0.0.1:5000"
    },
    "lastfm": {
        "api_key": "",
        "api_secret": "",
        "username": "",
        "password": "",
        "enabled": False,
        "scrobble_threshold": 0.75,
        "min_scrobble_seconds": 30
    },
    "settings": {
        "framebuffer": "/dev/fb1",
        "start_screen": "weather",
        "fallback_city": "",
        "use_gpsd": True,
        "use_google_geo": True,
        "time_display": True,
        "progressbar_display": True,
        "enable_current_track_display": True
    },
    "services": {
        "wyze": {
            "enabled": False,
            "webhook_token": ""
        },
        "xbox": {
            "enabled": False,
            "client_id": "",
            "client_secret": "",
            "refresh_token": "",
            "poll_interval": 60
            ,"presence_url": ""
        },
        "konnected": {
            "enabled": False,
            "webhook_token": ""
        }
    },
    "ui": {
    "overlay": {
        "enabled": False,
        "token": "",
        "port": 5000,
        "events": {
            "scrobble": True,
            "track_change": True,
            "cover_fallback": False
            ,"device_notify": True
        }
    },
    # Control showing the Pi's IP on main screen
    "display_ip_on_main": True,
    "wifi": {
        "ap_ssid": "Neonwifi-Manager",
        "ap_ip": "192.168.42.1",
        "rescan_time": 600
    },
    "auto_start": {
        "auto_start_hud": True,
        "auto_start_neonwifi": True,
        "check_internet": True
    },
    "clock": {
        "type": "digital",
        "background": "color",
        "color": "black"
    },
    "buttons": {
        "button_a": 5,
        "button_b": 6,
        "button_x": 16,
        "button_y": 24
    },
    "logging": {
        "max_log_lines": 10000,
        "max_backup_files": 5
    },
    "ui": {
        "theme": "dark"
    }
}

hud_process = None
neonwifi_process = None
last_logged_song = None
session = requests.Session()
retries = Retry(total=3, backoff_factor=0.5, status_forcelist=[429, 500, 502, 503, 504])
adapter = HTTPAdapter(max_retries=retries, pool_connections=10, pool_maxsize=10)
session.mount('http://', adapter)
session.mount('https://', adapter)

# DB connection + lock
_db_conn = None
_db_lock = threading.Lock()

def init_notifications_db():
    global _db_conn
    if _db_conn is None:
        _db_conn = sqlite3.connect('neon_notifications.db', check_same_thread=False)
    conn = _db_conn
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS notifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_ts INTEGER,
            source TEXT,
            type TEXT,
            payload TEXT
        )
    ''')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_notifications_ts ON notifications(created_ts)')
    conn.commit()
    return conn


def store_notification(ev):
    try:
        conn = init_notifications_db()
        with _db_lock:
            cursor = conn.cursor()
            payload_json = json.dumps(ev.get('payload', {}))
            cursor.execute('INSERT INTO notifications (created_ts, source, type, payload) VALUES (?,?,?,?)', (int(ev.get('timestamp', int(time.time()))), ev.get('source', ''), ev.get('type', ''), payload_json))
            conn.commit()
    except Exception as e:
        logger = logging.getLogger('Launcher')
        logger.error(f"Failed to store notification: {e}")

def load_config():
    if not os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, 'w') as f:
            toml.dump(DEFAULT_CONFIG, f)
        return DEFAULT_CONFIG.copy()
    
    try:
        with open(CONFIG_PATH, 'r') as f:
            return toml.load(f)
    except Exception as e:
        print(f"Error loading config: {e}")
        print("Using default configuration")
        return DEFAULT_CONFIG.copy()

def save_config(config):
    with open(CONFIG_PATH, 'w') as f:
        toml.dump(config, f)

def setup_logging():
    logging.getLogger('werkzeug').setLevel(logging.WARNING)
    logger = logging.getLogger('Launcher')
    for handler in logger.handlers[:]:
        logger.removeHandler(handler)
    
    logger.setLevel(logging.INFO)
    formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)
    try:
        config = load_config()
        log_config = config.get("logging", {})
        max_lines = log_config.get("max_log_lines", 10000)
        max_bytes = max_lines * 100  
        backup_count = log_config.get("max_backup_files", 5)
        file_handler = RotatingFileHandler(
            'neondisplay.log', 
            maxBytes=max_bytes, 
            backupCount=backup_count
        )
        file_handler.setLevel(logging.INFO)
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
        if not os.path.exists('neondisplay.log') or os.path.getsize('neondisplay.log') == 0:
            with open('neondisplay.log', 'a') as f:
                f.write(f"Log file initialized at {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
                
    except Exception as e:
        print(f"Failed to setup file logging: {e}")
        
    return logger

def check_internet_connection(timeout=5):
    try:
        response = session.get("http://www.google.com", timeout=timeout)
        return response.status_code == 200
    except requests.RequestException:
        try:
            import socket
            socket.create_connection(("8.8.8.8", 53), timeout=timeout)
            return True
        except socket.error:
            return False

def wait_for_internet(timeout=60, check_interval=5):
    logger = logging.getLogger('Launcher')
    logger.info("🔍 Waiting for internet connection...")
    start_time = time.time()
    while time.time() - start_time < timeout:
        if check_internet_connection():
            logger.info("✅ Internet connection established")
            return True
        logger.info("⏳ No internet connection, waiting...")
        time.sleep(check_interval)
    logger.error("❌ Internet connection timeout")
    return False

def safe_check_spotify_auth():
    if not check_internet_connection(timeout=3):
        return False, "No internet connection"
    return check_spotify_auth()

def auto_launch_applications():
    logger = logging.getLogger('Launcher')
    config = load_config()
    auto_config = config.get("auto_start", {})
    logger.info("🔧 Auto-launching applications based on configuration...")
    if auto_config.get("check_internet", True):
        if not wait_for_internet(timeout=30):
            logger.warning("❌ No internet - starting neonwifi if enabled")
            if auto_config.get("auto_start_neonwifi", True):
                start_neonwifi()
            return
    if auto_config.get("auto_start_neonwifi", True):
        start_neonwifi()
    if auto_config.get("auto_start_hud", True):
        spotify_authenticated, _ = check_spotify_auth()
        config_ready = is_config_ready()
        if config_ready and spotify_authenticated:
            start_hud()
            logger.info("✅ HUD auto-started")
        else:
            logger.warning("⚠️ HUD not auto-started: configuration incomplete")

def is_config_ready():
    config = load_config()
    return all([
        config["api_keys"]["openweather"],
        config["api_keys"]["client_id"], 
        config["api_keys"]["client_secret"]
    ])

def check_spotify_auth():
    config = load_config()
    if not config["api_keys"]["client_id"] or not config["api_keys"]["client_secret"]:
        return False, "Missing client credentials"
    try:
        if not os.path.exists(".spotify_cache"):
            return False, "No cached token found"
        sp_oauth = SpotifyOAuth(
            client_id=config["api_keys"]["client_id"],
            client_secret=config["api_keys"]["client_secret"],
            redirect_uri=config["api_keys"]["redirect_uri"],
            scope="user-read-currently-playing user-modify-playback-state user-read-playback-state",
            cache_path=".spotify_cache"
        )
        token_info = sp_oauth.get_cached_token()
        if not token_info:
            return False, "No valid token found"
        if sp_oauth.is_token_expired(token_info):
            logger = logging.getLogger('Launcher')
            logger.info("Token expired, attempting refresh...")
            token_info = sp_oauth.refresh_access_token(token_info['refresh_token'])
            
            if not token_info:
                return False, "Token refresh failed"
        try:
            sp = spotipy.Spotify(auth=token_info['access_token'])
            current_user = sp.current_user()
            return True, f"Authenticated as {current_user.get('display_name', 'Unknown User')}"
        except Exception as e:
            logger = logging.getLogger('Launcher')
            logger.error(f"Token validation failed: {e}")
            return False, f"Token validation failed: {str(e)}"
    except Exception as e:
        logger = logging.getLogger('Launcher')
        logger.error(f"Error checking Spotify auth: {e}")
        return False, f"Authentication error: {str(e)}"

def get_spotify_client():
    config = load_config()
    if not config["api_keys"]["client_id"] or not config["api_keys"]["client_secret"]:
        return None, "Missing client credentials"
    try:
        sp_oauth = SpotifyOAuth(
            client_id=config["api_keys"]["client_id"],
            client_secret=config["api_keys"]["client_secret"],
            redirect_uri=config["api_keys"]["redirect_uri"],
            scope="user-read-currently-playing user-modify-playback-state user-read-playback-state",
            cache_path=".spotify_cache"
        )
        token_info = sp_oauth.get_cached_token()
        if not token_info:
            return None, "No valid token available"
        if sp_oauth.is_token_expired(token_info):
            logger = logging.getLogger('Launcher')
            logger.info("Refreshing expired token...")
            try:
                token_info = sp_oauth.refresh_access_token(token_info['refresh_token'])
                if not token_info:
                    return None, "Failed to refresh token"
            except Exception as e:
                logger.error(f"Token refresh error: {e}")
                return None, f"Token refresh failed: {str(e)}"
        sp = spotipy.Spotify(auth=token_info['access_token'])
        return sp, "Success"


    def get_overlay_token_from_config(cfg=None):
        """Return decrypted overlay token if config uses encryption, otherwise return plain token."""
        try:
            if cfg is None:
                cfg = load_config()
            overlay_cfg = cfg.get('overlay', {})
            if overlay_cfg.get('encrypted') and overlay_cfg.get('encrypted_token'):
                enc = overlay_cfg.get('encrypted_token')
                if not HAS_CRYPTO:
                    return None
                key_path = os.path.join('secrets', 'overlay_key.key')
                if not os.path.exists(key_path):
                    return None
                with open(key_path, 'rb') as kf:
                    k = kf.read()
                f = Fernet(k)
                try:
                    token = f.decrypt(enc.encode('utf-8')).decode('utf-8')
                    return token
                except Exception:
                    return None
            return overlay_cfg.get('token')
        except Exception:
            return None
    except Exception as e:
        logger = logging.getLogger('Launcher')
        logger.error(f"Error creating Spotify client: {e}")
        return None, f"Client creation failed: {str(e)}"

def is_hud_running():
    global hud_process
    current_time = time.time()
    if hasattr(is_hud_running, '_last_check') and current_time - is_hud_running._last_check < 2:
        return is_hud_running._cached_result
    if hud_process is not None:
        if hud_process.poll() is None:
            result = True
        else:
            hud_process = None
            result = False
    else:
        try:
            result = bool(subprocess.run(['pgrep', '-f', 'hud.py'], 
                            capture_output=True, text=True).stdout.strip())
        except Exception:
            result = False
    is_hud_running._cached_result = result
    is_hud_running._last_check = current_time
    return result

def is_neonwifi_running():
    global neonwifi_process
    if neonwifi_process is not None:
        if neonwifi_process.poll() is None:
            return True
        else:
            neonwifi_process = None
    try:
        result = subprocess.run(['pgrep', '-f', 'neonwifi.py'], 
                            capture_output=True, text=True)
        return bool(result.stdout.strip())
    except Exception:
        return False

def parse_song_from_log(log_line):
    if 'Now playing:' in log_line:
        try:
            if '🎵 Now playing:' in log_line:
                song_part = log_line.split('🎵 Now playing: ')[1].strip()
            else:
                song_part = log_line.split('Now playing: ')[1].strip()
            separators = [' -- ', ' - ', ' – ']
            artist_part = 'Unknown Artist'
            song = song_part
            for separator in separators:
                if separator in song_part:
                    artist_part, song = song_part.split(separator, 1)
                    break
            artists = []
            if artist_part != 'Unknown Artist':
                artists = [artist.strip() for artist in artist_part.split(',')]
                artists = [artist for artist in artists if artist]
            if not artists:
                artists = ['Unknown Artist']
                artist_part = 'Unknown Artist'
            return {
                'song': song.strip(),
                'artist': artist_part.strip(),
                'artists': artists,
                'full_track': f"{artist_part} -- {song}".strip()
            }
        except Exception as e:
            logger = logging.getLogger('Launcher')
            logger.error(f"Error parsing song from log: {e}")
            return None
    return None

def start_hud():
    global hud_process, last_logged_song
    logger = logging.getLogger('Launcher')
    if is_hud_running():
        return False, "HUD is already running"
    try:
        hud_process = subprocess.Popen(
            [sys.executable, 'hud.py'],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            universal_newlines=True
        )
        def log_hud_output():
            for line in iter(hud_process.stdout.readline, ''):
                if line.strip():
                    logger.info(f"[HUD] {line.strip()}")
                    song_info = parse_song_from_log(line)
                    if song_info:
                        update_song_count(song_info)
        def monitor_current_track_state():
            while hud_process and hud_process.poll() is None:
                log_current_track_state()
                time.sleep(1)
        output_thread = threading.Thread(target=log_hud_output)
        output_thread.daemon = True
        output_thread.start()
        track_monitor_thread = threading.Thread(target=monitor_current_track_state)
        track_monitor_thread.daemon = True
        track_monitor_thread.start()
        time.sleep(2)
        if hud_process.poll() is None:
            return True, "HUD started successfully"
        else:
            return False, "HUD failed to start (check neondisplay.log for details)"
    except Exception as e:
        logger.error(f"Error starting HUD: {str(e)}")
        return False, f"Error starting HUD: {str(e)}"

def stop_hud():
    global hud_process, last_logged_song
    logger = logging.getLogger('Launcher')
    if not is_hud_running():
        return False, "HUD is not running"
    try:
        logger.info("Stopping HUD...")
        hud_process.terminate()
        try:
            hud_process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            hud_process.kill()
            hud_process.wait()
        hud_process = None
        last_logged_song = None
        try:
            if os.path.exists('.current_track_state.toml'):
                with open('.current_track_state.toml', 'w') as f:
                    empty_state = {
                        'current_track': {
                            'title': 'No track playing',
                            'artists': '',
                            'album': '',
                            'current_position': 0,
                            'duration': 0,
                            'is_playing': False,
                            'timestamp': time.time()
                        }
                    }
                    toml.dump(empty_state, f)
                logger.info("Cleared current track state")
        except Exception as e:
            logger.error(f"Error clearing track state: {e}")
        
        logger.info("HUD stopped successfully")
        return True, "HUD stopped successfully"
    except Exception as e:
        logger.error(f"Error stopping HUD: {str(e)}")
        return False, f"Error stopping HUD: {str(e)}"

def start_neonwifi():
    global neonwifi_process
    logger = logging.getLogger('Launcher')
    if is_neonwifi_running():
        return False, "neonwifi is already running"
    try:
        neonwifi_process = subprocess.Popen(
            [sys.executable, 'neonwifi.py'],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            universal_newlines=True
        )
        def log_neonwifi_output():
            for line in iter(neonwifi_process.stdout.readline, ''):
                if line.strip():
                    logger.info(f"[neonwifi] {line.strip()}")
        output_thread = threading.Thread(target=log_neonwifi_output)
        output_thread.daemon = True
        output_thread.start()
        time.sleep(3)
        if neonwifi_process.poll() is None:
            return True, "neonwifi started successfully"
        else:
            return False, "neonwifi failed to start (check neondisplay.log for details)"
    except Exception as e:
        logger.error(f"Error starting neonwifi: {str(e)}")
        return False, f"Error starting neonwifi: {str(e)}"

def stop_neonwifi():
    global neonwifi_process
    logger = logging.getLogger('Launcher')
    if not is_neonwifi_running():
        return False, "neonwifi is not running"
    try:
        logger.info("Stopping neonwifi...")
        if neonwifi_process:
            neonwifi_process.terminate()
            try:
                neonwifi_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                neonwifi_process.kill()
                neonwifi_process.wait()
            neonwifi_process = None
        subprocess.run(['pkill', '-f', 'neonwifi.py'], check=False)
        time.sleep(2)
        logger.info("neonwifi stopped successfully")
        return True, "neonwifi stopped successfully"
    except Exception as e:
        logger.error(f"Error stopping neonwifi: {str(e)}")
        return False, f"Error stopping neonwifi: {str(e)}"


def search_lyrics_for_track(track_name, artist_name):
    try:
        api_url = "https://lrclib.net/api/search"
        params = {
            'track_name': track_name,
            'artist_name': artist_name
        }
        logger = logging.getLogger('Launcher')
        response = session.get(api_url, params=params, timeout=10)
        if response.status_code == 200:
            results = response.json()
            if results:
                first_result = results[0]
                lyrics_id = first_result.get('id')
                if lyrics_id:
                    lyrics_response = session.get(f"https://lrclib.net/api/get/{lyrics_id}", timeout=10)
                    if lyrics_response.status_code == 200:
                        lyrics_data = lyrics_response.json()
                        return {
                            'success': True,
                            'lyrics': lyrics_data.get('syncedLyrics', ''),
                            'plain_lyrics': lyrics_data.get('plainLyrics', ''),
                            'track_name': lyrics_data.get('trackName', track_name),
                            'artist_name': lyrics_data.get('artistName', artist_name),
                            'album_name': lyrics_data.get('albumName', ''),
                            'duration': lyrics_data.get('duration', 0)
                        }
            return {'success': False, 'error': 'No lyrics found for this track'}
        else:
            logger.error(f"LRCLib API error: {response.status_code}")
            return {'success': False, 'error': f'API returned status code {response.status_code}'}
    except requests.RequestException as e:
        logger = logging.getLogger('Launcher')
        logger.error(f"Lyrics search network error: {e}")
        return {'success': False, 'error': f'Network error: {str(e)}'}
    except Exception as e:
        logger = logging.getLogger('Launcher')
        logger.error(f"Lyrics search unexpected error: {e}")
        return {'success': False, 'error': f'Unexpected error: {str(e)}'}

@app.route('/status/hud')
def status_hud():
    return {'running': is_hud_running()}

@app.route('/status/neonwifi')
def status_neonwifi():
    return {'running': is_neonwifi_running()}


@app.route('/health')
def health():
    try:
        loadavg = os.getloadavg()
    except Exception:
        loadavg = (0, 0, 0)
        health_info = {
        'uptime_seconds': int(time.time() - LAUNCHER_START_TIME),
        'hud_running': is_hud_running(),
        'neonwifi_running': is_neonwifi_running(),
        'cpu_load': loadavg,
        'db_connected': _db_conn is not None if '_db_conn' in globals() else False
        }
        # Last.fm status
        try:
            cfg = load_config()
            lastfm_cfg = cfg.get('lastfm', {})
            health_info['lastfm_enabled'] = bool(lastfm_cfg.get('enabled', False))
            health_info['lastfm_configured'] = bool(lastfm_cfg.get('api_key') and lastfm_cfg.get('username'))
        except Exception:
            health_info['lastfm_enabled'] = False
            health_info['lastfm_configured'] = False
        try:
            overlay_cfg = cfg.get('overlay', {})
            health_info['overlay_enabled'] = bool(overlay_cfg.get('enabled', False))
            health_info['overlay_configured'] = bool(overlay_cfg.get('token'))
        except Exception:
            health_info['overlay_enabled'] = False
            health_info['overlay_configured'] = False
        return health_info

def rate_limit(min_interval=0.5):
    def decorator(f):
        last_called = [0.0]
        @wraps(f)
        def wrapped(*args, **kwargs):
            elapsed = time.time() - last_called[0]
            left_to_wait = min_interval - elapsed
            if left_to_wait > 0:
                time.sleep(left_to_wait)
            last_called[0] = time.time()
            return f(*args, **kwargs)
        return wrapped
    return decorator

@app.context_processor
def utility_processor():
    return dict(zip=zip)

@app.route('/')
def index():
    config = load_config()
    auto_config = config.get("auto_start", {})
    ui_config = config.get("ui", {"theme": "dark"}) 
    config_ready = is_config_ready()
    spotify_configured = bool(config["api_keys"]["client_id"] and config["api_keys"]["client_secret"])
    spotify_authenticated, _ = check_spotify_auth()
    hud_running = is_hud_running()
    neonwifi_running = is_neonwifi_running()
    enable_current_track = config["settings"].get("enable_current_track_display", True)
    return render_template(
        'setup.html', 
        config=config, 
        config_ready=config_ready,
        spotify_configured=spotify_configured,
        spotify_authenticated=spotify_authenticated,
        hud_running=hud_running,
        neonwifi_running=neonwifi_running,
        auto_config=auto_config,
        enable_current_track_display=enable_current_track,
        ui_config=ui_config
    )

@app.route('/toggle_theme', methods=['POST'])
def toggle_theme():
    config = load_config()
    new_theme = request.form.get('theme', 'dark')
    if 'ui' not in config:
        config['ui'] = {}
    config['ui']['theme'] = new_theme
    save_config(config)
    return redirect(url_for('index'))

@app.route('/toggle_themeac', methods=['POST'])
def toggle_themeac():
    config = load_config()
    new_theme = request.form.get('theme', 'dark')
    if 'ui' not in config:
        config['ui'] = {}
    config['ui']['theme'] = new_theme
    save_config(config)
    return redirect(url_for('advanced_config'))

@app.route('/save_all_config', methods=['POST'])
def save_all_config():
    config = load_config()
    auto_start_hud = 'auto_start_hud' in request.form
    auto_start_neonwifi = 'auto_start_neonwifi' in request.form
    config["auto_start"] = {
        "auto_start_hud": auto_start_hud,
        "auto_start_neonwifi": auto_start_neonwifi
    }
    save_config(config)
    if is_hud_running():
        stop_hud()
        time.sleep(3)
    if auto_start_hud == True:
        start_hud()
    if is_neonwifi_running():
        stop_neonwifi()
        time.sleep(3)
    if auto_start_neonwifi == True:
        start_neonwifi()
    flash('success', 'All settings saved successfully!')
    return redirect(url_for('index'))

@app.route('/start_hud', methods=['POST'])
def start_hud_route():
    success, message = start_hud()
    if success:
        flash('success', message)
    else:
        flash('error', message)
    return redirect(url_for('index'))

@app.route('/stop_hud', methods=['POST'])
def stop_hud_route():
    success, message = stop_hud()
    if success:
        flash('success', message)
    else:
        flash('error', message)
    return redirect(url_for('index'))

@app.route('/start_neonwifi', methods=['POST'])
def start_neonwifi_route():
    success, message = start_neonwifi()
    if success:
        flash('success', message)
    else:
        flash('error', message)
    return redirect(url_for('index'))

@app.route('/stop_neonwifi', methods=['POST'])
def stop_neonwifi_route():
    success, message = stop_neonwifi()
    if success:
        flash('success', message)
    else:
        flash('error', message)
    return redirect(url_for('index'))

@app.route('/spotify_auth')
def spotify_auth_page():
    config = load_config()
    if not config["api_keys"]["client_id"] or not config["api_keys"]["client_secret"]:
        flash('error', 'Please save Spotify Client ID and Secret first.')
        return redirect(url_for('index'))
    try:
        sp_oauth = SpotifyOAuth(
            client_id=config["api_keys"]["client_id"],
            client_secret=config["api_keys"]["client_secret"],
            redirect_uri=config["api_keys"]["redirect_uri"],
            scope="user-read-currently-playing user-modify-playback-state user-read-playback-state",
            cache_path=".spotify_cache",
            show_dialog=True
        )
        auth_url = sp_oauth.get_authorize_url()
        return render_template('spotify_auth.html', auth_url=auth_url)
    except Exception as e:
        flash('error', f'Spotify authentication error: {str(e)}')
        return redirect(url_for('index'))

@app.route('/process_callback_url', methods=['POST'])
def process_callback_url():
    config = load_config()
    callback_url = request.form.get('callback_url', '').strip()
    if not callback_url:
        flash('error', 'Please paste the callback URL')
        return redirect(url_for('spotify_auth_page'))
    try:
        parsed_url = urllib.parse.urlparse(callback_url)
        query_params = urllib.parse.parse_qs(parsed_url.query)
        if 'error' in query_params:
            error = query_params['error'][0]
            flash('error', f'Spotify authentication failed: {error}')
            return redirect(url_for('index'))
        if 'code' not in query_params:
            flash('error', 'No authorization code found in the URL.')
            return redirect(url_for('spotify_auth_page'))
        code = query_params['code'][0]
        sp_oauth = SpotifyOAuth(
            client_id=config["api_keys"]["client_id"],
            client_secret=config["api_keys"]["client_secret"],
            redirect_uri=config["api_keys"]["redirect_uri"],
            scope="user-read-currently-playing user-modify-playback-state user-read-playback-state",
            cache_path=".spotify_cache"
        )
        token_info = sp_oauth.get_access_token(code)
        if token_info:
            flash('success', 'Spotify authentication successful!')
        else:
            flash('error', 'Spotify authentication failed.')
    except Exception as e:
        flash('error', f'Authentication error: {str(e)}')
        if os.path.exists(".spotify_cache"):
            os.remove(".spotify_cache")
    return redirect(url_for('index'))

@app.route('/view_logs')
def view_logs():
    lines = request.args.get('lines', 100, type=int)
    live = request.args.get('live', False, type=bool)
    config = load_config()
    ui_config = config.get("ui", {"theme": "dark"})
    current_theme = ui_config.get("theme", "dark")
    log_file = 'neondisplay.log'
    if not os.path.exists(log_file):
        log_content = "Log file does not exist. It will be created when there are log messages.\n\n"
        log_content += f"Log file path: {os.path.abspath(log_file)}"
        if live:
            return log_content
        return render_template('logs.html',
            log_content=log_content,
            lines=lines,
            current_theme=current_theme
        )
    try:
        with open(log_file, 'r') as f:
            all_lines = f.readlines()
            recent_lines = all_lines[-lines:] if lines > 0 else all_lines
            log_content = ''.join(recent_lines)
        if not log_content.strip():
            log_content = "Log file exists but is empty. No log messages yet."
    except Exception as e:
        log_content = f"Error reading log file: {str(e)}\n\n"
        log_content += f"Log file path: {os.path.abspath(log_file)}"
    if live:
        return log_content
    return render_template('logs.html',
        log_content=log_content,
        lines=lines,
        current_theme=current_theme
    )

@app.route('/clear_logs', methods=['POST'])
def clear_logs():
    log_file = 'neondisplay.log'
    try:
        backup_folder = "backuplogs"
        clear_option = request.form.get('clear_option', 'current')
        if clear_option == 'all':
            backups_cleared = 0
            for i in range(1, 100):
                backup_file = os.path.join(backup_folder, f"neondisplay.log.{i}")
                if os.path.exists(backup_file):
                    os.remove(backup_file)
                    backups_cleared += 1
                else:
                    break
            message = f'Backup logs cleared ({backups_cleared} files removed)'
        else:
            with open(log_file, 'w') as f:
                f.write(f"Logs cleared at {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
            message = 'Current log cleared'
        return message, 200
    except Exception as e:
        return f'Error clearing logs: {str(e)}', 500

@app.route('/music_stats_data')
def music_stats_data():
    try:
        lines = int(request.args.get('lines', 1000))
    except:
        lines = 1000
    song_stats, artist_stats, total_plays, unique_songs, unique_artists = generate_music_stats(lines)
    song_chart_data = generate_chart_data(song_stats, 'Songs')
    artist_chart_data = generate_chart_data(artist_stats, 'Artists')
    song_chart_items = list(zip(song_chart_data['labels'], song_chart_data['data'], song_chart_data['colors']))
    artist_chart_items = list(zip(artist_chart_data['labels'], artist_chart_data['data'], artist_chart_data['colors']))
    return {
        'song_chart_items': song_chart_items,
        'artist_chart_items': artist_chart_items,
        'total_plays': total_plays,
        'unique_songs': unique_songs,
        'unique_artists': unique_artists
    }

@app.route('/music_stats')
def music_stats():
    config = load_config()
    ui_config = config.get("ui", {"theme": "dark"})
    try:
        lines = int(request.args.get('lines', 1000))
    except:
        lines = 1000
    song_stats, artist_stats, total_plays, unique_songs, unique_artists = generate_music_stats(lines)
    song_chart_data = generate_chart_data(song_stats, 'Songs')
    artist_chart_data = generate_chart_data(artist_stats, 'Artists')
    song_chart_items = list(zip(song_chart_data['labels'], song_chart_data['data'], song_chart_data['colors']))
    artist_chart_items = list(zip(artist_chart_data['labels'], artist_chart_data['data'], artist_chart_data['colors']))
    return render_template('music_stats.html', 
                        song_chart_items=song_chart_items,
                        artist_chart_items=artist_chart_items,
                        lines=lines,
                        total_plays=total_plays,
                        unique_songs=unique_songs,
                        unique_artists=unique_artists,
                        ui_config=ui_config)

@app.route('/stream/current_track')
def stream_current_track():
    def generate():
        last_data = None
        update_counter = 0
        while True:
            current_track = get_current_track()
            track_data = {
                'song': current_track['song'],
                'artist': current_track['artist'],
                'album': current_track['album'],
                'progress': current_track['progress'],
                'duration': current_track['duration'],
                'is_playing': current_track['is_playing'],
                'has_track': current_track['has_track'],
                'timestamp': datetime.now().isoformat()
            }
            update_counter += 1
            if track_data != last_data or update_counter >= 3:
                if track_data != last_data:
                    yield f"data: {json.dumps(track_data)}\n\n"
                    last_data = track_data
                update_counter = 0
            time.sleep(0.5)
    return Response(generate(), mimetype='text/event-stream')

@app.route('/api/current_track')
def api_current_track():
    current_track = get_current_track()
    return {
        'track': current_track,
        'timestamp': datetime.now().isoformat()
    }

@app.route('/current_album_art')
def current_album_art():
    try:
        art_path = 'static/current_album_art.jpg'
        if os.path.exists(art_path):
            return send_file(art_path, mimetype='image/jpeg')
        else:
            from PIL import Image, ImageDraw
            img = Image.new('RGB', (300, 300), color=(40, 40, 60))
            draw = ImageDraw.Draw(img)
            draw.rectangle([10, 10, 290, 290], outline=(100, 100, 150), width=3)
            draw.text((150, 120), "🎵", fill=(200, 200, 220), anchor="mm")
            draw.text((150, 180), "No Album Art", fill=(150, 150, 170), anchor="mm")
            img_io = io.BytesIO()
            img.save(img_io, 'JPEG', quality=85)
            img_io.seek(0)
            return send_file(img_io, mimetype='image/jpeg')
    except Exception as e:
        logger = logging.getLogger('Launcher')
        logger.error(f"Error serving album art: {e}")
        try:
            error_img = Image.new('RGB', (300, 300), color=(60, 40, 40))
            draw = ImageDraw.Draw(error_img)
            draw.text((150, 150), "❌ Error", fill=(220, 150, 150), anchor="mm")
            
            img_io = io.BytesIO()
            error_img.save(img_io, 'JPEG')
            img_io.seek(0)
            return send_file(img_io, mimetype='image/jpeg')
        except:
            return "Album art not available", 404

@app.route('/lyrics/search')
def search_lyrics():
    track_name = request.args.get('track_name', '').strip()
    artist_name = request.args.get('artist_name', '').strip()
    if not track_name or not artist_name:
        return {'success': False, 'error': 'Track name and artist name are required'}
    try:
        api_url = f"https://lrclib.net/api/search"
        params = {'track_name': track_name,'artist_name': artist_name}
        response = session.get(api_url, params=params, timeout=10)
        if response.status_code == 200:
            results = response.json()
            if results:
                first_result = results[0]
                lyrics_id = first_result.get('id')
                if lyrics_id:
                    lyrics_response = session.get(f"https://lrclib.net/api/get/{lyrics_id}", timeout=10)
                    if lyrics_response.status_code == 200:
                        lyrics_data = lyrics_response.json()
                        return {'success': True,'lyrics': lyrics_data.get('syncedLyrics', ''),'plain_lyrics': lyrics_data.get('plainLyrics', ''),'track_name': lyrics_data.get('trackName', track_name),'artist_name': lyrics_data.get('artistName', artist_name),'album_name': lyrics_data.get('albumName', ''),'duration': lyrics_data.get('duration', 0)}
            return {'success': False, 'error': 'No lyrics found for this track'}
        else:
            return {'success': False, 'error': f'API returned status code {response.status_code}'}
    except requests.RequestException as e:
        logger = logging.getLogger('Launcher')
        logger.error(f"Lyrics search error: {e}")
        return {'success': False, 'error': f'Network error: {str(e)}'}
    except Exception as e:
        logger = logging.getLogger('Launcher')
        logger.error(f"Lyrics search unexpected error: {e}")
        return {'success': False, 'error': f'Unexpected error: {str(e)}'}

@app.route('/lyrics/current')
def get_current_track_lyrics():
    try:
        current_track = get_current_track()
        if not current_track.get('has_track') or current_track.get('song') in ['No track playing', 'Error loading track']:
            return {'success': False, 'error': 'No track currently playing'}
        track_name = current_track['song']
        artist_name = current_track['artist']
        if '(' in artist_name:
            artist_name = artist_name.split('(')[0].strip()
        result = search_lyrics_for_track(track_name, artist_name)
        return result
    except Exception as e:
        logger = logging.getLogger('Launcher')
        logger.error(f"Current track lyrics error: {e}")
        return {'success': False, 'error': f'Error getting current track lyrics: {str(e)}'}

@app.route('/spotify_play', methods=['POST'])
@rate_limit(0.5)
def spotify_play():
    if not check_internet_connection(timeout=3):
        return {'success': False, 'error': 'No internet connection'}
    try:
        sp, message = get_spotify_client()
        if not sp:
            return {'success': False, 'error': message}
        sp.start_playback()
        return {'success': True, 'message': 'Playback started'}
    except Exception as e:
        logger = logging.getLogger('Launcher')
        logger.error(f"Play error: {str(e)}")
        return {'success': False, 'error': str(e)}

@app.route('/spotify_pause', methods=['POST'])
@rate_limit(0.5)
def spotify_pause():
    try:
        sp, message = get_spotify_client()
        if not sp:
            return {'success': False, 'error': message}
        sp.pause_playback()
        return {'success': True, 'message': 'Playback paused'}
    except Exception as e:
        logger = logging.getLogger('Launcher')
        logger.error(f"Pause error: {str(e)}")
        return {'success': False, 'error': str(e)}

@app.route('/spotify_next', methods=['POST'])
@rate_limit(0.5)
def spotify_next():
    if not check_internet_connection(timeout=3):
        return {'success': False, 'error': 'No internet connection'}
    try:
        sp, message = get_spotify_client()
        if not sp:
            return {'success': False, 'error': message}
        sp.next_track()
        return {'success': True, 'message': 'Skipped to next track'}
    except Exception as e:
        logger = logging.getLogger('Launcher')
        logger.error(f"Next track error: {str(e)}")
        return {'success': False, 'error': str(e)}

@app.route('/spotify_previous', methods=['POST'])
@rate_limit(0.5)
def spotify_previous():
    if not check_internet_connection(timeout=3):
        return {'success': False, 'error': 'No internet connection'}
    try:
        sp, message = get_spotify_client()
        if not sp:
            return {'success': False, 'error': message}
        sp.previous_track()
        return {'success': True, 'message': 'Went to previous track'}
    except Exception as e:
        logger = logging.getLogger('Launcher')
        logger.error(f"Previous track error: {str(e)}")
        return {'success': False, 'error': str(e)}

@app.route('/spotify_get_volume', methods=['GET'])
def spotify_get_volume():
    try:
        sp, message = get_spotify_client()
        if not sp:
            return {'success': False, 'error': message}
        if not check_internet_connection(timeout=3):
            return {'success': False, 'error': 'No internet connection'}
        playback = sp.current_playback()
        if playback and 'device' in playback:
            current_volume = playback['device'].get('volume_percent', 50)
            return {'success': True, 'volume': current_volume}
        else:
            return {'success': True, 'volume': 50}
    except Exception as e:
        logger = logging.getLogger('Launcher')
        logger.error(f"Get volume error: {str(e)}")
        return {'success': False, 'error': str(e)}

@app.route('/spotify_volume', methods=['POST'])
@rate_limit(0.5)
def spotify_volume():
    if not check_internet_connection(timeout=3):
        return {'success': False, 'error': 'No internet connection'}
    try:
        volume = request.json.get('volume', 50)
        volume = max(0, min(100, volume))
        sp, message = get_spotify_client()
        if not sp:
            return {'success': False, 'error': message}
        sp.volume(volume)
        return {'success': True, 'message': f'Volume set to {volume}%'}
    except Exception as e:
        logger = logging.getLogger('Launcher')
        logger.error(f"Volume set error: {str(e)}")
        return {'success': False, 'error': str(e)}

@app.route('/spotify_seek', methods=['POST'])
@rate_limit(0.5)
def spotify_seek():
    try:
        position_ms = request.json.get('position_ms', 0)
        sp, message = get_spotify_client()
        if not sp:
            return {'success': False, 'error': message}
        playback = sp.current_playback()
        if not playback or not playback.get('is_playing', False):
            return {'success': False, 'error': 'No active playback'}
        sp.seek_track(position_ms)
        return {'success': True, 'message': 'Playback position set'}
    except Exception as e:
        logger = logging.getLogger('Launcher')
        logger.error(f"Seek error: {str(e)}")
        return {'success': False, 'error': str(e)}

@app.route('/spotify_search', methods=['POST'])
@rate_limit(1.0)
def spotify_search():
    try:
        query = request.json.get('query', '').strip()
        if not query:
            return {'success': False, 'error': 'No search query provided'}
        sp, message = get_spotify_client()
        if not sp:
            return {'success': False, 'error': message}
        results = sp.search(q=query, type='track', limit=20)
        tracks = []
        for item in results['tracks']['items']:
            image_url = None
            if item['album']['images']:
                image_url = item['album']['images'][-1]['url'] if item['album']['images'] else None
            duration_ms = item['duration_ms']
            duration_min = duration_ms // 60000
            duration_sec = (duration_ms % 60000) // 1000
            duration_str = f"{duration_min}:{duration_sec:02d}"
            artists = ', '.join([artist['name'] for artist in item['artists']])
            tracks.append({
                'name': item['name'],
                'artists': artists,
                'album': item['album']['name'],
                'duration': duration_str,
                'uri': item['uri'],
                'image_url': image_url
            })
        return {'success': True, 'tracks': tracks}
    except Exception as e:
        logger = logging.getLogger('Launcher')
        logger.error(f"Spotify search error: {str(e)}")
        return {'success': False, 'error': str(e)}

@app.route('/spotify_add_to_queue', methods=['POST'])
@rate_limit(0.5)
def spotify_add_to_queue():
    try:
        track_uri = request.json.get('track_uri', '').strip()
        if not track_uri:
            return {'success': False, 'error': 'No track URI provided'}
        sp, message = get_spotify_client()
        if not sp:
            return {'success': False, 'error': message}
        sp.add_to_queue(track_uri)
        return {'success': True, 'message': 'Track added to queue'}
    except Exception as e:
        logger = logging.getLogger('Launcher')
        logger.error(f"Spotify add to queue error: {str(e)}")
        return {'success': False, 'error': str(e)}

@app.route('/spotify_get_queue', methods=['GET'])
def spotify_get_queue():
    try:
        sp, message = get_spotify_client()
        if not sp:
            return {'success': False, 'error': message}
        playback = sp.current_playback()
        queue = sp.queue()
        queue_tracks = []
        if playback and playback.get('item'):
            current_track = playback['item']
            artists = ', '.join([artist['name'] for artist in current_track['artists']])
            image_url = current_track['album']['images'][-1]['url'] if current_track['album']['images'] else None
            queue_tracks.append({
                'name': current_track['name'],
                'artists': artists,
                'album': current_track['album']['name'],
                'uri': current_track['uri'],
                'image_url': image_url,
                'is_current': True
            })
        if queue and queue.get('queue'):
            for track in queue['queue']:
                artists = ', '.join([artist['name'] for artist in track['artists']])
                image_url = track['album']['images'][-1]['url'] if track['album']['images'] else None
                queue_tracks.append({
                    'name': track['name'],
                    'artists': artists,
                    'album': track['album']['name'],
                    'uri': track['uri'],
                    'image_url': image_url,
                    'is_current': False
                })
        return {'success': True, 'queue': queue_tracks}
    except Exception as e:
        logger = logging.getLogger('Launcher')
        logger.error(f"Spotify get queue error: {str(e)}")
        return {'success': False, 'error': str(e)}

@app.route('/spotify_play_track', methods=['POST'])
@rate_limit(0.5)
def spotify_play_track():
    try:
        track_uri = request.json.get('track_uri', '').strip()
        if not track_uri:
            return {'success': False, 'error': 'No track URI provided'}
        sp, message = get_spotify_client()
        if not sp:
            return {'success': False, 'error': message}
        sp.start_playback(uris=[track_uri])
        return {'success': True, 'message': 'Track started'}
    except Exception as e:
        logger = logging.getLogger('Launcher')
        logger.error(f"Spotify play track error: {str(e)}")
        return {'success': False, 'error': str(e)}

@app.route('/search_results')
def search_results():
    config = load_config()
    ui_config = config.get("ui", {"theme": "dark"})
    query = request.args.get('query', '')
    tracks_json = request.args.get('tracks', '[]')
    try:
        tracks = json.loads(tracks_json)
    except:
        tracks = []
    return render_template('search_results.html', 
                        query=query, 
                        tracks=tracks,
                        ui_config=ui_config)

@app.route('/clear_song_logs', methods=['POST'])
def clear_song_logs():
    try:
        functions_with_conn = [update_song_count, load_song_counts, generate_music_stats]
        for func in functions_with_conn:
            if hasattr(func, 'db_conn'):
                cursor = func.db_conn.cursor()
                cursor.execute('DELETE FROM song_plays')
                cursor.execute('VACUUM')
                func.db_conn.commit()
        return 'Song logs cleared', 200
    except Exception as e:
        return f'Error clearing song logs: {str(e)}', 500

@app.route('/advanced_config')
def advanced_config():
    config = load_config()
    spotify_configured = bool(config["api_keys"]["client_id"] and config["api_keys"]["client_secret"])
    spotify_authenticated, _ = check_spotify_auth()
    ui_config = config.get("ui", {"theme": "dark"})
        lastfm_configured = bool(config.get('lastfm', {}).get('api_key') and config.get('lastfm', {}).get('username'))
        lastfm_enabled = bool(config.get('lastfm', {}).get('enabled', False))
        return render_template('advanced_config.html',
            config=config, 
            spotify_configured=spotify_configured,
            spotify_authenticated=spotify_authenticated,
            lastfm_configured=lastfm_configured,
            lastfm_enabled=lastfm_enabled,
            ui_config=ui_config
        )


    @app.route('/regenerate_overlay_token', methods=['POST'])
    def regenerate_overlay_token():
        """Generate or rotate shared overlay token.
        Returns JSON with new token or error.
        """
        try:
            cfg = load_config()
            if 'overlay' not in cfg:
                cfg['overlay'] = {}
            import secrets
            new_token = secrets.token_hex(16)
            # If overlay token encryption is enabled, encrypt it and store 'encrypted_token' instead
            encrypt_tokens = cfg.get('overlay', {}).get('encrypted', False)
            if encrypt_tokens and HAS_CRYPTO:
                key_path = os.path.join('secrets', 'overlay_key.key')
                os.makedirs('secrets', exist_ok=True)
                if not os.path.exists(key_path):
                    k = Fernet.generate_key()
                    with open(key_path, 'wb') as kf:
                        kf.write(k)
                    os.chmod(key_path, 0o600)
                else:
                    with open(key_path, 'rb') as kf:
                        k = kf.read()
                f = Fernet(k)
                enc = f.encrypt(new_token.encode('utf-8'))
                cfg['overlay']['encrypted_token'] = enc.decode('utf-8')
                # keep token blank to avoid accidental exposure
                cfg['overlay']['token'] = ''
            else:
                cfg['overlay']['token'] = new_token
            # Persist
            save_config(cfg)
            return {'token': new_token}, 200
        except Exception as e:
            logger = logging.getLogger('Launcher')
            logger.error(f"Failed to regenerate overlay token: {e}")
            return {'error': 'failed'}, 500


    @app.route('/rotate_overlay_key', methods=['POST'])
    def rotate_overlay_key():
        """Rotate the overlay encryption key and re-encrypt stored overlay token.
        This will generate a new key file and re-encrypt the token into `overlay.encrypted_token`.
        Returns the new token plaintext in response if available.
        """
        try:
            cfg = load_config()
            overlay_cfg = cfg.get('overlay', {})
            # get plain token (if encrypted, decrypt)
            token_plain = get_overlay_token_from_config(cfg)
            if not token_plain:
                # if no token is set, regenerate one
                import secrets
                token_plain = secrets.token_hex(16)
            if not HAS_CRYPTO:
                return {'error': 'cryptography not installed'}, 500
            key_path = os.path.join('secrets', 'overlay_key.key')
            os.makedirs('secrets', exist_ok=True)
            new_k = Fernet.generate_key()
            with open(key_path, 'wb') as kf:
                kf.write(new_k)
            os.chmod(key_path, 0o600)
            f = Fernet(new_k)
            enc = f.encrypt(token_plain.encode('utf-8'))
            overlay_cfg['encrypted_token'] = enc.decode('utf-8')
            overlay_cfg['token'] = ''
            overlay_cfg['encrypted'] = True
            cfg['overlay'] = overlay_cfg
            save_config(cfg)
            return {'token': token_plain}, 200
        except Exception as e:
            logger = logging.getLogger('Launcher')
            logger.error(f"Failed to rotate overlay key: {e}")
            return {'error': 'failed'}, 500


    @app.route('/events', methods=['POST'])
    def ingest_event():
        """Endpoint for HUD (local) to POST events for streaming to overlay clients."""
        global recent_events, event_condition
        # If overlay is enabled, require a shared token to accept posts
        cfg_local = load_config()
        overlay_cfg = cfg_local.get('overlay', {})
        if not overlay_cfg.get('enabled', False):
            return 'Overlay disabled', 403
        # overlay enabled, require token
        if overlay_cfg.get('enabled', False):
            expected_token = get_overlay_token_from_config(cfg_local)
            if not expected_token:
                # overlay enabled but no token configured; reject
                return 'Overlay not configured (no token)', 403
            header_token = request.headers.get('X-Overlay-Token')
            if header_token != expected_token:
                logger = logging.getLogger('Launcher')
                logger.warning('Overlay event rejected: invalid token')
                return 'Invalid token', 403
            # Require local requests (loopback)
            ip = request.remote_addr
            if ip not in ('127.0.0.1', '::1'):
                logger = logging.getLogger('Launcher')
                logger.warning(f'Overlay event rejected: non-local IP {ip}')
                return 'Invalid source', 403
        try:
            data = request.get_json(force=True)
            if not data or 'type' not in data:
                return 'Invalid payload', 400
            ev = {
                'type': data.get('type'),
                'timestamp': int(time.time()),
                'payload': data
            }
            # normalize common sources
            src = data.get('source') or data.get('service') or data.get('source_name')
            if src:
                ev['source'] = src
            # also store in notifications area for HUD to retrieve
            try:
                if 'notifications' not in globals():
                    globals()['notifications'] = []
                notifications = globals().get('notifications')
                notifications.append(ev)
                # cap 20 entries
                if len(notifications) > 20:
                    globals()['notifications'] = notifications[-20:]
            except Exception:
                pass
            with event_condition:
                recent_events.append(ev)
                # cap events
                if len(recent_events) > 30:
                    recent_events = recent_events[-30:]
                event_condition.notify_all()
            # log notification for HUD and store in notifications as well
            try:
                notifications = globals().get('notifications', [])
                notifications.append(ev)
                if len(notifications) > 30:
                    globals()['notifications'] = notifications[-30:]
            except Exception:
                pass
            # persist notifications
            try:
                store_notification(ev)
            except Exception:
                pass
            return 'ok', 200
        except Exception as e:
            logger = logging.getLogger('Launcher')
            logger.error(f"Error ingesting event: {e}")
            return 'error', 500


    def event_stream_generator():
        last_sent_index = 0
        while True:
            with event_condition:
                if len(recent_events) <= last_sent_index:
                    event_condition.wait(timeout=15)
                # send any new events
                while last_sent_index < len(recent_events):
                    ev = recent_events[last_sent_index]
                    last_sent_index += 1
                    yield f"data: {json.dumps(ev)}\n\n"


    @app.route('/event_stream')
    def event_stream():
        return Response(event_stream_generator(), mimetype='text/event-stream')


    @app.route('/notifications')
    def list_notifications():
        """Return a JSON list of recent notifications for HUD to fetch."""
        try:
            conn = init_notifications_db()
            with _db_lock:
                cursor = conn.cursor()
                cursor.execute('SELECT id, created_ts, source, type, payload FROM notifications ORDER BY created_ts DESC LIMIT 50')
                rows = cursor.fetchall()
            notifs = []
            for r in rows:
                try:
                    payload = json.loads(r[4]) if r[4] else {}
                except Exception:
                    payload = {}
                notifs.append({'id': r[0], 'timestamp': r[1], 'source': r[2], 'type': r[3], 'payload': payload})
            return {'notifications': notifs}
        except Exception:
            return {'notifications': []}


    @app.route('/notifications/clear', methods=['POST'])
    def clear_notifications():
        try:
            conn = init_notifications_db()
            with _db_lock:
                cursor = conn.cursor()
                cursor.execute('DELETE FROM notifications')
                conn.commit()
            globals()['notifications'] = []
            return {'success': True, 'message': 'Notifications cleared'}
        except Exception as e:
            logger = logging.getLogger('Launcher')
            logger.error(f"Failed to clear notifications: {e}")
            return {'success': False, 'error': str(e)}, 500


    @app.route('/notifications/<int:notif_id>', methods=['DELETE'])
    def delete_notification(notif_id):
        try:
            conn = init_notifications_db()
            with _db_lock:
                cursor = conn.cursor()
                cursor.execute('DELETE FROM notifications WHERE id = ?', (notif_id,))
                conn.commit()
            return {'success': True}
        except Exception as e:
            logger = logging.getLogger('Launcher')
            logger.error(f"Failed to delete notification {notif_id}: {e}")
            return {'success': False, 'error': str(e)}, 500


    @app.route('/notifications/ui')
    def notifications_ui():
        try:
            conn = init_notifications_db()
            with _db_lock:
                cursor = conn.cursor()
                cursor.execute('SELECT id, created_ts, source, type, payload FROM notifications ORDER BY created_ts DESC LIMIT 200')
                rows = cursor.fetchall()
            items = []
            for r in rows:
                try:
                    payload = json.loads(r[4]) if r[4] else {}
                except Exception:
                    payload = {}
                items.append({'id': r[0], 'timestamp': r[1], 'source': r[2], 'type': r[3], 'payload': payload})
            ui_config = load_config().get('ui', {'theme': 'dark'})
            return render_template('notifications.html', notifications=items, ui_config=ui_config)
        except Exception as e:
            logger = logging.getLogger('Launcher')
            logger.error(f"Failed to render notifications UI: {e}")
            return render_template('notifications.html', notifications=[], ui_config={'theme': 'dark'})


def xbox_polling_loop():
    logger = logging.getLogger('Launcher')
    last_presence = None
    while True:
        try:
            cfg = load_config()
            xbox_cfg = cfg.get('services', {}).get('xbox', {})
            if not xbox_cfg.get('enabled', False):
                time.sleep(5)
                continue
            presence_url = xbox_cfg.get('presence_url')
            # If access token is present, prefer Microsoft Graph API instead of a presence URL
            access_token = xbox_cfg.get('access_token')
            refresh_token = xbox_cfg.get('refresh_token')
            interval = int(xbox_cfg.get('poll_interval', 60))
            if access_token:
                # Ensure we have a valid token - refresh if needed
                try:
                    token_expires_at = int(xbox_cfg.get('token_expires_at', 0))
                    now_ts = int(time.time())
                    if token_expires_at and now_ts + 30 >= token_expires_at:
                        # refresh
                        refreshed = refresh_xbox_token(xbox_cfg)
                        if refreshed:
                            xbox_cfg = load_config().get('services', {}).get('xbox', {})
                    headers = {'Authorization': f"Bearer {xbox_cfg.get('access_token')}"}
                    resp = session.get('https://graph.microsoft.com/v1.0/me/presence', headers=headers, timeout=10)
                    resp.raise_for_status()
                    data = resp.json()
                    presence = data.get('availability') or data.get('activity') or data
                    if presence and presence != last_presence:
                        ev = {'type': 'xbox_presence', 'timestamp': int(time.time()), 'payload': data, 'source': 'xbox'}
                        with event_condition:
                            recent_events.append(ev)
                            if len(recent_events) > 30:
                                recent_events = recent_events[-30:]
                            event_condition.notify_all()
                        notifs = globals().get('notifications', [])
                        notifs.append(ev)
                        globals()['notifications'] = notifs[-30:]
                        last_presence = presence
                except Exception as e:
                    logger.debug(f"Xbox Graph poll error: {e}")
            elif presence_url:
                try:
                    resp = session.get(presence_url, timeout=10)
                    resp.raise_for_status()
                    data = resp.json()
                    presence = data.get('presence') or data.get('state') or data
                    if presence and presence != last_presence:
                        ev = {'type': 'xbox_presence', 'timestamp': int(time.time()), 'payload': data, 'source': 'xbox'}
                        with event_condition:
                            recent_events.append(ev)
                            if len(recent_events) > 30:
                                recent_events = recent_events[-30:]
                            event_condition.notify_all()
                        notifs = globals().get('notifications', [])
                        notifs.append(ev)
                        globals()['notifications'] = notifs[-30:]
                        last_presence = presence
                except Exception as e:
                    logger.debug(f"Xbox poll error: {e}")
            time.sleep(interval)
        except Exception as e:
            logger.error(f"Xbox polling thread error: {e}")
            time.sleep(10)


def xbox_get_authorize_url():
    cfg = load_config()
    xbox_cfg = cfg.get('services', {}).get('xbox', {})
    client_id = xbox_cfg.get('client_id')
    redirect_uri = cfg.get('api_keys', {}).get('redirect_uri', 'http://127.0.0.1:5000')
    if not client_id:
        return None
    # Microsoft OAuth v2 endpoint
    scope = 'offline_access User.Read presence.Read'
    params = {
        'client_id': client_id,
        'response_type': 'code',
        'redirect_uri': redirect_uri,
        'response_mode': 'query',
        'scope': scope,
    }
    url = 'https://login.microsoftonline.com/common/oauth2/v2.0/authorize?' + urllib.parse.urlencode(params)
    return url


def exchange_xbox_code_for_tokens(code):
    cfg = load_config()
    xbox_cfg = cfg.get('services', {}).get('xbox', {})
    client_id = xbox_cfg.get('client_id')
    client_secret = xbox_cfg.get('client_secret')
    redirect_uri = cfg.get('api_keys', {}).get('redirect_uri', 'http://127.0.0.1:5000')
    if not (client_id and client_secret and code):
        return None
    token_url = 'https://login.microsoftonline.com/common/oauth2/v2.0/token'
    data = {
        'client_id': client_id,
        'client_secret': client_secret,
        'code': code,
        'redirect_uri': redirect_uri,
        'grant_type': 'authorization_code'
    }
    try:
        resp = session.post(token_url, data=data, timeout=10)
        resp.raise_for_status()
        token_data = resp.json()
        # token_data includes access_token, refresh_token, expires_in
        return token_data
    except Exception as e:
        logger = logging.getLogger('Launcher')
        logger.error(f"Xbox token exchange failed: {e}")
        return None


def refresh_xbox_token(xbox_cfg):
    try:
        client_id = xbox_cfg.get('client_id')
        client_secret = xbox_cfg.get('client_secret')
        refresh_token = xbox_cfg.get('refresh_token')
        if not (client_id and client_secret and refresh_token):
            return False
        token_url = 'https://login.microsoftonline.com/common/oauth2/v2.0/token'
        data = {
            'client_id': client_id,
            'client_secret': client_secret,
            'refresh_token': refresh_token,
            'grant_type': 'refresh_token'
        }
        resp = session.post(token_url, data=data, timeout=10)
        resp.raise_for_status()
        token_data = resp.json()
        cfg = load_config()
        if 'services' not in cfg:
            cfg['services'] = {}
        if 'xbox' not in cfg['services']:
            cfg['services']['xbox'] = {}
        cfg['services']['xbox']['access_token'] = token_data.get('access_token')
        cfg['services']['xbox']['refresh_token'] = token_data.get('refresh_token', refresh_token)
        expires = int(time.time()) + int(token_data.get('expires_in', 3600))
        cfg['services']['xbox']['token_expires_at'] = expires
        save_config(cfg)
        return True
    except Exception as e:
        logger = logging.getLogger('Launcher')
        logger.error(f"Xbox token refresh failed: {e}")
        return False


@app.route('/xbox_connect')
def xbox_connect():
    url = xbox_get_authorize_url()
    if not url:
        flash('error', 'Xbox client ID is not configured. Please set it in Advanced Configuration')
        return redirect(url_for('advanced_config'))
    return redirect(url)


@app.route('/xbox_callback')
def xbox_callback():
    try:
        code = request.args.get('code')
        error = request.args.get('error')
        if error:
            flash('error', f'Xbox auth failed: {error}')
            return redirect(url_for('advanced_config'))
        if not code:
            flash('error', 'No authorization code provided')
            return redirect(url_for('advanced_config'))
        token_data = exchange_xbox_code_for_tokens(code)
        if not token_data:
            flash('error', 'Token exchange failed')
            return redirect(url_for('advanced_config'))
        cfg = load_config()
        if 'services' not in cfg:
            cfg['services'] = {}
        if 'xbox' not in cfg['services']:
            cfg['services']['xbox'] = {}
        cfg['services']['xbox']['access_token'] = token_data.get('access_token')
        cfg['services']['xbox']['refresh_token'] = token_data.get('refresh_token')
        cfg['services']['xbox']['token_expires_at'] = int(time.time()) + int(token_data.get('expires_in', 3600))
        cfg['services']['xbox']['enabled'] = True
        save_config(cfg)
        flash('success', 'Xbox successfully connected and tokens saved')
    except Exception as e:
        logger = logging.getLogger('Launcher')
        logger.error(f"Xbox callback error: {e}")
        flash('error', 'Xbox callback handling failed')
    return redirect(url_for('advanced_config'))



    @app.route('/device_notify', methods=['POST'])
    def device_notify():
        """A dedicated endpoint for Wyze, Konnected, Xbox (or other) webhooks.
        The payload should contain a `source` or `service` field identifying the origin.
        Token checking and local-only checks apply similar to `/events`.
        """
        cfg = load_config()
        services_cfg = cfg.get('services', {})
        # check header token if provided for any service-specific webhook
        try:
            data = request.get_json(force=True)
        except Exception:
            return 'Invalid JSON', 400
        source = (data.get('source') or data.get('service') or data.get('source_name') or '').lower()
        # map source to service configs
        service_cfg = services_cfg.get(source, {}) if source else None
        if service_cfg:
            expected_token = service_cfg.get('webhook_token')
            if service_cfg.get('enabled', False):
                if expected_token:
                    header_token = request.headers.get('X-Webhook-Token')
                    if header_token != expected_token:
                        return 'Invalid token', 403
                # accept event and forward to event stream and notifications
                # Wyze-specific handling: optional snapshot url
                if source == 'wyze':
                    snapshot_url = data.get('snapshot_url') or data.get('snapshot') or data.get('image_url')
                    if snapshot_url:
                        try:
                            # try to fetch and save
                            headers = {'User-Agent': 'NeonDisplay/1.0'}
                            resp = session.get(snapshot_url, headers=headers, timeout=10)
                            resp.raise_for_status()
                            os.makedirs('static', exist_ok=True)
                            with open('static/wyze_last.jpg', 'wb') as f:
                                f.write(resp.content)
                            ev['snapshot'] = '/static/wyze_last.jpg'
                            # Attach image URL so overlay may show thumbnails
                        except Exception as e:
                            logger = logging.getLogger('Launcher')
                            logger.warning(f"Wyze snapshot fetch failed: {e}")
        else:
            # fallback to overlay token if configured
            overlay_cfg = cfg.get('overlay', {})
            if overlay_cfg.get('enabled'):
                expected_token = get_overlay_token_from_config(cfg)
                header_token = request.headers.get('X-Overlay-Token')
                if header_token != expected_token:
                    return 'Invalid token', 403
            else:
                # If no overlay and no service mapping, reject
                return 'No service configured', 403
        # now normalize and add to events
        ev = {'type': 'device_notify', 'timestamp': int(time.time()), 'payload': data}
        if source:
            ev['source'] = source
        # Konnected: parse sensors field into readable message
        if source == 'konnected':
            try:
                device = data.get('device') or data.get('sensor') or data.get('name')
                state = data.get('state') or data.get('value') or data.get('status')
                if device and state is not None:
                    ev['message'] = f"{device}: {state}"
            except Exception:
                pass
        # Xbox: if payload contains presence/achievement, transform to message
        if source == 'xbox':
            try:
                event_type = data.get('event') or data.get('type')
                if event_type:
                    if 'achievement' in str(event_type).lower():
                        ev['message'] = f"Xbox Achievement: {data.get('title', '')}"
                    elif 'presence' in str(event_type).lower():
                        # presence payload may have username and state
                        uname = data.get('user') or data.get('gamer') or data.get('xbox_name')
                        state = data.get('state') or data.get('presence')
                        if uname and state:
                            ev['message'] = f"Xbox {uname}: {state}"
            except Exception:
                pass
        # enqueue and notify
        with event_condition:
            # check overlay event type preferences
            overlay_cfg = cfg.get('overlay', {})
            overlay_events = overlay_cfg.get('events', {})
            if overlay_cfg.get('enabled', False) and not overlay_events.get('device_notify', True):
                # Device notifications disabled; quietly accept but don't post
                return 'ok', 200
            recent_events.append(ev)
            if len(recent_events) > 30:
                recent_events = recent_events[-30:]
            event_condition.notify_all()
        # also store in notifications
        try:
            notifs = globals().get('notifications', [])
            notifs.append(ev)
            globals()['notifications'] = notifs[-30:]
        except Exception:
            pass
        # persist notifications
        try:
            store_notification(ev)
        except Exception:
            pass
        except Exception:
            pass
        return 'ok', 200


    @app.route('/overlay')
    def overlay():
        return render_template('overlay.html')

@app.route('/save_advanced_config', methods=['POST'])
def save_advanced_config():
    config = load_config()
    try:
        config["api_keys"]["openweather"] = request.form.get('openweather', '').strip()
        config["api_keys"]["google_geo"] = request.form.get('google_geo', '').strip()
        config["api_keys"]["client_id"] = request.form.get('client_id', '').strip()
        config["api_keys"]["client_secret"] = request.form.get('client_secret', '').strip()
        config["api_keys"]["redirect_uri"] = request.form.get('redirect_uri', 'http://127.0.0.1:5000').strip()
        config["settings"]["fallback_city"] = request.form.get('fallback_city', '').strip()
        config["display"]["type"] = request.form.get('display_type', 'framebuffer')
        config["display"]["framebuffer"] = request.form.get('framebuffer_device', '/dev/fb1')
        config["display"]["rotation"] = int(request.form.get('rotation', 0))
        if "st7789" not in config["display"]:
            config["display"]["st7789"] = {}
        config["display"]["st7789"]["spi_port"] = int(request.form.get('spi_port', 0))
        config["display"]["st7789"]["spi_cs"] = int(request.form.get('spi_cs', 1))
        config["display"]["st7789"]["dc_pin"] = int(request.form.get('dc_pin', 9))
        config["display"]["st7789"]["backlight_pin"] = int(request.form.get('backlight_pin', 13))
        config["display"]["st7789"]["spi_speed"] = int(request.form.get('spi_speed', 60000000))
        config["fonts"]["large_font_path"] = request.form.get('large_font_path', '')
        config["fonts"]["large_font_size"] = int(request.form.get('large_font_size', 36))
        config["fonts"]["medium_font_path"] = request.form.get('medium_font_path', '')
        config["fonts"]["medium_font_size"] = int(request.form.get('medium_font_size', 24))
        config["fonts"]["small_font_path"] = request.form.get('small_font_path', '')
        config["fonts"]["small_font_size"] = int(request.form.get('small_font_size', 16))
        config["fonts"]["spot_large_font_path"] = request.form.get('spot_large_font_path', '')
        config["fonts"]["spot_large_font_size"] = int(request.form.get('spot_large_font_size', 26))
        config["fonts"]["spot_medium_font_path"] = request.form.get('spot_medium_font_path', '')
        config["fonts"]["spot_medium_font_size"] = int(request.form.get('spot_medium_font_size', 18))
        config["fonts"]["spot_small_font_path"] = request.form.get('spot_small_font_path', '')
        config["fonts"]["spot_small_font_size"] = int(request.form.get('spot_small_font_size', 12))
        if "buttons" not in config:
            config["buttons"] = {}
        config["buttons"]["button_a"] = int(request.form.get('button_a', 5))
        config["buttons"]["button_b"] = int(request.form.get('button_b', 6))
        config["buttons"]["button_x"] = int(request.form.get('button_x', 16))
        config["buttons"]["button_y"] = int(request.form.get('button_y', 24))
        config["wifi"]["ap_ssid"] = request.form.get('ap_ssid', 'Neonwifi-Manager')
        config["wifi"]["ap_ip"] = request.form.get('ap_ip', '192.168.42.1')
        config["wifi"]["rescan_time"] = int(request.form.get('rescan_time', 600))
        config["settings"]["progressbar_display"] = 'progressbar_display' in request.form
        config["settings"]["time_display"] = 'time_display' in request.form
        config["display_ip_on_main"] = 'display_ip_on_main' in request.form
        config["settings"]["start_screen"] = request.form.get('start_screen', 'weather')
        config["settings"]["use_gpsd"] = 'use_gpsd' in request.form
        config["settings"]["use_google_geo"] = 'use_google_geo' in request.form
        config["settings"]["enable_current_track_display"] = 'enable_current_track_display' in request.form
        if "logging" not in config:
            config["logging"] = {}
        config["logging"]["max_log_lines"] = int(request.form.get('max_log_lines', 10000))
        config["logging"]["max_backup_files"] = int(request.form.get('max_backup_files', 5))
        config["api_keys"]["redirect_uri"] = request.form.get('redirect_uri', 'http://127.0.0.1:5000')
        config["clock"]["background"] = request.form.get('clock_background', 'color')
        config["clock"]["color"] = request.form.get('clock_color', '#000000')
        config["clock"]["type"] = request.form.get('clock_type', 'digital')
        # Last.fm settings
        if 'lastfm' not in config:
            config['lastfm'] = {}
        config['lastfm']['enabled'] = 'lastfm_enabled' in request.form
        config['lastfm']['api_key'] = request.form.get('lastfm_api_key', '').strip()
        config['lastfm']['api_secret'] = request.form.get('lastfm_api_secret', '').strip()
        config['lastfm']['username'] = request.form.get('lastfm_username', '').strip()
        config['lastfm']['password'] = request.form.get('lastfm_password', '').strip()
        try:
            config['lastfm']['scrobble_threshold'] = float(request.form.get('lastfm_threshold', 0.75))
        except Exception:
            config['lastfm']['scrobble_threshold'] = 0.75
        try:
            config['lastfm']['min_scrobble_seconds'] = int(request.form.get('lastfm_min_seconds', 30))
        except Exception:
            config['lastfm']['min_scrobble_seconds'] = 30
        # Overlay settings
        if 'overlay' not in config:
            config['overlay'] = {}
        config['overlay']['enabled'] = 'overlay_enabled' in request.form
        # Token encryption toggle
        config['overlay']['encrypted'] = 'overlay_encrypt' in request.form
        given_token = request.form.get('overlay_token', '').strip()
        if given_token:
            config['overlay']['token'] = given_token
        else:
            # Regenerate token if overlay enabled and no token provided
            if config['overlay'].get('enabled') and not config['overlay'].get('token'):
                import secrets
                cfg_token = secrets.token_hex(16)
                config['overlay']['token'] = cfg_token
        # If token encryption is enabled, encrypt the token and store 'encrypted_token' instead
        if config['overlay'].get('encrypted', False) and HAS_CRYPTO:
            token_to_encrypt = config['overlay'].get('token')
            if token_to_encrypt:
                key_path = os.path.join('secrets', 'overlay_key.key')
                os.makedirs('secrets', exist_ok=True)
                if not os.path.exists(key_path):
                    k = Fernet.generate_key()
                    with open(key_path, 'wb') as kf:
                        kf.write(k)
                    os.chmod(key_path, 0o600)
                else:
                    with open(key_path, 'rb') as kf:
                        k = kf.read()
                f = Fernet(k)
                enc = f.encrypt(token_to_encrypt.encode('utf-8'))
                config['overlay']['encrypted_token'] = enc.decode('utf-8')
                # clear plaintext token to avoid accidental exposure
                config['overlay']['token'] = ''
        else:
            # if encryption disabled but encrypted token present, decrypt to plain token
            if not config['overlay'].get('encrypted', False) and config['overlay'].get('encrypted_token') and HAS_CRYPTO:
                try:
                    key_path = os.path.join('secrets', 'overlay_key.key')
                    if os.path.exists(key_path):
                        with open(key_path, 'rb') as kf:
                            k = kf.read()
                        f = Fernet(k)
                        dec = f.decrypt(config['overlay'].get('encrypted_token').encode('utf-8'))
                        config['overlay']['token'] = dec.decode('utf-8')
                        config['overlay']['encrypted_token'] = ''
                except Exception:
                    pass
        # overlay event toggles
        if 'events' not in config['overlay']:
            config['overlay']['events'] = {}
        config['overlay']['events']['scrobble'] = 'overlay_event_scrobble' in request.form
        config['overlay']['events']['track_change'] = 'overlay_event_track_change' in request.form
        config['overlay']['events']['cover_fallback'] = 'overlay_event_cover_fallback' in request.form
        config['overlay']['events']['device_notify'] = 'overlay_event_device_notify' in request.form
        # Services
        if 'services' not in config:
            config['services'] = {}
        if 'wyze' not in config['services']:
            config['services']['wyze'] = {}
        config['services']['wyze']['enabled'] = 'wyze_enabled' in request.form
        config['services']['wyze']['webhook_token'] = request.form.get('wyze_webhook_token', '').strip() or config['services']['wyze'].get('webhook_token', '')
        if 'xbox' not in config['services']:
            config['services']['xbox'] = {}
        config['services']['xbox']['enabled'] = 'xbox_enabled' in request.form
        config['services']['xbox']['client_id'] = request.form.get('xbox_client_id', '').strip() or config['services']['xbox'].get('client_id', '')
        config['services']['xbox']['client_secret'] = request.form.get('xbox_client_secret', '').strip() or config['services']['xbox'].get('client_secret', '')
        try:
            config['services']['xbox']['poll_interval'] = int(request.form.get('xbox_poll_interval', config['services']['xbox'].get('poll_interval', 60)))
        except Exception:
            config['services']['xbox']['poll_interval'] = config['services']['xbox'].get('poll_interval', 60)
        config['services']['xbox']['presence_url'] = request.form.get('xbox_presence_url', '').strip() or config['services']['xbox'].get('presence_url', '')
        if 'konnected' not in config['services']:
            config['services']['konnected'] = {}
        config['services']['konnected']['enabled'] = 'konnected_enabled' in request.form
        config['services']['konnected']['webhook_token'] = request.form.get('konnected_webhook_token', '').strip() or config['services']['konnected'].get('webhook_token', '')
        save_config(config)
        flash('success', 'Advanced configuration saved successfully!')
        def restart_process(process_name, stop_func, start_func, check_func):
            max_wait_time = 10
            wait_interval = 0.5
            if check_func():
                success, message = stop_func()
                if not success:
                    flash('warning', f'Warning stopping {process_name}: {message}')
            start_time = time.time()
            while check_func() and (time.time() - start_time) < max_wait_time:
                time.sleep(wait_interval)
            if check_func():
                flash('warning', f'{process_name} did not stop gracefully, forcing restart')
                if process_name.lower() == 'hud':
                    subprocess.run(['pkill', '-f', 'hud.py'], check=False)
                else:
                    subprocess.run(['pkill', '-f', 'neonwifi.py'], check=False)
                time.sleep(2)
            success, message = start_func()
            if not success:
                flash('error', f'Error starting {process_name}: {message}')
        was_hud_running = is_hud_running()
        if was_hud_running:
            restart_process('HUD', stop_hud, start_hud, is_hud_running)
        was_neonwifi_running = is_neonwifi_running()
        if was_neonwifi_running:
            restart_process('neonwifi', stop_neonwifi, start_neonwifi, is_neonwifi_running)
    except Exception as e:
        flash('error', f'Error saving configuration: {str(e)}')
    return redirect(url_for('advanced_config'))

@app.route('/reset_advanced_config', methods=['POST'])
def reset_advanced_config():
    config = load_config()
    config["display"] = DEFAULT_CONFIG["display"].copy()
    config["fonts"] = DEFAULT_CONFIG["fonts"].copy()
    config["api_keys"] = DEFAULT_CONFIG["api_keys"].copy()
    config["settings"] = DEFAULT_CONFIG["settings"].copy()
    config["wifi"] = DEFAULT_CONFIG["wifi"].copy()
    config["buttons"] = DEFAULT_CONFIG["buttons"].copy()
    config["logging"] = DEFAULT_CONFIG["logging"].copy()
    config["clock"] = DEFAULT_CONFIG["clock"].copy()
    preserved_ui = config.get("ui", {}).copy()
    preserved_auto_start = config.get("auto_start", {}).copy()
    config["ui"] = preserved_ui
    config["auto_start"] = preserved_auto_start
    save_config(config)
    flash('success', 'Advanced configuration reset to defaults!')
    return redirect(url_for('advanced_config'))

@app.route('/shutdown', methods=['POST'])
def shutdown():
    """Endpoint to gracefully shutdown the server"""
    logger = logging.getLogger('Launcher')
    logger.info("Shutting down server via API request")
    shutdown_func = request.environ.get('werkzeug.server.shutdown')
    if shutdown_func is None:
        raise RuntimeError('Not running with the Werkzeug Server')
    shutdown_func()
    return 'Server shutting down...'

def get_last_logged_song():
    if not os.path.exists('songs.toml'):
        return None
    try:
        with open('songs.toml', 'r') as f:
            content = f.read()
        data = toml.loads(content)
        plays = data.get('play', [])
        if not plays:
            return None
        last_play = plays[-1]
        return last_play.get('full_track')
    except Exception as e:
        logger = logging.getLogger('Launcher')
        logger.error(f"Error reading last logged song: {e}")
        return None

def ensure_log_file():
    log_file = 'neondisplay.log'
    try:
        if not os.path.exists(log_file):
            with open(log_file, 'w') as f:
                f.write(f"Log file created at {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
            return True
        with open(log_file, 'a') as f:
            f.write(f"Log check at {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
        return True
    except Exception as e:
        print(f"Log file error: {e}")
        return False

def log_current_track_state():
    global last_logged_song
    try:
        if not os.path.exists('.current_track_state.toml'):
            return
        state_data = toml.load('.current_track_state.toml')
        track_data = state_data.get('current_track', {})
        if not track_data.get('title') or track_data.get('title') in ['No track playing', 'Unknown Track']:
            return
        current_position = track_data.get('current_position', 0)
        duration = track_data.get('duration', 1)
        if duration > 0 and (current_position / duration) < 0.1:
            return        
        artists_data = track_data.get('artists', '')
        artists_list = []
        if isinstance(artists_data, list):
            artists_list = [str(artist).strip() for artist in artists_data if artist and str(artist).strip()]
        elif isinstance(artists_data, str) and artists_data.strip():
            artists_list = [artist.strip() for artist in artists_data.split(',') if artist.strip()]
        else:
            artists_list = ['Unknown Artist']
        if not artists_list:
            artists_list = ['Unknown Artist']
        artist_str = ', '.join(artists_list)
        current_song = f"{artist_str} -- {track_data.get('title', '')}".strip()
        if last_logged_song and current_song == last_logged_song:
            return
        song_info = {
            'song': track_data.get('title', ''),
            'artists': artists_list,
            'full_track': current_song
        }
        update_song_count(song_info)
    except Exception as e:
        logger = logging.getLogger('Launcher')
        logger.error(f"Error logging from current track state: {e}")

def init_song_database():
    global _db_conn
    if _db_conn is None:
        _db_conn = sqlite3.connect('song_stats.db', check_same_thread=False)
    conn = _db_conn
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS song_plays (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            song_hash TEXT UNIQUE,
            song_data TEXT,
            play_count INTEGER DEFAULT 0,
            last_played TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_count ON song_plays(play_count)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_hash ON song_plays(song_hash)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_last_played ON song_plays(last_played)')
    conn.commit()
    return conn

def backup_db_if_needed():
    try:
        backup_file = 'song_stats.db.backup'
        if not os.path.exists(backup_file) or \
            (time.time() - os.path.getmtime(backup_file)) > 1800:
            if os.path.exists('song_stats.db'):
                shutil.copy2('song_stats.db', backup_file)
                logger = logging.getLogger('Launcher')
                logger.info(f"✅ Database backed up to {backup_file}")
    except Exception as e:
        logger = logging.getLogger('Launcher')
        logger.error(f"❌ Database backup failed: {e}")

def update_song_count(song_info):
    global last_logged_song
    logger = logging.getLogger('Launcher')
    current_song = song_info.get('full_track', '').strip()
    if last_logged_song and current_song == last_logged_song:
        return
    if not hasattr(update_song_count, 'lock'):
        update_song_count.lock = threading.Lock()
    with update_song_count.lock:
        with _db_lock:
        try:
            conn = init_song_database()
            if not conn:
                return
            cursor = conn.cursor()
            if not song_info or not current_song:
                conn.close()
                return
            song_hash = hashlib.md5(current_song.encode('utf-8')).hexdigest()[:16]
            cursor.execute('''
                INSERT INTO song_plays (song_hash, song_data, play_count, last_played)
                VALUES (?, ?, 1, datetime('now'))
                ON CONFLICT(song_hash) DO UPDATE SET 
                play_count = play_count + 1,
                last_played = datetime('now')
            ''', (song_hash, current_song))
            conn.commit()
            backup_db_if_needed()
            last_logged_song = current_song
        except Exception as e:
            logger.error(f"Error updating song count: {e}")
            try:
                conn.close()
            except:
                pass

def get_current_track():
    try:
        if not is_hud_running():
            return {
                'song': 'No track playing',
                'artist': '',
                'album': '',
                'progress': '0:00',
                'duration': '0:00',
                'is_playing': False,
                'has_track': False
            }
        state_file = '.current_track_state.toml'
        if os.path.exists(state_file):
            state_data = toml.load(state_file)
            track_data = state_data.get('current_track', {})
            timestamp = track_data.get('timestamp', 0)
            if time.time() - timestamp < 60:
                progress_sec = track_data.get('current_position', 0)
                duration_sec = track_data.get('duration', 0)
                progress_min = progress_sec // 60
                progress_sec = progress_sec % 60
                duration_min = duration_sec // 60
                duration_sec = duration_sec % 60
                artists = track_data.get('artists', 'Unknown Artist')
                if isinstance(artists, list):
                    artists_str = ', '.join(artists)
                else:
                    artists_str = artists
                return {
                    'song': track_data.get('title', 'Unknown Track'),
                    'artist': artists_str,
                    'album': track_data.get('album', 'Unknown Album'),
                    'progress': f"{progress_min}:{progress_sec:02d}",
                    'duration': f"{duration_min}:{duration_sec:02d}",
                    'is_playing': track_data.get('is_playing', False),
                    'has_track': track_data.get('title') != 'No track playing'
                }
        return {
            'song': 'No track playing',
            'artist': '',
            'album': '',
            'progress': '0:00',
            'duration': '0:00',
            'is_playing': False,
            'has_track': False
        }
    except Exception as e:
        logger = logging.getLogger('Launcher')
        logger.error(f"Error getting current track: {e}")
        return {
            'song': 'Error loading track',
            'artist': '',
            'album': '',
            'progress': '0:00',
            'duration': '0:00',
            'is_playing': False,
            'has_track': False
        }

def load_song_counts():
    try:
        conn = init_song_database()
        with _db_lock:
            cursor = conn.cursor()
        cursor.execute('''
            SELECT song_data, play_count 
            FROM song_plays 
            ORDER BY play_count DESC, last_played DESC
            LIMIT 1000
        ''')
        result = dict(cursor.fetchall())
        # keep connection open
        return result
    except Exception as e:
        logger = logging.getLogger('Launcher')
        logger.error(f"Error loading song counts: {e}")
        try:
            # we don't close module connection here
            pass
        except:
            pass
        return {}

def generate_music_stats(max_items=1000):
    try:
        conn = init_song_database()
        with _db_lock:
            cursor = conn.cursor()
        cursor.execute('SELECT SUM(play_count), COUNT(*) FROM song_plays')
        total_plays, unique_songs = cursor.fetchone()
        total_plays = total_plays or 0
        cursor.execute('''
            SELECT song_data, play_count 
            FROM song_plays 
            ORDER BY play_count DESC, last_played DESC
            LIMIT ?
        ''', (max_items,))
        song_stats = {}
        for song_data, play_count in cursor.fetchall():
            song_stats[song_data] = play_count
        cursor.execute('SELECT song_data, play_count FROM song_plays')
        artist_stats = {}
        all_artists_set = set()
        for song_data, play_count in cursor.fetchall():
            if ' -- ' in song_data:
                artist_part = song_data.split(' -- ')[0].strip()
                artists = [artist.strip() for artist in artist_part.split(',')]
                for artist in artists:
                    if artist:
                        artist_stats[artist] = artist_stats.get(artist, 0) + play_count
                        all_artists_set.add(artist)
            else:
                unknown_artist = 'Unknown Artist'
                artist_stats[unknown_artist] = artist_stats.get(unknown_artist, 0) + play_count
                all_artists_set.add(unknown_artist)
        sorted_artists = sorted(artist_stats.items(), key=lambda x: x[1], reverse=True)
        artist_stats = dict(sorted_artists[:max_items])
        unique_artists = len(all_artists_set)
        # keep connection open
        return song_stats, artist_stats, total_plays, unique_songs, unique_artists
    except Exception as e:
        logger = logging.getLogger('Launcher')
        logger.error(f"Error generating music stats: {e}")
        try:
            # module-level conn is kept open
        except:
            pass
        return {}, {}, 0, 0, 0

def generate_chart_data(stats, label_type):
    if not stats:
        return {'labels': [], 'data': [], 'colors': []}
    labels = list(stats.keys())
    data = list(stats.values())
    colors = []
    for i in range(len(labels)):
        hue = (i * 137.5) % 360
        colors.append(f'hsl({hue}, 70%, 60%)')
    return {
        'labels': labels,
        'data': data,
        'colors': colors,
        'label_type': label_type
    }

def cleanup():
    logger = logging.getLogger('Launcher')
    global hud_process, neonwifi_process
    logger.info("🧹 Performing cleanup...")
    functions_with_conn = [update_song_count, load_song_counts, generate_music_stats]
    for func in functions_with_conn:
        # Close the module-level connection once
    try:
        global _db_conn
        if _db_conn:
            _db_conn.close()
            _db_conn = None
            logger.info("Closed song_stats.db connection")
    except Exception as e:
        logger.error(f"Error closing module DB connection: {e}")
    if hud_process and hud_process.poll() is None:
        logger.info("Stopping HUD process...")
        hud_process.terminate()
        try:
            hud_process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            logger.warning("HUD didn't terminate gracefully, killing...")
            hud_process.kill()
            hud_process.wait()
        hud_process = None
    if neonwifi_process and neonwifi_process.poll() is None:
        logger.info("Stopping neonwifi process...")
        neonwifi_process.terminate()
        try:
            neonwifi_process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            logger.warning("neonwifi didn't terminate gracefully, killing...")
            neonwifi_process.kill()
            neonwifi_process.wait()
        neonwifi_process = None
    subprocess.run(['pkill', '-f', 'hud.py'], check=False, timeout=5)
    subprocess.run(['pkill', '-f', 'neonwifi.py'], check=False, timeout=5)
    logger.info("Cleanup completed")

def signal_handler(sig, frame):
    logger = logging.getLogger('Launcher')
    logger.info("")
    logger.info("Shutting down launcher...")
    cleanup()
    os._exit(0)

def main():
    ensure_log_file()
    load_config()
    logger = setup_logging()
    logger.info("🚀 Starting HUD Launcher")
    def get_lan_ips():
        ips = []
        try:
            hostname = socket.gethostname()
            all_ips = socket.getaddrinfo(hostname, None)
            for addr_info in all_ips:
                ip = addr_info[4][0]
                if '.' in ip and not ip.startswith('127.'):
                    ips.append(ip)
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                    s.connect(("8.8.8.8", 80))
                    local_ip = s.getsockname()[0]
                    if local_ip not in ips and not local_ip.startswith('127.'):
                        ips.append(local_ip)
            except:
                pass
        except Exception as e:
            logger.warning(f"Could not determine LAN IP: {e}")
        return list(set(ips))
    lan_ips = get_lan_ips()
    auto_launch_applications()
    # Ensure the DB connection is initialized and registered with functions
    try:
        conn = init_song_database()
        update_song_count.db_conn = conn
        load_song_counts.db_conn = conn
        generate_music_stats.db_conn = conn
    except Exception:
        pass
    try:
        init_notifications_db()
    except Exception:
        pass
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    import logging as pylogging
    log = pylogging.getLogger('werkzeug')
    log.setLevel(pylogging.WARNING)
    app.config['SEND_FILE_MAX_AGE_DEFAULT'] = 0
    chosen_port = None
    ports_to_try = [5000, 5001]
    for port in ports_to_try:
        try:
            test_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            test_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            test_sock.bind(('0.0.0.0', port))
            test_sock.close()
            chosen_port = port
            break
        except OSError as e:
            if "Address already in use" in str(e) or "in use" in str(e).lower():
                logger.debug(f"Port {port} is busy, trying next...")
                continue
            else:
                logger.error(f"Socket error on port {port}: {e}")
                continue
    if chosen_port is None:
        logger.error("❌ Could not find an available port. All ports 5000-5003 are busy.")
        cleanup()
        return
    if lan_ips:
        for ip in lan_ips:
            logger.info(f"📍 Web UI available at: http://{ip}:{chosen_port}")
    else:
        logger.info(f"📍 Web UI available at: http://127.0.0.1:{chosen_port}")
    
    logger.info("⏹️  Press Ctrl+C to stop the launcher")
    try:
        app.run(host='0.0.0.0', port=chosen_port, debug=False, use_reloader=False)
    except Exception as e:
        logger.error(f"❌ Flask server crashed: {e}")
    finally:
        cleanup()

def start_background_threads():
    # start xbox polling
    try:
        cfg = load_config()
        xbox_cfg = cfg.get('services', {}).get('xbox', {})
        if xbox_cfg.get('enabled', False) and (xbox_cfg.get('presence_url') or xbox_cfg.get('access_token') or xbox_cfg.get('refresh_token')):
            Thread(target=xbox_polling_loop, daemon=True).start()
    except Exception:
        pass

if __name__ == '__main__':
    try:
        start_background_threads()
        main()
    except KeyboardInterrupt:
        signal_handler(signal.SIGINT, None)
    finally:
        cleanup()

## Entrypoint is handled above; no additional main() call here.