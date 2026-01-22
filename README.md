# Tidal Automation

Automated playlist management for Tidal, featuring genre-based filtering, playlist rotation, and bulk favoriting.

## Features

- **Filter New Arrivals**: Monitors Tidal's editorial "New Arrivals" playlists, filters out unwanted genres (hip-hop, rap, R&B, reggaeton), and adds passing tracks to a personal "New Music" playlist
- **Playlist Rotation**: Keeps a "Master Playlist" at a maximum size by moving the oldest tracks to an "Archive" playlist
- **Bulk Favoriting**: Automatically favorites/likes all tracks from playlists matching a prefix (e.g., `_CBM`)
- **Native Genre Detection**: Uses Tidal's v2 API for accurate track-level genre data
- **Duplicate Avoidance**: Tracks processed songs to prevent duplicates
- **Dry Run Mode**: Preview all changes before applying them

## Requirements

- Python 3.10+
- Tidal account (HiFi or HiFi Plus)
- macOS (for launchd scheduling) or any OS for manual execution

## Installation

1. Clone the repository:
   ```bash
   git clone https://github.com/cbmartinx/tidal-automation.git
   cd tidal-automation
   ```

2. Create and activate a virtual environment:
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   ```

3. Install dependencies:
   ```bash
   pip install tidalapi requests python-dotenv
   ```

4. Copy the example config:
   ```bash
   cp config.example.json config.json
   ```

5. Authenticate with Tidal:
   ```bash
   python3 tidal_automation.py --login
   ```
   Follow the prompts to authorize via OAuth. Your session will be saved to `tidal_session.json`.

## Configuration

Edit `config.json` to customize behavior:

```json
{
  "source_playlist_ids": [
    "1b418bb8-90a7-4f87-901d-707993838346",
    "da0d8a6d-de60-4528-9f1a-1316f054b4c4"
  ],
  "destination_playlist_name": "New Music",
  "destination_playlist_id": "",
  "session_path": "tidal_session.json",
  "genre_blocklist": [
    "hip hop",
    "hip-hop",
    "rap",
    "r&b",
    "rnb",
    "rhythm and blues",
    "reggaeton"
  ],
  "genre_detection": "tidal",
  "unknown_genre_policy": "keep",
  "dry_run": false,
  "processed_tracks_path": "cache/processed_tracks.json",
  "tidal": {
    "genre_cache_path": "cache/tidal_genres.json",
    "min_interval_seconds": 0.1
  },
  "spotify": {
    "cache_path": "cache/spotify.json",
    "min_interval_seconds": 0.1
  },
  "rotate": {
    "master_playlist_id": "YOUR_MASTER_PLAYLIST_ID",
    "archive_playlist_id": "YOUR_ARCHIVE_PLAYLIST_ID",
    "max_tracks": 200
  },
  "like": {
    "playlist_prefix": "_CBM"
  }
}
```

### Configuration Options

| Option | Description |
|--------|-------------|
| `source_playlist_ids` | Tidal playlist IDs to monitor for new tracks |
| `destination_playlist_name` | Name of the playlist to add filtered tracks to |
| `destination_playlist_id` | (Optional) Specific playlist ID to use as destination |
| `genre_blocklist` | Genres to filter out (case-insensitive, partial matching) |
| `genre_detection` | Genre source: `tidal` (recommended) or `spotify` (fallback) |
| `unknown_genre_policy` | How to handle tracks with no genre: `keep` or `skip` |
| `dry_run` | Set to `true` to preview changes without modifying playlists |
| `rotate.master_playlist_id` | Playlist ID to keep at max size |
| `rotate.archive_playlist_id` | Playlist ID to move overflow tracks to |
| `rotate.max_tracks` | Maximum tracks to keep in master playlist |
| `like.playlist_prefix` | Prefix to match playlists for bulk favoriting |

### Finding Playlist IDs

Playlist IDs can be found in Tidal URLs:
```
https://tidal.com/playlist/cc2bc46e-0795-437a-b559-08c56a78dcbb
                         ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
                         This is the playlist ID
```

## Usage

### Commands

```bash
# Filter new arrivals and add to destination playlist
python3 tidal_automation.py filter

# Rotate oldest tracks from master to archive
python3 tidal_automation.py rotate

# Favorite all tracks from matching playlists
python3 tidal_automation.py like

# Run all operations (filter + rotate + like)
python3 tidal_automation.py all

# Preview changes without modifying anything
python3 tidal_automation.py all --dry-run

# Re-authenticate with Tidal
python3 tidal_automation.py --login

