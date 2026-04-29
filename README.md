# Twitch Movie Voting Bot

This Twitch bot allows viewers to vote on movies from a configured local directory. It automatically plays the most voted movie via OBS WebSocket at the end of the current one. If no votes are cast, a random movie will be played.

## Features

- Scans a local directory for movie files.
- Allows users to vote for the next movie via Twitch chat (`!vote <movie name>`).
- Handles partial name matching and ties (randomly picks from tied winners).
- Displays current movie (`!currentmovie`), remaining time (`!time`), and vote results (`!results`).
- Automatically plays the winning (or a random) movie using OBS WebSocket integration.
- Refreshes Twitch authentication tokens automatically.
- Provides periodic reminders to vote in chat.

## Setup

### Prerequisites

- Python 3.8+
- OBS Studio installed
- OBS WebSocket Plugin installed and configured (know your OBS WebSocket IP, port, and password).
- Twitch Account for the Bot
- Twitch Application created on the [Twitch Developer Console](https://dev.twitch.tv/console) to get a Client ID and Client Secret. You'll also need to generate initial Access and Refresh tokens (see [Twitch Authentication Docs](https://dev.twitch.tv/docs/authentication/getting-tokens-oauth/)).

### Installation

1.  **Clone the repository:**
    ```bash
    git clone [https://github.com/yourusername/twitch-movie-bot.git](https://github.com/yourusername/twitch-movie-bot.git)
    cd twitch-movie-bot
    ```

2.  **Create a virtual environment:** (Recommended)
    ```bash
    python -m venv venv
    # Activate the environment:
    # Windows:
    .\venv\Scripts\activate
    # Linux/macOS:
    source venv/bin/activate
    ```

3.  **Install dependencies:**
    ```bash
    pip install -r requirements.txt
    ```

4.  **Create the configuration file (`.env`):**
    Create a file named `.env` in the main `twitch-movie-bot` directory (the same directory as `bot.py`). Add the following variables, replacing the placeholder values with your actual credentials and settings:

    ```dotenv
    # .env file

    # Twitch Application Credentials
    TWITCH_CLIENT_ID="YOUR_TWITCH_CLIENT_ID"
    TWITCH_CLIENT_SECRET="YOUR_TWITCH_CLIENT_SECRET"

    # Initial Twitch Bot Tokens (Get these via Twitch OAuth flow)
    # The bot will refresh these and update this file automatically
    TWITCH_ACCESS_TOKEN="INITIAL_BOT_ACCESS_TOKEN"
    TWITCH_REFRESH_TOKEN="INITIAL_BOT_REFRESH_TOKEN"

    # Your Twitch Channel Name (where the bot will operate)
    CHANNEL_NAME="your_twitch_channel_name"

    # OBS WebSocket Connection Details
    OBS_WEBSOCKET_IP="192.168.1.100" # IP address where OBS is running (often 127.0.0.1 if on the same PC)
    OBS_WEBSOCKET_PORT="4455"        # Port configured in OBS WebSocket settings (default is 4455)
    OBS_WEBSOCKET_PASSWORD="YOUR_OBS_WEBSOCKET_PASSWORD" # Password set in OBS WebSocket settings

    # OBS Scene and Source Configuration
    SCENE_NAME="Your OBS Scene Name"       # The OBS scene containing the media source
    MEDIA_SOURCE_NAME="Your Media Source Name" # The name of the OBS Media Source used for playback

    # Movie Playback Configuration
    MOVIE_DIRECTORY="C:/path/to/your/movies" # Full path to the folder containing movie files

    # Optional: Link for the !movies command
    # MOVIE_LIST_URL="[https://your-movie-list-url.com](https://your-movie-list-url.com)"
    ```

5.  **Configure OBS:**
    * Ensure you have an OBS Scene matching `SCENE_NAME`.
    * Inside that scene, add a "Media Source" (VLC Source might also work but this code targets standard Media Source) and name it exactly as specified in `MEDIA_SOURCE_NAME`. You can initially point it to any dummy video file; the bot will change it.

### Running the Bot

1.  Make sure your virtual environment is activated.
2.  Make sure OBS is running with the WebSocket server enabled.
3.  Run the bot script:
    ```bash
    python bot.py
    ```

The bot should connect to Twitch and OBS, scan the movie directory, and start the first random movie. Logs will be printed to the console and saved to `bot.log`.

## Usage

Interact with the bot in your Twitch chat:

-   `!help`: Shows available commands.
-   `!vote <movie name>`: Vote for a movie (e.g., `!vote The Matrix`).
-   `!currentmovie`: See what's playing.
-   `!time`: Check remaining time.
-   `!results`: View current vote standings.
-   `!movies`: Get a link to the movie list (if `MOVIE_LIST_URL` is set in `.env`).

## Stopping the Bot

Press `Ctrl + C` in the terminal where the bot is running. The bot will attempt to shut down gracefully.