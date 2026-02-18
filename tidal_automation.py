#!/usr/bin/env python3
"""
Tidal Automation Script

Filters "New Arrivals" playlists by genre and appends to a "New Music" playlist.
Uses tidalapi for playlist operations and Tidal v2 API for genre data.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests
import tidalapi
from dotenv import load_dotenv

# --- Logging Setup ---
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)


# --- Utility Functions ---
def load_json(path: str | Path) -> dict[str, Any]:
    """Load JSON file, return empty dict if not found."""
    path = Path(path)
    if path.exists():
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_json(path: str | Path, data: dict[str, Any]) -> None:
    """Save data to JSON file."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, default=str)


# --- Tidal Genre Client ---
class TidalGenreClient:
    """Client for fetching genre data from Tidal v2 API."""

    BASE_URL = "https://openapi.tidal.com/v2"

    def __init__(
        self,
        session: tidalapi.Session,
        cache_path: str | Path,
        min_interval_seconds: float = 0.1,
    ) -> None:
        self.session = session
        self.cache_path = Path(cache_path)
        self.cache: dict[str, Any] = load_json(self.cache_path)
        self.min_interval = min_interval_seconds
        self.last_request_time = 0.0
        self._genre_name_cache: dict[str, str] = {}

    def _rate_limit(self) -> None:
        """Ensure minimum interval between requests."""
        elapsed = time.time() - self.last_request_time
        if elapsed < self.min_interval:
            time.sleep(self.min_interval - elapsed)
        self.last_request_time = time.time()

    def _get_headers(self) -> dict[str, str]:
        """Get authorization headers using session token."""
        return {
            "Authorization": f"Bearer {self.session.access_token}",
            "Content-Type": "application/vnd.api+json",
        }

    def _fetch_genre_name(self, genre_id: str) -> str:
        """Fetch genre name by ID from v2 API."""
        if genre_id in self._genre_name_cache:
            return self._genre_name_cache[genre_id]

        self._rate_limit()
        url = f"{self.BASE_URL}/genres/{genre_id}"
        resp = requests.get(url, headers=self._get_headers(), timeout=30)

        if resp.status_code == 200:
            name = resp.json()["data"]["attributes"]["genreName"]
            self._genre_name_cache[genre_id] = name
            return name
        else:
            log.warning(f"Failed to fetch genre {genre_id}: {resp.status_code}")
            return f"Unknown({genre_id})"

    def get_track_genres(self, track_id: str) -> list[str]:
        """Get genre names for a track."""
        cache_key = f"track:{track_id}"
        if cache_key in self.cache:
            return self.cache[cache_key]

        self._rate_limit()
        url = f"{self.BASE_URL}/tracks/{track_id}?include=genres"
        resp = requests.get(url, headers=self._get_headers(), timeout=30)

        if resp.status_code != 200:
            log.warning(f"Failed to fetch track {track_id}: {resp.status_code}")
            return []

        data = resp.json()
        genre_refs = data.get("data", {}).get("relationships", {}).get("genres", {}).get("data", [])
        genre_ids = [g["id"] for g in genre_refs]

        genres = [self._fetch_genre_name(gid) for gid in genre_ids]
        self.cache[cache_key] = genres
        return genres

    def save(self) -> None:
        """Persist cache to disk."""
        save_json(self.cache_path, self.cache)


