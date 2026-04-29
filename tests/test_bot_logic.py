# MovieBot/tests/test_bot_logic.py
# Updated tests for obs-websocket-py v5 async API and other fixes

import os
import sys
# Ensure project root is on path before importing third-party modules
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import pytest
from unittest.mock import patch, MagicMock, mock_open, call, AsyncMock, PropertyMock
import asyncio
import requests  # For mocking requests exceptions
from datetime import timedelta
import time # For mocking time
import random # For mocking random.choice
import inspect # For MissingRequiredArgument

# Add project root to sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

# Import functions and classes from bot module
from bot import (
    scan_movies, get_movie_duration, refresh_access_token, MovieBot, _get_movie_basename,
    _is_bot_closed, _normalize_access_token
)
from config import (
    DEFAULT_CONFIG, CHANNEL_NAME, SCENE_NAME, MEDIA_SOURCE_NAME,
    save_tokens, get_tokens # Assuming these are still needed, though save_tokens is mocked
)
# Corrected twitchio imports
from twitchio.ext import commands, routines
from twitchio.errors import AuthenticationError
from twitchio.ext import commands
# Import v5 obswebsocket components
from obswebsocket import requests as obs_requests
import obswebsocket.exceptions as obs_exceptions
from obswebsocket import obsws # Import the client class


# --- Test Constants ---
TEST_USER = "test_user"
TEST_BOT_NICK = "testbotnick"
TEST_BOT_ID = "12345test"

MOVIE_PATH_1 = os.path.normpath("C:/moviez/Movie One.mp4")
MOVIE_PATH_2 = os.path.normpath("C:/moviez/Movie Two.mkv")
MOVIE_PATH_3 = os.path.normpath("C:/moviez/Test Movie.mp4")
MOVIE_NAME_1 = _get_movie_basename(MOVIE_PATH_1)
MOVIE_NAME_2 = _get_movie_basename(MOVIE_PATH_2)
MOVIE_NAME_3 = _get_movie_basename(MOVIE_PATH_3)
NONEXISTENT_MOVIE_PATH = os.path.normpath("C:/moviez/NonExistent.mp4")
NONEXISTENT_MOVIE_NAME = "NonExistent Movie Name" # Used in test_vote_command_scenarios
AMBIGUOUS_MOVIE_NAME = "Movie" # Used in test_vote_command_scenarios
UNKNOWN_COMMAND_NAME = "!unknowncommand" # Used in test_event_command_error

TEST_OBS_IP = "127.0.0.1"
TEST_OBS_PORT = 4455
TEST_OBS_PASS = "password"
TEST_SCENE_ITEM_ID = 123

# --- Pytest Fixtures ---

@pytest.fixture
def mock_ctx(mocker):
    """Creates a mock TwitchIO context object."""
    ctx = MagicMock(spec=commands.Context)
    ctx.author = MagicMock(spec=['name']) # Specify attributes for author
    ctx.author.name = TEST_USER
    ctx.channel = MagicMock(spec=['name', 'send'])
    ctx.channel.name = CHANNEL_NAME
    # ctx.view not typically needed unless testing parsing logic that uses it.
    ctx.send = AsyncMock()
    ctx.message = MagicMock(spec=['content']) # Specify attributes for message
    ctx.message.content = ""
    ctx.command = None # Will be set by tests if a command is found
    return ctx

@pytest.fixture
def mock_obs_ws_client(mocker):
    """Creates a mock OBS WebSocket client (v5) instance."""
    mock_ws = mocker.MagicMock(spec=obsws)
    mock_ws.connect = mocker.MagicMock()
    mock_ws.disconnect = mocker.MagicMock()
    mock_ws.call = mocker.MagicMock()
    type(mock_ws).is_connected = mocker.PropertyMock(return_value=True)
    type(mock_ws).is_identified = mocker.PropertyMock(return_value=True)
    return mock_ws

@pytest.fixture
def dummy_bot_instance(mocker, monkeypatch, event_loop, mock_obs_ws_client):
    """ Provides a MovieBot instance for testing, mocking externals (including OBS v5). """
    monkeypatch.setenv("TWITCH_CLIENT_ID", "test_client_id")
    monkeypatch.setenv("TWITCH_CLIENT_SECRET", "test_client_secret")
    monkeypatch.setenv("CHANNEL_NAME", CHANNEL_NAME or "test_channel")
    monkeypatch.setenv("OBS_WEBSOCKET_IP", TEST_OBS_IP)
    monkeypatch.setenv("OBS_WEBSOCKET_PORT", str(TEST_OBS_PORT))
    monkeypatch.setenv("OBS_WEBSOCKET_PASSWORD", TEST_OBS_PASS)
    monkeypatch.setenv("SCENE_NAME", SCENE_NAME)
    monkeypatch.setenv("MEDIA_SOURCE_NAME", MEDIA_SOURCE_NAME)
    monkeypatch.setenv("MOVIE_DIRECTORY", "c:/dummy/movies")
    mocker.patch('config.get_tokens', return_value={'access_token': 'test_access', 'refresh_token': 'test_refresh'})

    mocker.patch('bot.scan_movies', return_value=[MOVIE_PATH_1, MOVIE_PATH_2, MOVIE_PATH_3])
    mocker.patch.object(commands.Bot, '__init__', return_value=None)
    mocker.patch.object(routines.Routine, 'start', return_value=None)
    mocker.patch.object(routines.Routine, 'stop', return_value=None)
    mocker.patch('bot.obsws', return_value=mock_obs_ws_client) # Use the fixture's mock client

    # Create Instance using the actual __init__ after patching its base
    # The MovieBot.__init__ itself will run.
    instance = MovieBot(access_token_param="test_access", refresh_token_initial="test_refresh")
    instance.loop = mocker.MagicMock()

    # Override instance state for tests
    instance.movies = [MOVIE_PATH_1, MOVIE_PATH_2, MOVIE_PATH_3] # Set from mocked scan_movies
    instance.votes = {}
    instance.voter_data = {}
    instance.current_movie_path = None
    instance.current_movie_duration = DEFAULT_CONFIG['DEFAULT_MOVIE_DURATION']
    instance.poll_active = False
    instance.movie_start_time = None
    instance.ws = mock_obs_ws_client # Assign the pre-configured mock client
    instance.obs_connected = True # Default to connected for tests, can be overridden
    instance.last_obs_check_time = 0
    instance._obs_connect_lock = asyncio.Lock() # Re-initialize lock
    instance._closing = asyncio.Event()
    instance._shutdown_requested = False

    # Manually set up commands as twitchio framework would
    instance._commands = {}
    for name, cmd_obj in instance.__class__.__dict__.items():
        if isinstance(cmd_obj, commands.core.Command):
            cmd_obj._instance = instance
            instance._commands[cmd_obj.name] = cmd_obj

    type(instance).nick = PropertyMock(return_value=TEST_BOT_NICK)
    type(instance).user_id = PropertyMock(return_value=TEST_BOT_ID)
    
    instance.mock_channel = MagicMock(spec=['send', 'name'])
    instance.mock_channel.send = AsyncMock()
    instance.mock_channel.name = CHANNEL_NAME
    mocker.patch.object(instance, 'get_channel', return_value=instance.mock_channel)

    instance.periodic_announcement_task = MagicMock(spec=routines.Routine)
    instance.periodic_announcement_task.start = MagicMock()
    instance.periodic_announcement_task.stop = MagicMock()
    instance.periodic_announcement_task._task = MagicMock()
    instance.periodic_announcement_task._task.done = MagicMock(return_value=False)
    instance.periodic_token_refresh_task = MagicMock(spec=routines.Routine)
    instance.periodic_token_refresh_task.start = MagicMock()
    instance.periodic_token_refresh_task.stop = MagicMock()
    instance.periodic_token_refresh_task._task = MagicMock()
    instance.periodic_token_refresh_task._task.done = MagicMock(return_value=False)
    instance.periodic_irc_health_task = MagicMock(spec=routines.Routine)
    instance.periodic_irc_health_task.start = MagicMock()
    instance.periodic_irc_health_task.stop = MagicMock()
    instance.periodic_irc_health_task._task = MagicMock()
    instance.periodic_irc_health_task._task.done = MagicMock(return_value=False)
    instance.periodic_movie_rescan_task = MagicMock(spec=routines.Routine)
    instance.periodic_movie_rescan_task.start = MagicMock()
    instance.periodic_movie_rescan_task.stop = MagicMock()
    instance.periodic_movie_rescan_task._task = MagicMock()
    instance.periodic_movie_rescan_task._task.done = MagicMock(return_value=False)
    mocker.patch.object(instance, 'start_first_movie_task')

    # Mock loop methods - these will be MagicMocks that record calls
    # Tests can override return_value or side_effect if specific behavior is needed per test
    instance.loop.time = mocker.MagicMock(return_value=time.time())
    instance.loop.call_later = mocker.MagicMock(return_value=mocker.MagicMock(spec=asyncio.Handle))
    instance.loop.create_task = mocker.MagicMock(return_value=mocker.MagicMock(spec=asyncio.Task))
    
    return instance


