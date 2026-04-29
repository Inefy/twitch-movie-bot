# StreamCinema Vote Bot / bot.py
# Updated code incorporating fixes for obs-websocket-py v5 and other improvements

import os
import asyncio
import requests
import json
from collections.abc import Awaitable
from moviepy.editor import VideoFileClip
from twitchio.ext import commands, routines
from twitchio.errors import AuthenticationError
# Import v5 obswebsocket components
from obswebsocket import obsws, requests as obs_requests
import obswebsocket.exceptions as obs_exceptions # OBS Exception classes
import random
from datetime import timedelta
import logging
import sys
import time
import shutil
from contextlib import suppress

# Import configuration and constant
from config import (
    TWITCH_CLIENT_ID,
    TWITCH_CLIENT_SECRET,
    OBS_WEBSOCKET_IP,
    OBS_WEBSOCKET_PORT,
    OBS_WEBSOCKET_PASSWORD,
    MOVIE_DIRECTORY,
    CHANNEL_NAME,
    SCENE_NAME,
    MEDIA_SOURCE_NAME,
    DEFAULT_CONFIG,
    get_tokens,
    save_tokens
)

# Valid movie file extensions
MOVIE_EXTENSIONS = (
    ".mp4", ".mkv", ".avi", ".mov", ".flv", ".wmv",
    ".mpeg", ".mpg", ".m4v", ".divx", ".xvid",
    ".vob", ".ts", ".webm", ".rm", ".asf",
)

OBS_MEDIA_ACTION_RESTART = "OBS_WEBSOCKET_MEDIA_INPUT_ACTION_RESTART"

# --- Logging Setup ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - [%(funcName)s] %(message)s',
    handlers=[
        logging.FileHandler("bot.log", encoding='utf-8'),
        logging.StreamHandler(sys.stdout)
    ]
)

# --- Helper Functions ---

def _get_movie_basename(path):
    """Safely gets the movie basename without extension."""
    if not path:
        return "Unknown Movie"
    try:
        return os.path.splitext(os.path.basename(path))[0]
    except Exception as e:
        logging.error(f"Error getting basename for path '{path}': {e}")
        return "Error Getting Name"

def _is_movie_file(name):
    """Return True if the file name has a valid movie extension."""
    return name.lower().endswith(MOVIE_EXTENSIONS)


def _get_runtime_env(var_name, fallback=None):
    """Read the latest environment value, falling back to imported defaults."""
    value = os.getenv(var_name)
    if value is None:
        value = fallback
    if isinstance(value, str):
        value = value.strip()
    return value


def _normalize_access_token(token):
    """Return a Twitch OAuth access token without the IRC-only oauth: prefix."""
    if not token:
        return token
    token = str(token).strip()
    return token[6:] if token.lower().startswith("oauth:") else token


def _is_bot_closed(bot_obj) -> bool:
    """Support both property-style and method-style ``is_closed`` APIs."""
    if bool(getattr(bot_obj, "_shutdown_requested", False)):
        return True

    closing_state = getattr(bot_obj, "_closing", None)
    if hasattr(closing_state, "is_set"):
        try:
            return bool(closing_state.is_set())
        except Exception:
            return False

    status = getattr(bot_obj, "is_closed", False)
    try:
        return status() if callable(status) else bool(status)
    except Exception:
        return False

# --- Movie Scanning ---
def scan_movies(directory=MOVIE_DIRECTORY):
    """Scans the specified directory (and one level subdirectory) for valid movie files."""
    paths = []
    logging.info(f"Scanning for movies in: {directory}")
    if not os.path.isdir(directory):
        logging.error(f"Movie directory not found: {directory}")
        return []

    try:
        with os.scandir(directory) as entries:
            for entry in entries:
                if entry.is_file(follow_symlinks=False) and _is_movie_file(entry.name):
                    paths.append(os.path.normpath(entry.path))
                elif entry.is_dir(follow_symlinks=False):
                    logging.debug(f"Scanning sub-directory: {entry.path}")
                    try:
                        with os.scandir(entry.path) as sub_entries:
                            for sub_entry in sub_entries:
                                if sub_entry.is_file(follow_symlinks=False) and _is_movie_file(sub_entry.name):
                                    paths.append(os.path.normpath(sub_entry.path))
                    except Exception as e:
                        logging.error(f"Error scanning sub-directory {entry.path}: {e}")

        logging.info(f"Found {len(paths)} movies.")
    except Exception as e:
        logging.error(f"Error scanning movie directory {directory}: {e}")
    return paths

async def get_movie_duration(path):
    """Gets the duration of a video file in seconds.

    The function first attempts to obtain the duration using ``ffprobe`` for
    accuracy.  If ``ffprobe`` is not available or fails, it falls back to
    MoviePy.  Should both methods fail or return a non-positive duration, a
    default duration from the configuration is returned.
    """
    normalized_path = os.path.normpath(path)
    logging.info(f"Getting duration for: {normalized_path}")

    async def _run_ffprobe(file_path):
        cmd = [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            file_path,
        ]
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(stderr.decode().strip())
        return int(float(stdout.decode().strip()))

    def _get_duration_moviepy(file_path):
        with VideoFileClip(file_path) as clip:
            if not clip.duration or clip.duration <= 0:
                raise ValueError("Invalid duration from moviepy")
            return int(clip.duration)

    # Try ffprobe first if available
    if shutil.which("ffprobe"):
        try:
            duration = await _run_ffprobe(normalized_path)
            if duration > 0:
                logging.info(f"Duration for {_get_movie_basename(path)}: {duration}s")
                return duration
        except Exception as e:
            logging.warning(f"ffprobe failed for {path}: {e}")
    else:
        logging.warning("ffprobe executable not found; skipping ffprobe duration check")

    # Fallback to moviepy
    try:
        duration = await asyncio.to_thread(_get_duration_moviepy, normalized_path)
        if duration > 0:
            logging.info(f"Duration for {_get_movie_basename(path)}: {duration}s")
            return duration
        raise ValueError("Non-positive duration from moviepy")
    except Exception as e:
        logging.error(f"Error getting duration for {path}: {e}. Using default.")
        return DEFAULT_CONFIG['DEFAULT_MOVIE_DURATION']

# --- Twitch Token Management ---
async def refresh_access_token(refresh_token_param):
    """Refreshes the Twitch access token using the refresh token.

    Returns (access_token, refresh_token). The caller can validate/track expiry separately.
    """
    logging.info("Attempting to refresh Twitch access token...")
    if not refresh_token_param:
        logging.error("Cannot refresh token: No refresh token provided.")
        return None, None

    client_id = _get_runtime_env("TWITCH_CLIENT_ID", TWITCH_CLIENT_ID)
    client_secret = _get_runtime_env("TWITCH_CLIENT_SECRET", TWITCH_CLIENT_SECRET)
    if not client_id or not client_secret:
        logging.error("Cannot refresh token: TWITCH_CLIENT_ID or TWITCH_CLIENT_SECRET is missing.")
        return None, refresh_token_param

    url = 'https://id.twitch.tv/oauth2/token'
    payload = {
        'client_id': client_id,
        'client_secret': client_secret,
        'grant_type': 'refresh_token',
        'refresh_token': refresh_token_param
    }
    max_retries = DEFAULT_CONFIG.get('TOKEN_REFRESH_RETRIES', 3)
    retry_delay = DEFAULT_CONFIG.get('TOKEN_REFRESH_RETRY_DELAY', 1)
    for attempt in range(1, max_retries + 1):
        try:
            # Pass arguments as keywords so tests can introspect the call easily.
            response = await asyncio.to_thread(requests.post, url=url, data=payload, timeout=10)
            response.raise_for_status()
            tokens = response.json()

            new_access_token = tokens.get('access_token')
            new_refresh_token = tokens.get('refresh_token', refresh_token_param)

            if not new_access_token:
                logging.error("Refresh response did not contain an access token.")
                return None, refresh_token_param

            logging.info("Twitch access token refreshed successfully.")
            save_tokens(new_access_token, new_refresh_token)
            return new_access_token, new_refresh_token

        except requests.exceptions.HTTPError as e:
            logging.error(f"HTTP error refreshing Twitch token: {e}")
            if e.response is not None:
                logging.error(f"Response status: {e.response.status_code}")
                logging.error(f"Response body: {e.response.text}")
            return None, refresh_token_param
        except requests.exceptions.RequestException as e:
            logging.error(
                f"Network error refreshing Twitch token (attempt {attempt}/{max_retries}): {e}"
            )
            if attempt < max_retries:
                await asyncio.sleep(retry_delay)
                continue
            return None, refresh_token_param
        except (json.JSONDecodeError, ValueError) as e:
            logging.error(f"Failed to decode JSON response from Twitch token endpoint: {e}")
            logging.error(f"Response body: {response.text if 'response' in locals() else 'N/A'}")
            return None, refresh_token_param
        except Exception as e:
            logging.exception(f"An unexpected error occurred during token refresh: {e}")
            return None, refresh_token_param


