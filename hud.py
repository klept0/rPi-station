#!/usr/bin/env python3
import time, requests, json, evdev, spotipy, colorsys, datetime, os, subprocess, toml, random, sys, copy, math, queue, threading, signal, numpy as np, hashlib
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from collections import OrderedDict
from io import BytesIO
from PIL import Image, ImageDraw, ImageFont, ImageFilter, ImageEnhance, ImageStat, ImageColor
from threading import Thread, Event, RLock
# Try to detect pillow-simd availability for optimized image ops
try:
    import pkg_resources
    USE_PILLOW_SIMD = bool(pkg_resources.get_distribution('pillow-simd'))
except Exception:
    try:
        import PIL
        USE_PILLOW_SIMD = 'pillow-simd' in getattr(PIL, '__version__', '')
    except Exception:
        USE_PILLOW_SIMD = False
from concurrent.futures import ThreadPoolExecutor, ProcessPoolExecutor
try:
    import pylast
    HAS_PYLAST = True
except Exception:
    pylast = None
    HAS_PYLAST = False
from spotipy.oauth2 import SpotifyOAuth
try:
    import RPi.GPIO as GPIO
    HAS_GPIO = True
except ImportError:
    HAS_GPIO = False
try:
    from cryptography.fernet import Fernet
    HAS_CRYPTO = True
except Exception:
    Fernet = None
    HAS_CRYPTO = False
sys.stdout.reconfigure(line_buffering=True)

SCREEN_WIDTH = 480
SCREEN_HEIGHT = 320
UPDATE_INTERVAL_WEATHER = 3600
GEO_UPDATE_INTERVAL = 3600
SCOPE = "user-read-currently-playing user-modify-playback-state user-read-playback-state"
USE_GPSD = True
USE_GOOGLE_GEO = True
SCREEN_AREA = SCREEN_WIDTH * SCREEN_HEIGHT
BG_DIR = "./bg"
CLOCK_TYPE = "digital"
CLOCK_BACKGROUND = "color"
CLOCK_COLOR = "black"
INTERNET_CHECK_INTERVAL = 120
# Last.fm client instance
lfm = None
lastfm_scrobbled = {}