# --- Test Functions: Core Logic (Module Level) ---

@patch("bot.os.path.isdir")
@patch("bot.os.scandir")
def test_scan_movies_finds_videos(mock_os_scandir, mock_os_isdir):  # Removed monkeypatch as it's not used
    mock_entry1 = MagicMock(spec=os.DirEntry); mock_entry1.name = "vid1.mp4"; mock_entry1.path = "/test_dir/vid1.mp4"; mock_entry1.is_file.return_value = True; mock_entry1.is_dir.return_value = False
    mock_entry2 = MagicMock(spec=os.DirEntry); mock_entry2.name = "pic.jpg"; mock_entry2.path = "/test_dir/pic.jpg"; mock_entry2.is_file.return_value = True; mock_entry2.is_dir.return_value = False
    mock_entry_symlink = MagicMock(spec=os.DirEntry); mock_entry_symlink.name = "link"; mock_entry_symlink.path = "/test_dir/link"
    mock_entry_symlink.is_file.side_effect = lambda *, follow_symlinks=True: False
    mock_entry_symlink.is_dir.side_effect = lambda *, follow_symlinks=True: True if follow_symlinks else False
    mock_entry_subdir = MagicMock(spec=os.DirEntry); mock_entry_subdir.name = "subdir"; mock_entry_subdir.path = "/test_dir/subdir"; mock_entry_subdir.is_file.return_value = False; mock_entry_subdir.is_dir.return_value = True
    mock_entry_sub1 = MagicMock(spec=os.DirEntry); mock_entry_sub1.name = "vid2.mkv"; mock_entry_sub1.path = "/test_dir/subdir/vid2.mkv"; mock_entry_sub1.is_file.return_value = True; mock_entry_sub1.is_dir.return_value = False
    mock_entry_sub_empty = MagicMock(spec=os.DirEntry); mock_entry_sub_empty.name = "empty"; mock_entry_sub_empty.path = "/test_dir/empty"; mock_entry_sub_empty.is_file.return_value = False; mock_entry_sub_empty.is_dir.return_value = True

    def make_scandir(entries):
        cm = MagicMock()
        cm.__enter__.return_value = iter(entries)
        cm.__exit__.return_value = False
        return cm

    def scandir_side_effect(path_arg):
        norm_path = os.path.normpath(path_arg)
        if norm_path == os.path.normpath("/test_dir"):
            return make_scandir([mock_entry1, mock_entry2, mock_entry_symlink, mock_entry_subdir, mock_entry_sub_empty])
        elif norm_path == os.path.normpath("/test_dir/subdir"):
            return make_scandir([mock_entry_sub1])
        elif norm_path == os.path.normpath("/test_dir/empty"):
            return make_scandir([])
        raise FileNotFoundError

    mock_os_scandir.side_effect = scandir_side_effect
    mock_os_isdir.return_value = True  # Assume root dir exists

    result = scan_movies(directory="/test_dir")
    normalized_result = sorted([os.path.normpath(p) for p in result])
    expected_paths_normalized = sorted([
        os.path.normpath("/test_dir/vid1.mp4"),
        os.path.normpath("/test_dir/subdir/vid2.mkv"),
    ])
    assert normalized_result == expected_paths_normalized
    assert mock_os_scandir.call_count == 3
    mock_os_isdir.assert_called_once_with("/test_dir")


@patch("bot.os.scandir")
@patch("bot.os.path.isdir", return_value=True)
def test_scan_movies_closes_scandir(mock_isdir, mock_scandir):
    cm = MagicMock()
    cm.__enter__.return_value = iter([])
    cm.__exit__ = MagicMock(return_value=False)
    mock_scandir.return_value = cm

    scan_movies(directory="/any")

    mock_scandir.assert_called_once_with("/any")
    cm.__enter__.assert_called_once()
    cm.__exit__.assert_called_once()


@patch("bot.os.path.isdir", return_value=False)
def test_scan_movies_dir_not_found(mock_os_isdir, caplog):
    test_dir = "/nonexistent"
    result = scan_movies(directory=test_dir)
    assert result == []
    assert f"Movie directory not found: {test_dir}" in caplog.text
    mock_os_isdir.assert_called_once_with(test_dir)


@pytest.mark.asyncio
@patch("bot.shutil.which", return_value="/usr/bin/ffprobe")
@patch("bot.VideoFileClip")
@patch("bot.asyncio.to_thread", new_callable=AsyncMock)
@patch("bot.asyncio.create_subprocess_exec", new_callable=AsyncMock)
async def test_get_movie_duration_success(mock_create_proc, mock_to_thread, mock_vfc_constructor, mock_which):
    mock_proc = AsyncMock()
    mock_proc.communicate.return_value = (b"123.45\n", b"")
    mock_proc.returncode = 0
    mock_create_proc.return_value = mock_proc

    duration = await get_movie_duration(MOVIE_PATH_1)

    assert duration == 123
    mock_create_proc.assert_called_once()
    mock_to_thread.assert_not_called()
    mock_vfc_constructor.assert_not_called()


@pytest.mark.asyncio
@patch("bot.shutil.which", return_value="/usr/bin/ffprobe")
@patch("bot.asyncio.to_thread", new_callable=AsyncMock)
@patch("bot.asyncio.create_subprocess_exec", new_callable=AsyncMock)
async def test_get_movie_duration_failure(mock_create_proc, mock_to_thread, mock_which, caplog, monkeypatch):
    mock_create_proc.side_effect = Exception("ffprobe missing")
    mock_to_thread.side_effect = Exception("moviepy fail")
    test_default = 9999
    monkeypatch.setitem(DEFAULT_CONFIG, 'DEFAULT_MOVIE_DURATION', test_default)

    duration = await get_movie_duration("bad_file.mp4")

    assert duration == test_default
    assert "Using default" in caplog.text
    mock_create_proc.assert_called_once()
    mock_to_thread.assert_called_once()


@pytest.mark.asyncio
@patch("bot.shutil.which", return_value=None)
@patch("bot.asyncio.to_thread", new_callable=AsyncMock, return_value=55)
@patch("bot.asyncio.create_subprocess_exec", new_callable=AsyncMock)
async def test_get_movie_duration_no_ffprobe(mock_create_proc, mock_to_thread, mock_which):
    duration = await get_movie_duration(MOVIE_PATH_1)

    assert duration == 55
    mock_create_proc.assert_not_called()
    mock_to_thread.assert_awaited_once()


@pytest.mark.asyncio
@patch("bot.save_tokens")
@patch("asyncio.to_thread", new_callable=AsyncMock)
async def test_refresh_access_token_success(mock_to_thread, mock_save_tokens_func, monkeypatch):
    monkeypatch.setenv("TWITCH_CLIENT_ID", "test_client_id")
    monkeypatch.setenv("TWITCH_CLIENT_SECRET", "test_client_secret")
    mock_response = MagicMock(spec=requests.Response)
    mock_response.json.return_value = {"access_token": "new_access", "refresh_token": "new_refresh"}
    mock_response.raise_for_status = MagicMock()
    mock_to_thread.return_value = mock_response

    access, refresh = await refresh_access_token("old_refresh")

    assert access == "new_access"
    assert refresh == "new_refresh"
    mock_to_thread.assert_called_once()
    assert mock_to_thread.call_args[0][0] == requests.post
    assert mock_to_thread.call_args[1]['url'] == 'https://id.twitch.tv/oauth2/token'
    assert mock_to_thread.call_args[1]['data']['refresh_token'] == 'old_refresh'
    mock_save_tokens_func.assert_called_once_with("new_access", "new_refresh")


@pytest.mark.asyncio
@patch("bot.save_tokens")
@patch("asyncio.to_thread", new_callable=AsyncMock)
async def test_refresh_access_token_http_error(mock_to_thread, mock_save_tokens_func, caplog, monkeypatch):
    monkeypatch.setenv("TWITCH_CLIENT_ID", "test_client_id")
    monkeypatch.setenv("TWITCH_CLIENT_SECRET", "test_client_secret")
    mock_response = MagicMock(spec=requests.Response)
    mock_response.status_code = 400
    mock_response.text = '{"message":"invalid refresh token"}'
    http_error = requests.exceptions.HTTPError("API Error", response=mock_response)
    mock_response.raise_for_status.side_effect = http_error
    mock_to_thread.return_value = mock_response

    access, refresh = await refresh_access_token("old_bad_refresh")

    assert access is None
    assert refresh == "old_bad_refresh"
    mock_to_thread.assert_called_once()
    mock_response.raise_for_status.assert_called_once()
    mock_save_tokens_func.assert_not_called()