async def validate_access_token(access_token):
    """Validate current Twitch access token and return metadata dict or None on failure."""
    access_token = _normalize_access_token(access_token)
    if not access_token:
        return None
    url = 'https://id.twitch.tv/oauth2/validate'
    headers = {'Authorization': f'OAuth {access_token}'}
    try:
        response = await asyncio.to_thread(requests.get, url=url, headers=headers, timeout=10)
        if response.status_code != 200:
            return None
        data = response.json()
        # Typical fields: client_id, login, user_id, scopes, expires_in
        return data if isinstance(data, dict) else None
    except Exception:
        return None


# --- Twitch Bot Class ---
class MovieBot(commands.Bot):

    def __init__(self, access_token_param, refresh_token_initial):
        self.current_refresh_token = refresh_token_initial
        access_token_param = _normalize_access_token(access_token_param)
        super().__init__(
            token=access_token_param,
            prefix='!',
            initial_channels=[CHANNEL_NAME]
        )

        # --- Instance State ---
        self.votes = {}
        self.voter_data = {}
        self.movies = []
        self.current_movie_path = None
        self.current_movie_duration = DEFAULT_CONFIG['DEFAULT_MOVIE_DURATION']
        self.poll_active = False
        self.movie_start_time = None
        self.ws = None # OBS WebSocket client instance (v5)
        self.obs_connected = False
        self.last_obs_check_time = 0
        self._obs_connect_lock = asyncio.Lock() # Lock for connection logic

        # IRC/WebSocket health state
        self._last_irc_ok_ts = time.monotonic()
        self._irc_recovering = False
        self._irc_reconnect_backoff_s = 5
        self._irc_recovery_failures = 0
        self._last_token_validate = 0.0
        self._token_expires_in = None
        self._startup_complete = False
        self._shutdown_requested = False
        self._startup_task = None
        self._end_poll_handle = None
        self._next_movie_handle = None
        self._playback_generation = 0

        self.movies = scan_movies(MOVIE_DIRECTORY)
        if not self.movies:
            logging.warning("No movies found in specified directory.")

    # ---- Chat (IRC) token + reconnect helpers --------------------------------
    def _format_irc_token(self, token: str) -> str:
        """Ensure IRC token has the required 'oauth:' prefix for PASS auth."""
        token = _normalize_access_token(token)
        if not token:
            return token
        return token if token.startswith("oauth:") else f"oauth:{token}"

    async def _apply_new_access_token(self, new_access: str):
        """Apply refreshed token to TwitchIO's HTTP client and IRC connection objects."""
        new_access = _normalize_access_token(new_access)
        try:
            # Update HTTP client token (used for Helix/REST)
            self._http.token = new_access

            # Update IRC connection token so future handshakes use new value
            connection = getattr(self, "_connection", None)
            if connection is not None:
                irc_token = self._format_irc_token(new_access)
                for attr_name in ("_token", "token"):
                    if hasattr(connection, attr_name):
                        value = irc_token if attr_name != "_token" else new_access
                        setattr(connection, attr_name, value)
                for attr_name in ("_password", "password", "_oauth_token"):
                    if hasattr(connection, attr_name):
                        setattr(connection, attr_name, irc_token)
        except Exception as e:
            logging.error(f"Failed to apply refreshed token to TwitchIO internals: {e}")
        else:
            logging.debug("Applied refreshed token to HTTP and IRC connection objects.")

    async def _force_irc_reconnect(self):
        """Trigger a chat reconnect so the next handshake uses the refreshed token."""
        connection = getattr(self, "_connection", None)
        if connection is None:
            logging.warning("Cannot force IRC reconnect: no connection object found.")
            return False

        websocket = getattr(connection, "ws", None) or getattr(connection, "_websocket", None)
        async def _invoke_close(close_callable):
            if asyncio.iscoroutinefunction(close_callable):
                return await close_callable()
            result = close_callable()
            if isinstance(result, Awaitable):
                return await result
            return result

        try:
            if websocket and not getattr(websocket, "closed", False) and hasattr(websocket, "close"):
                logging.info("Forcing IRC reconnect by closing underlying websocket...")
                await _invoke_close(getattr(websocket, "close"))
            elif hasattr(connection, "_connect") and callable(getattr(connection, "_connect")):
                reconnect_requested = bool(getattr(connection, "_reconnect_requested", False))
                keeper = getattr(connection, "_keeper", None)
                keeper_active = bool(keeper and not keeper.done())
                if reconnect_requested or keeper_active:
                    logging.info("IRC reconnect already appears to be in progress; not starting another.")
                    return True
                logging.info("Forcing IRC reconnect by starting TwitchIO connection task...")
                self.loop.create_task(connection._connect())
            elif hasattr(connection, "close") and callable(getattr(connection, "close")):
                logging.info("Forcing IRC reconnect by closing the connection...")
                await _invoke_close(getattr(connection, "close"))
            else:
                logging.warning("No websocket/connection close method found; cannot force reconnect.")
                return False
        except Exception as e:
            logging.error(f"Error while forcing IRC reconnect: {e}")
            return False

        await asyncio.sleep(1.0)
        logging.info("IRC reconnect triggered successfully.")
        return True

    async def _hard_reset_irc_connection(self, reason: str = "unknown"):
        """Replace TwitchIO's IRC connection object while preserving bot state."""
        old_connection = getattr(self, "_connection", None)
        token = _normalize_access_token(getattr(getattr(self, "_http", None), "token", None))
        if not token and old_connection is not None:
            token = _normalize_access_token(getattr(old_connection, "_token", None))
        if not token:
            logging.error("Cannot hard reset IRC connection: no access token available.")
            return False

        initial_channels = [CHANNEL_NAME]
        modes = None
        retain_cache = True
        if old_connection is not None:
            initial_channels = list(getattr(old_connection, "_initial_channels", initial_channels) or initial_channels)
            modes = getattr(old_connection, "modes", None)
            retain_cache = getattr(old_connection, "_retain_cache", True)

        logging.warning(f"Hard resetting Twitch IRC connection (reason={reason}).")

        if old_connection is not None:
            for attr_name in ("_keeper", "_task_cleaner"):
                task = getattr(old_connection, attr_name, None)
                if task and not task.done():
                    task.cancel()
            for task in list(getattr(old_connection, "_background_tasks", []) or []):
                if task and not task.done():
                    task.cancel()
            websocket = getattr(old_connection, "_websocket", None)
            if websocket and not getattr(websocket, "closed", False):
                try:
                    await websocket.close()
                except Exception as e:
                    logging.debug(f"Error closing old IRC websocket during hard reset: {e}")

        http = getattr(self, "_http", None)
        session = getattr(http, "session", None)
        if session is not None and getattr(session, "closed", False):
            http.session = None

        connection_cls = type(old_connection) if old_connection is not None else None
        if connection_cls is None:
            logging.error("Cannot hard reset IRC connection: TwitchIO connection class unavailable.")
            return False

        self._connection = connection_cls(
            loop=self.loop,
            heartbeat=getattr(self, "_heartbeat", 30.0),
            client=self,
            token=token,
            modes=modes,
            initial_channels=initial_channels,
            retain_cache=retain_cache,
        )

        try:
            await self._connection._connect()
            await asyncio.wait_for(self._connection.is_ready.wait(), timeout=20)
        except Exception as e:
            logging.error(f"Hard IRC reset failed: {e}")
            return False

        self._last_irc_ok_ts = time.monotonic()
        logging.info("Hard IRC reset completed; chat connection is ready.")
        return True

    def _is_irc_connected(self) -> bool:
        """Best-effort check for Twitch IRC websocket health across twitchio versions."""
        try:
            connection = getattr(self, "_connection", None)
            if not connection:
                return False

            ready = getattr(connection, "is_ready", None)
            if hasattr(ready, "is_set") and not ready.is_set():
                return False

            ws = getattr(connection, "ws", None) or getattr(connection, "_websocket", None)
            if ws is None:
                return False

            for attr in ("closed", "is_closed"):
                if hasattr(ws, attr):
                    val = getattr(ws, attr)
                    v = val() if callable(val) else val
                    if isinstance(v, bool):
                        return not v

            if hasattr(ws, "open"):
                val = getattr(ws, "open")
                v = val() if callable(val) else val
                if isinstance(v, bool):
                    return v

            if hasattr(connection, "is_connected"):
                val = getattr(connection, "is_connected")
                v = val() if callable(val) else val
                if isinstance(v, bool):
                    return v

            return True
        except Exception:
            return False

    async def _recover_irc_connection(self, reason: str = "unknown"):
        """Attempt robust IRC recovery with token refresh + backoff."""
        if self._shutdown_requested:
            logging.info("Skipping IRC recovery because the bot is shutting down.")
            return
        if self._irc_recovering:
            logging.debug("IRC recovery already in progress; skipping duplicate trigger.")
            return

        self._irc_recovering = True
        try:
            logging.warning(f"Starting IRC recovery (reason={reason})")

            # Always try to refresh token before reconnecting.
            new_access, new_refresh = await refresh_access_token(self.current_refresh_token)
            if new_access and new_refresh:
                self.current_refresh_token = new_refresh
                await self._apply_new_access_token(new_access)
            else:
                logging.warning("Token refresh failed during IRC recovery; retrying reconnect with existing token.")

            await self._force_irc_reconnect()
            await asyncio.sleep(5)

            if self._is_irc_connected():
                self._last_irc_ok_ts = time.monotonic()
                self._irc_reconnect_backoff_s = 5
                self._irc_recovery_failures = 0
                logging.info("IRC recovery successful.")
            else:
                self._irc_recovery_failures += 1
                if self._irc_recovery_failures >= 2:
                    if await self._hard_reset_irc_connection(reason=reason):
                        self._irc_reconnect_backoff_s = 5
                        self._irc_recovery_failures = 0
                        return
                delay = self._irc_reconnect_backoff_s
                self._irc_reconnect_backoff_s = min(self._irc_reconnect_backoff_s * 2, 300)
                logging.error(f"IRC recovery attempt did not restore connection. Backing off {delay}s.")
                await asyncio.sleep(delay)
        except Exception as e:
            self._irc_recovery_failures += 1
            delay = self._irc_reconnect_backoff_s
            self._irc_reconnect_backoff_s = min(self._irc_reconnect_backoff_s * 2, 300)
            logging.error(f"IRC recovery failed with exception: {e}. Backoff {delay}s")
            await asyncio.sleep(delay)
        finally:
            self._irc_recovering = False

    async def _ensure_valid_twitch_token(self, force_refresh: bool = False) -> bool:
        """Keep Twitch token healthy without needless reconnect churn.

        - Validate current token periodically.
        - Refresh only when forced, invalid, or near expiry.
        """
        try:
            now = time.monotonic()
            should_validate = (now - self._last_token_validate) > 300 or self._token_expires_in is None
            token_data = None

            if should_validate and not force_refresh:
                token_data = await validate_access_token(self._http.token)
                self._last_token_validate = now
                if token_data and isinstance(token_data.get('expires_in'), int):
                    self._token_expires_in = token_data.get('expires_in')

            needs_refresh = force_refresh or (token_data is None and should_validate)
            if not needs_refresh and self._token_expires_in is not None and self._token_expires_in < 1800:
                needs_refresh = True

            if not needs_refresh:
                return True

            new_access, new_refresh = await refresh_access_token(self.current_refresh_token)
            if not (new_access and new_refresh):
                logging.warning("Token refresh failed in _ensure_valid_twitch_token")
                return False

            old_access = getattr(self._http, 'token', None)
            self.current_refresh_token = new_refresh
            await self._apply_new_access_token(new_access)

            # Update validation cache after refresh.
            token_data = await validate_access_token(new_access)
            self._last_token_validate = time.monotonic()
            if token_data and isinstance(token_data.get('expires_in'), int):
                self._token_expires_in = token_data.get('expires_in')

            # Reconnect IRC only when needed (connection unhealthy) to reduce socket churn.
            if (new_access != old_access) and (not self._is_irc_connected()):
                await self._force_irc_reconnect()

            return True
        except Exception as e:
            logging.error(f"_ensure_valid_twitch_token failed: {e}")
            return False

    @property
    def prefix(self):
        """Expose the command prefix expected by ``commands.Bot``.

        Recent versions of ``twitchio`` store the prefix on the private
        attribute ``_prefix``.  The original implementation referenced
        ``self.prefix`` which no longer exists and caused an
        ``AttributeError`` when processing chat messages.  Providing this
        property keeps the rest of the code compatible without relying on
        the internal attribute name.
        """
        return self._prefix

    def _ws_is_identified(self):
        """Return True if the OBS websocket exists and reports itself identified.

        The obs-websocket-py API changed between versions where ``is_identified``
        is sometimes exposed as a boolean attribute and in other versions as a
        callable method.  This helper normalises those differences so the bot can
        reliably determine the connection state.
        """
        if not self.ws:
            return False
        try:
            attr = getattr(self.ws, "is_identified")
        except AttributeError:
            # Fall back to checking the underlying websocket connection, if available
            try:
                return bool(getattr(self.ws, "ws", None) and getattr(self.ws.ws, "connected", False))
            except Exception:
                return False
        if self._shutdown_requested:
            logging.debug("Skipping OBS websocket state check during shutdown.")
            return True

        try:
            return attr() if callable(attr) else bool(attr)
        except Exception:
            return False

    def _cancel_scheduled_callback(self, attr_name: str):
        """Cancel a previously scheduled callback handle if one exists."""
        handle = getattr(self, attr_name, None)
        if handle is None:
            return
        try:
            handle.cancel()
        except Exception as e:
            logging.debug(f"Failed to cancel scheduled callback '{attr_name}': {e}")
        finally:
            setattr(self, attr_name, None)

    def _schedule_callback(self, attr_name: str, delay: float, callback, description: str):
        """Replace an existing delayed callback with a new one."""
        self._cancel_scheduled_callback(attr_name)
        handle = self.loop.call_later(delay, callback)
        setattr(self, attr_name, handle)
        logging.info(f"Scheduled {description} in {delay} seconds.")
        return handle

    # --- OBS WebSocket Management (Updated for obs-websocket-py sync API) ---
    async def connect_obs(self):
        """Establishes OBS WebSocket connection."""
        async with self._obs_connect_lock:  # Ensure only one connection attempt at a time
            if self.obs_connected and self._ws_is_identified():
                logging.debug("Already connected to OBS.")
                return True

            if self.ws:
                try:
                    logging.info("Disconnecting existing OBS ws client before reconnecting.")
                    await asyncio.to_thread(self.ws.disconnect)
                except Exception as e:
                    logging.warning(f"Error during previous OBS disconnect: {e}")
                finally:
                    self.ws = None
                    self.obs_connected = False

            logging.info(f"Connecting to OBS WebSocket (v5) at {OBS_WEBSOCKET_IP}:{OBS_WEBSOCKET_PORT}...")
            self.ws = obsws(OBS_WEBSOCKET_IP, OBS_WEBSOCKET_PORT, OBS_WEBSOCKET_PASSWORD, timeout=10)  # Added timeout

            try:
                await asyncio.to_thread(self.ws.connect)
                logging.info("Connected and identified to OBS WebSocket (v5).")

                if await self._check_obs_scene_source():  # Pass self.ws implicitly
                    logging.info("OBS Scene and Source verified.")
                    self.obs_connected = True
                    self.last_obs_check_time = time.monotonic()  # Reset check time on successful connect
                    return True
                else:
                    logging.error("OBS connection successful, but Scene or Media Source check failed.")
                    await asyncio.to_thread(self.ws.disconnect)
                    self.ws = None
                    self.obs_connected = False
                    return False
            except obs_exceptions.ConnectionFailure as e:
                logging.error(f"OBS WebSocket connection failed: {e}")
                self.ws = None
                self.obs_connected = False
                return False
            except asyncio.TimeoutError:
                logging.error("OBS WebSocket connection timed out during connect/identify.")
                self.ws = None
                self.obs_connected = False
                return False
            except Exception as e:
                logging.exception(f"An unexpected error occurred connecting to OBS: {e}")
                if self.ws:  # Attempt disconnect if ws object exists
                    try:
                        await asyncio.to_thread(self.ws.disconnect)
                    except Exception:
                        pass
                self.ws = None
                self.obs_connected = False
                return False

    async def _check_obs_scene_source(self):
        """Internal helper to check scene/source using the current self.ws (v5)."""
        if not self._ws_is_identified():
            logging.error("Cannot check OBS scene/source: WebSocket not connected/identified.")
            return False

        try:
            # Check scene exists
            scene_list_req = obs_requests.GetSceneList()
            scene_list_resp = await asyncio.to_thread(self.ws.call, scene_list_req)
            # Responses from obswebsocket may expose scenes via attribute or getScenes() method
            scenes = []
            if hasattr(scene_list_resp, 'scenes'):
                scenes = scene_list_resp.scenes
            else:
                try:
                    scenes = scene_list_resp.getScenes()
                except (AttributeError, KeyError):
                    scenes = []

            scene_names = []
            for scene in scenes:
                if isinstance(scene, dict):
                    name = scene.get('sceneName') or scene.get('name')
                else:
                    name = getattr(scene, 'sceneName', None) or getattr(scene, 'name', None)
                scene_names.append(name)

            if SCENE_NAME not in scene_names:
                logging.error(f"OBS Scene '{SCENE_NAME}' not found in list: {scene_names}")
                return False
            logging.debug(f"OBS Scene '{SCENE_NAME}' found.")

            # Check media source exists within the scene - Use GetSceneItemId which implicitly checks existence
            get_id_req = obs_requests.GetSceneItemId(sceneName=SCENE_NAME, sourceName=MEDIA_SOURCE_NAME)
            id_response = await asyncio.to_thread(self.ws.call, get_id_req)
            
            # If GetSceneItemId succeeds, id_response should contain sceneItemId.
            # obswebsocket-py v5+ SceneItemIdResponse has 'sceneItemId' (older protocol versions, or library might use scene_item_id)
            # Check common attribute names; the library maps JSON camelCase to snake_case.
            # obs-websocket responses may expose the scene item id in a variety of ways
            scene_item_id_value = None
            if hasattr(id_response, 'scene_item_id'):
                scene_item_id_value = id_response.scene_item_id
            elif hasattr(id_response, 'sceneItemId'):  # Direct protocol naming
                scene_item_id_value = id_response.sceneItemId
            elif hasattr(id_response, 'getSceneItemId'):
                try:
                    scene_item_id_value = id_response.getSceneItemId()
                except Exception:
                    scene_item_id_value = None
            elif isinstance(getattr(id_response, 'datain', None), dict):
                # Some versions expose raw data via ``datain``
                scene_item_id_value = id_response.datain.get('sceneItemId') or id_response.datain.get('scene_item_id')

            # ``scene_item_id`` can legitimately be ``0`` so explicitly check for ``None``
            if scene_item_id_value is None:
                logging.error(
                    f"OBS Media Source '{MEDIA_SOURCE_NAME}' not found in scene '{SCENE_NAME}' (or ID was missing/invalid in response)."
                )
                return False

            logging.info(f"OBS Scene '{SCENE_NAME}' and Source '{MEDIA_SOURCE_NAME}' verified.")
            return True
        except obs_exceptions.ObjectError as e:  # Generic OBS request failure
            logging.error(f"OBS Request Failure checking scene/source (e.g., item not found): {e}")
            return False
        except AttributeError as e:
            logging.error(f"Error accessing response data structure from obswebsocket-py v5: {e}")
            return False
        except Exception as e:
            logging.exception(f"Unexpected error checking OBS scene/source: {e}")
            return False

    async def ensure_obs_connection(self):
        """Checks if connected (v5). If not, attempts to connect. Returns True if connected."""
        current_time = time.monotonic()
        # Check connection status less aggressively than before
        if self.obs_connected and self._ws_is_identified():
            # Reduce frequency of passive checks
            if current_time - self.last_obs_check_time > DEFAULT_CONFIG['OBS_CHECK_INTERVAL'] * 2: # Less frequent passive check
                self.last_obs_check_time = current_time
                try:
                    version_req = obs_requests.GetVersion()
                    await asyncio.wait_for(asyncio.to_thread(self.ws.call, version_req), timeout=5)
                    logging.debug("Passive OBS connection check successful.")
                    return True # Still connected
                except (obs_exceptions.ObjectError, asyncio.TimeoutError, ConnectionError, Exception) as e:
                    logging.warning(f"Passive OBS check failed ({type(e).__name__}): {e}. Marking as disconnected.")
                    self.obs_connected = False
                    # Fall through to reconnect logic
            else:
                return True # Assume connected based on flag and recent checks

        # If not connected or passive check failed, try connecting using the lock
        if not self.obs_connected:
            logging.warning("OBS connection is down or check failed. Attempting to reconnect...")
            if await self.connect_obs():
                logging.info("Successfully reconnected OBS in ensure_obs_connection.")
                self.obs_connected = True
                return True
            else:
                logging.error("Failed to establish OBS connection in ensure_obs_connection.")
                return False
        return self.obs_connected # Return current state after attempts

    async def safe_obs_call(self, request_obj):
        """Wrapper to make OBS calls safely (v5 async), ensuring connection."""
        if not await self.ensure_obs_connection():
            logging.error(f"Cannot make OBS call ({type(request_obj).__name__}): Connection not established.")
            return None

        try:
            # Use await directly for v5's async call, add timeout
            response = await asyncio.wait_for(asyncio.to_thread(self.ws.call, request_obj), timeout=10)
            logging.debug(f"OBS call successful: {type(request_obj).__name__}")
            return response
        except obs_exceptions.ObjectError as e:
            logging.error(f"OBS Request Failure during call '{type(request_obj).__name__}': {e}")
            return None  # Indicate failure
        except (asyncio.TimeoutError, ConnectionError, obs_exceptions.ConnectionFailure) as e:
            logging.error(
                f"OBS Connection Failure/Timeout during call '{type(request_obj).__name__}': {e}. Marking disconnected."
            )
            self.obs_connected = False
            return None  # Indicate failure
        except Exception as e:
            logging.exception(f"Unexpected error during OBS call '{type(request_obj).__name__}': {e}")
            self.obs_connected = False # Assume connection might be broken
            return None # Indicate failure

    # --- Core Bot Logic ---
    def pick_random_movie(self):
        """Selects a random movie from the available list."""
        if not self.movies:
            logging.warning("Cannot pick random movie: Movie list is empty.")
            return None
        try:
            return random.choice(self.movies)
        except IndexError: # Should not happen if self.movies check passes, but for safety
            logging.error("Error picking random movie despite list not being empty.")
            return None

    async def _get_scene_item_id(self, scene_name, source_name):
        """Helper to get the scene item ID."""
        get_id_req = obs_requests.GetSceneItemId(sceneName=scene_name, sourceName=source_name)
        id_response = await self.safe_obs_call(get_id_req)
        
        scene_item_id_value = None
        if id_response:  # Check if response is not None
            if hasattr(id_response, 'scene_item_id'):
                scene_item_id_value = id_response.scene_item_id
            elif hasattr(id_response, 'sceneItemId'):  # Fallback for direct protocol naming
                scene_item_id_value = id_response.sceneItemId
            else:
                data = getattr(id_response, 'datain', None)
                if isinstance(data, dict):
                    scene_item_id_value = data.get('sceneItemId')

        if scene_item_id_value is not None:
            logging.debug(f"Got Scene Item ID for '{source_name}': {scene_item_id_value}")
            return scene_item_id_value
        else:
            logging.error(f"Failed to get Scene Item ID for '{source_name}' in scene '{scene_name}'. Response: {id_response}")
            return None

    async def _set_program_scene(self, scene_name):
        """Ensure OBS program output is on the configured movie scene."""
        set_scene_request = obs_requests.SetCurrentProgramScene(sceneName=scene_name)
        response = await self.safe_obs_call(set_scene_request)
        if response is None:
            logging.error(f"Failed to switch OBS program scene to '{scene_name}'.")
            return False
        logging.info(f"OBS program scene set to '{scene_name}'.")
        return True

    async def _restart_media_input(self, input_name):
        """Force the media input to restart so the new file begins playback immediately."""
        restart_request = obs_requests.TriggerMediaInputAction(
            inputName=input_name,
            mediaAction=OBS_MEDIA_ACTION_RESTART,
        )
        response = await self.safe_obs_call(restart_request)
        if response is None:
            logging.error(f"Failed to restart OBS media input '{input_name}'.")
            return False
        logging.info(f"OBS media input '{input_name}' restarted.")
        return True

    async def load_media_in_obs(self, path):
        """Attempts to load media into OBS source (v5 async) with retries."""
        normalized_path = os.path.normpath(path)
        media_basename = _get_movie_basename(path)
        logging.info(f"Attempting to load into OBS '{MEDIA_SOURCE_NAME}': {media_basename}")

        scene_item_id = await self._get_scene_item_id(SCENE_NAME, MEDIA_SOURCE_NAME)
        if scene_item_id is None:
            logging.error("Cannot load media: Failed to get Scene Item ID for the media source.")
            return False # Cannot proceed without the ID

        settings = {"local_file": normalized_path}
        set_settings_request = obs_requests.SetInputSettings(inputName=MEDIA_SOURCE_NAME, inputSettings=settings, overlay=True)
        hide_request = obs_requests.SetSceneItemEnabled(sceneName=SCENE_NAME, sceneItemId=scene_item_id, sceneItemEnabled=False)
        show_request = obs_requests.SetSceneItemEnabled(sceneName=SCENE_NAME, sceneItemId=scene_item_id, sceneItemEnabled=True)

        for attempt in range(1, DEFAULT_CONFIG['LOAD_RETRIES'] + 1):
            logging.info(f"Media load attempt {attempt}/{DEFAULT_CONFIG['LOAD_RETRIES']} for '{media_basename}'...")

            logging.debug(f"Hiding '{MEDIA_SOURCE_NAME}' (Item ID: {scene_item_id})")
            hide_response = await self.safe_obs_call(hide_request)
            if hide_response is None:
                logging.warning(f"Failed to send hide command for '{MEDIA_SOURCE_NAME}' on attempt {attempt}.")

            await asyncio.sleep(0.2) 

            logging.debug(f"Setting input settings for '{media_basename}'")
            set_response = await self.safe_obs_call(set_settings_request)

            if set_response is not None:
                logging.info(f"Successfully sent 'SetInputSettings' for '{media_basename}'.")
                await asyncio.sleep(0.8) 

                logging.debug(f"Showing '{MEDIA_SOURCE_NAME}' (Item ID: {scene_item_id})")
                show_response = await self.safe_obs_call(show_request)

                if show_response is not None:
                    if not await self._set_program_scene(SCENE_NAME):
                        logging.warning(
                            f"Media '{media_basename}' was loaded, but OBS did not switch to scene '{SCENE_NAME}' on attempt {attempt}."
                        )
                    else:
                        await asyncio.sleep(0.3)
                        if await self._restart_media_input(MEDIA_SOURCE_NAME):
                            logging.info(
                                f"Media '{media_basename}' loaded, scene activated, and playback restarted successfully in OBS."
                            )
                            return True # SUCCESS
                        logging.warning(
                            f"Media '{media_basename}' was loaded and scene was active, but the media input did not restart on attempt {attempt}."
                        )
                else:
                    logging.warning(f"Sent 'SetInputSettings' successfully, but failed to send show command for '{MEDIA_SOURCE_NAME}' on attempt {attempt}.")
            else:
                logging.warning(f"Failed to send 'SetInputSettings' command for '{media_basename}' (attempt {attempt}).")

            if attempt < DEFAULT_CONFIG['LOAD_RETRIES']:
                logging.info(f"Retrying media load in {DEFAULT_CONFIG['LOAD_RETRY_DELAY']} seconds...")
                await asyncio.sleep(DEFAULT_CONFIG['LOAD_RETRY_DELAY'])
            else:
                logging.error(f"Failed to load media '{media_basename}' into OBS after {DEFAULT_CONFIG['LOAD_RETRIES']} attempts.")
                return False 

        return False # Fallback failure

    async def play_movie(self, path):
        """Plays the specified movie path in OBS and starts the poll."""
        if self._shutdown_requested:
            logging.info("Ignoring play_movie request during shutdown.")
            return

        movie_basename = _get_movie_basename(path)
        logging.info(f"--- Starting sequence for movie: {movie_basename} ---")

        if not path or not await asyncio.to_thread(os.path.exists, path):
            logging.error(f"Invalid or non-existent movie path: {path}. Cannot play.")
            self.loop.create_task(self._handle_playback_failure(failed_path=path))
            return

        if not await self.load_media_in_obs(path):
            logging.error(f"Failed to load '{movie_basename}' into OBS.")
            self.loop.create_task(self._handle_playback_failure(failed_path=path))
            return

        self._cancel_scheduled_callback('_next_movie_handle')
        self._cancel_scheduled_callback('_end_poll_handle')
        self._playback_generation += 1
        playback_generation = self._playback_generation
        self.current_movie_path = path
        self.current_movie_duration = await get_movie_duration(path)
        self.movie_start_time = self.loop.time()
        self.poll_active = True
        self.votes.clear()
        self.voter_data.clear()

        logging.info(f"Movie '{movie_basename}' is now playing. Duration: {self.current_movie_duration}s. Poll is ACTIVE.")

        self._schedule_callback(
            '_end_poll_handle',
            self.current_movie_duration,
            lambda: self.loop.create_task(self.end_poll(expected_generation=playback_generation)),
            f"end_poll for '{movie_basename}'",
        )


    async def _handle_playback_failure(self, failed_path=None):
        """Handles failures during movie load/play attempt."""
        if self._shutdown_requested:
            logging.info("Ignoring playback failure handling during shutdown.")
            return

        logging.warning("Handling playback failure. Resetting state.")
        self._playback_generation += 1
        self._cancel_scheduled_callback('_end_poll_handle')
        self.votes.clear()
        self.voter_data.clear()
        self.poll_active = False
        current_failed_path = self.current_movie_path 
        self.current_movie_path = None
        self.movie_start_time = None

        path_that_failed = failed_path or current_failed_path
        failed_basename = _get_movie_basename(path_that_failed) if path_that_failed else "Unknown"
        logging.info(f"Attempting to select fallback movie after failure of '{failed_basename}'.")

        available_movies = [m for m in self.movies if m != path_that_failed]

        if not available_movies:
            logging.critical(f"No alternative movies available after playback failure of '{failed_basename}'. Stopping playback attempts.")
            try:
                channel = self.get_channel(CHANNEL_NAME)
                if channel:
                    await channel.send(f"Critical error: Failed to play '{failed_basename}' and no fallback movies found. Please check bot setup.")
            except Exception as e:
                logging.error(f"Failed to send critical error message to chat: {e}")
            return

        next_movie = random.choice(available_movies)
        next_basename = _get_movie_basename(next_movie)
        logging.info(f"Scheduling random fallback movie after failure: {next_basename}")

        self._schedule_callback(
            '_next_movie_handle',
            DEFAULT_CONFIG['POLL_REACTION_DELAY'],
            lambda: self.loop.create_task(self.play_movie(next_movie)),
            f"fallback playback for '{next_basename}'",
        )


    async def end_poll(self, expected_generation=None):
        """Determines winner and schedules the next movie."""
        if self._shutdown_requested:
            logging.info("Ignoring end_poll request during shutdown.")
            return

        if expected_generation is not None and expected_generation != self._playback_generation:
            logging.info(
                "Ignoring stale end_poll callback for generation %s; current generation is %s.",
                expected_generation,
                self._playback_generation,
            )
            return

        logging.info("--- Ending poll for the current movie ---")
        self._playback_generation += 1
        self._cancel_scheduled_callback('_end_poll_handle')
        self.poll_active = False
        self.movie_start_time = None

        await asyncio.sleep(1.0) 

        next_movie_path = None
        winner_basename = "Randomly Selected"
        vote_count = 0

        if not self.votes:
            logging.info("No votes received. Picking random movie.")
            next_movie_path = self.pick_random_movie()
            if not next_movie_path:
                logging.error("No votes and no fallback movies available.")
                try:
                    channel = self.get_channel(CHANNEL_NAME)
                    if channel: await channel.send("Movie ended, but no votes were cast and no fallback movies found.")
                except Exception as e: logging.error(f"Failed to send no-fallback message: {e}")
                self.current_movie_path = None 
                self.votes.clear() 
                self.voter_data.clear()
                return 
        else:
            try:
                max_votes = max(self.votes.values())
                winners = [path for path, count in self.votes.items() if count == max_votes]
                winner_path = random.choice(winners)
                next_movie_path = winner_path
                winner_basename = _get_movie_basename(winner_path)
                vote_count = self.votes[winner_path]

                message = f"Poll ended! Next movie: '{winner_basename}' ({vote_count} votes)."
                if len(winners) > 1:
                    tied_movies_str = ", ".join([f"'{_get_movie_basename(p)}'" for p in winners])
                    logging.info(f"Tie vote ({vote_count} votes) between {tied_movies_str}. Randomly selected: '{winner_basename}'")
                    message = f"Tie vote ({vote_count} votes)! Randomly selected: '{winner_basename}'."

                logging.info(f"Selected next movie: '{winner_basename}' with {vote_count} votes.")
                try:
                    channel = self.get_channel(CHANNEL_NAME)
                    if channel: await channel.send(message)
                except Exception as e: logging.error(f"Failed to send winner message: {e}")

            except Exception as e:
                logging.exception("Error determining vote winner. Picking random movie.")
                next_movie_path = self.pick_random_movie()
                winner_basename = "Randomly Selected (Error)"
                if not next_movie_path:
                    logging.error("Error determining winner AND no random movie available.")
                    self.current_movie_path = None
                    self.votes.clear()
                    self.voter_data.clear()
                    return 

        self.current_movie_path = None
        self.votes.clear()
        self.voter_data.clear()

        if next_movie_path:
            self._schedule_callback(
                '_next_movie_handle',
                DEFAULT_CONFIG['POLL_REACTION_DELAY'],
                lambda: self.loop.create_task(self.play_movie(next_movie_path)),
                f"next movie '{winner_basename}'",
            )
        else:
            logging.info("No next movie determined. Playback cycle stops.")


    def get_remaining_time(self):
        """Calculates the remaining time for the current movie."""
        if self.movie_start_time is None or not self.poll_active or self.current_movie_path is None:
            return 0
        elapsed_time = self.loop.time() - self.movie_start_time
        remaining_time = max(self.current_movie_duration - elapsed_time, 0)
        return remaining_time

    # --- Event Handlers ---
    async def event_ready(self):
        logging.info(f'Bot logged in as | {self.nick}')
        logging.info(f'User id is | {self.user_id}')
        logging.info(f"Attempting to join channel: {CHANNEL_NAME}")

        self._last_irc_ok_ts = time.monotonic()
        if self._shutdown_requested:
            logging.info("event_ready received during shutdown; ignoring startup work.")
            return
        if self._startup_complete:
            logging.info("event_ready received again; skipping duplicate startup scheduling.")
            return
        self._startup_complete = True

        await self.ensure_obs_connection()

        logging.info("Starting periodic announcement task.")
        self.periodic_announcement_task.start()
        logging.info("Starting periodic token refresh task.")
        self.periodic_token_refresh_task.start()
        logging.info("Starting periodic IRC health task.")
        self.periodic_irc_health_task.start()
        logging.info("Starting periodic movie rescan task.")
        self.periodic_movie_rescan_task.start()
        logging.info("Scheduling first movie task.")
        self.start_first_movie_task()

    async def event_channel_join(self, channel):
        logging.info(f"Successfully joined channel: {channel.name}")

    async def event_channel_join_failure(self, channel_name):
        logging.critical(f"Failed to join channel: {channel_name}. Check channel name and bot permissions.")

    async def event_message(self, message):
        if message.echo or not message.content or not message.content.startswith(self.prefix):
            return
        await self.handle_commands(message)

    async def event_disconnect(self, *args, **kwargs):
        """TwitchIO disconnect hook (best-effort, version tolerant)."""
        logging.warning(f"Twitch IRC disconnect event received: args={args} kwargs={kwargs}")
        if self._shutdown_requested:
            logging.info("Ignoring disconnect recovery during shutdown.")
            return
        await self._recover_irc_connection(reason="event_disconnect")

    async def event_error(self, error: Exception, data=None):
        """Global TwitchIO error hook; recover on websocket/auth instability."""
        logging.error(f"TwitchIO event_error: {error}; data={data}")
        if self._shutdown_requested:
            logging.info("Ignoring event_error recovery because shutdown is in progress.")
            return
        err_s = str(error).lower()
        if any(k in err_s for k in ["auth", "token", "unauthorized"]):
            await self._ensure_valid_twitch_token(force_refresh=True)
            await self._recover_irc_connection(reason="event_error_auth")
        elif any(k in err_s for k in ["websocket", "keep_alive", "closed"]):
            await self._recover_irc_connection(reason="event_error_socket")

    async def event_command_error(self, context: commands.Context, error: Exception):
        user = context.author.name if context.author else "Someone"
        command_name = context.command.name if context.command else context.message.content.split()[0]

        if isinstance(error, commands.CommandNotFound):
            await context.send(f"Sorry @{user}, the command '{command_name}' was not found. Use !help.")
        elif isinstance(error, commands.MissingRequiredArgument):
            param_name = error.param.name if hasattr(error, 'param') and hasattr(error.param, 'name') else 'argument'
            await context.send(f"Oops @{user}! You missed an argument: '{param_name}'. Usage: !help {command_name if context.command else ''}") # Added safety for command_name in help
            logging.warning(f"Command '{command_name}' by {user} missing arg: {param_name}")
        elif isinstance(error, commands.BadArgument):
            await context.send(f"Oops @{user}! Invalid argument for '{command_name}'. {error}")
            logging.warning(f"Command '{command_name}' by {user} bad arg: {error}")
        elif isinstance(error, commands.CommandOnCooldown):
            await context.send(f"@{user}, command '{command_name}' is on cooldown. Try again in {error.retry_after:.1f}s.")
        elif isinstance(error, commands.CheckFailure):
            await context.send(f"Sorry @{user}, you can't use '{command_name}'.")
            logging.warning(f"CheckFailure for command '{command_name}' by {user}: {error}")
        elif isinstance(error, AuthenticationError):
            logging.critical("Twitch Authentication Error during command! Token may be invalid.")
            await context.send("Authentication error. Bot may need attention.")
        else:
            logging.exception(f"Ignoring unexpected exception in command '{command_name}' by {user}:", exc_info=error)
            await context.send(f"Error running '{command_name}'. Owner notified.")

    # --- Commands ---
    @commands.command(name='help')
    async def help_command(self, ctx: commands.Context):
        """Displays help information."""
        help_lines = [
            f"--- {self.nick} StreamCinema Vote Bot ---",
            f"`!vote <movie name>` : Vote for the next movie.",
            f"`!currentmovie` : Show the current movie.",
            f"`!time` : Show remaining time.",
            f"`!results` : Show current vote counts.",
            f"`!movies` : Link to movie list.",
        ]
        try:
            for line in help_lines:
                await ctx.send(line)
                await asyncio.sleep(DEFAULT_CONFIG['HELP_MESSAGE_DELAY'])
        except Exception as e:
            logging.error(f"Error sending help message: {e}")
            await ctx.send(f"Commands: !vote, !currentmovie, !time, !results, !movies")

    @commands.command(name='vote')
    async def vote(self, ctx: commands.Context, *, movie_name: str):
        """Registers a user's vote."""
        if not self.poll_active:
            await ctx.send("No active poll now, wait for the next movie!")
            return

        user = ctx.author.name
        movie_name_lower = movie_name.lower().strip()
        if not movie_name_lower:
            await ctx.send(f"@{user}, please specify a movie name. Usage: !vote <name>")
            return

        exact_matches = [m for m in self.movies if movie_name_lower == _get_movie_basename(m).lower()]
        matches = []

        if exact_matches:
            matches = exact_matches
            logging.debug(f"Exact match for '{movie_name}' by {user}: {_get_movie_basename(matches[0])}")
        else:
            matches = [m for m in self.movies if movie_name_lower in _get_movie_basename(m).lower()]
            logging.debug(f"Substring matches for '{movie_name}' by {user}: {[ _get_movie_basename(m) for m in matches]}")

        if not matches:
            await ctx.send(f"Sorry @{user}, couldn't find '{movie_name}'. Use !movies for the list.")
            return
        elif len(matches) > 1:
            possible_matches_names = [_get_movie_basename(m) for m in matches[:DEFAULT_CONFIG['MAX_MOVIE_MATCH_DISPLAY']]]
            if len(matches) > DEFAULT_CONFIG['MAX_MOVIE_MATCH_DISPLAY']: possible_matches_names.append("...")
            await ctx.send(f"Multiple matches for '{movie_name}': {', '.join(possible_matches_names)}. Be more specific.")
            return

        selected_movie_path = matches[0]
        selected_basename = _get_movie_basename(selected_movie_path)

        previous_vote_path = self.voter_data.get(user)
        if previous_vote_path:
            if previous_vote_path == selected_movie_path:
                await ctx.send(f"@{user}, you already voted for '{selected_basename}'.")
                return
            else:
                if previous_vote_path in self.votes:
                    self.votes[previous_vote_path] -= 1
                    if self.votes[previous_vote_path] <= 0:
                        del self.votes[previous_vote_path]
                prev_basename = _get_movie_basename(previous_vote_path)
                logging.info(f"User {user} changed vote from '{prev_basename}' to '{selected_basename}'.")

        self.votes[selected_movie_path] = self.votes.get(selected_movie_path, 0) + 1
        self.voter_data[user] = selected_movie_path

        logging.info(f"Vote registered for '{selected_basename}' from {user}. New total: {self.votes[selected_movie_path]}")
        await ctx.send(f"@{user}, vote registered for '{selected_basename}'!")

    @commands.command(name='currentmovie')
    async def current_movie_command(self, ctx: commands.Context):
        """Displays the current movie."""
        if self.current_movie_path and self.poll_active:
            base_name = _get_movie_basename(self.current_movie_path)
            await ctx.send(f"Now playing: {base_name}")
        else:
            await ctx.send("No movie playing or poll inactive.")

    @commands.command(name='results')
    async def vote_results(self, ctx: commands.Context):
        """Shows current vote counts."""
        if not self.poll_active:
            await ctx.send("No active poll right now.")
            return
        if self.votes:
            sorted_votes = sorted(self.votes.items(), key=lambda item: (-item[1], _get_movie_basename(item[0])))
            results_text = ", ".join([f"'{_get_movie_basename(p)}': {c}" for p, c in sorted_votes])
            await ctx.send(f"Current Votes: {results_text}")
        else:
            await ctx.send("No votes cast yet.")

    @commands.command(name='time')
    async def time_command(self, ctx: commands.Context):
        """Shows remaining movie time."""
        if not self.current_movie_path or not self.poll_active:
            await ctx.send("No movie playing or poll inactive.")
            return

        remaining_seconds = self.get_remaining_time()
        remaining_formatted = str(timedelta(seconds=int(remaining_seconds)))
        duration_formatted = str(timedelta(seconds=self.current_movie_duration))
        movie_name = _get_movie_basename(self.current_movie_path)
        await ctx.send(f"Current: '{movie_name}' | Total: {duration_formatted} | Left: {remaining_formatted}")

    @commands.command(name='movies')
    async def movies_command(self, ctx: commands.Context):
        """Provides a link to the movie list or lists available."""
        movie_list_url = os.getenv("MOVIE_LIST_URL") # Ensure MOVIE_LIST_URL is loaded in config or set
        if movie_list_url:
            await ctx.send(f"Available movies list: {movie_list_url}")
        elif self.movies:
            movie_names = [_get_movie_basename(m) for m in self.movies[:15]] 
            suffix = '...' if len(self.movies) > 15 else ''
            await ctx.send(f"Available: {', '.join(movie_names)}{suffix}")
        else:
            await ctx.send("No movies seem to be loaded.")


    # --- Background Tasks ---
    @routines.routine(seconds=DEFAULT_CONFIG['TOKEN_REFRESH_INTERVAL'], wait_first=True)
    async def periodic_token_refresh_task(self):
        """Keep Twitch auth valid while minimizing reconnect flaps."""
        ok = await self._ensure_valid_twitch_token(force_refresh=False)
        if not ok:
            logging.warning("Periodic token health check/refresh failed; attempting IRC recovery path.")
            await self._recover_irc_connection(reason="token-health-failed")

    @periodic_token_refresh_task.before_routine
    async def before_periodic_token_refresh(self):
        """Wait for the bot to be ready before starting token refresh."""
        await self.wait_for_ready()
        logging.info("Bot ready, periodic token refresh task starting loop.")

    @routines.routine(seconds=10, wait_first=True)
    async def periodic_irc_health_task(self):
        """Monitor Twitch IRC websocket health and auto-recover with backoff."""
        try:
            if self._is_irc_connected():
                self._last_irc_ok_ts = time.monotonic()
                return

            down_for = time.monotonic() - self._last_irc_ok_ts
            logging.warning(f"IRC health check: websocket appears down for {down_for:.1f}s")

            # Only trigger recovery once the connection has been unhealthy briefly
            if down_for >= 10:
                await self._recover_irc_connection(reason=f"health-check-down-{int(down_for)}s")
        except Exception as e:
            logging.error(f"Error in periodic_irc_health_task: {e}")

    @periodic_irc_health_task.before_routine
    async def before_periodic_irc_health(self):
        await self.wait_for_ready()
        logging.info("Bot ready, periodic IRC health task starting loop.")

    @routines.routine(seconds=DEFAULT_CONFIG['ANNOUNCEMENT_INTERVAL'], wait_first=True)
    async def periodic_announcement_task(self):
        """Sends periodic messages to chat."""
        try:
            channel = self.get_channel(CHANNEL_NAME)
            if channel:
                message = f"Use `!help` for commands."
                if self.poll_active:
                    message = f"Vote for the next movie using `!vote <name>`! " + message
                else:
                    message = f"Next poll starts after the current movie! " + message
                await channel.send(message)
            else:
                logging.debug("Periodic announcement: Channel not found/joined yet.")
        except AuthenticationError:
            logging.critical("Twitch Auth Error in periodic announcement. Attempting token repair + recovery.")
            await self._ensure_valid_twitch_token(force_refresh=True)
            await self._recover_irc_connection(reason="announcement-auth")
        except Exception as e:
            logging.error(f"Error during periodic announcement: {e}")

    @periodic_announcement_task.before_routine
    async def before_periodic_announcement(self):
        """Wait until the bot is ready before starting the task."""
        await self.wait_for_ready()
        logging.info("Bot ready, periodic announcement task starting loop.")

    @routines.routine(seconds=600, wait_first=True)
    async def periodic_movie_rescan_task(self):
        """Periodically refresh movie list from disk to avoid stale/missing paths."""
        try:
            updated = scan_movies(MOVIE_DIRECTORY)
            if not updated:
                logging.warning("Movie rescan found no files; keeping current in-memory list.")
                return
            before = len(self.movies)
            self.movies = updated
            after = len(self.movies)
            if before != after:
                logging.info(f"Movie list refreshed: {before} -> {after}")
        except Exception as e:
            logging.error(f"Error in periodic_movie_rescan_task: {e}")

    @periodic_movie_rescan_task.before_routine
    async def before_periodic_movie_rescan(self):
        await self.wait_for_ready()
        logging.info("Bot ready, periodic movie rescan task starting loop.")

    def start_first_movie_task(self):
        """Schedules the first movie playback via create_task."""
        if self._shutdown_requested:
            logging.info("Skipping initial movie scheduling during shutdown.")
            return
        if self._startup_task and not self._startup_task.done():
            logging.info("Initial movie task is already scheduled.")
            return

        async def _task():
            try:
                await asyncio.sleep(5)
                if self._shutdown_requested:
                    logging.info("Initial movie task aborted because shutdown has started.")
                    return
                if self.current_movie_path or self.poll_active:
                    logging.info("Initial movie task skipped because playback is already active.")
                    return

                logging.info("Attempting initial movie playback.")
                if not self.movies:
                    logging.warning("Cannot start initial movie: No movies found.")
                    try:
                        channel = self.get_channel(CHANNEL_NAME)
                        if channel:
                            await channel.send("Warning: No movies found. Cannot start playback.")
                    except Exception:
                        pass
                    return

                first_movie = self.pick_random_movie()
                if first_movie:
                    if await self.ensure_obs_connection():
                        await self.play_movie(first_movie)
                    else:
                        logging.error("Cannot play first movie: OBS connection failed on startup.")
                        try:
                            channel = self.get_channel(CHANNEL_NAME)
                            if channel:
                                await channel.send("Error: Failed to connect to OBS. Cannot start movie.")
                        except Exception:
                            pass
                else:
                    logging.warning("No movies available for initial playback (pick_random failed).")
            finally:
                self._startup_task = None

        self._startup_task = self.loop.create_task(_task())
        logging.info("Created task for starting the first movie.")


    async def close(self):
        """Gracefully shuts down the bot."""
        if self._shutdown_requested:
            logging.info("Shutdown already in progress; continuing best-effort cleanup.")
        else:
            logging.info("--- Initiating Bot Shutdown ---")
            self._shutdown_requested = True
        self._cancel_scheduled_callback('_end_poll_handle')
        self._cancel_scheduled_callback('_next_movie_handle')

        startup_task = self._startup_task
        self._startup_task = None
        if startup_task and not startup_task.done():
            logging.info("Cancelling pending initial movie task.")
            startup_task.cancel()
            with suppress(asyncio.CancelledError):
                await startup_task

        # Routine objects from twitchio do not expose an is_running attribute.
        # Instead, check the underlying task to determine if the routine is active
        # before attempting a graceful stop.
        if getattr(self, 'periodic_announcement_task', None):
            task = getattr(self.periodic_announcement_task, '_task', None)
            if task and not task.done():
                logging.info("Stopping periodic announcement task.")
                self.periodic_announcement_task.stop()
        if getattr(self, 'periodic_token_refresh_task', None):
            task = getattr(self.periodic_token_refresh_task, '_task', None)
            if task and not task.done():
                logging.info("Stopping periodic token refresh task.")
                self.periodic_token_refresh_task.stop()
        if getattr(self, 'periodic_irc_health_task', None):
            task = getattr(self.periodic_irc_health_task, '_task', None)
            if task and not task.done():
                logging.info("Stopping periodic IRC health task.")
                self.periodic_irc_health_task.stop()
        if getattr(self, 'periodic_movie_rescan_task', None):
            task = getattr(self.periodic_movie_rescan_task, '_task', None)
            if task and not task.done():
                logging.info("Stopping periodic movie rescan task.")
                self.periodic_movie_rescan_task.stop()

        if self.ws:
            logging.info("Disconnecting from OBS WebSocket.")
            try:
                await asyncio.to_thread(self.ws.disconnect)
            except Exception as e:
                logging.error(f"Error during OBS disconnect: {e}")
            finally:
                self.ws = None
                self.obs_connected = False

        logging.info("Closing Twitch Bot connection.")
        closing_event = getattr(self, "_closing", None)
        if closing_event is None:
            logging.info("Twitch closing event was never initialized; skipping base close.")
        elif hasattr(closing_event, "is_set") and closing_event.is_set():
            logging.info("Twitch Bot connection already closed.")
        else:
            await super().close()
        logging.info("--- Bot Shutdown Complete ---")