# Enable verbose logging
python3 tidal_automation.py all -v
```

### Command Details

#### `filter`
1. Fetches tracks from source playlists (e.g., Tidal's "New Arrivals")
2. Checks each track's genres against the blocklist
3. Adds passing tracks to the destination playlist
4. Skips tracks that have already been processed

#### `rotate`
1. Checks if the master playlist exceeds `max_tracks`
2. Takes the oldest tracks (from the top of the playlist)
3. Adds them to the archive playlist (at the bottom)
4. Removes them from the master playlist

#### `like`
1. Finds all playlists matching the configured prefix
2. Collects all unique track IDs
3. Skips tracks already in favorites
4. Favorites all remaining tracks

## Scheduling (macOS)

A launchd plist is included for daily automation.

### Installation

```bash
# Create symlink to LaunchAgents
ln -sf "$(pwd)/launchd/com.cbmartinx.tidal-automation.plist" ~/Library/LaunchAgents/

# Load the job
launchctl load ~/Library/LaunchAgents/com.cbmartinx.tidal-automation.plist

# Verify it's loaded
launchctl list | grep tidal-automation
```

### Configuration

The default schedule runs at 9:15 AM daily. Edit the plist to change:

```xml
<key>StartCalendarInterval</key>
<dict>
  <key>Hour</key>
  <integer>9</integer>
  <key>Minute</key>
  <integer>15</integer>
</dict>
```

### Logs

- stdout: `logs/tidal-automation.log`
- stderr: `logs/tidal-automation.err.log`

### Management

```bash
# Unload the job
launchctl unload ~/Library/LaunchAgents/com.cbmartinx.tidal-automation.plist

# Reload after changes
launchctl unload ~/Library/LaunchAgents/com.cbmartinx.tidal-automation.plist
launchctl load ~/Library/LaunchAgents/com.cbmartinx.tidal-automation.plist

# Run manually
launchctl start com.cbmartinx.tidal-automation
```

## Genre Detection

### Tidal Native (Recommended)

Uses Tidal's v2 API (`openapi.tidal.com/v2`) to fetch track-level genre data. This provides accurate genre classification directly from Tidal's catalog.

### Spotify Fallback

If Tidal genre detection fails or you prefer Spotify's genre taxonomy, you can use Spotify for artist-level genre lookups:

1. Create a Spotify app at [developer.spotify.com](https://developer.spotify.com/dashboard)
2. Create a `.env` file:
   ```
   SPOTIFY_CLIENT_ID="your_client_id"
   SPOTIFY_CLIENT_SECRET="your_client_secret"
   ```
3. Set `"genre_detection": "spotify"` in config.json

Note: Spotify only provides artist-level genres, not track-level.

## File Structure

```
tidal-automation/
├── tidal_automation.py      # Main script
├── config.json              # Your configuration (git-ignored)
├── config.example.json      # Example configuration
├── tidal_session.json       # OAuth session (git-ignored)
├── .env                     # Environment variables (git-ignored)
├── .env.example             # Example environment file
├── cache/
│   ├── processed_tracks.json    # Tracks already processed
│   ├── tidal_genres.json        # Genre cache
│   └── spotify.json             # Spotify cache (if used)
├── logs/
│   ├── tidal-automation.log     # stdout from scheduled runs
│   └── tidal-automation.err.log # stderr from scheduled runs
└── launchd/
    └── com.cbmartinx.tidal-automation.plist  # macOS scheduler
```

## How It Works

### Track Processing Flow

```
New Arrivals Playlist
         │
         ▼
   ┌─────────────┐
   │ Already     │──Yes──▶ Skip
   │ processed?  │
   └─────────────┘
         │ No
         ▼
   ┌─────────────┐
   │ Fetch genre │
   │ from Tidal  │
   └─────────────┘
         │
         ▼
   ┌─────────────┐
   │ Genre in    │──Yes──▶ Block & mark processed
   │ blocklist?  │
   └─────────────┘
         │ No
         ▼
   Add to "New Music" & mark processed
```

### Playlist Rotation Flow

```
Master Playlist (207 tracks)
         │
         ▼
   ┌─────────────┐
   │ Over limit? │──No──▶ Done
   │ (max: 200)  │
   └─────────────┘
         │ Yes (7 over)
         ▼
   Take 7 oldest tracks (top)
         │
         ├──▶ Add to Archive (bottom)
         │
         └──▶ Remove from Master

Master Playlist (200 tracks) ✓
```

## Troubleshooting

### Session Expired

If you see authentication errors, re-authenticate:
```bash
python3 tidal_automation.py --login
```

### Playlist Not Found

Ensure the playlist ID is correct and the playlist is accessible to your account. Editorial playlists use UUIDs, while user playlists may use different formats.

### Genre Not Found

Some new releases may not have genre data yet. The `unknown_genre_policy` setting controls whether these tracks are kept or skipped.

### Rate Limiting

The script includes built-in rate limiting (`min_interval_seconds`). If you encounter rate limit errors, increase this value in the config.

## License

MIT License - see LICENSE file for details.

## Acknowledgments

- [tidalapi](https://github.com/tamland/python-tidal) - Unofficial Tidal API client
- Tidal's v2 API for genre data