@pytest.mark.asyncio
async def test_periodic_token_refresh_updates_bot(mocker, monkeypatch):
    """Ensure the periodic token refresh task updates the bot tokens."""
    monkeypatch.setenv("TWITCH_CLIENT_ID", "client")
    monkeypatch.setenv("TWITCH_CLIENT_SECRET", "secret")
    monkeypatch.setenv("CHANNEL_NAME", CHANNEL_NAME or "test_channel")
    monkeypatch.setenv("OBS_WEBSOCKET_IP", TEST_OBS_IP)
    monkeypatch.setenv("OBS_WEBSOCKET_PORT", str(TEST_OBS_PORT))
    monkeypatch.setenv("OBS_WEBSOCKET_PASSWORD", TEST_OBS_PASS)
    mocker.patch('bot.scan_movies', return_value=[])
    mocker.patch.object(commands.Bot, '__init__', return_value=None)
    mocker.patch('bot.obsws', return_value=MagicMock())

    bot_instance = MovieBot(access_token_param="old_access", refresh_token_initial="old_refresh")
    bot_instance._http = MagicMock()
    # Create a mock websocket connection object matching twitchio's internal structure
    bot_instance._connection = MagicMock()
    bot_instance._connection._token = "old_access"
    refresh_mock = AsyncMock(return_value=("new_access", "new_refresh"))
    mocker.patch('bot.refresh_access_token', refresh_mock)

    await bot_instance.periodic_token_refresh_task._coro(bot_instance)

    refresh_mock.assert_awaited_once_with("old_refresh")
    assert bot_instance._http.token == "new_access"
    assert bot_instance._connection._token == "new_access"
    assert bot_instance.current_refresh_token == "new_refresh"


# --- Test Bot Class Methods ---

def test_pick_random_movie_empty(dummy_bot_instance):
    dummy_bot_instance.movies = []
    assert dummy_bot_instance.pick_random_movie() is None

def test_pick_random_movie_single(dummy_bot_instance):
    dummy_bot_instance.movies = [MOVIE_PATH_1]
    assert dummy_bot_instance.pick_random_movie() == MOVIE_PATH_1

@patch("random.choice")
def test_pick_random_movie_multiple(mock_random_choice, dummy_bot_instance):
    # dummy_bot_instance.movies is already [MOVIE_PATH_1, MOVIE_PATH_2, MOVIE_PATH_3] from fixture
    mock_random_choice.return_value = MOVIE_PATH_2
    picked = dummy_bot_instance.pick_random_movie()
    assert picked == MOVIE_PATH_2
    mock_random_choice.assert_called_once_with(dummy_bot_instance.movies)


@pytest.mark.asyncio
async def test_connect_obs_success(dummy_bot_instance, mock_obs_ws_client, mocker):
    mock_obs_ws_client.connect.reset_mock()
    mocker.patch.object(dummy_bot_instance, '_check_obs_scene_source', new_callable=AsyncMock, return_value=True)
    dummy_bot_instance.ws = None  # Start as if not connected
    dummy_bot_instance.obs_connected = False

    result = await dummy_bot_instance.connect_obs()  # This will use the mock_obs_ws_client via the fixture

    assert result is True
    assert dummy_bot_instance.ws is mock_obs_ws_client
    assert dummy_bot_instance.obs_connected is True
    mock_obs_ws_client.connect.assert_called_once()
    dummy_bot_instance._check_obs_scene_source.assert_awaited_once()


@pytest.mark.asyncio
async def test_connect_obs_connection_failure(dummy_bot_instance, mock_obs_ws_client, mocker, caplog):
    mock_obs_ws_client.connect.side_effect = obs_exceptions.ConnectionFailure("Test connect error")
    mocker.patch.object(dummy_bot_instance, '_check_obs_scene_source', new_callable=AsyncMock)
    dummy_bot_instance.ws = None
    dummy_bot_instance.obs_connected = False

    result = await dummy_bot_instance.connect_obs()

    assert result is False
    assert dummy_bot_instance.ws is None
    assert dummy_bot_instance.obs_connected is False
    mock_obs_ws_client.connect.assert_called_once()
    dummy_bot_instance._check_obs_scene_source.assert_not_awaited()
    assert "OBS WebSocket connection failed: Test connect error" in caplog.text

# ... (other connect_obs tests: identify_timeout, scene_check_failure - seem okay) ...
@pytest.mark.asyncio
async def test_connect_obs_timeout(dummy_bot_instance, mock_obs_ws_client, mocker, caplog):
    mock_obs_ws_client.connect.side_effect = asyncio.TimeoutError("Identify timeout")
    mocker.patch.object(dummy_bot_instance, '_check_obs_scene_source', new_callable=AsyncMock)
    dummy_bot_instance.ws = None
    dummy_bot_instance.obs_connected = False

    result = await dummy_bot_instance.connect_obs()

    assert result is False
    assert dummy_bot_instance.ws is None
    assert dummy_bot_instance.obs_connected is False
    mock_obs_ws_client.connect.assert_called_once()
    dummy_bot_instance._check_obs_scene_source.assert_not_awaited()
    assert "OBS WebSocket connection timed out" in caplog.text


@pytest.mark.asyncio
async def test_connect_obs_scene_check_failure(dummy_bot_instance, mock_obs_ws_client, mocker, caplog):
    mock_obs_ws_client.connect.return_value = None
    mocker.patch.object(dummy_bot_instance, '_check_obs_scene_source', new_callable=AsyncMock, return_value=False)
    dummy_bot_instance.ws = None
    dummy_bot_instance.obs_connected = False

    result = await dummy_bot_instance.connect_obs()

    assert result is False
    assert dummy_bot_instance.ws is None
    assert dummy_bot_instance.obs_connected is False
    mock_obs_ws_client.connect.assert_called_once()
    dummy_bot_instance._check_obs_scene_source.assert_awaited_once()
    mock_obs_ws_client.disconnect.assert_called_once()
    assert "OBS connection successful, but Scene or Media Source check failed." in caplog.text


@pytest.mark.asyncio
async def test_ensure_obs_connection_already_connected_passive_check_ok(dummy_bot_instance, mock_obs_ws_client, mocker):
    dummy_bot_instance.obs_connected = True
    dummy_bot_instance.ws = mock_obs_ws_client 
    dummy_bot_instance.last_obs_check_time = 0
    mock_time_monotonic = mocker.patch('time.monotonic', return_value=DEFAULT_CONFIG['OBS_CHECK_INTERVAL'] * 3)
    # We expect connect_obs NOT to be called if passive check is fine
    dummy_bot_instance.connect_obs = AsyncMock() # Make it an AsyncMock to check calls

    mock_obs_ws_client.call.return_value = MagicMock()

    result = await dummy_bot_instance.ensure_obs_connection()

    assert result is True
    mock_obs_ws_client.call.assert_called_once()
    assert isinstance(mock_obs_ws_client.call.call_args[0][0], obs_requests.GetVersion)
    assert dummy_bot_instance.last_obs_check_time == mock_time_monotonic.return_value
    dummy_bot_instance.connect_obs.assert_not_awaited()


@pytest.mark.asyncio
async def test_ensure_obs_connection_already_connected_passive_check_fails(dummy_bot_instance, mock_obs_ws_client, mocker, caplog):
    dummy_bot_instance.obs_connected = True
    dummy_bot_instance.ws = mock_obs_ws_client
    dummy_bot_instance.last_obs_check_time = 0
    mocker.patch('time.monotonic', return_value=DEFAULT_CONFIG['OBS_CHECK_INTERVAL'] * 3)
    dummy_bot_instance.connect_obs = AsyncMock(return_value=True) # Mock connect_obs to succeed

    mock_obs_ws_client.call.side_effect = obs_exceptions.ObjectError("Passive check failed")

    result = await dummy_bot_instance.ensure_obs_connection()

    assert result is True
    mock_obs_ws_client.call.assert_called_once()
    assert "Passive OBS check failed" in caplog.text
    dummy_bot_instance.connect_obs.assert_awaited_once()
    assert dummy_bot_instance.obs_connected is True