# --- Main Execution ---
async def run_bot():
    logging.info("--- Bot Starting Up ---")
    initial_tokens = get_tokens()
    access_token = initial_tokens.get("access_token")
    refresh_token_from_env = initial_tokens.get("refresh_token")

    if not access_token or not refresh_token_from_env:
        logging.critical("Missing initial TWITCH_ACCESS_TOKEN or TWITCH_REFRESH_TOKEN in .env file or environment.")
        print("\nFATAL ERROR: Set TWITCH_ACCESS_TOKEN and TWITCH_REFRESH_TOKEN in .env or environment variables.", file=sys.stderr)
        sys.exit(1)

    client_id = _get_runtime_env("TWITCH_CLIENT_ID", TWITCH_CLIENT_ID)
    client_secret = _get_runtime_env("TWITCH_CLIENT_SECRET", TWITCH_CLIENT_SECRET)
    channel_name = _get_runtime_env("CHANNEL_NAME", CHANNEL_NAME)
    movie_directory = _get_runtime_env("MOVIE_DIRECTORY", MOVIE_DIRECTORY)
    missing_settings = []
    if not client_id:
        missing_settings.append("TWITCH_CLIENT_ID")
    if not client_secret:
        missing_settings.append("TWITCH_CLIENT_SECRET")
    if not channel_name:
        missing_settings.append("CHANNEL_NAME")
    if missing_settings:
        joined = ", ".join(missing_settings)
        logging.critical(f"Missing required startup settings: {joined}")
        print(f"\nFATAL CONFIGURATION ERROR: Missing required settings: {joined}.", file=sys.stderr)
        sys.exit(1)
    if not movie_directory or not os.path.isdir(movie_directory):
        logging.critical(f"MOVIE_DIRECTORY is missing or invalid: {movie_directory!r}")
        print("\nFATAL CONFIGURATION ERROR: MOVIE_DIRECTORY must point to an existing directory.", file=sys.stderr)
        sys.exit(1)

    logging.info("Attempting pre-run token validation/refresh...")
    new_access, new_refresh = await refresh_access_token(refresh_token_from_env)
    if new_access and new_refresh:
        access_token = new_access
        current_bot_refresh_token = new_refresh
        logging.info("Using refreshed token for bot connection.")
    else:
        current_bot_refresh_token = refresh_token_from_env
        logging.warning("Failed to refresh token pre-startup. Validating existing access token...")
        token_data = await validate_access_token(access_token)
        if not token_data:
            logging.critical("Existing TWITCH_ACCESS_TOKEN is invalid and refresh failed. Aborting startup.")
            print("\nFATAL ERROR: Twitch token invalid and refresh failed. Re-auth required.", file=sys.stderr)
            sys.exit(1)
        logging.info("Existing access token validated successfully; continuing startup.")

    bot = MovieBot(access_token_param=access_token, refresh_token_initial=current_bot_refresh_token)

    try:
        await bot.start()
    except AuthenticationError as e:
        logging.critical(f"Twitch Authentication Failed on connect: {e}. Check tokens/credentials.")
        print("\nFATAL ERROR: Twitch authentication failed. Check logs and .env or environment variables.", file=sys.stderr)
    except asyncio.CancelledError:
        logging.info("Bot run task cancelled (likely during shutdown).")
    except Exception as e:
        logging.exception(f"Critical error in main bot loop: {e}")
    finally:
        if 'bot' in locals() and not _is_bot_closed(bot):
            logging.info("Attempting graceful shutdown from main...")
            await bot.close()
        logging.info("Bot process ending.")

if __name__ == "__main__":
    try:
        asyncio.run(run_bot())
    except KeyboardInterrupt:
        logging.info("Shutdown requested via KeyboardInterrupt.")
    except ValueError as e: 
        logging.critical(f"Configuration Error: {e}")
        print(f"\nFATAL CONFIGURATION ERROR: {e}. Check .env file or environment variables.", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        logging.critical(f"Failed to start bot due to unexpected error: {e}", exc_info=True)
        print(f"\nFATAL STARTUP ERROR: {e}. Check logs.", file=sys.stderr)
        sys.exit(1)