DEFAULT_CONFIG = {
    "display": {
        "type": "dummy",
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
        "process_pool_workers": None,
        "time_display": True,
        "sleep_timeout": 300,
        "progressbar_display": True,
        "enable_current_track_display": True
    },
    "wifi": {
        "ap_ssid": "Neonwifi-Manager",
        "ap_ip": "192.168.42.1",
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
    "ui": {
        "theme": "dark"
    }
}

bg_cache = {}
text_bbox_cache = {}
weather_cache = {}
# album_bg_cache will be an LRU cache (OrderedDict)
album_bg_cache = OrderedDict()
album_bg_cache_lock = RLock()
resized_image_cache = OrderedDict()
dithered_image_cache = OrderedDict()
dithered_cache_lock = RLock()
IMG_CACHE_MAX = 6
executor = ThreadPoolExecutor(max_workers=3)
process_executor = None  # initialized lazily to avoid forking in certain environments
pending_bg_futures = {}
pending_artist_futures = {}
pending_dither_futures = {}
pending_album_futures = {}
# global requests session with retries
session = requests.Session()
retries = Retry(total=3, backoff_factor=0.5, status_forcelist=[429, 500, 502, 503, 504])
adapter = HTTPAdapter(max_retries=retries, pool_connections=10, pool_maxsize=10)
session.mount("http://", adapter)
session.mount("https://", adapter)

# Debounced state writer queue
state_write_queue = queue.Queue(maxsize=1)
exit_event = Event()
art_lock = RLock()
artist_image_lock = RLock()
scroll_lock = RLock()
st7789_display = None
waveshare_epd = None
waveshare_base_image = None
partial_refresh_count = 0
epd2in13_V3 = None
epdconfig = None
bg_generation_queue = queue.Queue(maxsize=5)
spotify_bg_cache = None
spotify_bg_cache_lock = threading.Lock()
current_album_art_hash = None
current_clock_artwork = None
current_clock_artwork_hash = None
clock_bg_image = None
clock_bg_lock = threading.Lock()
button_last_press = {5: 0, 6: 0, 16: 0, 24: 0}
_gamma_r = np.array([int(((i / 255.0) ** (1 / 1.5)) * 31 + 0.5) for i in range(256)], dtype=np.uint8)
_gamma_g = np.array([int(((i / 255.0) ** (1 / 1.5)) * 63 + 0.5) for i in range(256)], dtype=np.uint8)
_gamma_b = np.array([int(((i / 255.0) ** (1 / 1.5)) * 31 + 0.5) for i in range(256)], dtype=np.uint8)
weather_info = None
spotify_track = None
sp = None
album_art_image = None
artist_image = None
scroll_state = {"title": {"offset": 0, "max_offset": 0, "active": False}, "artists": {"offset": 0, "max_offset": 0, "active": False}, "album": {"offset": 0, "max_offset": 0, "active": False}}
bg_map = {"Clear": "bg_clear.png", "Clouds": "bg_clouds.png", "Rain": "bg_rain.png", "Drizzle": "bg_drizzle.png", "Thunderstorm": "bg_storm.png", "Snow": "bg_snow.png", "Mist": "bg_mist.png", "Fog": "bg_fog.png", "Haze": "bg_haze.png", "Smoke": "bg_smoke.png", "Dust": "bg_dust.png", "Sand": "bg_sand.png", "Ash": "bg_ash.png", "Squall": "bg_squall.png", "Tornado": "bg_tornado.png"}
art_pos = [float(SCREEN_WIDTH - 155), float(SCREEN_HEIGHT - 155)]
artist_pos = [5, float(SCREEN_HEIGHT - 105)]
artist_velocity = [0.7, 0.7]
art_velocity = [1, 1]
artist_on_top = False
spotify_layout_cache = None
scrolling_text_cache = {}
last_display_time = 0
waveshare_lock = RLock()
file_write_lock = threading.Lock()
last_activity_time = time.time()
display_sleeping = False
last_saved_album_art_hash = None
internet_available = True
last_internet_check = 0
notifications = []

def load_config(path="config.toml"):
    if not os.path.exists(path):
        with open(path, 'w') as f:
            toml.dump(DEFAULT_CONFIG, f)
        return DEFAULT_CONFIG.copy()
    try:
        with open(path, 'r') as f:
            loaded_config = toml.load(f)
        merged_config = copy.deepcopy(DEFAULT_CONFIG)
        for category in loaded_config:
            if category in merged_config:
                if isinstance(merged_config[category], dict) and isinstance(loaded_config[category], dict):
                    for key in loaded_config[category]:
                        merged_config[category][key] = loaded_config[category][key]
                else:
                    merged_config[category] = loaded_config[category]
            else:
                merged_config[category] = loaded_config[category]
        with open(path, 'w') as f:
            toml.dump(merged_config, f)
        return merged_config
    except Exception as e:
        print(f"Error loading config: {e}, using defaults")
        with open(path, 'w') as f:
            toml.dump(DEFAULT_CONFIG, f)
        return DEFAULT_CONFIG.copy()

config = load_config()
if config["display"]["type"] == "waveshare_epd":
    try:
        from waveshare_epd.epd2in13_V3 import EPD
        HAS_WAVESHARE_EPD = True
    except ImportError:
        HAS_WAVESHARE_EPD = False
        EPD = None
HAS_ST7789 = False
if config["display"]["type"] == "st7789":
    try:
        import st7789
        HAS_ST7789 = True
    except ImportError:
        HAS_ST7789 = False
display_type = config.get("display", {}).get("type", "framebuffer")
if display_type == "st7789":
    ANIMATION_FPS = 30
    TEXT_SCROLL_FPS = 30
elif display_type == "waveshare_epd":
    ANIMATION_FPS = 2
    TEXT_SCROLL_FPS = 2
else:
    ANIMATION_FPS = 15
    TEXT_SCROLL_FPS = 15
ANIMATION_FRAME_TIME = 1.0 / ANIMATION_FPS
TEXT_SCROLL_FRAME_TIME = 1.0 / TEXT_SCROLL_FPS
DEFAULT_ANIMATION_FPS = ANIMATION_FPS
DEFAULT_TEXT_SCROLL_FPS = TEXT_SCROLL_FPS
def set_fps(anim_fps, scroll_fps):
    global ANIMATION_FPS, TEXT_SCROLL_FPS, ANIMATION_FRAME_TIME, TEXT_SCROLL_FRAME_TIME
    try:
        ANIMATION_FPS = max(1, int(anim_fps))
        TEXT_SCROLL_FPS = max(1, int(scroll_fps))
        ANIMATION_FRAME_TIME = 1.0 / ANIMATION_FPS
        TEXT_SCROLL_FRAME_TIME = 1.0 / TEXT_SCROLL_FPS
    except Exception:
        pass

def perf_monitor_loop():
    """Monitor CPU load and adjust frame rates to keep system responsive."""
    while not exit_event.is_set():
        try:
            cores = os.cpu_count() or 1
            try:
                loadavg = os.getloadavg()[0]
            except Exception:
                loadavg = 0
            ratio = loadavg / cores
            if ratio > 0.75:
                new_anim = max(5, DEFAULT_ANIMATION_FPS // 2)
                new_text = max(5, DEFAULT_TEXT_SCROLL_FPS // 2)
            else:
                new_anim = DEFAULT_ANIMATION_FPS
                new_text = DEFAULT_TEXT_SCROLL_FPS
            set_fps(new_anim, new_text)
        except Exception:
            pass
        if exit_event.wait(10):
            break
LARGE_FONT = ImageFont.truetype(config["fonts"]["large_font_path"], config["fonts"]["large_font_size"])
MEDIUM_FONT = ImageFont.truetype(config["fonts"]["medium_font_path"], config["fonts"]["medium_font_size"])
SMALL_FONT = ImageFont.truetype(config["fonts"]["small_font_path"], config["fonts"]["small_font_size"])
SPOT_LARGE_FONT = ImageFont.truetype(config["fonts"]["spot_large_font_path"], config["fonts"]["spot_large_font_size"])
SPOT_MEDIUM_FONT = ImageFont.truetype(config["fonts"]["spot_medium_font_path"], config["fonts"]["spot_medium_font_size"])
SPOT_SMALL_FONT = ImageFont.truetype(config["fonts"]["spot_small_font_path"], config["fonts"]["spot_small_font_size"])
OPENWEATHER_API_KEY = config["api_keys"]["openweather"]
GOOGLE_GEO_API_KEY = config["api_keys"]["google_geo"]
SPOTIFY_CLIENT_ID = config["api_keys"]["client_id"]
SPOTIFY_CLIENT_SECRET = config["api_keys"]["client_secret"]
REDIRECT_URI = config["api_keys"]["redirect_uri"]
START_SCREEN = config["settings"]["start_screen"]
FALLBACK_CITY = config["settings"]["fallback_city"]
USE_GPSD = config["settings"]["use_gpsd"]
USE_GOOGLE_GEO = config["settings"]["use_google_geo"]
TIME_DISPLAY = config["settings"]["time_display"]
SLEEP_TIMEOUT = config["settings"]["sleep_timeout"]
PROGRESSBAR_DISPLAY = config["settings"]["progressbar_display"]
ENABLE_CURRENT_TRACK_DISPLAY = config["settings"]["enable_current_track_display"]
FRAMEBUFFER = config["settings"]["framebuffer"]
BUTTON_A = config["buttons"]["button_a"]
BUTTON_B = config["buttons"]["button_b"]
BUTTON_X = config["buttons"]["button_x"]
BUTTON_Y = config["buttons"]["button_y"]
CLOCK_TYPE = config["clock"]["type"]
CLOCK_BACKGROUND = config["clock"]["background"]
CLOCK_COLOR = config["clock"].get("color", "black")
ENABLE_LASTFM_SCROBBLE = config.get('lastfm', {}).get('enabled', False)
LASTFM_SCROBBLE_THRESHOLD = float(config.get('lastfm', {}).get('scrobble_threshold', 0.75))
LASTFM_MIN_SECONDS = int(config.get('lastfm', {}).get('min_scrobble_seconds', 30))
OVERLAY_ENABLED = config.get('overlay', {}).get('enabled', False)
OVERLAY_TOKEN = config.get('overlay', {}).get('token', '')
MIN_DISPLAY_INTERVAL = 0.001
DEBOUNCE_TIME = 0.3
UPDATE_INTERVAL_WEATHER = 3600
WAKEUP_CHECK_INTERVAL = 10
GEO_UPDATE_INTERVAL = 900

def get_cached_bg(bg_path, size):
    key = (bg_path, size)
    if key not in bg_cache:
        bg_img = Image.open(bg_path).resize(size, Image.BILINEAR)
        bg_cache[key] = bg_img
    return bg_cache[key]

def get_cached_text_bbox(text, font):
    key = (text, getattr(font, "path", None), getattr(font, "size", None))
    if key not in text_bbox_cache:
        text_bbox_cache[key] = font.getbbox(text)
    return text_bbox_cache[key]

def cleanup_caches():
    global bg_cache, text_bbox_cache, album_bg_cache, scrolling_text_cache
    if len(bg_cache) > 5:
        bg_cache.clear()
    if len(text_bbox_cache) > 50:
        text_bbox_cache.clear()
    if len(album_bg_cache) > 3:
        keys = list(album_bg_cache.keys())[-3:]
        album_bg_cache = {k: album_bg_cache[k] for k in keys}
    if len(scrolling_text_cache) > 3:
        scrolling_text_cache.clear()

def check_internet_connection(timeout=5):
    try:
        response = session.get("http://www.google.com", timeout=timeout)
        return response.status_code == 200
    except requests.RequestException:
        try:
            socket.create_connection(("8.8.8.8", 53), timeout=timeout)
            return True
        except:
            return False

def update_internet_status():
    global internet_available, last_internet_check
    current_time = time.time()
    if current_time - last_internet_check > INTERNET_CHECK_INTERVAL:
        internet_available = check_internet_connection(timeout=3)
        last_internet_check = current_time
    return internet_available

def get_location_via_gpsd(timeout=5, debug=True):
    try:
        dev_result = subprocess.run(['gpspipe', '-w', '-n', '2'], capture_output=True, text=True, check=False)
        devices_found = False
        for line in dev_result.stdout.splitlines():
            try:
                js = json.loads(line)
                if js.get("class") == "DEVICES":
                    if js.get("devices"): devices_found = True
                    else: return None, None
            except json.JSONDecodeError:
                continue
        if not devices_found: return None, None
        tpv_result = subprocess.run(['timeout', str(timeout), 'gpspipe', '-w', '-n', '10'], capture_output=True, text=True, check=False)
        if tpv_result.returncode == 124:
            return None, None
        elif tpv_result.returncode != 0:
            return None, None
        for line in tpv_result.stdout.splitlines():
            try:
                report = json.loads(line)
                if report.get('class') == 'TPV' and 'lat' in report and 'lon' in report:
                    return report['lat'], report['lon']
            except json.JSONDecodeError:
                continue
        return None, None
    except FileNotFoundError:
        return None, None

def get_location_via_google_geolocation(api_key):
    url = f"https://www.googleapis.com/geolocation/v1/geolocate?key={api_key}"
    try:
        response = session.post(url, json={}, timeout=15)
        response.raise_for_status()
        data = response.json()
        if 'location' in data and 'lat' in data['location'] and 'lng' in data['location']:
            return data['location']['lat'], data['location']['lng']
        return None, None
    except requests.exceptions.RequestException:
        return None, None
    except json.JSONDecodeError:
        return None, None
    except KeyError:
        return None, None

def get_location_via_openweathermap_geocoding(api_key, city_name):
    if not update_internet_status():
        return None, None
    if not city_name: return None, None
    url = f"http://api.openweathermap.org/geo/1.0/direct?q={city_name}&limit=1&appid={api_key}"
    try:
        response = session.get(url, timeout=10)
        response.raise_for_status()
        data = response.json()
        if data and len(data) > 0:
            return data[0]['lat'], data[0]['lon']
        return None, None
    except requests.exceptions.RequestException:
        return None, None
    except (KeyError, IndexError):
        return None, None

def get_cached_weather(lat, lon):
    cache_key = f"{lat:.2f}_{lon:.2f}"
    if cache_key in weather_cache:
        cached_data, timestamp = weather_cache[cache_key]
        if time.time() - timestamp < 300:
            return cached_data
    return None

def cache_weather(lat, lon, data):
    weather_cache[f"{lat:.2f}_{lon:.2f}"] = (data, time.time())

def get_weather_data_by_coords(api_key, lat, lon, units):
    if not update_internet_status():
        print("⚠️ Skipping weather update - no internet")
        cache_key = f"{lat:.2f}_{lon:.2f}"
        if cache_key in weather_cache:
            return weather_cache[cache_key][0]
        return None
    if lat is None or lon is None: return None
    cache_key = f"{lat:.2f}_{lon:.2f}"
    current_time = time.time()
    if cache_key in weather_cache:
        cached_data, timestamp = weather_cache[cache_key]
        if current_time - timestamp < 600:
            return cached_data
    url = f"http://api.openweathermap.org/data/2.5/weather?lat={lat}&lon={lon}&appid={api_key}&units={units}"
    try:
        response = session.get(url, timeout=10)
        if response.status_code == 429:
            print("⚠️ Weather API rate limit approached, extending cache")
            if cache_key in weather_cache:
                return weather_cache[cache_key][0]
            return None
        response.raise_for_status()
        data = response.json()
        weather_info_local = {"city": data["name"], "country": data["sys"]["country"], "temp": round(data["main"]["temp"]), "feels_like": round(data["main"]["feels_like"]), "description": data["weather"][0]["description"].title(), "humidity": data["main"]["humidity"], "pressure": data["main"]["pressure"], "wind_speed": round(data["wind"]["speed"], 1), "icon_id": data["weather"][0]["icon"], "main": data["weather"][0]["main"].title()}
        weather_cache[cache_key] = (weather_info_local, current_time)
        return weather_info_local
    except requests.exceptions.RequestException as e:
        print(f"Weather API error: {e}")
        if cache_key in weather_cache:
            return weather_cache[cache_key][0]
        return None
    except KeyError:
        return None

def get_contrasting_colors(img, n=2):
    if img.mode != "RGB": img = img.convert("RGB")
    small_img = img.resize((50, 50), Image.BILINEAR)
    pixels = list(small_img.getdata())
    avg_r = sum(p[0] for p in pixels) // len(pixels)
    avg_g = sum(p[1] for p in pixels) // len(pixels)
    avg_b = sum(p[2] for p in pixels) // len(pixels)
    avg_h, avg_s, avg_v = colorsys.rgb_to_hsv(avg_r/255, avg_g/255, avg_b/255)
    opposite_h = (avg_h + 0.5) % 1.0
    colors = []
    data_saturation = min(0.9, avg_s + 0.3)
    label_saturation = max(0.6, data_saturation - 0.2)
    base_brightness = 0.9 if avg_v < 0.3 else (0.8 if avg_v > 0.7 else 0.85)
    r1, g1, b1 = colorsys.hsv_to_rgb(opposite_h, data_saturation, base_brightness)
    colors.append((int(r1*255), int(g1*255), int(b1*255)))
    if n > 1:
        secondary_h = (opposite_h + 0.12) % 1.0
        label_brightness = base_brightness - 0.05 if base_brightness > 0.7 else base_brightness
        r2, g2, b2 = colorsys.hsv_to_rgb(secondary_h, label_saturation, label_brightness)
        colors.append((int(r2*255), int(g2*255), int(b2*255)))
    return colors[:n]


def init_lastfm_client():
    global lfm
    try:
        cfg = config.get('lastfm', {})
        api_key = cfg.get('api_key')
        api_secret = cfg.get('api_secret')
        username = cfg.get('username')
        password = cfg.get('password')
        if not api_key or not api_secret or not username or not password:
            return None
        if not HAS_PYLAST:
            print("⚠️ pylast not installed, Last.fm support disabled")
            return None
        # pylast expects password hash
        import hashlib as _hashlib
        password_hash = _hashlib.md5(password.encode('utf-8')).hexdigest()
        lfm = pylast.LastFMNetwork(api_key=api_key, api_secret=api_secret, username=username, password_hash=password_hash)
        # Attempt a simple call to verify credentials
        try:
            _ = lfm.get_user(username)
            print("✅ Last.fm client initialized")
            return lfm
        except Exception as e:
            print(f"⚠️ Last.fm auth failed: {e}")
            lfm = None
            return None
    except Exception as e:
        print(f"⚠️ Last.fm client init error: {e}")
        lfm = None
        return None


def report_now_playing_to_lastfm(track):
    global lfm
    if not ENABLE_LASTFM_SCROBBLE or lfm is None or not track or not track.get('title'):
        return
    try:
        artist = track.get('artists', '')
        title = track.get('title', '')
        album = track.get('album', '')
        duration = int(track.get('duration', 0))
        # pylast requires strings, duration in seconds
        lfm.update_now_playing(artist=artist, title=title, duration=duration, album=album)
        print(f"✅ Last.fm now playing: {artist} - {title}")
    except Exception as e:
        print(f"⚠️ Last.fm update_now_playing failed: {e}")


def scrobble_to_lastfm(track, timestamp=None):
    global lfm, lastfm_scrobbled
    if not ENABLE_LASTFM_SCROBBLE or lfm is None or not track or not track.get('title'):
        return
    try:
        artist = track.get('artists', '')
        title = track.get('title', '')
        album = track.get('album', '')
        duration = int(track.get('duration', 0))
        ts = timestamp or int(time.time())
        # Avoid duplicate scrobbles for same track+ts
        key = f"{artist}:{title}:{ts}"
        if key in lastfm_scrobbled:
            return
        lfm.scrobble(artist=artist, title=title, timestamp=ts, duration=duration, album=album)
        lastfm_scrobbled[key] = True
        print(f"✅ Scrobbled to Last.fm: {artist} - {title} (start_ts={ts})")
        # Fire-and-forget overlay event (local neondisplay server)
        try:
            event = {'type': 'scrobble', 'artist': artist, 'title': title, 'start_ts': ts}
            if executor is not None:
                executor.submit(post_overlay_event, event)
            else:
                post_overlay_event(event)
        except Exception:
            pass
    except Exception as e:
        print(f"⚠️ Last.fm scrobble failed: {e}")


def post_overlay_event(event):
    """Post an event back to the local neondisplay server for overlay streaming.
    This is fire-and-forget and best-effort.
    """
    try:
        cfg = load_config()
        overlay_cfg = cfg.get('overlay', {})
        if not overlay_cfg.get('enabled', False):
            return
        overlay_port = int(overlay_cfg.get('port', 5000))
        overlay_host = overlay_cfg.get('host', '127.0.0.1') if 'host' in overlay_cfg else '127.0.0.1'
        overlay_token = overlay_cfg.get('token', '')
        # If encryption is enabled, attempt to read encrypted token using file or env key source
        try:
            if overlay_cfg.get('encrypted', False) and overlay_cfg.get('encrypted_token') and HAS_CRYPTO:
                key_source = overlay_cfg.get('key_source', 'file')
                if key_source == 'env':
                    env_name = overlay_cfg.get('env_key_name', 'OVERLAY_SECRET_KEY')
                    key_val = os.environ.get(env_name)
                    if key_val:
                        k = key_val.encode('utf-8') if isinstance(key_val, str) else key_val
                    else:
                        k = None
                else:
                    key_path = os.path.join('secrets', 'overlay_key.key')
                    k = None
                    if os.path.exists(key_path):
                        with open(key_path, 'rb') as kf:
                            k = kf.read()
                if k:
                    f = Fernet(k)
                    overlay_token = f.decrypt(overlay_cfg.get('encrypted_token').encode('utf-8')).decode('utf-8')
        except Exception:
            pass
        if not overlay_token:
            return
        url = f'http://{overlay_host}:{overlay_port}/events'
        headers = {'X-Overlay-Token': overlay_token}
        session.post(url, json=event, timeout=1, headers=headers)
    except Exception:
        pass

def make_background_from_art(size, album_art_img):
    width, height = size
    if album_art_img is None:
        bg = Image.new("RGB", size, (40, 40, 60))
        for y in range(height):
            factor = 0.7 + (y / height) * 0.6
            for x in range(width):
                r, g, b = bg.getpixel((x, y))
                bg.putpixel((x, y), (int(r * factor), int(g * factor), int(b * factor)))
        return bg
    if album_art_img.mode != "RGB":
        album_art_img = album_art_img.convert("RGB")
    small_for_color = album_art_img.resize((50, 50), Image.BILINEAR)
    pixels = list(small_for_color.getdata())
    r = sum(p[0] for p in pixels) // len(pixels)
    g = sum(p[1] for p in pixels) // len(pixels)
    b = sum(p[2] for p in pixels) // len(pixels)
    avg_color = (int(r * 0.7), int(g * 0.7), int(b * 0.7))
    bg = Image.new("RGB", size, avg_color)
    art_size = height
    scaled_art = album_art_img.resize((art_size, art_size), Image.BILINEAR)
    blurred_art = scaled_art.filter(ImageFilter.GaussianBlur(2))
    enhancer = ImageEnhance.Brightness(blurred_art)
    blurred_art = enhancer.enhance(0.6)
    fade_width = min(80, art_size // 4)
    x_coords = np.arange(art_size)
    left_fade_mask = np.where(x_coords < fade_width,
                            255 * ((x_coords / fade_width) ** 0.7),
                            255).astype(np.uint8)
    right_fade_mask = np.where(x_coords > art_size - fade_width,
                            255 * (((art_size - x_coords) / fade_width) ** 0.7),
                            255).astype(np.uint8)
    combined_alpha = np.minimum(left_fade_mask, right_fade_mask)
    mask_array = np.tile(combined_alpha, (art_size, 1))
    mask = Image.fromarray(mask_array, mode='L')
    art_x = (width - art_size) // 2
    bg.paste(blurred_art, (art_x, 0), mask)
    return bg


def get_musicbrainz_cover_art(artist_name, album_name):
    """Search MusicBrainz for a release and return a Cover Art Archive URL for the front image (or None)."""
    try:
        if not artist_name or not album_name:
            return None
        # Build a search query: artist and release
        from urllib.parse import quote_plus
        query = f'artist:"{artist_name}" AND release:"{album_name}"'
        url = f'https://musicbrainz.org/ws/2/release/?query={quote_plus(query)}&fmt=json&limit=1'
        headers = {'User-Agent': 'rPi-station/1.0 (https://github.com/klept0/rPi-station)'}
        resp = session.get(url, headers=headers, timeout=8)
        resp.raise_for_status()
        data = resp.json()
        releases = data.get('releases', [])
        if not releases:
            return None
        release_id = releases[0].get('id')
        if not release_id:
            return None
        # Try the Cover Art Archive for the release front image
        cover_url = f'https://coverartarchive.org/release/{release_id}/front'
        # Check that the URL exists
        r = session.head(cover_url, timeout=5, headers=headers)
        if r.status_code == 200:
            return cover_url
        else:
            # Cover Art Archive may return 404; try front-500 or the /front-250
            for suffix in ['front-500', 'front-250', 'front-1200']:
                tryu = f'https://coverartarchive.org/release/{release_id}/{suffix}'
                r2 = session.head(tryu, timeout=5, headers=headers)
                if r2.status_code == 200:
                    return tryu
            return None
    except Exception:
        return None


def _make_background_bytes(art_bytes, size):
    # Worker function to generate a background image from art bytes.
    # This runs in a separate process; avoid global state.
    try:
        from PIL import Image as PILImage, ImageFilter as PILImageFilter, ImageEnhance as PILImageEnhance
        import numpy as _np
        from io import BytesIO as _BytesIO
        if art_bytes is None:
            bg = PILImage.new("RGB", size, (40, 40, 60))
            # simple vertical gradient
            width, height = size
            pixels = bg.load()
            for y in range(height):
                factor = 0.7 + (y / height) * 0.6
                for x in range(width):
                    r, g, b = pixels[x, y]
                    pixels[x, y] = (int(r * factor), int(g * factor), int(b * factor))
            buf = _BytesIO()
            bg.save(buf, format='PNG')
            return buf.getvalue()
        img = PILImage.open(_BytesIO(art_bytes)).convert('RGB')
        width, height = size
        small_for_color = img.resize((50, 50), PILImage.BILINEAR)
        pixels = list(small_for_color.getdata())
        r = sum(p[0] for p in pixels) // len(pixels)
        g = sum(p[1] for p in pixels) // len(pixels)
        b = sum(p[2] for p in pixels) // len(pixels)
        avg_color = (int(r * 0.7), int(g * 0.7), int(b * 0.7))
        bg = PILImage.new("RGB", (width, height), avg_color)
        art_size = height
        scaled_art = img.resize((art_size, art_size), PILImage.BILINEAR)
        blurred_art = scaled_art.filter(PILImageFilter.GaussianBlur(2))
        enhancer = PILImageEnhance.Brightness(blurred_art)
        blurred_art = enhancer.enhance(0.6)
        fade_width = min(80, art_size // 4)
        x_coords = _np.arange(art_size)
        left_fade_mask = _np.where(x_coords < fade_width,
                                255 * ((x_coords / fade_width) ** 0.7),
                                255).astype(_np.uint8)
        right_fade_mask = _np.where(x_coords > art_size - fade_width,
                                255 * (((art_size - x_coords) / fade_width) ** 0.7),
                                255).astype(_np.uint8)
        combined_alpha = _np.minimum(left_fade_mask, right_fade_mask)
        mask_array = _np.tile(combined_alpha, (art_size, 1))
        mask = PILImage.fromarray(mask_array, mode='L')
        art_x = (width - art_size) // 2
        bg.paste(blurred_art, (art_x, 0), mask)
        buf = _BytesIO()
        bg.save(buf, format='PNG')
        return buf.getvalue()
    except Exception as e:
        try:
            # On failure return a basic black PNG
            from PIL import Image as PILImage
            from io import BytesIO as _BytesIO
            bg = PILImage.new('RGB', size, 'black')
            buf = _BytesIO(); bg.save(buf, format='PNG'); return buf.getvalue()
        except Exception:
            return b''


    def _process_artist_image_bytes(art_bytes):
        try:
            from PIL import Image as PILImage
            from io import BytesIO as _BytesIO
            if not art_bytes:
                return b''
            img = PILImage.open(_BytesIO(art_bytes)).convert('RGBA')
            img = img.resize((100, 100), PILImage.BILINEAR)
            buf = _BytesIO()
            img.save(buf, format='PNG')
            return buf.getvalue()
        except Exception:
            return b''


    def _process_album_art_bytes(art_bytes, size=(150,150)):
        try:
            from PIL import Image as PILImage
            from io import BytesIO as _BytesIO
            import colorsys as _colorsys
            if not art_bytes:
                return (b'', (0,255,0), (0,255,255))
            img = PILImage.open(_BytesIO(art_bytes)).convert('RGB')
            img.thumbnail(size, PILImage.BILINEAR)
            bio = _BytesIO(); img.save(bio, format='PNG'); img_bytes = bio.getvalue()
            # compute contrasting colors
            small_for_color = img.resize((50,50), PILImage.BILINEAR)
            pixels = list(small_for_color.getdata())
            r = sum(p[0] for p in pixels) // len(pixels)
            g = sum(p[1] for p in pixels) // len(pixels)
            b = sum(p[2] for p in pixels) // len(pixels)
            avg_h, avg_s, avg_v = _colorsys.rgb_to_hsv(r/255.0, g/255.0, b/255.0)
            opposite_h = (avg_h + 0.5) % 1.0
            data_saturation = min(0.9, avg_s + 0.3)
            label_saturation = max(0.6, data_saturation - 0.2)
            base_brightness = 0.9 if avg_v < 0.3 else (0.8 if avg_v > 0.7 else 0.85)
            r1,g1,b1 = _colorsys.hsv_to_rgb(opposite_h, data_saturation, base_brightness)
            main_color = (int(r1*255), int(g1*255), int(b1*255))
            secondary_h = (opposite_h + 0.12) % 1.0
            label_brightness = base_brightness - 0.05 if base_brightness > 0.7 else base_brightness
            r2,g2,b2 = _colorsys.hsv_to_rgb(secondary_h, label_saturation, label_brightness)
            secondary_color = (int(r2*255), int(g2*255), int(b2*255))
            return (img_bytes, main_color, secondary_color)
        except Exception:
            return (b'', (0,255,0), (0,255,255))


    def _dither_image_bytes(art_bytes, size=(40,40)):
        try:
            from PIL import Image as PILImage
            from io import BytesIO as _BytesIO
            if not art_bytes:
                return b''
            img = PILImage.open(_BytesIO(art_bytes)).convert('RGB')
            img = img.resize(size, PILImage.BILINEAR)
            bw = img.convert('L')
            bw = bw.convert('1')
            buf = _BytesIO()
            bw.save(buf, format='PNG')
            return buf.getvalue()
        except Exception:
            return b''

def compute_img_hash(img):
    """Compute a stable MD5 hash for an image by saving to PNG bytes."""
    try:
        buf = BytesIO()
        img.save(buf, format='PNG')
        import hashlib
        return hashlib.md5(buf.getvalue()).hexdigest()
    except Exception:
        return None


def get_local_ip():
    """Return the local IP address of the device (first non-loopback)."""
    try:
        import socket
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        # Doesn't need to be reachable
        s.connect(('8.8.8.8', 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return '127.0.0.1'


ALBUM_BG_CACHE_MAX = 3
def get_cached_background(size, album_art_img, album_art_hash=None):
    """Return a cached background for a given album art & size. Uses an LRU OrderedDict.
    album_art_hash may be provided to avoid repeated hashing."""
    global album_bg_cache
    if album_art_img is None:
        return Image.new("RGB", size, "black")
    if album_art_hash is None:
        album_art_hash = compute_img_hash(album_art_img)
    key = (album_art_hash, size)
    with album_bg_cache_lock:
        if key in album_bg_cache:
            album_bg_cache.move_to_end(key)
            return album_bg_cache[key].copy()
    bg = make_background_from_art(size, album_art_img)
    with album_bg_cache_lock:
        album_bg_cache[key] = bg.copy()
        if len(album_bg_cache) > ALBUM_BG_CACHE_MAX:
            album_bg_cache.popitem(last=False)
    return bg


def get_cached_resized_image(img, size, mode='RGB'):
    """Return a resized/converted version of img, using LRU cache keyed by image hash & size."""
    if img is None:
        return None
    img_hash = compute_img_hash(img)
    key = (img_hash, size, mode)
    if key in resized_image_cache:
        resized_image_cache.move_to_end(key)
        return resized_image_cache[key].copy()
    try:
        new_img = img.copy()
        if new_img.size != size:
            new_img = new_img.resize(size, Image.BILINEAR)
        if mode and new_img.mode != mode:
            new_img = new_img.convert(mode)
        resized_image_cache[key] = new_img.copy()
        if len(resized_image_cache) > IMG_CACHE_MAX:
            resized_image_cache.popitem(last=False)
        return new_img
    except Exception:
        return img


def get_cached_dithered_image(img, size=(40, 40)):
    if img is None:
        return None
    img_hash = compute_img_hash(img)
    key = (img_hash, size, '1')
    with dithered_cache_lock:
        if key in dithered_image_cache:
            dithered_image_cache.move_to_end(key)
            return dithered_image_cache[key].copy()
    if process_executor is not None:
        # Submit to process pool and return None until result is ready
        try:
            bio = BytesIO(); img.save(bio, format='PNG'); art_bytes = bio.getvalue()
            fut = process_executor.submit(_dither_image_bytes, art_bytes, size)
            pending_dither_futures[fut] = {'size': size, 'key': key}
            return None
        except Exception:
            pass
    # fallback synchronous conversion
    try:
        small_img = img.resize(size, Image.BILINEAR)
        gray_img = small_img.convert('L')
        bw_img = gray_img.convert('1')
        with dithered_cache_lock:
            dithered_image_cache[key] = bw_img.copy()
            if len(dithered_image_cache) > IMG_CACHE_MAX:
                dithered_image_cache.popitem(last=False)
        return bw_img
    except Exception:
        return img.convert('1')

def background_generation_worker():
    global spotify_bg_cache, current_album_art_hash, clock_bg_image
    global spotify_bg_cache, current_album_art_hash, clock_bg_image, process_executor, pending_bg_futures
    # Lazily initialize process executor since forking at import time can be problematic
    if process_executor is None:
        try:
            cpu_count = os.cpu_count() or 1
            config_workers = config.get('settings', {}).get('process_pool_workers', None)
            if config_workers and isinstance(config_workers, int) and config_workers > 0:
                workers = config_workers
            else:
                workers = max(1, cpu_count // 2)
            process_executor = ProcessPoolExecutor(max_workers=workers)
            print(f"✅ ProcessPoolExecutor initialized with {workers} workers")
        except Exception as e:
            print(f"⚠️ ProcessPoolExecutor init failed: {e}")
            process_executor = None
    while not exit_event.is_set():
        # First, check for completed futures (background images, artists, dithered images)
        for fut, meta in list(pending_bg_futures.items()):
            if fut.done():
                try:
                    data = fut.result()
                    if data:
                        img = Image.open(BytesIO(data)).convert('RGB')
                        size, bg_type, album_hash = meta['size'], meta['bg_type'], meta['album_hash']
                        if bg_type == 'spotify':
                            with spotify_bg_cache_lock:
                                spotify_bg_cache = img.copy()
                                current_album_art_hash = album_hash
                        elif bg_type == 'clock':
                            with clock_bg_lock:
                                clock_bg_image = img.copy()
                        key = (album_hash, size)
                        try:
                            album_bg_cache[key] = img.copy()
                            album_bg_cache.move_to_end(key)
                        except Exception:
                            pass
                except Exception as e:
                    print(f"Background generation future error: {e}")
                finally:
                    pending_bg_futures.pop(fut, None)
            # Artist futures
            for fut, meta in list(pending_artist_futures.items()):
                if fut.done():
                    try:
                        data = fut.result()
                        if data:
                            img = Image.open(BytesIO(data)).convert('RGBA')
                            with artist_image_lock:
                                artist_image = img
                            try:
                                update_display()
                            except Exception:
                                pass
                    except Exception as e:
                        print(f"Artist generation future error: {e}")
                    finally:
                        pending_artist_futures.pop(fut, None)
            # Album art futures
            for fut, meta in list(pending_album_futures.items()):
                if fut.done():
                    try:
                        img_bytes, main_color, secondary_color = fut.result()
                        if img_bytes:
                            img = Image.open(BytesIO(img_bytes)).convert('RGB')
                            with art_lock:
                                album_art_image = img
                            spotify_track_local = spotify_track
                            if spotify_track_local:
                                spotify_track_local['main_color'] = main_color
                                spotify_track_local['secondary_color'] = secondary_color
                            update_spotify_layout(spotify_track_local)
                            if START_SCREEN == 'spotify':
                                update_display()
                    except Exception as e:
                        print(f"Album art future error: {e}")
                    finally:
                        pending_album_futures.pop(fut, None)
            # Dither futures
            for fut, meta in list(pending_dither_futures.items()):
                if fut.done():
                    try:
                        data = fut.result()
                        if data:
                            bw = Image.open(BytesIO(data)).convert('1')
                            size, key = meta.get('size'), meta.get('key')
                            with dithered_cache_lock:
                                dithered_image_cache[key] = bw.copy()
                                dithered_image_cache.move_to_end(key)
                    except Exception as e:
                        print(f"Dither generation future error: {e}")
                    finally:
                        pending_dither_futures.pop(fut, None)
        try:
            album_img, size, bg_type = bg_generation_queue.get(timeout=1)
        except queue.Empty:
            continue
        try:
            album_hash = compute_img_hash(album_img) if album_img else None
            if process_executor is not None:
                try:
                    if album_img is not None:
                        bio = BytesIO(); album_img.save(bio, format='PNG'); art_bytes = bio.getvalue()
                    else:
                        art_bytes = None
                    fut = process_executor.submit(_make_background_bytes, art_bytes, size)
                    pending_bg_futures[fut] = {'size': size, 'bg_type': bg_type, 'album_hash': album_hash}
                except Exception as e:
                    # fallback: generate inline
                    generated_bg = get_cached_background(size, album_img, album_art_hash=album_hash)
                    if bg_type == 'spotify':
                        with spotify_bg_cache_lock:
                            spotify_bg_cache = generated_bg.copy() if generated_bg else None
                            current_album_art_hash = album_hash
                    elif bg_type == 'clock':
                        with clock_bg_lock:
                            clock_bg_image = generated_bg.copy() if generated_bg else None
            else:
                generated_bg = get_cached_background(size, album_img, album_art_hash=album_hash)
                if bg_type == 'spotify':
                    with spotify_bg_cache_lock:
                        spotify_bg_cache = generated_bg.copy() if generated_bg else None
                        current_album_art_hash = album_hash
                elif bg_type == 'clock':
                    with clock_bg_lock:
                        clock_bg_image = generated_bg.copy() if generated_bg else None
        except Exception as e:
            print(f"Background generation worker error: {e}")
        finally:
            try:
                bg_generation_queue.task_done()
            except Exception:
                pass
        # check for new notifications from neondisplay every loop iteration
        try:
            # poll notifications regardless of process executor availability
                # poll local neondisplay for notifications
                try:
                    url = 'http://127.0.0.1:5000/notifications'
                    resp = session.get(url, timeout=0.5)
                    if resp.status_code == 200:
                        data = resp.json()
                        notifs = data.get('notifications', [])
                        if notifs:
                            # Save into HUD notifications list
                            try:
                                globals()['notifications'] = notifs[-20:]
                            except Exception:
                                pass
                except Exception:
                    pass
        except Exception:
            pass

def request_background_generation(album_img):
    global current_clock_artwork, current_clock_artwork_hash
    if album_img is not None:
        album_hash = compute_img_hash(album_img)
        with clock_bg_lock:
            if current_clock_artwork is None or album_hash != current_clock_artwork_hash:
                current_clock_artwork = album_img.copy()
                current_clock_artwork_hash = album_hash
        current_hash = album_hash
        if hasattr(request_background_generation, 'last_queued_hash'):
            if request_background_generation.last_queued_hash == current_hash:
                return
        request_background_generation.last_queued_hash = current_hash
        try:
            bg_generation_queue.put((album_img, (SCREEN_WIDTH, SCREEN_HEIGHT), "spotify"), block=False)
        except queue.Full:
            pass
        if CLOCK_BACKGROUND == "album":
            try:
                bg_generation_queue.put((album_img, (SCREEN_WIDTH, SCREEN_HEIGHT), "clock"), block=False)
            except queue.Full:
                pass
    else:
        with clock_bg_lock:
            current_clock_artwork = None
            current_clock_artwork_hash = None

def get_background_path(weather_info):
    if not weather_info:
        candidate = "bg_default.png"
    else:
        main = weather_info.get('main', '').capitalize()
        candidate = bg_map.get(main, "bg_default.png")
    full_path = os.path.join(BG_DIR, candidate) if candidate else None
    if full_path and os.path.exists(full_path):
        return candidate
    fallback_path = os.path.join(BG_DIR, "bg_default.png")
    if os.path.exists(fallback_path):
        return "bg_default.png"
    return None

def draw_text_aliased(draw, image, position, text, font, fill):
    mask = Image.new("L", image.size, 0)
    mask_draw = ImageDraw.Draw(mask)
    mask_draw.text(position, text, fill=255, font=font)
    color_layer = Image.new("RGB", image.size, fill)
    image.paste(color_layer, (0, 0), mask)

def draw_weather_image(weather_info):
    img = Image.new("RGB", (SCREEN_WIDTH, SCREEN_HEIGHT), "black")
    bg_filename = get_background_path(weather_info)
    if bg_filename:
        bg_path = os.path.join(BG_DIR, bg_filename)
        bg_img = get_cached_bg(bg_path, (SCREEN_WIDTH, SCREEN_HEIGHT))
        img.paste(bg_img, (0, 0))
    draw = ImageDraw.Draw(img)
    if weather_info:
        text_elements = []
        title = f"{weather_info['city']}, {weather_info['country']}"
        text_elements.append((title, (10, 10), LARGE_FONT, "white"))
        temp_text = f"{weather_info['temp']}°C"
        text_elements.append((temp_text, (10, 60), LARGE_FONT, "cyan"))
        feels_text = f"Feels like: {weather_info['feels_like']}°C"
        text_elements.append((feels_text, (10, 110), MEDIUM_FONT, "lightblue"))
        desc_text = weather_info['description']
        text_elements.append((desc_text, (10, 150), MEDIUM_FONT, "yellow"))
        humidity_text = f"Humidity: {weather_info['humidity']}%"
        text_elements.append((humidity_text, (10, 190), SMALL_FONT, "orange"))
        pressure_text = f"Pressure: {weather_info['pressure']} hPa"
        text_elements.append((pressure_text, (10, 215), SMALL_FONT, "orange"))
        wind_text = f"Wind: {weather_info['wind_speed']} m/s"
        text_elements.append((wind_text, (10, 240), SMALL_FONT, "orange"))
        overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
        overlay_draw = ImageDraw.Draw(overlay)
        for text, position, font, color in text_elements:
            bbox = get_cached_text_bbox(text, font)
            actual_bbox = (position[0] + bbox[0], position[1] + bbox[1], position[0] + bbox[2], position[1] + bbox[3])
            overlay_draw.rectangle([actual_bbox[0]-5, actual_bbox[1]-5, actual_bbox[2]+5, actual_bbox[3]+5], fill=(0, 0, 0, 200))
        img = Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")
        draw = ImageDraw.Draw(img)
        for text, position, font, color in text_elements:
            draw_text_aliased(draw, img, position, text, font, color)
        if "icon_id" in weather_info:
                try:
                    icon_url = f"http://openweathermap.org/img/wn/{weather_info['icon_id']}@2x.png"
                    resp = session.get(icon_url, timeout=5)
                resp.raise_for_status()
                icon_img = Image.open(BytesIO(resp.content)).convert("RGBA")
                icon_img.thumbnail((128, 128), Image.BILINEAR)
                img.paste(icon_img, (SCREEN_WIDTH - icon_img.size[0], SCREEN_HEIGHT - icon_img.size[1] - 40), icon_img)
            except Exception:
                pass
        if TIME_DISPLAY:
            now = datetime.datetime.now().strftime("%H:%M")
            time_bbox = get_cached_text_bbox(now, MEDIUM_FONT)
            time_width = time_bbox[2] - time_bbox[0]
            time_height = time_bbox[3] - time_bbox[1]
            x = SCREEN_WIDTH - time_width - 10
            y = SCREEN_HEIGHT - time_height - 10
            overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
            overlay_draw = ImageDraw.Draw(overlay)
            overlay_draw.rectangle([x-5, y-5, x + time_width + 5, y + time_height + 5], fill=(0, 0, 0,200))
            img = Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")
            draw_text_aliased(draw, img, (x, y), now, MEDIUM_FONT, "gray")
    else:
        error_text = "Failed to fetch weather data."
        bbox = get_cached_text_bbox(error_text, MEDIUM_FONT)
        text_width = bbox[2] - bbox[0]
        text_height = bbox[3] - bbox[1]
        x = (SCREEN_WIDTH - text_width) // 2
        y = (SCREEN_HEIGHT - text_height) // 2
        overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
        overlay_draw = ImageDraw.Draw(overlay)
        overlay_draw.rectangle([x-5, y-5, x + text_width + 5, y + text_height + 5], fill=(0, 0, 0,200))
        img = Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")
        draw = ImageDraw.Draw(img)
        draw_text_aliased(draw, img, (x, y), error_text, MEDIUM_FONT, "red")
        if TIME_DISPLAY:
            now = datetime.datetime.now().strftime("%H:%M")
            time_bbox = get_cached_text_bbox(now, MEDIUM_FONT)
            time_width = time_bbox[2] - time_bbox[0]
            time_height = time_bbox[3] - time_bbox[1]
            time_x = SCREEN_WIDTH - time_width - 10
            time_y = SCREEN_HEIGHT - time_height - 10
            overlay_draw.rectangle([time_x-5, time_y-5, time_x + time_width + 5, time_y + time_height + 5], fill=(0, 0, 0,200))
            img = Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")
            draw = ImageDraw.Draw(img)
            draw_text_aliased(draw, img, (time_x, time_y), now, MEDIUM_FONT, "gray")
    return img

def update_spotify_layout(track_data):
    global spotify_layout_cache
    if not track_data:
        spotify_layout_cache = None
        return
    fields = [("title", "Track  :", track_data.get("title", "")), ("artists", "Artists:", track_data.get("artists", "")), ("album", "Album:", track_data.get("album", ""))]
    layout = []
    y = 5
    padding = 4
    x_offset = 5
    for key, label, data in fields:
        if not data: continue
        label_bbox = get_cached_text_bbox(label, SPOT_MEDIUM_FONT)
        text_bbox = get_cached_text_bbox(data, SPOT_MEDIUM_FONT)
        label_width = label_bbox[2] - label_bbox[0]
        text_width = text_bbox[2] - text_bbox[0]
        text_height = text_bbox[3] - text_bbox[1]
        field_height = max(label_bbox[3] - label_bbox[1], text_height) + padding * 2
        left_boundary = x_offset + label_width + 6
        visible_width = SCREEN_WIDTH - 5 - left_boundary
        layout.append({'key': key, 'label': label, 'data': data, 'y': y, 'field_height': field_height, 'label_width': label_width, 'text_width': text_width, 'left_boundary': left_boundary, 'visible_width': visible_width, 'needs_scroll': text_width > visible_width})
        y += field_height
    spotify_layout_cache = layout

def create_scrolling_text_image(text, font, color, total_width):
    img = Image.new("RGBA", (total_width, font.size + 10), (0,0,0,0))
    draw = ImageDraw.Draw(img)
    draw.text((0, 5), text, font=font, fill=color)
    draw.text((total_width // 2, 5), text, font=font, fill=color)
    return img

def draw_spotify_image(spotify_track):
    global album_art_image, artist_image, artist_on_top
    if display_sleeping:
        return Image.new("RGB", (SCREEN_WIDTH, SCREEN_HEIGHT), "black")
        with art_lock:
            art_img = album_art_image
    bg_to_use = None
    with spotify_bg_cache_lock:
        if spotify_bg_cache is not None and art_img is not None and current_album_art_hash is not None:
            bg_to_use = spotify_bg_cache.copy()
    if bg_to_use is None:
        bg_to_use = get_cached_background((SCREEN_WIDTH, SCREEN_HEIGHT), art_img, album_art_hash=current_album_art_hash)
    img = bg_to_use
    if spotify_track and 'main_color' in spotify_track and 'secondary_color' in spotify_track:
        main_color = spotify_track['main_color']
        secondary_color = spotify_track['secondary_color']
    else:
        if album_art_image:
            main_color, secondary_color = get_contrasting_colors(album_art_image)
        else:
            main_color, secondary_color = (0, 255, 0), (0, 255, 255)
    album_img_to_draw = None
    artist_img_to_draw = None
    album_pos = None
    artist_pos_to_draw = None
    if art_img:
        int_pos = (int(art_pos[0]), int(art_pos[1]))
        if art_img.mode != "RGB":
            bg = Image.new("RGB", art_img.size, "black")
            if art_img.mode in ("RGBA", "LA"):
                bg.paste(art_img, mask=art_img.split()[-1])
            else:
                bg.paste(art_img)
            album_img_to_draw = get_cached_resized_image(bg, bg.size, 'RGB')
        else:
            album_img_to_draw = get_cached_resized_image(art_img, art_img.size, 'RGB')
        album_pos = int_pos
    with artist_image_lock:
        art_img_artist = artist_image
    if art_img_artist:
        int_artist_pos = (int(artist_pos[0]), int(artist_pos[1]))
        if art_img_artist.mode != "RGB":
            bg = Image.new("RGB", art_img_artist.size, "black")
            if art_img_artist.mode in ("RGBA", "LA"):
                bg.paste(art_img_artist, mask=art_img_artist.split()[-1])
            else:
                bg.paste(art_img_artist)
            artist_img_to_draw = get_cached_resized_image(bg, bg.size, 'RGB')
        else:
            artist_img_to_draw = get_cached_resized_image(art_img_artist, art_img_artist.size, 'RGB')
        artist_pos_to_draw = int_artist_pos
    if artist_on_top:
        if album_img_to_draw and album_pos: img.paste(album_img_to_draw, album_pos)
        if artist_img_to_draw and artist_pos_to_draw: img.paste(artist_img_to_draw, artist_pos_to_draw)
    else:
        if artist_img_to_draw and artist_pos_to_draw: img.paste(artist_img_to_draw, artist_pos_to_draw)
        if album_img_to_draw and album_pos: img.paste(album_img_to_draw, album_pos)
    overlay = Image.new("RGBA", (SCREEN_WIDTH, SCREEN_HEIGHT), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    layout = spotify_layout_cache
    if layout:
        for item in layout:
            bg_width = min(item['label_width'] + 6 + item['text_width'] + 6, SCREEN_WIDTH - 5 - 5)
            draw.rectangle([5, item['y'], 5 + bg_width, item['y'] + item['field_height']], fill=(0,0,0,200))
            draw.text((5, item['y'] + 4), item['label'], fill=secondary_color, font=SPOT_MEDIUM_FONT)
            if item['needs_scroll']:
                scrolling_img = scrolling_text_cache.get(item['key'])
                if scrolling_img:
                    offset = scroll_state[item['key']]["offset"]
                    crop_x = offset % (item['text_width'] + 50)
                    cropped = scrolling_img.crop((crop_x, 0, crop_x + item['visible_width'], item['field_height']))
                    draw.rectangle([item['left_boundary'], item['y'], item['left_boundary'] + item['visible_width'], item['y'] + item['field_height']], fill=(0,0,0,200))
                    overlay.paste(cropped, (item['left_boundary'], item['y']), cropped)
                else:
                    draw.text((item['left_boundary'], item['y'] + 4), item['data'], fill=main_color, font=SPOT_MEDIUM_FONT)
            else:
                draw.text((item['left_boundary'], item['y'] + 4), item['data'], fill=main_color, font=SPOT_MEDIUM_FONT)
    else:
        img = Image.new("RGB", (SCREEN_WIDTH, SCREEN_HEIGHT), "black")
        if os.path.exists(os.path.join(BG_DIR, "no_track.png")):
            bg_path = os.path.join(BG_DIR, "no_track.png")
            bg_img = get_cached_bg(bg_path, (SCREEN_WIDTH, SCREEN_HEIGHT))
            img.paste(bg_img, (0, 0))
        error_text = "No track playing"
        bbox = get_cached_text_bbox(error_text, MEDIUM_FONT)
        draw.rectangle([5, 5, min(bbox[2]+11, SCREEN_WIDTH-5), bbox[3]+9], fill=(0,0,0,200))
        draw.text((11, 9), error_text, fill="red", font=MEDIUM_FONT)
    if PROGRESSBAR_DISPLAY:
        progress_bar_height = 10
        border_width = 2
        progress_bar_y = SCREEN_HEIGHT - progress_bar_height
        time_y_offset = progress_bar_height + border_width + 1
        draw.rectangle([
            0, 
            progress_bar_y - border_width, 
            SCREEN_WIDTH, 
            SCREEN_HEIGHT
        ], fill=(*secondary_color, 150))
        draw.rectangle([
            border_width, 
            progress_bar_y, 
            SCREEN_WIDTH - border_width, 
            SCREEN_HEIGHT
        ], fill=(0, 0, 0, 200))
        if spotify_track and 'current_position' in spotify_track and 'duration' in spotify_track:
            current_pos = spotify_track['current_position']
            duration = spotify_track['duration']
            if duration > 0:
                progress_percent = min(current_pos / duration, 1.0)
            else:
                progress_percent = 0
            progress_width = int((SCREEN_WIDTH - 2 * border_width) * progress_percent)
            draw.rectangle([
                border_width, 
                progress_bar_y, 
                border_width + progress_width, 
                SCREEN_HEIGHT
            ], fill=(*main_color, 180))
            current_min = current_pos // 60
            current_sec = current_pos % 60
            duration_min = duration // 60
            duration_sec = duration % 60
            time_text = f"{current_min}:{current_sec:02d} / {duration_min}:{duration_sec:02d}"
            time_bbox = SPOT_LARGE_FONT.getbbox(time_text)
            time_width = time_bbox[2] - time_bbox[0]
            time_height = time_bbox[3] - time_bbox[1]
            padding = 5
            background_width = time_width + 2 * padding
            background_height = time_height + 2 * padding
            time_x = 5
            time_y = SCREEN_HEIGHT - background_height - time_y_offset
            draw.rectangle([time_x, time_y, time_x + background_width, time_y + background_height], fill=(0, 0, 0, 200))
            text_x = time_x + padding
            text_y = time_y + padding - time_bbox[1]
            draw.text((text_x, text_y), time_text, fill=secondary_color, font=SPOT_LARGE_FONT)
    else:
        progress_bar_height = 0
        time_y_offset = 0
        if spotify_track and 'current_position' in spotify_track and 'duration' in spotify_track:
            current_pos = spotify_track['current_position']
            duration = spotify_track['duration']
    if TIME_DISPLAY:
        now = datetime.datetime.now().strftime("%H:%M")
        time_bbox = SPOT_LARGE_FONT.getbbox(now)
        time_width = time_bbox[2] - time_bbox[0]
        time_height = time_bbox[3] - time_bbox[1]
        padding = 5
        background_width = time_width + 2 * padding
        background_height = time_height + 2 * padding
        time_x = SCREEN_WIDTH - background_width - 5
        time_y = SCREEN_HEIGHT - background_height - time_y_offset
        draw.rectangle([time_x, time_y, time_x + background_width, time_y + background_height], fill=(0, 0, 0, 170))
        text_x = time_x + padding
        text_y = time_y + padding - time_bbox[1]
        draw.text((text_x, text_y), now, fill=main_color, font=SPOT_LARGE_FONT)
    img = Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")
    return img

def draw_clock_image():
    global weather_info, clock_bg_image
    img = Image.new("RGB", (SCREEN_WIDTH, SCREEN_HEIGHT), "black")
    avg_color = (128, 128, 128)
    bg_applied = False
    if CLOCK_BACKGROUND == "album":
        with clock_bg_lock:
            bg_to_use = clock_bg_image
        if bg_to_use is not None:
            img.paste(bg_to_use, (0, 0))
            stat = ImageStat.Stat(bg_to_use)
            avg_color = tuple(int(v) for v in stat.mean[:3])
            bg_applied = True
        else:
            try:
                if CLOCK_COLOR:
                    bg_color = ImageColor.getrgb(CLOCK_COLOR)
                else:
                    bg_color = (0, 0, 0)
                img.paste(Image.new("RGB", (SCREEN_WIDTH, SCREEN_HEIGHT), bg_color))
                avg_color = bg_color
                bg_applied = True
            except Exception:
                img.paste(Image.new("RGB", (SCREEN_WIDTH, SCREEN_HEIGHT), "black"))
                avg_color = (0, 0, 0)
                bg_applied = True
    elif CLOCK_BACKGROUND == "weather":
        bg_filename = get_background_path(weather_info)
        if bg_filename:
            bg_path = os.path.join(BG_DIR, bg_filename)
            if os.path.exists(bg_path):
                bg_img = get_cached_bg(bg_path, (SCREEN_WIDTH, SCREEN_HEIGHT))
                img.paste(bg_img, (0, 0))
                stat = ImageStat.Stat(bg_img)
                avg_color = tuple(int(v) for v in stat.mean[:3])
                bg_applied = True
    if not bg_applied:
        try:
            if CLOCK_COLOR:
                bg_color = ImageColor.getrgb(CLOCK_COLOR)
            else:
                hue_shift = (datetime.datetime.now().second / 60.0) % 1.0
                rr, gg, bb = colorsys.hsv_to_rgb(hue_shift, 0.5, 0.2)
                bg_color = (int(rr * 255), int(gg * 255), int(bb * 255))
            img.paste(Image.new("RGB", (SCREEN_WIDTH, SCREEN_HEIGHT), bg_color))
            avg_color = bg_color
        except Exception:
            img.paste(Image.new("RGB", (SCREEN_WIDTH, SCREEN_HEIGHT), "black"))
            avg_color = (0, 0, 0)
    r, g, b = [x / 255.0 for x in avg_color]
    h, s, v = colorsys.rgb_to_hsv(r, g, b)
    base_hue = (h + 0.5) % 1.0
    contrast_saturation = 0.8 + (0.4 * (1.0 - s))
    contrast_brightness = 0.85 if v < 0.5 else 0.25
    palette = []
    for i in range(5):
        hh = (base_hue + i * 0.18) % 1.0
        rr, gg, bb = colorsys.hsv_to_rgb(hh, contrast_saturation, contrast_brightness)
        rr, gg, bb = int(rr * 255), int(gg * 255), int(bb * 255)
        rr = min(max(rr + (128 - avg_color[0]) // 2, 0), 255)
        gg = min(max(gg + (128 - avg_color[1]) // 2, 0), 255)
        bb = min(max(bb + (128 - avg_color[2]) // 2, 0), 255)
        palette.append((rr, gg, bb))
    face_color, notch_color, hour_color, minute_color, second_color = palette
    draw = ImageDraw.Draw(img)
    now = datetime.datetime.now()
    # Always draw a digital clock (we removed analog support in favor of digital-only)
    time_str = now.strftime("%H:%M:%S")
    date_str = now.strftime("%A, %B %d, %Y")
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    time_bbox = get_cached_text_bbox(time_str, LARGE_FONT)
    time_width = time_bbox[2] - time_bbox[0]
    time_height = time_bbox[3] - time_bbox[1]
    time_x = (SCREEN_WIDTH - time_width) // 2
    time_y = (SCREEN_HEIGHT - time_height) // 2 - 30
    date_bbox = get_cached_text_bbox(date_str, MEDIUM_FONT)
    date_width = date_bbox[2] - date_bbox[0]
    date_x = (SCREEN_WIDTH - date_width) // 2
    date_y = time_y + time_height + 20
    img = Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")
    draw = ImageDraw.Draw(img)
    draw_text_aliased(draw, img, (time_x, time_y), time_str, LARGE_FONT, face_color)
    draw_text_aliased(draw, img, (date_x, date_y), date_str, MEDIUM_FONT, notch_color)
    # Optionally show the device IP in small font
    try:
        cfg = config
        if cfg.get('display_ip_on_main', False):
            ip = get_local_ip()
            small_font = ImageFont.truetype(config['fonts']['small_font_path'], config['fonts']['small_font_size'])
            ip_bbox = get_cached_text_bbox(ip, small_font)
            ip_x = 5
            ip_y = SCREEN_HEIGHT - 20
            draw_text_aliased(draw, img, (ip_x, ip_y), ip, small_font, (220, 220, 220))
    except Exception:
        pass
    # Show latest notification if available
    try:
        notifs = globals().get('notifications', [])
        if notifs:
            last_notif = notifs[-1]
            payload = last_notif.get('payload', {})
            message = last_notif.get('message') or payload.get('message') or payload.get('event') or payload.get('state') or payload.get('title') or str(payload.get('message', ''))
            if message:
                notif_text = f"{last_notif.get('source', '')}: {message}" if last_notif.get('source') else message
                notif_font = ImageFont.truetype(config['fonts']['small_font_path'], max(12, int(config['fonts']['small_font_size']*0.9)))
                notif_bbox = get_cached_text_bbox(notif_text, notif_font)
                notif_width = notif_bbox[2] - notif_bbox[0]
                notif_x = SCREEN_WIDTH - notif_width - 8
                notif_y = 8
                draw_text_aliased(draw, img, (notif_x, notif_y), notif_text, notif_font, (255, 255, 255))
    except Exception:
        pass
    # Show a small Wyze snapshot if available
    try:
        wyze_path = os.path.join('static', 'wyze_last.jpg')
        if os.path.exists(wyze_path):
            wyze_img = Image.open(wyze_path).convert('RGB')
            wyze_thumb = get_cached_resized_image(wyze_img, (60, 60), 'RGB')
            img.paste(wyze_thumb, (SCREEN_WIDTH - 70, SCREEN_HEIGHT - 70))
    except Exception:
        pass
    return img

def setup_spotify_oauth():
    return SpotifyOAuth(
        client_id=config["api_keys"]["client_id"],
        client_secret=config["api_keys"]["client_secret"],
        redirect_uri=config["api_keys"]["redirect_uri"],
        scope=SCOPE,
        cache_path=".spotify_cache",
        open_browser=False,
        show_dialog=False
    )

def fetch_and_store_artist_image(sp, artist_id):
    global artist_image
    if not artist_id:
        with artist_image_lock: 
            artist_image = None
        return
    max_retries = 3
    for attempt in range(max_retries):
        try:
            artist = sp.artist(artist_id)
            images = artist.get('images', [])
            if not images:
                with artist_image_lock: 
                    artist_image = None
                return
            url = None
            for img in images:
                if abs(img['width'] - 150) <= 20 and abs(img['height'] - 150) <= 20:
                    url = img['url']
                    break
            if not url:
                url = images[-1]['url']
            if not url:
                with artist_image_lock: 
                    artist_image = None
                return
            headers = {'User-Agent': 'Mozilla/5.0'}
            resp = session.get(url, headers=headers, timeout=10)
            resp.raise_for_status()
            if 'image' not in resp.headers.get('content-type', '').lower(): 
                raise ValueError("Not an image")
            art_bytes = resp.content
            # Offload image processing to process pool
            if process_executor is not None:
                try:
                    fut = process_executor.submit(_process_artist_image_bytes, art_bytes)
                    pending_artist_futures[fut] = {'artist_id': artist_id}
                except Exception:
                    img = Image.open(BytesIO(art_bytes)).convert("RGBA")
                    img = img.resize((100, 100), Image.BILINEAR)
                    with artist_image_lock:
                        artist_image = img
            else:
                img = Image.open(BytesIO(art_bytes)).convert("RGBA")
                img = img.resize((100, 100), Image.BILINEAR)
                with artist_image_lock:
                    artist_image = img
            break
        except Exception as e:
            if attempt < max_retries - 1:
                wait_time = (attempt + 1) * 2
                print(f"🔄 Artist image fetch attempt {attempt + 1} failed: {e}, retrying in {wait_time}s")
                time.sleep(wait_time)
            else:
                if "NoneType" not in str(e):
                    print(f"⚠️ Artist image fetch failed after {max_retries} attempts: {e}")
                with artist_image_lock: 
                    artist_image = None

def write_current_track_state(track_data):
    """Queue a write to the current track state. A background writer thread will perform the write to disk.
    This reduces frequent blocking writes and ensures atomic updates."""
    if not ENABLE_CURRENT_TRACK_DISPLAY:
        return
    try:
        state_data = {}
        if track_data:
            state_data['current_track'] = {
                'title': track_data.get('title', 'Unknown Track') or "Unknown Track",
                'artists': track_data.get('artists', 'Unknown Artist') or "Unknown Artist",
                'album': track_data.get('album', 'Unknown Album') or "Unknown Album",
                'current_position': int(track_data.get('current_position', 0)),
                'duration': int(track_data.get('duration', 0)),
                'is_playing': bool(track_data.get('is_playing', False)),
                'timestamp': time.time()
            }
        else:
            state_data['current_track'] = {
                'title': 'No track playing',
                'artists': '',
                'album': '',
                'current_position': 0,
                'duration': 0,
                'is_playing': False,
                'timestamp': time.time()
            }
        for key, value in state_data['current_track'].items():
            if value is None:
                state_data['current_track'][key] = ""
            elif isinstance(value, str) and '\n' in value:
                state_data['current_track'][key] = value.replace('\n', ' ')
        try:
            state_write_queue.put(state_data, block=False)
        except queue.Full:
            # Replace the existing queued state with the newest one
            try:
                _ = state_write_queue.get_nowait()
            except Exception:
                pass
            try:
                state_write_queue.put(state_data, block=False)
            except Exception:
                pass
    except Exception as e:
        print(f"Critical error queuing track state write: {e}")


def writer_worker():
    """Background thread to actually write state data to disk
    atomically and perform validation."""
    global state_write_queue, file_write_lock
    while not exit_event.is_set():
        try:
            state_data = state_write_queue.get(timeout=1)
        except queue.Empty:
            continue
        try:
            with file_write_lock:
                temp_path = '.current_track_state.toml.tmp'
                try:
                    with open(temp_path, 'w', encoding='utf-8') as f:
                        toml.dump(state_data, f)
                    # Validate the written content
                    if os.path.exists(temp_path):
                        with open(temp_path, 'r', encoding='utf-8') as f:
                            content = f.read().strip()
                        if content:
                            toml.loads(content)
                            os.replace(temp_path, '.current_track_state.toml')
                except Exception as e:
                    print(f"Writer worker error: {e}")
                    try:
                        os.remove(temp_path)
                    except Exception:
                        pass
        finally:
            try:
                state_write_queue.task_done()
            except Exception:
                pass

def initialize_spotify_client():
    sp_oauth = setup_spotify_oauth()
    try:
        token_info = sp_oauth.get_cached_token()
        if not token_info:
            return None
        if sp_oauth.is_token_expired(token_info):
            token_info = sp_oauth.refresh_access_token(token_info['refresh_token'])
        sp = spotipy.Spotify(auth_manager=sp_oauth)
        sp.current_user()
        return sp
    except Exception as e:
        print(f"Spotify authentication failed: {e}")
        return None

def authenticate_spotify_interactive():
    print("\n" + "="*60)
    print("Spotify Authentication Required")
    print("="*60)
    sp_oauth = setup_spotify_oauth()
    auth_url = sp_oauth.get_authorize_url()
    print(f"Please visit this URL to authenticate:")
    print(f"{auth_url}")
    print("\nAfter authorization, you'll be redirected to a URL.")
    print("Paste that full redirect URL here:")
    try:
        redirect_url = input().strip()
        token_info = sp_oauth.get_access_token(redirect_url)
        if token_info:
            sp = spotipy.Spotify(auth_manager=sp_oauth)
            sp.current_user()
            print("✅ Authentication successful!")
            return sp
        else:
            print("❌ Failed to get access token")
            return None
    except Exception as e:
        print(f"❌ Authentication error: {e}")
        return None

def weather_loop():
    global START_SCREEN, weather_info
    lat, lon = None, None
    if USE_GPSD: lat, lon = get_location_via_gpsd(timeout=2)
    if (lat is None or lon is None) and USE_GOOGLE_GEO: lat, lon = get_location_via_google_geolocation(GOOGLE_GEO_API_KEY)
    if lat is None or lon is None:
        lat, lon = get_location_via_openweathermap_geocoding(OPENWEATHER_API_KEY, FALLBACK_CITY)
        if lat is None or lon is None: return
    last_geo = time.time()
    last_weather = 0
    last_display_update = 0
    last_cache_cleanup = time.time()
    GEO_UPDATE_INTERVAL = 900
    while not exit_event.is_set():
        now = time.time()
        if now - last_cache_cleanup > 600:
            cleanup_caches()
            last_cache_cleanup = now
        if now - last_geo > GEO_UPDATE_INTERVAL:
            new_lat, new_lon = None, None
            if USE_GPSD: new_lat, new_lon = get_location_via_gpsd(timeout=2)
            if (new_lat is None or new_lon is None) and USE_GOOGLE_GEO: new_lat, new_lon = get_location_via_google_geolocation(GOOGLE_GEO_API_KEY)
            if new_lat is not None and new_lon is not None: lat, lon = new_lat, new_lon
            last_geo = now
        if now - last_weather > UPDATE_INTERVAL_WEATHER:
            new_weather = get_weather_data_by_coords(OPENWEATHER_API_KEY, lat, lon, "metric")
            if new_weather is not None: 
                weather_info = new_weather
                if weather_info and "icon_id" in weather_info:
                    try:
                        icon_url = f"http://openweathermap.org/img/wn/{weather_info['icon_id']}.png"
                        resp = session.get(icon_url, timeout=5)
                        resp.raise_for_status()
                        icon_img = Image.open(BytesIO(resp.content)).convert("RGBA")
                        icon_img = icon_img.resize((30, 30), Image.BILINEAR)
                        icon_img_bw = icon_img.convert('1')
                        weather_info['cached_icon'] = icon_img_bw
                    except Exception as e:
                        print(f"Weather icon fetch error: {e}")
                        weather_info['cached_icon'] = None
                else:
                    weather_info['cached_icon'] = None
            last_weather = now
        if START_SCREEN == "weather" and now - last_display_update >= 1:
            update_display()
            last_display_update = now
        if exit_event.wait(2):
            break

def animate_text_scroll():
    while not exit_event.is_set():
        if display_sleeping:
            if exit_event.wait(0.5):
                break
            continue
        with scroll_lock:
            for key in scroll_state:
                state = scroll_state[key]
                if state["active"] and state["max_offset"] > 0:
                    state["offset"] += 2
                    if state["offset"] >= state["max_offset"]:
                        state["offset"] = 0
        if exit_event.wait(TEXT_SCROLL_FRAME_TIME):
            break

def animate_images():
    global START_SCREEN, art_pos, art_velocity, artist_pos, artist_velocity, artist_on_top
    last_animation_time = time.time()
    while not exit_event.is_set():
        if display_sleeping:
            if exit_event.wait(0.5):
                break
            continue
        current_time = time.time()
        frame_time = current_time - last_animation_time
        last_animation_time = current_time        
        speed_factor = 0.4
        step_multiplier = 0.8
        needs_update = False
        with art_lock:
            album_img = album_art_image
        if album_img is not None:
            w, h = album_img.size
            x, y = art_pos
            dx, dy = art_velocity
            new_x = x + (dx * frame_time * 60 * speed_factor * step_multiplier)
            new_y = y + (dy * frame_time * 60 * speed_factor * step_multiplier)
            new_dx, new_dy = dx, dy
            if new_x <= 0 or new_x + w >= SCREEN_WIDTH:
                new_dx = -dx
                new_x = max(0.0, min(new_x, float(SCREEN_WIDTH - w)))
            if new_y <= 0 or new_y + h >= SCREEN_HEIGHT:
                new_dy = -dy
                new_y = max(0.0, min(new_y, float(SCREEN_HEIGHT - h)))
            with art_lock:
                art_pos[:] = [new_x, new_y]
                art_velocity[:] = [new_dx, new_dy]
            needs_update = True
        with artist_image_lock:
            artist_img = artist_image
        if artist_img is not None:
            w, h = artist_img.size
            x, y = artist_pos
            dx, dy = artist_velocity
            new_x = x + (dx * frame_time * 60 * speed_factor * step_multiplier)
            new_y = y + (dy * frame_time * 60 * speed_factor * step_multiplier)
            new_dx, new_dy = dx, dy
            bounced = False
            if new_x <= 0 or new_x + w >= SCREEN_WIDTH:
                new_dx = -dx
                new_x = max(0.0, min(new_x, float(SCREEN_WIDTH - w)))
                bounced = True
            if new_y <= 0 or new_y + h >= SCREEN_HEIGHT:
                new_dy = -dy
                new_y = max(0.0, min(new_y, float(SCREEN_HEIGHT - h)))
                bounced = True
            with artist_image_lock:
                artist_pos[:] = [new_x, new_y]
                artist_velocity[:] = [new_dx, new_dy]
            if bounced and random.random() < 0.5:
                artist_on_top = not artist_on_top
                needs_update = True
            needs_update = True
        if needs_update and START_SCREEN == "spotify":
            update_display()
        if exit_event.wait(ANIMATION_FRAME_TIME):
            break

def init_st7789_display():
    global st7789_display
    if not HAS_ST7789: return None
    try:
        st7789_config = config["display"].get("st7789", {})
        config_rotation = config["display"].get("rotation", 0)
        st7789_display = st7789.ST7789(
            port=st7789_config.get("spi_port", 0),
            cs=st7789_config.get("spi_cs", 1),
            dc=st7789_config.get("dc_pin", 9),
            backlight=st7789_config.get("backlight_pin", 13),
            width=320,
            height=240,
            rotation=config_rotation,
            spi_speed_hz=st7789_config.get("spi_speed", 60000000)
        )
        print(f"ST7789 display initialized with rotation: {config_rotation}°")
        return st7789_display
    except Exception as e:
        print(f"ST7789 init failed: {e}")
        return None

def display_image_on_st7789(image):
    global st7789_display
    try:
        if st7789_display is None:
            st7789_display = init_st7789_display()
            if st7789_display is None: return
        scaled_image = get_cached_resized_image(image, (320, 240), 'RGB')
        try:
            buf = scaled_image.tobytes()
            md = hashlib.md5(buf).hexdigest()
            last_hash = getattr(display_image_on_st7789, 'last_image_hash', None)
            if md == last_hash:
                return
            display_image_on_st7789.last_image_hash = md
        except Exception:
            pass
        st7789_display.display(scaled_image)
    except Exception as e:
        print(f"ST7789 display error: {e}")
        display_image_on_original_fb(image)

def display_image_on_original_fb(image):
    try:
        rotation = config["display"].get("rotation", 0)
        if rotation == 180:
            rotated_image = image.rotate(180, expand=False)
        else:
            rotated_image = image.rotate(rotation, expand=True)
        rotated_image = get_cached_resized_image(rotated_image, (SCREEN_WIDTH, SCREEN_HEIGHT), 'RGB')
        arr = np.array(rotated_image, dtype=np.uint8)
        r = _gamma_r[arr[:, :, 0]].astype(np.uint16)
        g = _gamma_g[arr[:, :, 1]].astype(np.uint16)
        b = _gamma_b[arr[:, :, 2]].astype(np.uint16)
        rgb565 = (r << 11) | (g << 5) | b
        output = np.empty((SCREEN_HEIGHT, SCREEN_WIDTH, 2), dtype=np.uint8)
        output[:, :, 0] = rgb565 & 0xFF
        output[:, :, 1] = (rgb565 >> 8) & 0xFF
        # compute md5 of the RGB565 buffer and skip writing if identical
        buf = output.tobytes()
        md = hashlib.md5(buf).hexdigest()
        last_hash = getattr(display_image_on_original_fb, 'last_image_hash', None)
        if md == last_hash:
            return
        display_image_on_original_fb.last_image_hash = md
        with open(FRAMEBUFFER, "wb") as fb:
            fb.write(buf)
    except PermissionError:
        print(f"Permission denied for {FRAMEBUFFER} - falling back to ST7789")
        if HAS_ST7789:
            display_image_on_st7789(image)
        else:
            print("No display available")
    except Exception as e:
        print(f"Framebuffer error: {e}")

def save_current_album_art(album_art_image, track_data=None):
    global last_saved_album_art_hash
    try:
        os.makedirs('static', exist_ok=True)
        if album_art_image is None:
            if os.path.exists('static/current_album_art.jpg'):
                os.remove('static/current_album_art.jpg')
                last_saved_album_art_hash = None
            return
        display_size = (300, 300)
        resized_art = get_cached_resized_image(album_art_image, display_size, 'RGB')
        resized_art.save('static/current_album_art.jpg', 'JPEG', quality=85)
        last_saved_album_art_hash = compute_img_hash(album_art_image)
    except Exception as e:
        print(f"❌ Error saving album art for web: {e}")

def cleanup_album_art():
    try:
        if os.path.exists('static/current_album_art.jpg'):
            os.remove('static/current_album_art.jpg')
    except Exception as e:
        print(f"Error cleaning up album art: {e}")

def find_touchscreen():
    for path in evdev.list_devices():
        dev = evdev.InputDevice(path)
        if "ADS7846" in dev.name or "Touchscreen" in dev.name or "touch" in dev.name.lower(): 
            return dev
    print("Touchscreen not found - touch controls disabled")
    return None

def handle_touch():
    global START_SCREEN
    device = find_touchscreen()
    if device is None:
        while not exit_event.is_set():
            if exit_event.wait(1):
                break
        return
    screen_order = ["weather", "spotify", "time"]
    for event in device.read_loop():
        if exit_event.is_set():
            break
        if event.type == evdev.ecodes.EV_KEY and event.code == evdev.ecodes.BTN_TOUCH and event.value == 1:
            current_index = screen_order.index(START_SCREEN)
            START_SCREEN = screen_order[(current_index + 1) % len(screen_order)]
            update_activity()
            update_display()

def handle_buttons():
    global START_SCREEN
    if not HAS_GPIO:
        print("GPIO not available - button controls disabled")
        while not exit_event.is_set():
            time.sleep(1)
        return
    GPIO.setmode(GPIO.BCM)
    buttons = [BUTTON_A, BUTTON_B, BUTTON_X, BUTTON_Y]
    for button in buttons:
        try:
            GPIO.setup(button, GPIO.IN, pull_up_down=GPIO.PUD_UP)
        except Exception as e:
            if "busy" in str(e).lower():
                print(f"⚠️ GPIO {button} already in use, skipping button setup.")
                return
            else:
                raise
    print("Button handler started:")
    print("A: Switch screens, B: Switch screens, X: Reset art, Y: Toggle time")
    while not exit_event.is_set():
        for button in buttons:
            try:
                if GPIO.input(button) == GPIO.LOW:
                    current_time = time.time()
                    if current_time - button_last_press[button] > DEBOUNCE_TIME:
                        button_last_press[button] = current_time
                        update_activity()
                        if button == BUTTON_A:
                            START_SCREEN = "spotify"
                            update_display()
                        elif button == BUTTON_B:
                            START_SCREEN = "weather"
                            update_display()
                        elif button == BUTTON_X:
                            if START_SCREEN == "time":
                                update_display()
                        elif button == BUTTON_Y:
                            global TIME_DISPLAY
                            TIME_DISPLAY = not TIME_DISPLAY
                            update_display()
            except Exception:
                pass
        time.sleep(0.1)
    GPIO.cleanup()

def init_waveshare_display():
    global waveshare_epd, waveshare_base_image, partial_refresh_count
    if not HAS_WAVESHARE_EPD:
        return None
    try:
        print("Attempting to initialize Waveshare display...")
        waveshare_epd = EPD()
        print("Initializing display...")
        waveshare_epd.init()
        waveshare_epd.Clear(0xFF)
        waveshare_base_image = Image.new('1', (250, 122), 255)
        partial_refresh_count = 0
        print("✅ Waveshare e-paper display initialized successfully")
        return waveshare_epd
    except Exception as e:
        print(f"❌ Waveshare display init failed: {e}")
        import traceback
        traceback.print_exc()
        return None

def load_previous_track_state():
    global previous_track_id
    previous_track_id = None
    try:
        if os.path.exists('.current_track_state.toml'):
            with open('.current_track_state.toml', 'r') as f:
                previous_state = toml.load(f)
                previous_track = previous_state.get('current_track', {})
                if previous_track.get('title') and previous_track.get('title') != 'No track playing':
                    previous_track_id = f"{previous_track.get('title', '')}_{previous_track.get('artists', '')}"
    except Exception as e:
        pass

def initialize_spotify_client_or_auth():
    global sp, spotify_track
    sp = initialize_spotify_client()
    if sp is None:
        sp = authenticate_spotify_interactive()
    if sp is None:
        spotify_track = {
            "title": "Spotify Authentication Required",
            "artists": "Authentication failed - restart to retry",
            "album": "HUD Setup",
            "current_position": 0,
            "duration": 1,
            "is_playing": False,
            "main_color": (255, 100, 100),
            "secondary_color": (200, 100, 100)
        }
        update_spotify_layout(spotify_track)
        if START_SCREEN == "spotify":
            update_display()
        return False
    return True

def handle_no_track_playing(current_time, last_successful_write, write_interval):
    global spotify_track, consecutive_no_track_count
    consecutive_no_track_count += 1
        if ENABLE_LASTFM_SCROBBLE and spotify_track is not None:
            # Scrobble if a track was previously playing and met threshold
            try:
                if spotify_track and spotify_track.get('is_playing', False):
                    duration = int(spotify_track.get('duration', 0))
                    position = int(spotify_track.get('current_position', 0))
                    # require both min seconds and percent threshold
                    if duration > 0 and position >= int(duration * LASTFM_SCROBBLE_THRESHOLD) and position >= LASTFM_MIN_SECONDS:
                        ts = int(time.time()) - int(position)
                        scrobble_to_lastfm(spotify_track, timestamp=ts)
                    else:
                        print(f"ℹ️ Skipping scrobble: played {position}s of {duration}s (<{int(LASTFM_SCROBBLE_THRESHOLD*100)}% or <{LASTFM_MIN_SECONDS}s) ")
            except Exception:
                pass
        spotify_track = None
        with art_lock: 
            album_art_image = None
            current_album_art_hash = None
        cleanup_album_art()
        if current_time - last_successful_write >= write_interval:
            write_current_track_state(None)
            last_successful_write = current_time
        update_spotify_layout(None)
        if START_SCREEN == "spotify":
            update_display()
    return last_successful_write

def fetch_and_process_album_art(art_url, spotify_track, item, is_continuation):
    global last_art_url, album_art_image
    if not is_continuation:
        try:
            # derive artist and album names for fallback lookups
            artists_list = [a['name'] for a in item.get('artists', [])]
            artist_str = ", ".join(artists_list) if artists_list else None
            album_str = item.get('album', {}).get('name') if item.get('album') else None
            # If no Spotify art URL, try MusicBrainz / Cover Art Archive fallback
            if not art_url:
                try:
                    mb_url = get_musicbrainz_cover_art(artist_str, album_str)
                    if mb_url:
                        art_url = mb_url
                except Exception:
                    pass
            if art_url:
                max_retries = 2
                for art_attempt in range(max_retries):
                    try:
                        headers = {'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36'}
                        resp = session.get(art_url, headers=headers, timeout=15)
                        resp.raise_for_status()
                        img = Image.open(BytesIO(resp.content)).convert("RGB")
                        img.thumbnail((150, 150), Image.NEAREST)
                        # compute hash early to avoid repeated background generation
                        try:
                            current_album_art_hash = compute_img_hash(img)
                        except Exception:
                            current_album_art_hash = None
                        # Save a copy to disk & request background generation immediately
                        save_current_album_art(img)
                        request_background_generation(img)
                        with album_bg_cache_lock:
                            album_bg_cache.clear()
                        # Offload color extraction and final thumbnail retainment to process pool
                        bio = BytesIO(); img.save(bio, format='PNG'); art_bytes = bio.getvalue()
                        if process_executor is not None:
                            try:
                                fut = process_executor.submit(_process_album_art_bytes, art_bytes, (150,150))
                                pending_album_futures[fut] = {'item': item}
                            except Exception:
                                # fallback: compute inline
                                img_bytes, main_color, secondary_color = _process_album_art_bytes(art_bytes, (150,150))
                                if img_bytes:
                                    img = Image.open(BytesIO(img_bytes)).convert('RGB')
                                    with art_lock:
                                        album_art_image = img
                                spotify_track['main_color'] = main_color
                                spotify_track['secondary_color'] = secondary_color
                        else:
                            img_bytes, main_color, secondary_color = _process_album_art_bytes(art_bytes, (150,150))
                            if img_bytes:
                                img = Image.open(BytesIO(img_bytes)).convert('RGB')
                                with art_lock:
                                    album_art_image = img
                            spotify_track['main_color'] = main_color
                            spotify_track['secondary_color'] = secondary_color
                        break
                    except Exception as e:
                        if art_attempt < max_retries - 1:
                            wait_time = (art_attempt + 1) * 2
                            print(f"🔄 Album art fetch attempt {art_attempt + 1} failed: {e}, retrying in {wait_time}s")
                            time.sleep(wait_time)
                        else:
                            # Final failure on initial art_url attempts - try MusicBrainz fallback
                            try:
                                mb_url = None
                                if artist_str and album_str:
                                    mb_url = get_musicbrainz_cover_art(artist_str, album_str)
                                if mb_url and mb_url != art_url:
                                    # try one more time with fallback
                                    headers = {'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36'}
                                    r = session.get(mb_url, headers=headers, timeout=15)
                                    r.raise_for_status()
                                    img = Image.open(BytesIO(r.content)).convert('RGB')
                                    img.thumbnail((150, 150), Image.NEAREST)
                                    try:
                                        current_album_art_hash = compute_img_hash(img)
                                    except Exception:
                                        current_album_art_hash = None
                                    save_current_album_art(img)
                                    request_background_generation(img)
                                    with album_bg_cache_lock:
                                        album_bg_cache.clear()
                                    bio = BytesIO(); img.save(bio, format='PNG'); art_bytes = bio.getvalue()
                                    if process_executor is not None:
                                        try:
                                            fut = process_executor.submit(_process_album_art_bytes, art_bytes, (150,150))
                                            pending_album_futures[fut] = {'item': item}
                                        except Exception:
                                            img_bytes, main_color, secondary_color = _process_album_art_bytes(art_bytes, (150,150))
                                            if img_bytes:
                                                img = Image.open(BytesIO(img_bytes)).convert('RGB')
                                                with art_lock:
                                                    album_art_image = img
                                            spotify_track['main_color'] = main_color
                                            spotify_track['secondary_color'] = secondary_color
                                            try:
                                                evt = {'type': 'cover_fallback', 'artist': artist_str, 'album': album_str}
                                                if executor is not None:
                                                    executor.submit(post_overlay_event, evt)
                                                else:
                                                    post_overlay_event(evt)
                                            except Exception:
                                                pass
                                    else:
                                        img_bytes, main_color, secondary_color = _process_album_art_bytes(art_bytes, (150,150))
                                        if img_bytes:
                                            img = Image.open(BytesIO(img_bytes)).convert('RGB')
                                            with art_lock:
                                                album_art_image = img
                                        spotify_track['main_color'] = main_color
                                        spotify_track['secondary_color'] = secondary_color
                                        try:
                                            evt = {'type': 'cover_fallback', 'artist': artist_str, 'album': album_str}
                                            if executor is not None:
                                                executor.submit(post_overlay_event, evt)
                                            else:
                                                post_overlay_event(evt)
                                        except Exception:
                                            pass
                                else:
                                    print(f"❌ Album art fetch failed after {max_retries} attempts: {e}")
                                    with art_lock: 
                                        album_art_image = None
                                        current_album_art_hash = None
                                    spotify_track['main_color'] = (0, 255, 0)
                                    spotify_track['secondary_color'] = (0, 255, 255)
                            except Exception as e2:
                                print(f"❌ Album art fetch failed after fallback: {e2}")
                                with art_lock: 
                                    album_art_image = None
                                    current_album_art_hash = None
                                spotify_track['main_color'] = (0, 255, 0)
                                spotify_track['secondary_color'] = (0, 255, 255)
                last_art_url = art_url
                save_current_album_art(img)
            else:
                with art_lock: 
                    album_art_image = None
                    current_album_art_hash = None
                spotify_track['main_color'] = (0, 255, 0)
                spotify_track['secondary_color'] = (0, 255, 255)
                save_current_album_art(None)
        except Exception as e:
            print(f"❌ Error loading album art: {e}")
            with art_lock: 
                album_art_image = None
                current_album_art_hash = None
            spotify_track['main_color'] = (0, 255, 0)
            spotify_track['secondary_color'] = (0, 255, 255)
        update_spotify_layout(spotify_track)
        scrolling_text_cache.clear()
        setup_scrolling_text_for_track(spotify_track)
        if item.get('artists') and len(item['artists']) > 0:
            primary_artist_id = item['artists'][0]['id']
            try:
                executor.submit(fetch_and_store_artist_image, sp, primary_artist_id)
            except Exception:
                Thread(target=fetch_and_store_artist_image, args=(sp, primary_artist_id), daemon=True).start()
    else:
        if album_art_image:
            main_color, secondary_color = get_contrasting_colors(album_art_image)
            spotify_track['main_color'] = main_color
            spotify_track['secondary_color'] = secondary_color
        else:
            spotify_track['main_color'] = (0, 255, 0)
            spotify_track['secondary_color'] = (0, 255, 255)
        update_spotify_layout(spotify_track)

def setup_scrolling_text_for_track(track_data):
    for key in ['title', 'artists', 'album']:
        data = track_data.get(key, "")
        if data:
            text_bbox = get_cached_text_bbox(data, SPOT_MEDIUM_FONT)
            text_width = text_bbox[2] - text_bbox[0]
            label_text = "Track:" if key == "title" else "Artists:" if key == "artists" else "Album:"
            label_bbox = get_cached_text_bbox(label_text, SPOT_MEDIUM_FONT)
            label_width = label_bbox[2] - label_bbox[0]
            visible_width = SCREEN_WIDTH - 5 - label_width - 6
            if text_width > visible_width:
                scrolling_img = create_scrolling_text_image(data, SPOT_MEDIUM_FONT, track_data['main_color'], text_width * 2 + 50)
                scrolling_text_cache[key] = scrolling_img
                with scroll_lock:
                    scroll_state[key]["active"] = True
                    scroll_state[key]["max_offset"] = text_width + 50
                    scroll_state[key]["offset"] = 0
            else:
                with scroll_lock:
                    scroll_state[key]["active"] = False
                    scroll_state[key]["max_offset"] = 0
                    scroll_state[key]["offset"] = 0

def handle_track_update(current_time, last_successful_write, write_interval, track, last_track_id, is_first_track_after_startup, previous_track_id):
    global spotify_track, consecutive_no_track_count, last_art_url
    consecutive_no_track_count = 0
    item = track['item']
    current_id = item.get('id')
    artists_list = [artist['name'] for artist in item.get('artists', [])]
    artist_str = ", ".join(artists_list) if artists_list else "Unknown Artist"
    album_str = item['album']['name'] if item.get('album') else "Unknown Album"
    current_position = track.get('progress_ms', 0) // 1000
    duration = item.get('duration_ms', 0) // 1000
    is_playing = track.get('is_playing', False)
    new_track = {
        "title": item.get('name', "Unknown Track"),
        "artists": artist_str,
        "album": album_str,
        "current_position": current_position,
        "duration": duration,
        "is_playing": is_playing
    }
    current_track_id = f"{new_track['title']}_{new_track['artists']}"
    is_continuation = (is_first_track_after_startup and previous_track_id and current_track_id == previous_track_id)
    track_changed = current_id != last_track_id or spotify_track is None
    art_url = None
    if item.get('album') and item['album'].get('images'):
        art_url = item['album']['images'][0]['url']
    art_changed = art_url != last_art_url
    if track_changed or art_changed or spotify_track is None:
        # Scrobble previously playing track before switching
        old_track_copy = copy.deepcopy(spotify_track) if spotify_track else None
        if ENABLE_LASTFM_SCROBBLE and old_track_copy and old_track_copy.get('is_playing', False):
            try:
                duration = int(old_track_copy.get('duration', 0))
                position = int(old_track_copy.get('current_position', 0))
                # require both min seconds and threshold percent
                if duration > 0 and position >= int(duration * LASTFM_SCROBBLE_THRESHOLD) and position >= LASTFM_MIN_SECONDS:
                    ts = int(time.time()) - int(position)
                    scrobble_to_lastfm(old_track_copy, timestamp=ts)
                else:
                    # Optionally print debug info for non-scrobbled tracks
                    print(f"ℹ️ Skipping scrobble: played {position}s of {duration}s (<{int(LASTFM_SCROBBLE_THRESHOLD*100)}% or <{LASTFM_MIN_SECONDS}s) ")
            except Exception:
                pass
        spotify_track = new_track
        update_activity()
        if current_time - last_successful_write >= write_interval:
            write_current_track_state(spotify_track)
            last_successful_write = current_time
        fetch_and_process_album_art(art_url, spotify_track, item, is_continuation)
        try:
            if lfm and spotify_track and spotify_track.get('is_playing', False):
                report_now_playing_to_lastfm(spotify_track)
        except Exception:
            pass
        # Post track change to overlay event stream
        try:
            evt = {'type': 'track_change', 'title': spotify_track.get('title', None), 'artist': spotify_track.get('artists', None)}
            if executor is not None:
                executor.submit(post_overlay_event, evt)
            else:
                post_overlay_event(evt)
        except Exception:
            pass
        last_track_id = current_id
        is_first_track_after_startup = False
        if START_SCREEN == "spotify":
            update_display()
    else:
        old_playing_state = spotify_track.get('is_playing', False) if spotify_track else False
        spotify_track['current_position'] = current_position
        spotify_track['is_playing'] = is_playing
        playing_state_changed = is_playing != old_playing_state
        if playing_state_changed and is_playing:
            update_activity()
        should_write = False
        if playing_state_changed:
            should_write = True
        elif is_playing and current_time - last_successful_write >= write_interval:
            should_write = True
        elif not is_playing and current_time - last_successful_write >= 0.5:
            should_write = True
        if START_SCREEN == "spotify":
            update_display()
        if should_write:
            write_current_track_state(spotify_track)
            last_successful_write = current_time
    return last_successful_write, last_track_id, is_first_track_after_startup

def handle_spotify_api_errors(e, api_error_count):
    global spotify_track, last_api_call
    if e.http_status == 429:
        print("⚠️ Spotify API rate limit hit, backing off for 60 seconds...")
        time.sleep(60)
        last_api_call = time.time() 
    elif e.http_status == 401:
        print("🔑 Spotify token expired - spotipy should handle refresh automatically")
        last_api_call = time.time()
    else:
        print(f"🎵 Spotify API error (attempt {api_error_count}): {e}")
        last_api_call = time.time()
    return True
                if art_url:  # This line is unchanged
def spotify_loop():
                        # Offload color extraction and final thumbnail retainment to process pool  # This line is unchanged
    last_successful_write = 0
                        if process_executor is not None:  # This line is unchanged
    base_track_check_interval = 2
                        else:  # This line is unchanged
    last_api_call = 0
                            spotify_track['main_color'] = main_color  # This line is unchanged
    consecutive_no_track_count = 0
                            spotify_track['secondary_color'] = secondary_color  # This line is unchanged
    current_check_interval = base_track_check_interval
                else:  # This line is unchanged
    load_previous_track_state()
                with art_lock:  # This line is unchanged
    if not initialize_spotify_client_or_auth():
                    spotify_track['main_color'] = (0, 255, 0)  # This line is unchanged
    last_track_id = None
        # Initialize last.fm client lazily and send now playing/scrobble updates  # This line is unchanged
    api_error_count = 0
    while not exit_event.is_set():
        current_time = time.time()
        if api_error_count > 0:
            current_check_interval = min(10 * (2 ** min(api_error_count-1, 2)), 60)
        elif spotify_track and spotify_track.get('is_playing', False):
            current_check_interval = base_track_check_interval
        except Exception:  # This line is unchanged
            consecutive_no_track_count = 0
        else:
            if consecutive_no_track_count >= max_consecutive_no_track:
                current_check_interval = idle_check_interval
            else:
                current_check_interval = base_track_check_interval
        time_since_last_api = current_time - last_api_call
        if time_since_last_api < current_check_interval:
            time.sleep(0.1)
            continue
        try:
            last_api_call = current_time
            track = sp.current_user_playing_track()
            api_error_count = 0
            if not track or not track.get('item'):
                last_successful_write = handle_no_track_playing(current_time, last_successful_write, write_interval)
                continue
            last_successful_write, last_track_id, is_first_track_after_startup = handle_track_update(current_time, last_successful_write, write_interval, track, last_track_id, is_first_track_after_startup, previous_track_id)
        except spotipy.exceptions.SpotifyException as e:
            api_error_count += 1
            if not handle_spotify_api_errors(e, api_error_count):
                return
        except requests.exceptions.Timeout:
            api_error_count += 1
            print(f"⏰ Spotify API timeout (attempt {api_error_count}), will retry with backoff")
            last_api_call = time.time()
        except requests.exceptions.ConnectionError as e:
            api_error_count += 1
            if api_error_count >= 3:
                if "Connection reset" in str(e) or "Connection aborted" in str(e):
                    print(f"🔄 Connection reset during token refresh (attempt {api_error_count}), retrying...")
                else:
                    print(f"🔌 Spotify connection error (attempt {api_error_count}): {e}")
            # Always pause briefly on connection errors
            if "Connection reset" in str(e) or "Connection aborted" in str(e):
                time.sleep(2)
            last_api_call = time.time()
        except Exception as e:
            api_error_count += 1
            print(f"❌ Unexpected Spotify error (attempt {api_error_count}): {e}")
            last_api_call = time.time()

def convert_to_1bit_dithered(album_art_img, size=(40, 40)):
    if album_art_img is None:
        return None
    small_img = album_art_img.resize(size, Image.BILINEAR)
    gray_img = small_img.convert('L')
    bw_img = gray_img.convert('1')
    return bw_img

def display_image_on_waveshare(image):
    global waveshare_epd, waveshare_base_image, partial_refresh_count
    with waveshare_lock:
        if waveshare_epd is None:
            if not init_waveshare_display():
                return
        try:
            display_width = 250
            display_height = 122
            # Use cached dithered conversion at the target display size
            image_bw = get_cached_dithered_image(image, size=(display_width, display_height))
            if image_bw is None:
                # fallback to a synchronous conversion to avoid display gaps
                image_bw = convert_to_1bit_dithered(image, size=(display_width, display_height))
            image = image_bw
            content_changed = getattr(image, 'content_changed', True)
            try:
                buf = image.tobytes()
                md = hashlib.md5(buf).hexdigest()
                last_hash = getattr(display_image_on_waveshare, 'last_image_hash', None)
                if md == last_hash and partial_refresh_count < 300:
                    waveshare_epd.displayPartial(waveshare_epd.getbuffer(image))
                    partial_refresh_count += 1
                else:
                    waveshare_epd.display(waveshare_epd.getbuffer(image))
                    display_image_on_waveshare.last_image_hash = md
                    partial_refresh_count = 0
            except Exception:
                if content_changed or partial_refresh_count >= 300:
                    waveshare_epd.display(waveshare_epd.getbuffer(image))
                waveshare_base_image = image.copy()
                partial_refresh_count = 0
            else:
                waveshare_epd.displayPartial(waveshare_epd.getbuffer(image))
                partial_refresh_count += 1
        except Exception as e:
            print(f"Waveshare display error: {e}")
            try:
                waveshare_epd.init()
                waveshare_epd.display(waveshare_epd.getbuffer(image))
                waveshare_base_image = image.copy()
                partial_refresh_count = 0
            except Exception as e2:
                print(f"Failed to reset waveshare display: {e2}")

def display_image_on_framebuffer(image):
    global last_display_time
    now = time.time()
    if now - last_display_time < MIN_DISPLAY_INTERVAL: 
        return
    last_display_time = now
    display_type = config.get("display", {}).get("type", "framebuffer")
    if display_type == "dummy":
        display_image_on_dummy()
    elif display_type == "st7789" and HAS_ST7789:
        display_image_on_st7789(image)
    elif display_type == "waveshare_epd" and HAS_WAVESHARE_EPD:
        display_image_on_waveshare(image)
    else:
        display_image_on_original_fb(image)

def update_display():
    global START_SCREEN
    display_type = config.get("display", {}).get("type", "framebuffer")
    if display_type == "waveshare_epd" and HAS_WAVESHARE_EPD:
        img = draw_waveshare(weather_info, spotify_track)
    else:
        if START_SCREEN == "weather":
            img = draw_weather_image(weather_info)
        elif START_SCREEN == "spotify":
            if display_sleeping:
                return
            img = draw_spotify_image(spotify_track)
        elif START_SCREEN == "time":
            img = draw_clock_image()
        else:
            img = draw_clock_image()
    display_image_on_framebuffer(img)

def clear_framebuffer():
    global HAS_ST7789
    display_type = config.get("display", {}).get("type", "framebuffer")
    if display_type == "dummy":
        return
    elif display_type == "waveshare_epd" and HAS_WAVESHARE_EPD:
        try:
            from waveshare_epd.epd2in13_V3 import EPD
            epd = EPD()
            epd.init()
            white_img = Image.new('1', (250, 122), 255)
            epd.display(epd.getbuffer(white_img))
            epd.sleep()
            return
        except Exception as e:
            try:
                from waveshare_epd.epd2in13_V3 import EPD
                epd = EPD()
                epd.init()
                epd.Clear(0xFF)
                epd.sleep()
            except Exception as e2:
                print(f"Failed to clear waveshare display: {e2}")
    elif display_type == "st7789" and HAS_ST7789 and st7789_display:
        black_img = Image.new("RGB", (320, 240), "black")
        st7789_display.display(black_img)
    else:
        try:
            black_image = Image.new("RGB", (SCREEN_WIDTH, SCREEN_HEIGHT), "black")
            display_image_on_original_fb(black_image)
            try:
                with open(FRAMEBUFFER, "wb") as f:
                    black_pixel = b'\x00\x00'
                    f.write(black_pixel * SCREEN_WIDTH * SCREEN_HEIGHT)
                    f.flush()
                    os.fsync(f.fileno())
            except Exception as e:
                print(f"Direct framebuffer write failed: {e}")
            print(f"✅ Framebuffer cleared: {FRAMEBUFFER}")
        except Exception as e:
            print(f"❌ Error clearing framebuffer: {e}")
            if HAS_ST7789:
                try:
                    black_img = Image.new("RGB", (320, 240), "black")
                    display_image_on_st7789(black_img)
                except:
                    pass

def cleanup_scroll_state():
    with scroll_lock:
        for key in scroll_state:
            scroll_state[key]["offset"] = 0
            scroll_state[key]["active"] = False

def display_image_on_dummy():
    pass

def update_activity():
    global last_activity_time, display_sleeping
    last_activity_time = time.time()
    if display_sleeping:
        display_sleeping = False

def check_sleep_state():
    global display_sleeping, START_SCREEN
    if START_SCREEN != "spotify":
        if display_sleeping:
            wake_up_display()
        return
    current_time = time.time()
    music_playing = spotify_track and spotify_track.get('is_playing', False)
    if display_sleeping:
        if music_playing:
            wake_up_display()
    else:
        if not music_playing and current_time - last_activity_time >= SLEEP_TIMEOUT:
            go_to_sleep()

def wake_up_display():
    global display_sleeping
    if display_sleeping:
        display_sleeping = False
        update_activity()
        update_display()

def go_to_sleep():
    global display_sleeping, last_display_time
    if not display_sleeping:
        display_sleeping = True
        print(f"🛌 Display sleeping due to {SLEEP_TIMEOUT}s of no playback")
        time.sleep(0.05)
        last_display_time = 0
        clear_framebuffer()

def sleep_monitor_loop():
    last_sleep_check = 0
    while not exit_event.is_set():
        current_time = time.time()
        if display_sleeping:
            if current_time - last_sleep_check >= WAKEUP_CHECK_INTERVAL:
                check_sleep_state()
                last_sleep_check = current_time
            time.sleep(1)
        else:
            check_sleep_state()
            last_sleep_check = current_time
            if exit_event.wait(5):
                break

def signal_handler(sig, frame):
    print(f"Received signal {sig}, shutting down quickly...")
    exit_event.set()

def main():
    global START_SCREEN, spotify_track
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    Thread(target=background_generation_worker, daemon=True).start()
    Thread(target=writer_worker, daemon=True).start()
    # Initialize optional clients
    if ENABLE_LASTFM_SCROBBLE:
        init_lastfm_client()
    Thread(target=weather_loop, daemon=True).start()
    Thread(target=spotify_loop, daemon=True).start()
    Thread(target=handle_touch, daemon=True).start()
    Thread(target=handle_buttons, daemon=True).start()
    Thread(target=animate_images, daemon=True).start()
    Thread(target=animate_text_scroll, daemon=True).start()
    Thread(target=sleep_monitor_loop, daemon=True).start() 
    Thread(target=perf_monitor_loop, daemon=True).start()
    if USE_PILLOW_SIMD:
        print("✅ pillow-simd detected: image operations are optimized")
    update_display()
    screen_update_intervals = {
        "weather": 30.0,
        "spotify": 0.5,
        "time": 1.0
    }
    last_display_update = 0
    try:
        while not exit_event.is_set():
            current_time = time.time()
            current_interval = screen_update_intervals.get(START_SCREEN, 1.0)
            if not display_sleeping and current_time - last_display_update >= current_interval:
                update_display()
                last_display_update = current_time
            if exit_event.wait(0.1):
                break
    except KeyboardInterrupt:
        print("\nShutting down...")
    finally:
        print("🔄 Starting cleanup...")
        original_screen = START_SCREEN
        START_SCREEN = "spotify"
        spotify_track = None
        update_spotify_layout(None)
        try:
            update_display()
            time.sleep(0.3)
        except:
            pass
        cleanup_scroll_state()
        exit_event.set()
        try:
            executor.shutdown(wait=False)
        except Exception:
            pass
        try:
            if process_executor is not None:
                process_executor.shutdown(wait=False)
        except Exception:
            pass
        time.sleep(0.5)
        clear_framebuffer()
        if HAS_GPIO:
            try:
                GPIO.cleanup()
            except:
                pass
        print("✅ Cleanup complete")

if __name__ == "__main__":
    main()