@pytest.mark.asyncio
async def test_ensure_obs_connection_needs_reconnect_success(dummy_bot_instance, mocker):
    dummy_bot_instance.obs_connected = False
    dummy_bot_instance.ws = None
    # Mock connect_obs on the instance for this specific test path
    dummy_bot_instance.connect_obs = AsyncMock(return_value=True)

    result = await dummy_bot_instance.ensure_obs_connection()

    assert result is True
    dummy_bot_instance.connect_obs.assert_awaited_once()
    assert dummy_bot_instance.obs_connected is True


@pytest.mark.asyncio
async def test_ensure_obs_connection_needs_reconnect_fails(dummy_bot_instance, mocker, caplog):
    dummy_bot_instance.obs_connected = False
    dummy_bot_instance.ws = None
    dummy_bot_instance.connect_obs = AsyncMock(return_value=False)

    result = await dummy_bot_instance.ensure_obs_connection()

    assert result is False
    dummy_bot_instance.connect_obs.assert_awaited_once()
    assert dummy_bot_instance.obs_connected is False
    assert "Failed to establish OBS connection in ensure_obs_connection." in caplog.text


@pytest.mark.asyncio
async def test_safe_obs_call_success(dummy_bot_instance, mock_obs_ws_client, mocker):
    dummy_bot_instance.ensure_obs_connection = AsyncMock(return_value=True)
    mock_response_data = {"status": "ok"}
    mock_obs_ws_client.call.return_value = mock_response_data

    request = obs_requests.GetVersion()
    response = await dummy_bot_instance.safe_obs_call(request)

    assert response == mock_response_data
    dummy_bot_instance.ensure_obs_connection.assert_awaited_once()
    mock_obs_ws_client.call.assert_called_once_with(request)


@pytest.mark.asyncio
async def test_safe_obs_call_connection_fails_initially(dummy_bot_instance, mock_obs_ws_client, mocker, caplog): # Added mock_obs_ws_client
    dummy_bot_instance.ensure_obs_connection = AsyncMock(return_value=False)
    # mock_obs_ws_client.call is already a MagicMock from the fixture

    request = obs_requests.GetVersion()
    response = await dummy_bot_instance.safe_obs_call(request)

    assert response is None
    dummy_bot_instance.ensure_obs_connection.assert_awaited_once()
    mock_obs_ws_client.call.assert_not_called()
    assert "Cannot make OBS call (GetVersion): Connection not established." in caplog.text


@pytest.mark.asyncio
async def test_safe_obs_call_request_failure(dummy_bot_instance, mock_obs_ws_client, mocker, caplog):
    dummy_bot_instance.ensure_obs_connection = AsyncMock(return_value=True)
    request_error = obs_exceptions.ObjectError("Invalid request")
    mock_obs_ws_client.call.side_effect = request_error

    request = obs_requests.SetCurrentProgramScene(sceneName="NonExistentScene")
    response = await dummy_bot_instance.safe_obs_call(request)

    assert response is None
    dummy_bot_instance.ensure_obs_connection.assert_awaited_once()
    mock_obs_ws_client.call.assert_called_once_with(request)
    assert "OBS Request Failure during call 'SetCurrentProgramScene': Invalid request" in caplog.text
    assert dummy_bot_instance.obs_connected is True


@pytest.mark.asyncio
async def test_safe_obs_call_connection_error_during_call(dummy_bot_instance, mock_obs_ws_client, mocker, caplog):
    dummy_bot_instance.ensure_obs_connection = AsyncMock(return_value=True)
    connection_error = ConnectionError("OBS disconnected during call")
    mock_obs_ws_client.call.side_effect = connection_error

    request = obs_requests.GetVersion()
    response = await dummy_bot_instance.safe_obs_call(request)

    assert response is None
    dummy_bot_instance.ensure_obs_connection.assert_awaited_once()
    mock_obs_ws_client.call.assert_called_once_with(request)
    assert "OBS Connection Failure/Timeout during call 'GetVersion'" in caplog.text
    assert dummy_bot_instance.obs_connected is False


@pytest.mark.asyncio
async def test_help_command_sends_lines(dummy_bot_instance, mock_ctx, mocker):
    mock_sleep = mocker.patch("asyncio.sleep", new_callable=AsyncMock)
    help_cmd = dummy_bot_instance._commands.get('help')
    assert help_cmd is not None, "Help command not found on instance"

    await help_cmd._callback(dummy_bot_instance, mock_ctx)  # Invoke callback with context

    expected_lines_count = 6 
    assert mock_ctx.send.await_count == expected_lines_count
    mock_ctx.send.assert_any_await(f"--- {TEST_BOT_NICK} Movie Bot ---")
    mock_ctx.send.assert_any_await(f"`!movies` : Link to movie list.")
    assert mock_sleep.await_count == expected_lines_count # sleep is called for each line
    if expected_lines_count > 0 : # only assert if sleep was expected
        mock_sleep.assert_any_await(DEFAULT_CONFIG['HELP_MESSAGE_DELAY'])


@pytest.mark.parametrize(
    "scenario, movie_name_input, initial_movies_list, initial_poll_active, initial_votes, initial_voter_data, expected_message_key, expected_votes_final, expected_voter_data_final",
    [
        ("exact_over_substring", MOVIE_NAME_1, [MOVIE_PATH_1, os.path.normpath("C:/moviez/Movie One Extra.mkv")], True, {}, {}, "vote_registered_exact", {MOVIE_PATH_1: 1}, {TEST_USER: MOVIE_PATH_1}),
        ("new_vote", MOVIE_NAME_1, [MOVIE_PATH_1, MOVIE_PATH_2], True, {}, {}, "vote_registered", {MOVIE_PATH_1: 1}, {TEST_USER: MOVIE_PATH_1}),
        ("poll_inactive", MOVIE_NAME_1, [MOVIE_PATH_1], False, {}, {}, "poll_inactive", {}, {}),
        ("movie_not_found", NONEXISTENT_MOVIE_NAME, [MOVIE_PATH_1], True, {}, {}, "not_found", {}, {}),
        ("multiple_matches", AMBIGUOUS_MOVIE_NAME, [MOVIE_PATH_1, MOVIE_PATH_2, MOVIE_PATH_3], True, {}, {}, "multiple_matches", {}, {}),
        ("change_vote", MOVIE_NAME_2, [MOVIE_PATH_1, MOVIE_PATH_2], True, {MOVIE_PATH_1: 1}, {TEST_USER: MOVIE_PATH_1}, "vote_registered_change", {MOVIE_PATH_2: 1}, {TEST_USER: MOVIE_PATH_2}),
        ("vote_same", MOVIE_NAME_1, [MOVIE_PATH_1], True, {MOVIE_PATH_1: 1}, {TEST_USER: MOVIE_PATH_1}, "already_voted", {MOVIE_PATH_1: 1}, {TEST_USER: MOVIE_PATH_1}),
    ]
)
@pytest.mark.asyncio
async def test_vote_command_scenarios(
    dummy_bot_instance, mock_ctx,
    scenario, movie_name_input, initial_movies_list, initial_poll_active,
    initial_votes, initial_voter_data, expected_message_key,
    expected_votes_final, expected_voter_data_final
): # Removed mocker as it's not directly used here
    dummy_bot_instance.poll_active = initial_poll_active
    dummy_bot_instance.movies = initial_movies_list 
    dummy_bot_instance.votes = initial_votes.copy()
    dummy_bot_instance.voter_data = initial_voter_data.copy()

    multi_match_display_list = [_get_movie_basename(p) for p in [MOVIE_PATH_1, MOVIE_PATH_2, MOVIE_PATH_3][:DEFAULT_CONFIG['MAX_MOVIE_MATCH_DISPLAY']]]


    expected_messages = {
        "vote_registered": f"@{TEST_USER}, vote registered for '{MOVIE_NAME_1}'!",
        "vote_registered_change": f"@{TEST_USER}, vote registered for '{MOVIE_NAME_2}'!",
        "vote_registered_exact": f"@{TEST_USER}, vote registered for '{MOVIE_NAME_1}'!",
        "poll_inactive": "No active poll now, wait for the next movie!",
        "not_found": f"Sorry @{TEST_USER}, couldn't find '{NONEXISTENT_MOVIE_NAME}'. Use !movies for the list.",
        "multiple_matches": f"Multiple matches for '{AMBIGUOUS_MOVIE_NAME}': {', '.join(multi_match_display_list)}. Be more specific.",
        "already_voted": f"@{TEST_USER}, you already voted for '{MOVIE_NAME_1}'."
    }

    vote_cmd = dummy_bot_instance._commands.get('vote')
    assert vote_cmd is not None

    await vote_cmd._callback(dummy_bot_instance, mock_ctx, movie_name=movie_name_input)

    expected_msg = expected_messages.get(expected_message_key)
    assert expected_msg, f"Missing expected message key: {expected_message_key} for scenario {scenario}"
    mock_ctx.send.assert_awaited_once_with(expected_msg)

    assert dummy_bot_instance.votes == expected_votes_final, f"Votes mismatch for scenario: {scenario}"
    assert dummy_bot_instance.voter_data == expected_voter_data_final, f"Voter data mismatch for scenario: {scenario}"