# --- Spotify Genre Client (fallback) ---
class SpotifyClient:
    """Spotify API client for artist genre lookups (fallback)."""

    TOKEN_URL = "https://accounts.spotify.com/api/token"
    SEARCH_URL = "https://api.spotify.com/v1/search"

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        cache_path: str | Path,
        min_interval_seconds: float = 0.1,
    ) -> None:
        self.client_id = client_id
        self.client_secret = client_secret
        self.cache_path = Path(cache_path)
        self.cache: dict[str, Any] = load_json(self.cache_path)
        self.min_interval = min_interval_seconds
        self.last_request_time = 0.0
        self._access_token: str | None = None
        self._token_expires: float = 0.0

    def _rate_limit(self) -> None:
        elapsed = time.time() - self.last_request_time
        if elapsed < self.min_interval:
            time.sleep(self.min_interval - elapsed)
        self.last_request_time = time.time()

    def _get_access_token(self) -> str:
        if self._access_token and time.time() < self._token_expires:
            return self._access_token

        self._rate_limit()
        resp = requests.post(
            self.TOKEN_URL,
            data={"grant_type": "client_credentials"},
            auth=(self.client_id, self.client_secret),
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        self._access_token = data["access_token"]
        self._token_expires = time.time() + data["expires_in"] - 60
        return self._access_token

    def get_artist_genres(self, artist: str) -> list[str]:
        cache_key = f"artist:{artist.lower()}"
        if cache_key in self.cache:
            return self.cache[cache_key]

        self._rate_limit()
        token = self._get_access_token()
        resp = requests.get(
            self.SEARCH_URL,
            params={"q": artist, "type": "artist", "limit": 1},
            headers={"Authorization": f"Bearer {token}"},
            timeout=30,
        )

        if resp.status_code != 200:
            log.warning(f"Spotify search failed for {artist}: {resp.status_code}")
            return []

        items = resp.json().get("artists", {}).get("items", [])
        genres = items[0]["genres"] if items else []
        self.cache[cache_key] = genres
        return genres

    def save(self) -> None:
        save_json(self.cache_path, self.cache)


# --- Main Automation ---
class TidalAutomation:
    """Main automation class for filtering and managing playlists."""

    def __init__(self, config: dict[str, Any], dry_run: bool = False) -> None:
        self.config = config
        self.dry_run = dry_run
        self.session: tidalapi.Session | None = None
        self.genre_client: TidalGenreClient | None = None
        self.spotify_client: SpotifyClient | None = None
        self.processed_path = Path(config.get("processed_tracks_path", "cache/processed_tracks.json"))
        self.processed_tracks: set[str] = set(load_json(self.processed_path).get("tracks", []))
        self.removed_path = Path("cache/removed_tracks.json")
        self.removed_tracks: set[str] = set(load_json(self.removed_path).get("tracks", []))
        self.snapshot_path = Path("cache/destination_snapshot.json")
        self.destination_snapshot: set[str] = set(load_json(self.snapshot_path).get("tracks", []))

    def login(self) -> bool:
        """Initialize Tidal session from saved credentials."""
        session_path = Path(self.config.get("session_path", "tidal_session.json"))

        if not session_path.exists():
            log.error(f"Session file not found: {session_path}")
            log.info("Run with --login to authenticate")
            return False

        self.session = tidalapi.Session()
        try:
            self.session.load_session_from_file(session_path)
            if not self.session.check_login():
                log.error("Session expired or invalid")
                return False
            log.info(f"Logged in as: {self.session.user.first_name} {self.session.user.last_name}")
            return True
        except Exception as e:
            log.error(f"Failed to load session: {e}")
            return False

    def _init_genre_client(self) -> None:
        """Initialize the genre detection client."""
        genre_detection = self.config.get("genre_detection", "tidal")

        if genre_detection == "tidal":
            tidal_cfg = self.config.get("tidal", {})
            self.genre_client = TidalGenreClient(
                session=self.session,
                cache_path=tidal_cfg.get("genre_cache_path", "cache/tidal_genres.json"),
                min_interval_seconds=tidal_cfg.get("min_interval_seconds", 0.1),
            )
        elif genre_detection == "spotify":
            spotify_id = os.getenv("SPOTIFY_CLIENT_ID")
            spotify_secret = os.getenv("SPOTIFY_CLIENT_SECRET")
            if not spotify_id or not spotify_secret:
                log.error("Spotify credentials not found in environment")
                sys.exit(1)
            spotify_cfg = self.config.get("spotify", {})
            self.spotify_client = SpotifyClient(
                client_id=spotify_id,
                client_secret=spotify_secret,
                cache_path=spotify_cfg.get("cache_path", "cache/spotify.json"),
                min_interval_seconds=spotify_cfg.get("min_interval_seconds", 0.1),
            )
        else:
            log.error(f"Unknown genre_detection method: {genre_detection}")
            sys.exit(1)

    def _get_genres(self, track: tidalapi.Track) -> list[str]:
        """Get genres for a track using configured method."""
        if self.genre_client:
            return self.genre_client.get_track_genres(str(track.id))
        elif self.spotify_client:
            return self.spotify_client.get_artist_genres(track.artist.name)
        return []

    def _is_blocked(self, genres: list[str]) -> bool:
        """Check if any genre matches the blocklist."""
        blocklist = self.config.get("genre_blocklist", [])
        genres_lower = [g.lower() for g in genres]

        for blocked in blocklist:
            blocked_lower = blocked.lower()
            for genre in genres_lower:
                if blocked_lower in genre or genre in blocked_lower:
                    return True
        return False

    def _get_or_create_destination_playlist(self) -> tidalapi.Playlist | None:
        """Get or create the destination playlist."""
        dest_name = self.config.get("destination_playlist_name", "New Music")
        dest_id = self.config.get("destination_playlist_id")

        if dest_id:
            try:
                return self.session.playlist(dest_id)
            except Exception as e:
                log.warning(f"Could not fetch destination playlist {dest_id}: {e}")

        # Search in user's playlists
        user_playlists = self.session.user.playlists()
        for pl in user_playlists:
            if pl.name == dest_name:
                log.info(f"Found existing destination playlist: {pl.name} ({pl.id})")
                return pl

        # Create new playlist
        if self.dry_run:
            log.info(f"[DRY RUN] Would create playlist: {dest_name}")
            return None

        log.info(f"Creating destination playlist: {dest_name}")
        return self.session.user.create_playlist(dest_name, f"Filtered new music - updated {datetime.now(timezone.utc).strftime('%Y-%m-%d')}")

    def _get_destination_track_ids(self, playlist: tidalapi.Playlist) -> set[str]:
        """Get all track IDs currently in destination playlist."""
        tracks = playlist.tracks()
        return {str(t.id) for t in tracks}

    def filter_playlist(self, source_playlist_id: str) -> list[tidalapi.Track]:
        """Filter a source playlist and return tracks to add."""
        try:
            source = self.session.playlist(source_playlist_id)
        except Exception as e:
            log.error(f"Could not fetch source playlist {source_playlist_id}: {e}")
            return []

        log.info(f"Processing playlist: {source.name} ({len(source.tracks())} tracks)")

        tracks_to_add = []
        for track in source.tracks():
            track_id = str(track.id)

            # Skip already processed
            if track_id in self.processed_tracks:
                log.debug(f"Skipping already processed: {track.name}")
                continue

            # Get genres
            genres = self._get_genres(track)
            genres_str = ", ".join(genres) if genres else "unknown"

            # Check blocklist
            if self._is_blocked(genres):
                log.info(f"BLOCKED: {track.artist.name} - {track.name} [{genres_str}]")
                self.processed_tracks.add(track_id)
                continue

            # Handle unknown genres
            if not genres:
                policy = self.config.get("unknown_genre_policy", "keep")
                if policy == "skip":
                    log.info(f"SKIPPED (unknown genre): {track.artist.name} - {track.name}")
                    self.processed_tracks.add(track_id)
                    continue
                log.info(f"KEEPING (unknown genre): {track.artist.name} - {track.name}")

            log.info(f"ADDING: {track.artist.name} - {track.name} [{genres_str}]")
            tracks_to_add.append(track)
            self.processed_tracks.add(track_id)

        return tracks_to_add

    def run_filter(self) -> None:
        """Filter new arrivals and add to destination playlist."""
        if not self.login():
            sys.exit(1)

        self._init_genre_client()

        # Get source playlist IDs
        source_ids = self.config.get("source_playlist_ids", [])
        if not source_ids:
            log.error("No source_playlist_ids configured")
            sys.exit(1)

        # Get or create destination playlist
        dest_playlist = self._get_or_create_destination_playlist()

        # Get existing tracks in destination (for duplicate avoidance)
        existing_track_ids: set[str] = set()
        if dest_playlist:
            existing_track_ids = self._get_destination_track_ids(dest_playlist)
            log.info(f"Destination playlist has {len(existing_track_ids)} existing tracks")

        # Detect user removals: tracks in our last snapshot that are no longer in the playlist
        if self.destination_snapshot:
            removed = self.destination_snapshot - existing_track_ids
            if removed:
                log.info(f"Detected {len(removed)} tracks removed by user — permanently excluding")
                self.removed_tracks.update(removed)

        if self.removed_tracks:
            log.info(f"Total removed tracks (never re-add): {len(self.removed_tracks)}")

        # Filter all source playlists
        all_tracks_to_add: list[tidalapi.Track] = []
        for source_id in source_ids:
            filtered = self.filter_playlist(source_id)
            # Remove duplicates (already in destination) and user-removed tracks
            new_tracks = [t for t in filtered
                          if str(t.id) not in existing_track_ids
                          and str(t.id) not in self.removed_tracks]
            all_tracks_to_add.extend(new_tracks)
            # Update existing set to avoid duplicates between sources
            existing_track_ids.update(str(t.id) for t in new_tracks)

        if not all_tracks_to_add:
            log.info("No new tracks to add")
        elif self.dry_run:
            log.info(f"[DRY RUN] Would add {len(all_tracks_to_add)} tracks to destination playlist")
        else:
            # Add tracks to destination
            track_ids = [str(t.id) for t in all_tracks_to_add]
            log.info(f"Adding {len(track_ids)} tracks to {dest_playlist.name}")
            dest_playlist.add(track_ids)
            existing_track_ids.update(track_ids)

        # Save caches
        if self.genre_client:
            self.genre_client.save()
        if self.spotify_client:
            self.spotify_client.save()

        # Save processed tracks, removed tracks, and destination snapshot (skip in dry-run mode)
        if not self.dry_run:
            save_json(self.processed_path, {"tracks": list(self.processed_tracks)})
            save_json(self.removed_path, {"tracks": list(self.removed_tracks)})
            save_json(self.snapshot_path, {"tracks": list(existing_track_ids)})
        log.info("Done!")

    def run_rotate(self) -> None:
        """Rotate oldest tracks from master playlist to archive."""
        if not self.login():
            sys.exit(1)

        rotate_cfg = self.config.get("rotate", {})
        master_id = rotate_cfg.get("master_playlist_id")
        archive_id = rotate_cfg.get("archive_playlist_id")
        max_tracks = rotate_cfg.get("max_tracks", 200)

        if not master_id or not archive_id:
            log.error("rotate.master_playlist_id and rotate.archive_playlist_id must be configured")
            sys.exit(1)

        # Fetch playlists
        try:
            master = self.session.playlist(master_id)
            archive = self.session.playlist(archive_id)
        except Exception as e:
            log.error(f"Could not fetch playlists: {e}")
            sys.exit(1)

        log.info(f"Master playlist: {master.name} ({master.num_tracks} tracks)")
        log.info(f"Archive playlist: {archive.name} ({archive.num_tracks} tracks)")
        log.info(f"Max tracks allowed: {max_tracks}")

        # Check if rotation needed
        overflow = master.num_tracks - max_tracks
        if overflow <= 0:
            log.info("No rotation needed - master playlist is within limit")
            return

        log.info(f"Need to rotate {overflow} tracks")

        # Get tracks to rotate (oldest = first in list)
        master_tracks = master.tracks()
        tracks_to_rotate = master_tracks[:overflow]

        for i, track in enumerate(tracks_to_rotate, 1):
            log.info(f"  {i}. {track.artist.name} - {track.name}")

        if self.dry_run:
            log.info(f"[DRY RUN] Would move {overflow} tracks from {master.name} to {archive.name}")
            return

        # Add to archive (appends to bottom)
        track_ids = [str(t.id) for t in tracks_to_rotate]
        log.info(f"Adding {len(track_ids)} tracks to {archive.name}")
        archive.add(track_ids)

        # Remove from master
        # tidalapi uses index for removal, tracks are at indices 0 to overflow-1
        log.info(f"Removing {len(track_ids)} tracks from {master.name}")
        master.remove_by_indices(list(range(overflow)))

        log.info("Rotation complete!")

    def run_like(self) -> None:
        """Like/favorite all tracks from configured playlists."""
        if not self.login():
            sys.exit(1)

        like_cfg = self.config.get("like", {})
        playlist_prefix = like_cfg.get("playlist_prefix", "_CBM")

        # Find playlists matching prefix
        playlists = []
        for pl in self.session.user.playlists():
            if playlist_prefix in pl.name:
                playlists.append(pl)

        if not playlists:
            log.error(f"No playlists found matching prefix: {playlist_prefix}")
            sys.exit(1)

        log.info(f"Found {len(playlists)} playlists matching '{playlist_prefix}':")
        total_tracks = 0
        for pl in playlists:
            log.info(f"  {pl.name}: {pl.num_tracks} tracks")
            total_tracks += pl.num_tracks

        # Get currently favorited track IDs to avoid re-liking
        log.info("Fetching current favorites...")
        current_fav_ids: set[str] = set()
        try:
            fav_tracks = self.session.user.favorites.tracks(limit=10000)
            current_fav_ids = {str(t.id) for t in fav_tracks}
        except Exception as e:
            log.warning(f"Could not fetch current favorites: {e}")

        log.info(f"Currently have {len(current_fav_ids)} favorited tracks")

        # Collect all unique track IDs to like
        tracks_to_like: list[tuple[str, str, str]] = []  # (id, artist, name)
        seen_ids: set[str] = set()

        for pl in playlists:
            log.info(f"Processing {pl.name}...")
            for track in pl.tracks(limit=10000):
                track_id = str(track.id)
                if track_id not in seen_ids and track_id not in current_fav_ids:
                    tracks_to_like.append((track_id, track.artist.name, track.name))
                    seen_ids.add(track_id)

        if not tracks_to_like:
            log.info("All tracks are already favorited!")
            return

        log.info(f"Found {len(tracks_to_like)} tracks to favorite")

        if self.dry_run:
            log.info(f"[DRY RUN] Would favorite {len(tracks_to_like)} tracks")
            for tid, artist, name in tracks_to_like[:10]:
                log.info(f"  {artist} - {name}")
            if len(tracks_to_like) > 10:
                log.info(f"  ... and {len(tracks_to_like) - 10} more")
            return

        # Like tracks
        favorites = self.session.user.favorites
        liked = 0
        for i, (track_id, artist, name) in enumerate(tracks_to_like, 1):
            try:
                favorites.add_track(int(track_id))
                liked += 1
                if i % 100 == 0:
                    log.info(f"Progress: {i}/{len(tracks_to_like)} tracks liked")
            except Exception as e:
                log.warning(f"Failed to like {artist} - {name}: {e}")

        log.info(f"Liked {liked} tracks!")


def do_login(config: dict[str, Any]) -> None:
    """Perform interactive OAuth login."""
    session = tidalapi.Session()
    login, future = session.login_oauth()
    print(f"\nVisit: {login.verification_uri_complete}")
    print(f"Or go to {login.verification_uri} and enter code: {login.user_code}")
    print("\nWaiting for authorization...")

    future.result()

    if session.check_login():
        session_path = Path(config.get("session_path", "tidal_session.json"))
        session.save_session_to_file(session_path)
        print(f"\nLogged in as: {session.user.first_name} {session.user.last_name}")
        print(f"Session saved to: {session_path}")
    else:
        print("Login failed!")
        sys.exit(1)


def main() -> None:
    parser = argparse.ArgumentParser(description="Tidal playlist automation")
    parser.add_argument("command", nargs="?", default="filter", choices=["filter", "rotate", "like", "all"],
                        help="Command to run: filter (new arrivals), rotate (master→archive), like (favorite tracks), all (filter+rotate+like)")
    parser.add_argument("--config", default="config.json", help="Path to config file")
    parser.add_argument("--dry-run", action="store_true", help="Preview changes without modifying playlists")
    parser.add_argument("--login", action="store_true", help="Perform OAuth login")
    parser.add_argument("-v", "--verbose", action="store_true", help="Enable debug logging")
    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    # Load environment
    load_dotenv()

    # Load config
    config_path = Path(args.config)
    if not config_path.exists():
        log.error(f"Config file not found: {config_path}")
        sys.exit(1)

    config = load_json(config_path)

    # Handle --dry-run override
    dry_run = args.dry_run or config.get("dry_run", False)

    if args.login:
        do_login(config)
        return

    automation = TidalAutomation(config, dry_run=dry_run)

    if args.command == "filter":
        automation.run_filter()
    elif args.command == "rotate":
        automation.run_rotate()
    elif args.command == "like":
        automation.run_like()
    elif args.command == "all":
        automation.run_filter()
        automation.run_rotate()
        automation.run_like()


if __name__ == "__main__":
    main()
