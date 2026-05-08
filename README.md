# MovieBot / Twitch Movie Vote Bot

Python Twitch bot that lets chat vote on the next movie and hands playback to OBS. It was built as practical stream automation for hosted movie nights: viewers vote in chat, the bot tracks the queue, and OBS switches to the winning local media file.

![Movie Night showcase](https://zacbatten.me/assets/movie-night-preview.png)

## Live Links

- Portfolio case study: [zacbatten.me/#moviebot](https://zacbatten.me/#moviebot)
- Movie Night showcase: [zacbatten.me/movie-night.html](https://zacbatten.me/movie-night.html)
- Movie library page: [zacbatten.me/movie-library.html](https://zacbatten.me/movie-library.html)

## What Zac Built

- Twitch chat commands for voting, current movie status, remaining time, results, movie list, and help.
- Movie scanning and partial title matching against a local media folder.
- Vote tracking with vote changes, tie handling, and random fallback selection.
- OBS WebSocket handoff that updates a configured media source and switches scenes.
- Twitch OAuth token validation and refresh for long-running sessions.
- IRC health checks that rebuild the Twitch chat connection if TwitchIO gets stuck reconnecting.
- Console/file logging and focused tests around configuration and bot behavior.

## Repository Metadata

Suggested GitHub About fields:

- Description: `Twitch chat movie voting bot that controls OBS playback for hosted movie nights.`
- Website: `https://zacbatten.me/#moviebot`
- Topics: `python`, `twitch`, `twitchio`, `obs-websocket`, `stream-automation`, `chatbot`, `moviebot`, `portfolio-project`

## Features

- Scans a local folder for playable movie files.
- Starts playback in OBS through OBS WebSocket.
- Opens a chat vote while the current movie is playing.
- Accepts `!vote <movie name>` with partial title matching.
- Supports `!currentmovie`, `!time`, `!results`, `!movies`, and `!help`.
- Refreshes Twitch OAuth tokens automatically.
- Reconnects to Twitch chat and OBS after recoverable connection problems.
- Writes runtime logs to the console and `bot.log`.

## Requirements

- Python 3.8+
- OBS Studio with OBS WebSocket enabled
- A Twitch bot account
- A Twitch developer application with Client ID and Client Secret
- Twitch OAuth access and refresh tokens for the bot account
- A local folder of movie/video files

Supported video extensions include `.mp4`, `.mkv`, `.avi`, `.mov`, `.webm`, `.wmv`, `.mpeg`, `.mpg`, `.m4v`, `.ts`, and several older formats.

## Quick Start

Clone the repo:

```bash
git clone https://github.com/Inefy/twitch-movie-bot.git
cd twitch-movie-bot
```

Create and activate a virtual environment:

```bash
python -m venv venv
```

Windows PowerShell:

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

Windows PowerShell:

```powershell
Copy-Item .env.example .env
```

Edit `.env` with your Twitch, OBS, and movie folder settings. Keep real credentials in `.env` only.

## Configuration

`.env.example` documents every supported setting without real credentials.

Important settings:

- `TWITCH_CLIENT_ID` and `TWITCH_CLIENT_SECRET` come from a Twitch developer application.
- `TWITCH_ACCESS_TOKEN` and `TWITCH_REFRESH_TOKEN` must belong to the bot account.
- `CHANNEL_NAME` is the Twitch channel the bot joins.
- `MOVIE_DIRECTORY` must be a real local path on the machine running the bot.
- `OBS_WEBSOCKET_IP`, `OBS_WEBSOCKET_PORT`, and `OBS_WEBSOCKET_PASSWORD` must match OBS.
- `SCENE_NAME` must contain the configured OBS media source.
- `MEDIA_SOURCE_NAME` must match the OBS Media Source the bot updates.
- `MOVIE_LIST_URL` is optional and powers the `!movies` command when present.

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

To stop the bot, press `Ctrl+C`. It will try to stop background tasks and disconnect from OBS cleanly.

## Tests

Install the development dependencies from `requirements.txt`, then run:

```bash
python -m pytest
```

The configuration module is import-safe for tests: required Twitch, OBS, and movie-folder values can be supplied at runtime instead of blocking import.

## Security

Never commit `.env`, Twitch tokens, Twitch client secrets, OBS passwords, or generated logs. This repo includes `.env.example` for placeholders only.

If credentials were ever committed before publishing, rotate them in Twitch/OBS and publish from clean history.

## License

MIT License. See [LICENSE](LICENSE).