@pytest.mark.asyncio
async def test_get_scene_item_id_success(dummy_bot_instance, mocker): # mock_obs_ws_client removed as safe_obs_call is mocked
    mock_safe_call = mocker.patch.object(dummy_bot_instance, 'safe_obs_call', new_callable=AsyncMock)
    mock_response = MagicMock()
    # Simulate the attribute that bot.py checks
    type(mock_response).scene_item_id = PropertyMock(return_value=TEST_SCENE_ITEM_ID)
    mock_safe_call.return_value = mock_response

    item_id = await dummy_bot_instance._get_scene_item_id(SCENE_NAME, MEDIA_SOURCE_NAME)

    assert item_id == TEST_SCENE_ITEM_ID
    mock_safe_call.assert_awaited_once()
    request_arg = mock_safe_call.await_args[0][0]
    assert isinstance(request_arg, obs_requests.GetSceneItemId)
    assert request_arg.dataout.get('sceneName') == SCENE_NAME
    assert request_arg.dataout.get('sourceName') == MEDIA_SOURCE_NAME


@pytest.mark.asyncio
async def test_get_scene_item_id_datain_fallback(dummy_bot_instance, mocker):
    mock_safe_call = mocker.patch.object(dummy_bot_instance, 'safe_obs_call', new_callable=AsyncMock)
    from types import SimpleNamespace
    mock_response = SimpleNamespace(datain={'sceneItemId': TEST_SCENE_ITEM_ID})
    mock_safe_call.return_value = mock_response

    item_id = await dummy_bot_instance._get_scene_item_id(SCENE_NAME, MEDIA_SOURCE_NAME)

    assert item_id == TEST_SCENE_ITEM_ID
    mock_safe_call.assert_awaited_once()
    request_arg = mock_safe_call.await_args[0][0]
    assert isinstance(request_arg, obs_requests.GetSceneItemId)
    assert request_arg.dataout.get('sceneName') == SCENE_NAME
    assert request_arg.dataout.get('sourceName') == MEDIA_SOURCE_NAME


@pytest.mark.asyncio
async def test_get_scene_item_id_failure(dummy_bot_instance, mocker, caplog):
    mock_safe_call = mocker.patch.object(dummy_bot_instance, 'safe_obs_call', new_callable=AsyncMock)
    mock_safe_call.return_value = None # Simulate safe_call failing

    item_id = await dummy_bot_instance._get_scene_item_id(SCENE_NAME, MEDIA_SOURCE_NAME)

    assert item_id is None
    mock_safe_call.assert_awaited_once()
    assert f"Failed to get Scene Item ID for '{MEDIA_SOURCE_NAME}'" in caplog.text


@pytest.mark.asyncio
async def test_check_obs_scene_source_get_scenes_method(dummy_bot_instance, mocker):
    class SceneListResp:
        def getScenes(self):
            return [{'name': SCENE_NAME}]

    class SceneItemIdResp:
        scene_item_id = TEST_SCENE_ITEM_ID

    dummy_bot_instance.ws = MagicMock()
    mocker.patch.object(dummy_bot_instance, '_ws_is_identified', return_value=True)
    mock_to_thread = mocker.patch(
        'asyncio.to_thread', new_callable=AsyncMock, side_effect=[SceneListResp(), SceneItemIdResp()]
    )

    result = await dummy_bot_instance._check_obs_scene_source()

    assert result is True
    assert mock_to_thread.await_count == 2


@pytest.mark.asyncio
async def test_load_media_in_obs_success(dummy_bot_instance, mocker):
    mocker.patch.object(dummy_bot_instance, '_get_scene_item_id', new_callable=AsyncMock, return_value=TEST_SCENE_ITEM_ID)
    mock_safe_call = mocker.patch.object(dummy_bot_instance, 'safe_obs_call', new_callable=AsyncMock, return_value=MagicMock())
    mock_sleep = mocker.patch('asyncio.sleep', new_callable=AsyncMock)

    result = await dummy_bot_instance.load_media_in_obs(MOVIE_PATH_1)

    assert result is True
    dummy_bot_instance._get_scene_item_id.assert_awaited_once_with(SCENE_NAME, MEDIA_SOURCE_NAME)
    assert mock_safe_call.await_count == 5
    hide_call_args = mock_safe_call.await_args_list[0][0][0]
    assert isinstance(hide_call_args, obs_requests.SetSceneItemEnabled)
    assert hide_call_args.dataout.get('sceneItemId') == TEST_SCENE_ITEM_ID
    assert hide_call_args.dataout.get('sceneItemEnabled') is False
    
    set_call_args = mock_safe_call.await_args_list[1][0][0]
    assert isinstance(set_call_args, obs_requests.SetInputSettings)
    assert set_call_args.dataout.get('inputName') == MEDIA_SOURCE_NAME
    assert set_call_args.dataout.get('inputSettings') == {"local_file": os.path.normpath(MOVIE_PATH_1)}

    show_call_args = mock_safe_call.await_args_list[2][0][0]
    assert isinstance(show_call_args, obs_requests.SetSceneItemEnabled)
    assert show_call_args.dataout.get('sceneItemId') == TEST_SCENE_ITEM_ID
    assert show_call_args.dataout.get('sceneItemEnabled') is True

    set_scene_call_args = mock_safe_call.await_args_list[3][0][0]
    assert isinstance(set_scene_call_args, obs_requests.SetCurrentProgramScene)
    assert set_scene_call_args.dataout.get('sceneName') == SCENE_NAME

    restart_call_args = mock_safe_call.await_args_list[4][0][0]
    assert isinstance(restart_call_args, obs_requests.TriggerMediaInputAction)
    assert restart_call_args.dataout.get('inputName') == MEDIA_SOURCE_NAME
    assert restart_call_args.dataout.get('mediaAction') == "OBS_WEBSOCKET_MEDIA_INPUT_ACTION_RESTART"
    assert mock_sleep.await_count == 3


@pytest.mark.asyncio
async def test_load_media_in_obs_get_id_fails(dummy_bot_instance, mocker, caplog):
    mocker.patch.object(dummy_bot_instance, '_get_scene_item_id', new_callable=AsyncMock, return_value=None)
    mock_safe_call = mocker.patch.object(dummy_bot_instance, 'safe_obs_call', new_callable=AsyncMock)

    result = await dummy_bot_instance.load_media_in_obs(MOVIE_PATH_1)

    assert result is False
    dummy_bot_instance._get_scene_item_id.assert_awaited_once_with(SCENE_NAME, MEDIA_SOURCE_NAME)
    mock_safe_call.assert_not_awaited()
    assert "Cannot load media: Failed to get Scene Item ID" in caplog.text


@pytest.mark.asyncio
async def test_load_media_in_obs_set_settings_fails_retries(dummy_bot_instance, mocker, caplog):
    mocker.patch.object(dummy_bot_instance, '_get_scene_item_id', new_callable=AsyncMock, return_value=TEST_SCENE_ITEM_ID)
    mock_safe_call = mocker.patch.object(dummy_bot_instance, 'safe_obs_call', new_callable=AsyncMock)
    mock_sleep = mocker.patch('asyncio.sleep', new_callable=AsyncMock)

    def safe_call_side_effect(request):
        if isinstance(request, obs_requests.SetInputSettings):
            return None 
        return MagicMock()
    mock_safe_call.side_effect = safe_call_side_effect

    result = await dummy_bot_instance.load_media_in_obs(MOVIE_PATH_1)

    assert result is False
    # Hide (success) + SetInputSettings (fail) = 2 calls per retry. Total retries = LOAD_RETRIES.
    assert mock_safe_call.await_count == DEFAULT_CONFIG['LOAD_RETRIES'] * 2 
    # Sleep between hide/set (0.2s) + sleep after SetInputSettings (0.8s if successful) + LOAD_RETRY_DELAY
    # Here, SetInputSettings fails, so only 0.2s sleep inside loop + LOAD_RETRY_DELAY before next attempt.
    # Total sleeps: (0.2s delay within attempt) * LOAD_RETRIES + (LOAD_RETRY_DELAY before next attempt) * (LOAD_RETRIES - 1)
    # Let's check specific sleep calls or total count more carefully.
    # Inside loop: sleep(0.2). If SetInput fails, then sleep(LOAD_RETRY_DELAY) if not last attempt.
    # Total 0.2s sleeps = LOAD_RETRIES
    # Total LOAD_RETRY_DELAY sleeps = LOAD_RETRIES - 1
    expected_sleep_count = DEFAULT_CONFIG['LOAD_RETRIES'] + (DEFAULT_CONFIG['LOAD_RETRIES'] -1) if DEFAULT_CONFIG['LOAD_RETRIES'] > 0 else 0
    assert mock_sleep.await_count == expected_sleep_count

    assert f"Failed to load media '{MOVIE_NAME_1}' into OBS after {DEFAULT_CONFIG['LOAD_RETRIES']} attempts" in caplog.text


