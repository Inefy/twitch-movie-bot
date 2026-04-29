# config.py
import os
import logging
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# --- Logging Setup ---
# Basic config for logging if this module is used standalone or before main bot logging
# This ensures that if config.py is imported and used before the bot's full logging is set up
# messages from config.py (like missing env var warnings) are still visible.
if not logging.getLogger().hasHandlers(): # Check if root logger already has handlers
    # Attempt to get the log level from an environment variable, default to INFO
    log_level_str = os.getenv('LOG_LEVEL', 'INFO').upper()
    log_level = getattr(logging, log_level_str, logging.INFO)
    
    logging.basicConfig(
        level=log_level,
        format='%(asctime)s - %(levelname)s - [%(name)s.%(funcName)s] %(message)s',
        handlers=[logging.StreamHandler()] # Default to console output for config issues
    )
config_logger = logging.getLogger(__name__)


# --- Environment Variable Loading ---

def get_env_variable(var_name, default=None, required=True, is_int=False):
    """Helper function to load environment variables with logging and validation."""
    value = os.getenv(var_name) # Get value first
    
    if value is None: # Value not set in environment
        if required and default is None: # Required, no default: critical error
            config_logger.critical(f"CRITICAL: Environment variable '{var_name}' not set and is required without a default.")
            raise ValueError(f"Missing required environment variable: {var_name}")
        elif default is not None: # Has a default (whether required or not)
            config_logger.info(f"Environment variable '{var_name}' not set, using default: '{default}'.")
            value = default # Use the default value
        elif not required and default is None: # Optional, no default: info, returns None
            config_logger.info(f"Optional environment variable '{var_name}' not set, no default provided. Will be None.")
            return None # Explicitly return None for optional unset variables

    # At this point, 'value' is either from env or the default.
    # If it was required and no default, an error was raised.
    # If it was optional and no default and not set, it's None and we've returned.
    
    if is_int:
        if value is None: # Can happen if default was None and it was not required
             config_logger.warning(f"Cannot convert None to int for '{var_name}'. Returning None.")
             return None
        try:
            return int(value)
        except ValueError:
            config_logger.critical(f"CRITICAL: Environment variable '{var_name}' ('{value}') must be an integer.")
            raise ValueError(f"Invalid integer value for environment variable: {var_name} (value: '{value}')")
    return value

# Twitch Configuration
#
# In the original implementation these variables were marked as required and the
# module raised a ``ValueError`` during import if they were missing.  This made
# it difficult to run the test-suite (or even import the module in isolation)
# without configuring a full environment first.  To make the configuration more
# flexible we treat them as optional at import time and allow the application or
# tests to supply them later via ``os.environ`` or other means.
TWITCH_CLIENT_ID = get_env_variable("TWITCH_CLIENT_ID", required=False)
TWITCH_CLIENT_SECRET = get_env_variable("TWITCH_CLIENT_SECRET", required=False)
CHANNEL_NAME = get_env_variable("CHANNEL_NAME", required=False)
# Tokens are loaded/managed dynamically by the bot using get_tokens/save_tokens

# OBS WebSocket Configuration
OBS_WEBSOCKET_IP = get_env_variable("OBS_WEBSOCKET_IP", default="127.0.0.1", required=False)
OBS_WEBSOCKET_PORT = get_env_variable("OBS_WEBSOCKET_PORT", default=4455, required=False, is_int=True)
OBS_WEBSOCKET_PASSWORD = get_env_variable("OBS_WEBSOCKET_PASSWORD", required=False) # Password may be supplied at runtime
SCENE_NAME = get_env_variable("SCENE_NAME", default="Scene", required=False)
MEDIA_SOURCE_NAME = get_env_variable("MEDIA_SOURCE_NAME", default="Media Source", required=False)

# Bot Configuration
# ``MOVIE_DIRECTORY`` is also optional at import time so unit tests can supply a
# temporary path without triggering a ``ValueError``.
MOVIE_DIRECTORY = get_env_variable("MOVIE_DIRECTORY", default="", required=False)
MOVIE_LIST_URL = get_env_variable("MOVIE_LIST_URL", default=None, required=False)


# --- Token Management Functions ---

def get_tokens():
    """Loads access and refresh tokens from environment variables."""
    return {
        "access_token": os.getenv("TWITCH_ACCESS_TOKEN"),
        "refresh_token": os.getenv("TWITCH_REFRESH_TOKEN")
    }

