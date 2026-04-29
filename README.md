# StreamCinema Vote Bot

Turn Twitch chat into the remote control for a 24/7 movie stream. StreamCinema Vote Bot watches a local movie folder, plays videos through OBS, and lets viewers vote in chat for what comes next.

## What It Does

- Scans a local movie folder and keeps an in-memory list of playable files.
- Starts playback in OBS through OBS WebSocket.
- Opens a Twitch chat poll while the current movie is playing.
- Lets viewers vote with `!vote <movie name>`.
- Supports partial title matching, vote changes, ties, and random fallback picks.
- Shows the current movie, time remaining, vote results, and available movie list.
- Refreshes Twitch OAuth tokens automatically during long runs.
- Watches Twitch IRC health and rebuilds the chat connection if TwitchIO gets stuck reconnecting.

## Commands

| Command | Description |
| --- | --- |
| `!vote <movie name>` | Vote for the next movie. |
| `!currentmovie` | Show what is currently playing. |
| `!time` | Show time left in the current movie. |
| `!results` | Show current vote totals. |
| `!movies` | Share your configured movie-list URL, or list a sample of local movies. |
| `!help` | Show available commands. |

## Requirements

- Python 3.8+
- OBS Studio
- OBS WebSocket enabled
- A Twitch bot account
- A Twitch developer application with Client ID and Client Secret
- Twitch OAuth access and refresh tokens for the bot account
- A local folder of movie/video files

Supported video extensions include `.mp4`, `.mkv`, `.avi`, `.mov`, `.webm`, `.wmv`, `.mpeg`, `.mpg`, `.m4v`, `.ts`, and several older formats.

## Quick Start

Clone the repo:

```bash
git clone https://github.com/yourusername/streamcinema-vote-bot.git
cd streamcinema-vote-bot
```

Create and activate a virtual environment:

```bash
python -m venv venv
```

Windows:

```powershell
.\venv\Scripts\Activate.ps1
```

macOS/Linux:

```bash
source venv/bin/activate
```

Install dependencies:

```bash
pip install -r requirements.txt
```

Create your local config:

```bash
cp .env.example .env
```

On Windows PowerShell:

```powershell
Copy-Item .env.example .env
```

Edit `.env` with your Twitch, OBS, and movie folder settings.

## Configuration

`.env.example` contains every supported setting:

```dotenv
TWITCH_CLIENT_ID="your_client_id"
TWITCH_CLIENT_SECRET="your_client_secret"
TWITCH_ACCESS_TOKEN="your_access_token"
TWITCH_REFRESH_TOKEN="your_refresh_token"
CHANNEL_NAME="your_channel"
MOVIE_DIRECTORY="C:/path/to/movies"

OBS_WEBSOCKET_IP="127.0.0.1"
OBS_WEBSOCKET_PORT="4455"
OBS_WEBSOCKET_PASSWORD="your_obs_password"
SCENE_NAME="Scene"
MEDIA_SOURCE_NAME="Media Source"
MOVIE_LIST_URL=""
```

Notes:

- `CHANNEL_NAME` should be the Twitch channel where the bot joins chat.
- `MOVIE_DIRECTORY` must be a real local path on the machine running the bot.
- `SCENE_NAME` must match the OBS scene containing your media source.
- `MEDIA_SOURCE_NAME` must match the OBS Media Source the bot should update.
- `MOVIE_LIST_URL` is optional and is used by the `!movies` command.

## OBS Setup

1. Open OBS.
2. Enable OBS WebSocket.
3. Create or choose the scene named in `SCENE_NAME`.
4. Add a standard OBS Media Source to that scene.
5. Name the source exactly as `MEDIA_SOURCE_NAME`.
6. Leave OBS running before starting the bot.

The bot updates that media source with each selected movie, switches OBS to the configured scene, and restarts the media input so playback begins immediately.

## Running

Start the bot from the project folder:

```bash
python bot.py
```

Logs are printed to the console and written to `bot.log`.

To stop the bot, press `Ctrl+C`. It will try to stop background tasks and disconnect from OBS cleanly.

## Reliability

This bot is designed for long-running streams:

- Twitch access tokens are validated and refreshed periodically.
- IRC health is checked every few seconds.
- If TwitchIO gets stuck in a closed websocket loop, the bot can rebuild the Twitch chat connection without resetting the active movie or poll state.
- OBS calls are wrapped with reconnect checks and timeouts.

## Security

Never commit `.env`, Twitch tokens, Twitch client secrets, OBS passwords, or generated logs. This repo includes `.env.example` for placeholders only.

If you accidentally committed credentials before making your repository public, rotate those credentials and publish from a clean repository with fresh git history.

## License

MIT License. See [LICENSE](LICENSE).