@pytest.mark.asyncio
async def test_load_media_in_obs_restart_fails_retries(dummy_bot_instance, mocker, caplog):
    mocker.patch.object(dummy_bot_instance, '_get_scene_item_id', new_callable=AsyncMock, return_value=TEST_SCENE_ITEM_ID)
    mock_safe_call = mocker.patch.object(dummy_bot_instance, 'safe_obs_call', new_callable=AsyncMock)
    mock_sleep = mocker.patch('asyncio.sleep', new_callable=AsyncMock)

    def safe_call_side_effect(request):
        if isinstance(request, obs_requests.TriggerMediaInputAction):
            return None
        return MagicMock()

    mock_safe_call.side_effect = safe_call_side_effect

    result = await dummy_bot_instance.load_media_in_obs(MOVIE_PATH_1)

    assert result is False
    assert mock_safe_call.await_count == DEFAULT_CONFIG['LOAD_RETRIES'] * 5
    expected_sleep_count = (DEFAULT_CONFIG['LOAD_RETRIES'] * 3) + (DEFAULT_CONFIG['LOAD_RETRIES'] - 1)
    assert mock_sleep.await_count == expected_sleep_count
    assert f"Failed to load media '{MOVIE_NAME_1}' into OBS after {DEFAULT_CONFIG['LOAD_RETRIES']} attempts" in caplog.text


@pytest.mark.asyncio
@patch("bot.get_movie_duration", new_callable=AsyncMock)
@patch.object(MovieBot, "load_media_in_obs", new_callable=AsyncMock)
@patch("asyncio.to_thread", new_callable=AsyncMock)
async def test_play_movie_success(
    mock_async_to_thread, mock_load_media, mock_get_duration,
    dummy_bot_instance, mocker # mocker is already a fixture
):
    test_path = MOVIE_PATH_1
    test_duration = 180
    mock_async_to_thread.return_value = True # os.path.exists
    mock_load_media.return_value = True
    mock_get_duration.return_value = test_duration
    start_time_val = 1000.0
    dummy_bot_instance.loop.time.return_value = start_time_val # Mock loop.time from fixture
    
    # Mock call_later and create_task from the already mocked loop in the fixture
    mock_call_later = dummy_bot_instance.loop.call_later
    mock_create_task = dummy_bot_instance.loop.create_task
    # Ensure create_task returns a mock task for consistency
    mock_create_task.return_value = mocker.MagicMock(spec=asyncio.Task)


    dummy_bot_instance.current_movie_path = None # Reset state
    dummy_bot_instance.poll_active = False
    dummy_bot_instance.votes = {MOVIE_PATH_2: 1} 
    dummy_bot_instance.voter_data = {"other": MOVIE_PATH_2}

    await dummy_bot_instance.play_movie(test_path)

    mock_async_to_thread.assert_awaited_once_with(os.path.exists, test_path)
    mock_load_media.assert_awaited_once_with(test_path)
    mock_get_duration.assert_awaited_once_with(test_path)
    assert dummy_bot_instance.current_movie_path == test_path
    assert dummy_bot_instance.current_movie_duration == test_duration
    assert dummy_bot_instance.movie_start_time == start_time_val
    assert dummy_bot_instance.poll_active is True
    assert dummy_bot_instance.votes == {} 
    assert dummy_bot_instance.voter_data == {}

    mock_call_later.assert_called_once()
    delay_arg, lambda_func = mock_call_later.call_args[0] # bot.py uses lambda
    assert delay_arg == test_duration
    assert callable(lambda_func)

    # Execute the lambda to simulate scheduling
    returned_value_from_lambda = lambda_func() # This should call create_task

    mock_create_task.assert_called_once()
    scheduled_coro = mock_create_task.call_args[0][0]
    assert asyncio.iscoroutine(scheduled_coro)
    assert scheduled_coro.__name__ == 'end_poll'
    # Prevent un-awaited coroutine warning in unit tests.
    scheduled_coro.close()
    # Check that the lambda returned what create_task returned (our mock task)
    assert returned_value_from_lambda == mock_create_task.return_value


@pytest.mark.asyncio
# _handle_playback_failure is an internal method, ensure it's correctly patched on the instance
@patch.object(MovieBot, "load_media_in_obs", new_callable=AsyncMock, return_value=False)
@patch("asyncio.to_thread", new_callable=AsyncMock, return_value=True) 
async def test_play_movie_load_media_fails(
    mock_async_to_thread, mock_load_media, dummy_bot_instance # _handle_playback_failure not directly mocked here
):
    # Mock create_task on the loop attached to dummy_bot_instance
    mock_create_task = dummy_bot_instance.loop.create_task
    mock_create_task.return_value = MagicMock(spec=asyncio.Task) # Ensure it returns a mock task
    
    # Mock the actual _handle_playback_failure method if we don't want its full logic to run
    # For this test, we want to see if play_movie *schedules* it.
    # So, we don't mock _handle_playback_failure itself yet.

    await dummy_bot_instance.play_movie(MOVIE_PATH_1)

    mock_async_to_thread.assert_awaited_once_with(os.path.exists, MOVIE_PATH_1)
    mock_load_media.assert_awaited_once_with(MOVIE_PATH_1)
    
    mock_create_task.assert_called_once()
    created_task_coro = mock_create_task.call_args[0][0]
    assert asyncio.iscoroutine(created_task_coro)
    assert created_task_coro.__name__ == '_handle_playback_failure'
    created_task_coro.close()
    # We can check args passed to _handle_playback_failure if it were a simple function
    # For a coroutine method, inspecting args within the coro is harder without executing it.
    # Here, we check that the _handle_playback_failure coroutine was created for the task.
    # To check the argument `failed_path=MOVIE_PATH_1`:
    # This requires inspecting the partial/bound method.
    # If `_handle_playback_failure` was `AsyncMock`, we could check its call args.
    # For now, verifying the correct coroutine name is scheduled is a good step.
    assert dummy_bot_instance.current_movie_path is None 


@pytest.mark.asyncio
@patch("asyncio.to_thread", new_callable=AsyncMock, return_value=False) 
async def test_play_movie_invalid_path(
    mock_async_to_thread, dummy_bot_instance # _handle_playback_failure not mocked here
):
    mock_create_task = dummy_bot_instance.loop.create_task
    mock_create_task.return_value = MagicMock(spec=asyncio.Task)

    await dummy_bot_instance.play_movie(NONEXISTENT_MOVIE_PATH)

    mock_async_to_thread.assert_awaited_once_with(os.path.exists, NONEXISTENT_MOVIE_PATH)
    mock_create_task.assert_called_once()
    created_task_coro = mock_create_task.call_args[0][0]
    assert asyncio.iscoroutine(created_task_coro)
    assert created_task_coro.__name__ == '_handle_playback_failure'
    created_task_coro.close()
    assert dummy_bot_instance.current_movie_path is None