def save_tokens(access_token, refresh_token):
    """Saves updated tokens back to the .env file and current os.environ."""
    dotenv_path = os.path.join(os.path.dirname(__file__), '.env')

    if not os.path.exists(dotenv_path):
        try:
            with open(dotenv_path, 'w', encoding='utf-8') as f:
                pass  # Just create the file
            config_logger.info(f"Created .env file at: {dotenv_path}")
        except IOError as e:
            config_logger.error(f"Error creating .env file at {dotenv_path}: {e}")
            _update_os_environ_tokens(access_token, refresh_token) # Still update os.environ
            return

    current_lines = []
    try:
        with open(dotenv_path, 'r', encoding='utf-8') as f:
            current_lines = f.readlines()
    except IOError as e:
        config_logger.warning(f"Error reading .env file {dotenv_path} for saving tokens: {e}. Will attempt to overwrite.")

    new_env_lines = []
    keys_to_replace = {"TWITCH_ACCESS_TOKEN", "TWITCH_REFRESH_TOKEN"}

    for line in current_lines:
        stripped_line = line.strip()
        if not stripped_line or stripped_line.startswith('#'): # Preserve comments and empty lines
            new_env_lines.append(line)
            continue
        
        key_in_line = ""
        if '=' in stripped_line:
            key_in_line = stripped_line.split('=', 1)[0].strip()
        
        if key_in_line in keys_to_replace:
            continue # Skip old token lines
        else:
            new_env_lines.append(line) # Keep other lines

    # Add the new token values if they are not None
    if access_token:
        new_env_lines.append(f'TWITCH_ACCESS_TOKEN="{access_token}"\n')
    if refresh_token:
        new_env_lines.append(f'TWITCH_REFRESH_TOKEN="{refresh_token}"\n')

    try:
        # Ensure the file ends with a newline if it's not empty
        if new_env_lines and not new_env_lines[-1].endswith('\n'):
            new_env_lines[-1] += '\n'
            
        with open(dotenv_path, 'w', encoding='utf-8') as f:
            f.writelines(new_env_lines)
        config_logger.info(f"Successfully saved tokens to {dotenv_path}")
    except IOError as e:
        config_logger.error(f"Error writing updated tokens to .env file {dotenv_path}: {e}")

    _update_os_environ_tokens(access_token, refresh_token)

def _update_os_environ_tokens(access_token, refresh_token):
    """Updates the tokens in the current process's environment variables."""
    if access_token:
        os.environ["TWITCH_ACCESS_TOKEN"] = access_token
        config_logger.debug("Updated TWITCH_ACCESS_TOKEN in os.environ.")
    else:
        if "TWITCH_ACCESS_TOKEN" in os.environ:
            del os.environ["TWITCH_ACCESS_TOKEN"]
            config_logger.debug("Removed TWITCH_ACCESS_TOKEN from os.environ.")

    if refresh_token:
        os.environ["TWITCH_REFRESH_TOKEN"] = refresh_token
        config_logger.debug("Updated TWITCH_REFRESH_TOKEN in os.environ.")
    else:
        if "TWITCH_REFRESH_TOKEN" in os.environ:
            del os.environ["TWITCH_REFRESH_TOKEN"]
            config_logger.debug("Removed TWITCH_REFRESH_TOKEN from os.environ.")

# --- Constants for Bot Logic ---
DEFAULT_CONFIG = {
    'DEFAULT_MOVIE_DURATION': 60 * 90,
    'POLL_REACTION_DELAY': 10,
    'ANNOUNCEMENT_INTERVAL': 900,
    'OBS_RECONNECT_DELAY': 15, # More of an implicit delay due to connection attempt timing
    'LOAD_RETRIES': 3,
    'LOAD_RETRY_DELAY': 3,
    'OBS_CHECK_INTERVAL': 60,
    'HELP_MESSAGE_DELAY': 0.2,
    'MAX_MOVIE_MATCH_DISPLAY': 5,
    'TOKEN_REFRESH_INTERVAL': 900,
    'TOKEN_REFRESH_RETRIES': 3,
    'TOKEN_REFRESH_RETRY_DELAY': 1,
}

# Optional: Load MOVIE_LIST_URL into DEFAULT_CONFIG if it's set
if MOVIE_LIST_URL:
    DEFAULT_CONFIG['MOVIE_LIST_URL'] = MOVIE_LIST_URL