@pytest.mark.asyncio
@patch("random.choice")
async def test_handle_playback_failure_fallback_available(mock_random_choice, dummy_bot_instance, mocker):
    failed_path = MOVIE_PATH_1
    fallback_path = MOVIE_PATH_2
    dummy_bot_instance.movies = [failed_path, fallback_path, MOVIE_PATH_3]
    dummy_bot_instance.poll_active = True 
    dummy_bot_instance.current_movie_path = failed_path
    mock_random_choice.return_value = fallback_path
    
    mock_call_later = dummy_bot_instance.loop.call_later
    mock_create_task = dummy_bot_instance.loop.create_task
    mock_create_task.return_value = mocker.MagicMock(spec=asyncio.Task)


    await dummy_bot_instance._handle_playback_failure(failed_path=failed_path)

    assert dummy_bot_instance.poll_active is False 
    assert dummy_bot_instance.current_movie_path is None 
    expected_available = [fallback_path, MOVIE_PATH_3]
    mock_random_choice.assert_called_once()
    assert set(mock_random_choice.call_args[0][0]) == set(expected_available)

    mock_call_later.assert_called_once()
    delay_arg, lambda_func_sched = mock_call_later.call_args[0] # bot.py uses lambda
    assert delay_arg == DEFAULT_CONFIG['POLL_REACTION_DELAY']
    assert callable(lambda_func_sched)

    # Execute lambda
    returned_task_from_lambda = lambda_func_sched()

    mock_create_task.assert_called_once()
    scheduled_coro = mock_create_task.call_args[0][0]
    assert asyncio.iscoroutine(scheduled_coro)
    assert scheduled_coro.__name__ == 'play_movie'
    scheduled_coro.close()
    # To check arg to play_movie, need to inspect the coroutine details if possible,
    # or mock play_movie itself and check its call args after the lambda executes it via create_task.
    # For now, verifying scheduling of 'play_movie' is the main goal.
    assert returned_task_from_lambda == mock_create_task.return_value


@pytest.mark.asyncio
@patch("random.choice") # Still mock random.choice to ensure it's not called
async def test_handle_playback_failure_no_fallback_movies(mock_random_choice, dummy_bot_instance, mocker, caplog):
    failed_path = MOVIE_PATH_1
    dummy_bot_instance.movies = [failed_path] 
    dummy_bot_instance.poll_active = True
    dummy_bot_instance.current_movie_path = failed_path
    
    mock_call_later = dummy_bot_instance.loop.call_later # Get from fixture
    mock_channel_send = dummy_bot_instance.mock_channel.send 

    await dummy_bot_instance._handle_playback_failure(failed_path=failed_path)

    assert dummy_bot_instance.poll_active is False
    assert dummy_bot_instance.current_movie_path is None
    mock_random_choice.assert_not_called()
    assert "No alternative movies available after playback failure" in caplog.text
    mock_channel_send.assert_awaited_once_with(
        f"Critical error: Failed to play '{MOVIE_NAME_1}' and no fallback movies found. Please check bot setup."
    )
    mock_call_later.assert_not_called()


@pytest.mark.asyncio
async def test_event_ready(dummy_bot_instance, mocker): # mocker is a fixture
    # Mock methods called by event_ready, ensure they are AsyncMocks if awaited
    dummy_bot_instance.ensure_obs_connection = AsyncMock()
    # start and start_first_movie_task are already mocked in the fixture

    await dummy_bot_instance.event_ready()

    dummy_bot_instance.ensure_obs_connection.assert_awaited_once()
    dummy_bot_instance.periodic_announcement_task.start.assert_called_once()
    dummy_bot_instance.periodic_token_refresh_task.start.assert_called_once()
    dummy_bot_instance.start_first_movie_task.assert_called_once()


@pytest.mark.asyncio
async def test_event_ready_does_not_treat_open_twitchio_closing_event_as_shutdown(dummy_bot_instance):
    dummy_bot_instance.ensure_obs_connection = AsyncMock()
    dummy_bot_instance._closing = asyncio.Event()

    await dummy_bot_instance.event_ready()

    dummy_bot_instance.ensure_obs_connection.assert_awaited_once()
    dummy_bot_instance.periodic_announcement_task.start.assert_called_once()
    dummy_bot_instance.periodic_token_refresh_task.start.assert_called_once()
    dummy_bot_instance.periodic_irc_health_task.start.assert_called_once()
    dummy_bot_instance.periodic_movie_rescan_task.start.assert_called_once()
    dummy_bot_instance.start_first_movie_task.assert_called_once()


@pytest.mark.asyncio
async def test_event_ready_is_idempotent(dummy_bot_instance):
    dummy_bot_instance.ensure_obs_connection = AsyncMock()
    dummy_bot_instance.start_first_movie_task = MagicMock()

    for attr_name in (
        "periodic_announcement_task",
        "periodic_token_refresh_task",
        "periodic_irc_health_task",
        "periodic_movie_rescan_task",
    ):
        routine = MagicMock(spec=routines.Routine)
        routine.start = MagicMock()
        setattr(dummy_bot_instance, attr_name, routine)

    await dummy_bot_instance.event_ready()
    await dummy_bot_instance.event_ready()

    dummy_bot_instance.ensure_obs_connection.assert_awaited_once()
    dummy_bot_instance.periodic_announcement_task.start.assert_called_once()
    dummy_bot_instance.periodic_token_refresh_task.start.assert_called_once()
    dummy_bot_instance.periodic_irc_health_task.start.assert_called_once()
    dummy_bot_instance.periodic_movie_rescan_task.start.assert_called_once()
    dummy_bot_instance.start_first_movie_task.assert_called_once()


@pytest.mark.asyncio
async def test_start_first_movie_task_plays_first_movie_when_obs_ready(dummy_bot_instance, mocker):
    dummy_bot_instance.start_first_movie_task = MovieBot.start_first_movie_task.__get__(dummy_bot_instance, MovieBot)
    dummy_bot_instance.ensure_obs_connection = AsyncMock(return_value=True)
    dummy_bot_instance.play_movie = AsyncMock()
    dummy_bot_instance.pick_random_movie = MagicMock(return_value=MOVIE_PATH_1)
    dummy_bot_instance.current_movie_path = None
    dummy_bot_instance.poll_active = False
    dummy_bot_instance._startup_task = None
    dummy_bot_instance.loop.create_task.return_value = mocker.MagicMock(spec=asyncio.Task)
    mock_sleep = mocker.patch("asyncio.sleep", new_callable=AsyncMock)

    dummy_bot_instance.start_first_movie_task()

    dummy_bot_instance.loop.create_task.assert_called_once()
    scheduled_coro = dummy_bot_instance.loop.create_task.call_args[0][0]
    assert asyncio.iscoroutine(scheduled_coro)

    await scheduled_coro

    mock_sleep.assert_awaited_once_with(5)
    dummy_bot_instance.pick_random_movie.assert_called_once()
    dummy_bot_instance.ensure_obs_connection.assert_awaited_once()
    dummy_bot_instance.play_movie.assert_awaited_once_with(MOVIE_PATH_1)
    assert dummy_bot_instance._startup_task is None


@pytest.mark.asyncio
async def test_start_first_movie_task_reports_obs_failure(dummy_bot_instance, mocker):
    dummy_bot_instance.start_first_movie_task = MovieBot.start_first_movie_task.__get__(dummy_bot_instance, MovieBot)
    dummy_bot_instance.ensure_obs_connection = AsyncMock(return_value=False)
    dummy_bot_instance.play_movie = AsyncMock()
    dummy_bot_instance.pick_random_movie = MagicMock(return_value=MOVIE_PATH_1)
    dummy_bot_instance.current_movie_path = None
    dummy_bot_instance.poll_active = False
    dummy_bot_instance._startup_task = None
    dummy_bot_instance.loop.create_task.return_value = mocker.MagicMock(spec=asyncio.Task)
    mock_sleep = mocker.patch("asyncio.sleep", new_callable=AsyncMock)

    dummy_bot_instance.start_first_movie_task()

    scheduled_coro = dummy_bot_instance.loop.create_task.call_args[0][0]
    await scheduled_coro

    mock_sleep.assert_awaited_once_with(5)
    dummy_bot_instance.pick_random_movie.assert_called_once()
    dummy_bot_instance.ensure_obs_connection.assert_awaited_once()
    dummy_bot_instance.play_movie.assert_not_awaited()
    dummy_bot_instance.mock_channel.send.assert_awaited_once_with(
        "Error: Failed to connect to OBS. Cannot start movie."
    )
    assert dummy_bot_instance._startup_task is None


@pytest.mark.asyncio
async def test_end_poll_ignores_stale_generation(dummy_bot_instance):
    dummy_bot_instance._playback_generation = 4
    dummy_bot_instance.poll_active = True
    dummy_bot_instance.movie_start_time = 123.0
    dummy_bot_instance.current_movie_path = MOVIE_PATH_1

    await dummy_bot_instance.end_poll(expected_generation=3)

    assert dummy_bot_instance.poll_active is True
    assert dummy_bot_instance.movie_start_time == 123.0
    assert dummy_bot_instance.current_movie_path == MOVIE_PATH_1
    dummy_bot_instance.loop.call_later.assert_not_called()


@pytest.mark.asyncio
@patch("bot.get_movie_duration", new_callable=AsyncMock, return_value=120)
@patch.object(MovieBot, "load_media_in_obs", new_callable=AsyncMock, return_value=True)
@patch("asyncio.to_thread", new_callable=AsyncMock, return_value=True)
async def test_play_movie_cancels_previous_handles(
    mock_async_to_thread, mock_load_media, mock_get_duration, dummy_bot_instance, mocker
):
    old_end_handle = mocker.MagicMock(spec=asyncio.Handle)
    old_next_handle = mocker.MagicMock(spec=asyncio.Handle)
    new_handle = mocker.MagicMock(spec=asyncio.Handle)
    dummy_bot_instance._end_poll_handle = old_end_handle
    dummy_bot_instance._next_movie_handle = old_next_handle
    dummy_bot_instance.loop.call_later.return_value = new_handle

    await dummy_bot_instance.play_movie(MOVIE_PATH_1)

    mock_async_to_thread.assert_awaited_once_with(os.path.exists, MOVIE_PATH_1)
    mock_load_media.assert_awaited_once_with(MOVIE_PATH_1)
    mock_get_duration.assert_awaited_once_with(MOVIE_PATH_1)
    old_end_handle.cancel.assert_called_once()
    old_next_handle.cancel.assert_called_once()
    assert dummy_bot_instance._end_poll_handle is new_handle


@pytest.mark.asyncio
async def test_event_command_error_command_not_found(dummy_bot_instance, mock_ctx):
    attempted_command_name = UNKNOWN_COMMAND_NAME[1:]
    error = commands.CommandNotFound("not found", attempted_command_name)
    mock_ctx.message.content = f"{UNKNOWN_COMMAND_NAME} arg1"
    # For CommandNotFound, context.command is typically None. Fixture default is None.

    await dummy_bot_instance.event_command_error(mock_ctx, error)

    # Bot code constructs command name from message.content if context.command is None
    expected_message = f"Sorry @{TEST_USER}, the command '{UNKNOWN_COMMAND_NAME}' was not found. Use !help."
    mock_ctx.send.assert_awaited_once_with(expected_message)


@pytest.mark.asyncio
async def test_event_command_error_missing_arg(dummy_bot_instance, mock_ctx):  # Removed mocker
    mock_command = MagicMock(spec=commands.Command)
    mock_command.name = 'testcmd'
    # For MissingRequiredArgument, error.param needs to be an inspect.Parameter-like object
    # The bot code safely accesses .name, so a MagicMock with a .name attribute is okay.
    mock_param = MagicMock()
    mock_param.name = 'movie_title'
    error = commands.MissingRequiredArgument(mock_param)
    mock_ctx.message.content = "!testcmd"  # Doesn't really matter for this error type
    mock_ctx.command = mock_command

    await dummy_bot_instance.event_command_error(mock_ctx, error)

    expected_message = f"Oops @{TEST_USER}! You missed an argument: 'argument'. Usage: !help {mock_command.name}"
    mock_ctx.send.assert_awaited_once_with(expected_message)


@pytest.mark.asyncio
async def test_close_stops_routine_and_disconnects(dummy_bot_instance, mocker):
    """Ensure close stops the announcement routine and disconnects OBS."""
    base_close = mocker.patch.object(commands.Bot, 'close', new=AsyncMock())
    to_thread = mocker.patch('bot.asyncio.to_thread', new=AsyncMock())

    dummy_bot_instance.periodic_announcement_task._task.done.return_value = False
    ws = dummy_bot_instance.ws

    await dummy_bot_instance.close()

    dummy_bot_instance.periodic_announcement_task.stop.assert_called_once()
    dummy_bot_instance.periodic_token_refresh_task.stop.assert_called_once()
    to_thread.assert_awaited_once_with(ws.disconnect)
    base_close.assert_awaited_once()


@pytest.mark.asyncio
async def test_close_cancels_scheduled_handles_and_startup_task(dummy_bot_instance, mocker):
    base_close = mocker.patch.object(commands.Bot, 'close', new=AsyncMock())
    to_thread = mocker.patch('bot.asyncio.to_thread', new=AsyncMock())

    end_handle = mocker.MagicMock(spec=asyncio.Handle)
    next_handle = mocker.MagicMock(spec=asyncio.Handle)
    startup_task = asyncio.create_task(asyncio.sleep(3600))
    ws = dummy_bot_instance.ws
    dummy_bot_instance._end_poll_handle = end_handle
    dummy_bot_instance._next_movie_handle = next_handle
    dummy_bot_instance._startup_task = startup_task

    await dummy_bot_instance.close()

    end_handle.cancel.assert_called_once()
    next_handle.cancel.assert_called_once()
    assert startup_task.cancelled()
    assert dummy_bot_instance._startup_task is None
    assert dummy_bot_instance._shutdown_requested is True
    assert isinstance(dummy_bot_instance._closing, asyncio.Event)
    to_thread.assert_awaited_once_with(ws.disconnect)
    base_close.assert_awaited_once()


def test_is_bot_closed_uses_twitchio_closing_event():
    bot_obj = MagicMock()
    bot_obj._shutdown_requested = False
    bot_obj._closing = asyncio.Event()

    assert _is_bot_closed(bot_obj) is False

    bot_obj._closing.set()
    assert _is_bot_closed(bot_obj) is True


def test_normalize_access_token_strips_oauth_prefix():
    assert _normalize_access_token("oauth:abc123") == "abc123"
    assert _normalize_access_token("OAuth:abc123") == "abc123"
    assert _normalize_access_token(" abc123 ") == "abc123"


def test_moviebot_init_passes_raw_access_token_to_twitchio(mocker, monkeypatch):
    monkeypatch.setenv("MOVIE_DIRECTORY", "c:/dummy/movies")
    mocker.patch('bot.scan_movies', return_value=[])
    base_init = mocker.patch.object(commands.Bot, '__init__', return_value=None)

    MovieBot(access_token_param="oauth:test_access", refresh_token_initial="refresh")

    base_init.assert_called_once()
    assert base_init.call_args.kwargs["token"] == "test_access"


@pytest.mark.asyncio
async def test_apply_new_access_token_uses_raw_token_for_twitchio_internal(dummy_bot_instance):
    dummy_bot_instance._http = MagicMock()
    dummy_bot_instance._connection = MagicMock()
    dummy_bot_instance._connection._token = "old"
    dummy_bot_instance._connection._password = "oauth:old"

    await dummy_bot_instance._apply_new_access_token("oauth:new_access")

    assert dummy_bot_instance._http.token == "new_access"
    assert dummy_bot_instance._connection._token == "new_access"
    assert dummy_bot_instance._connection._password == "oauth:new_access"


def test_is_irc_connected_requires_ready_event(dummy_bot_instance):
    dummy_bot_instance._connection = MagicMock()
    dummy_bot_instance._connection.is_ready = asyncio.Event()
    dummy_bot_instance._connection._websocket = MagicMock()
    dummy_bot_instance._connection._websocket.closed = False

    assert dummy_bot_instance._is_irc_connected() is False

    dummy_bot_instance._connection.is_ready.set()
    assert dummy_bot_instance._is_irc_connected() is True


@pytest.mark.asyncio
async def test_hard_reset_irc_connection_replaces_connection(dummy_bot_instance):
    class DummyWebsocket:
        closed = False

        async def close(self):
            self.closed = True

    class DummyConnection:
        def __init__(self, *, loop, heartbeat, client, token, modes=None, initial_channels=None, retain_cache=True):
            self._loop = loop
            self._heartbeat = heartbeat
            self._client = client
            self._token = token
            self.modes = modes
            self._initial_channels = initial_channels or []
            self._retain_cache = retain_cache
            self._keeper = None
            self._task_cleaner = None
            self._background_tasks = []
            self._websocket = DummyWebsocket()
            self.is_ready = asyncio.Event()

        async def _connect(self):
            self.is_ready.set()

    old_connection = DummyConnection(
        loop=dummy_bot_instance.loop,
        heartbeat=30.0,
        client=dummy_bot_instance,
        token="old",
        modes=("commands", "tags", "membership"),
        initial_channels=["test_channel"],
    )
    dummy_bot_instance._connection = old_connection
    dummy_bot_instance._http = MagicMock()
    dummy_bot_instance._http.token = "oauth:new_access"
    dummy_bot_instance._http.session = MagicMock()
    dummy_bot_instance._http.session.closed = True

    result = await dummy_bot_instance._hard_reset_irc_connection(reason="test")

    assert result is True
    assert dummy_bot_instance._connection is not old_connection
    assert dummy_bot_instance._connection._token == "new_access"
    assert dummy_bot_instance._connection.is_ready.is_set()
    assert dummy_bot_instance._http.session is None
