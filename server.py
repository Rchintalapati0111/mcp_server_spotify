import contextlib
import logging
import os
import base64
import time
import random
from collections.abc import AsyncIterator
from typing import Any, Dict, List, Optional

import asyncio
import json
import secrets
import hashlib
from urllib.parse import urlencode

import click
import aiohttp
from dotenv import load_dotenv

import mcp.types as types
from mcp.server.lowlevel import Server
from mcp.server.sse import SseServerTransport
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager

from starlette.applications import Starlette
from starlette.responses import (
    Response,
    RedirectResponse,
    PlainTextResponse,
    HTMLResponse,
)
from starlette.routing import Mount, Route
from starlette.types import Receive, Scope, Send
import sys


# -----------------------------------------------------------------------------
# Setup
# -----------------------------------------------------------------------------

logger = logging.getLogger(__name__)
load_dotenv()

# Spotify API constants (env is validated in main())
SPOTIFY_CLIENT_ID = os.getenv("SPOTIFY_CLIENT_ID")
SPOTIFY_CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET")
SPOTIFY_ACCESS_TOKEN = os.getenv("SPOTIFY_ACCESS_TOKEN")  # For user-specific operations
SPOTIFY_REFRESH_TOKEN = os.getenv("SPOTIFY_REFRESH_TOKEN")

SPOTIFY_API_BASE = "https://api.spotify.com/v1"
SPOTIFY_AUTH_URL = "https://accounts.spotify.com/api/token"
SPOTIFY_MCP_SERVER_PORT = int(os.getenv("SPOTIFY_MCP_SERVER_PORT", "5000"))


# OAuth redirect and PKCE in-memory store
SPOTIFY_REDIRECT_URI = os.getenv("SPOTIFY_REDIRECT_URI", "http://localhost:5000/oauth/callback")
_PKCE_STORE: Dict[str, Dict[str, str]] = {}  # state -> {verifier, ts}

# Optional: auto-load a saved refresh token (handy for dev)
try:
    with open(".secrets/spotify_tokens.json") as f:
        tok = json.load(f).get("refresh_token")
        if tok and not SPOTIFY_REFRESH_TOKEN:
            SPOTIFY_REFRESH_TOKEN = tok
except FileNotFoundError:
    pass


# -----------------------------------------------------------------------------
# Shared HTTP session + token caches
# -----------------------------------------------------------------------------

_http_session: Optional[aiohttp.ClientSession] = None

async def get_http_session() -> aiohttp.ClientSession:
    global _http_session
    if _http_session is None or _http_session.closed:
        _http_session = aiohttp.ClientSession()
    return _http_session

async def close_http_session():
    global _http_session
    if _http_session and not _http_session.closed:
        await _http_session.close()

_app_token_cache: Dict[str, Any] = {"value": None, "exp": 0}
_user_token_cache: Dict[str, Any] = {"value": None, "exp": 0}

class SpotifyAuthError(Exception):
    pass

class SpotifyAPIError(Exception):
    pass

# -----------------------------------------------------------------------------
# Enhanced Auth helpers with caching and better error handling
# -----------------------------------------------------------------------------

async def get_client_credentials_token(force_refresh: bool = False) -> str:
    """
    Get access token using client credentials flow; cache until expiry.
    """
    if not force_refresh and _app_token_cache["value"] and time.time() < _app_token_cache["exp"] - 60:
        return _app_token_cache["value"]

    if not SPOTIFY_CLIENT_ID or not SPOTIFY_CLIENT_SECRET:
        raise SpotifyAuthError("Missing SPOTIFY_CLIENT_ID or SPOTIFY_CLIENT_SECRET")

    auth_string = f"{SPOTIFY_CLIENT_ID}:{SPOTIFY_CLIENT_SECRET}"
    auth_b64 = base64.b64encode(auth_string.encode("ascii")).decode("ascii")

    headers = {"Authorization": f"Basic {auth_b64}", "Content-Type": "application/x-www-form-urlencoded"}
    data = {"grant_type": "client_credentials"}

    session = await get_http_session()
    async with session.post(SPOTIFY_AUTH_URL, headers=headers, data=data) as response:
        if response.status != 200:
            text = await response.text()
            raise SpotifyAuthError(f"Failed to get access token: {response.status} - {text}")
        token_data = await response.json()
        _app_token_cache["value"] = token_data["access_token"]
        _app_token_cache["exp"] = time.time() + int(token_data.get("expires_in", 3600))
        return _app_token_cache["value"]

async def refresh_user_token() -> str:
    if not SPOTIFY_REFRESH_TOKEN:
        raise SpotifyAuthError("No SPOTIFY_REFRESH_TOKEN available for user auth")
    if not SPOTIFY_CLIENT_ID or not SPOTIFY_CLIENT_SECRET:
        raise SpotifyAuthError("Missing SPOTIFY_CLIENT_ID or SPOTIFY_CLIENT_SECRET")

    auth_string = f"{SPOTIFY_CLIENT_ID}:{SPOTIFY_CLIENT_SECRET}"
    auth_b64 = base64.b64encode(auth_string.encode("ascii")).decode("ascii")
    headers = {"Authorization": f"Basic {auth_b64}", "Content-Type": "application/x-www-form-urlencoded"}
    data = {"grant_type": "refresh_token", "refresh_token": SPOTIFY_REFRESH_TOKEN}

    session = await get_http_session()
    async with session.post(SPOTIFY_AUTH_URL, headers=headers, data=data) as response:
        if response.status != 200:
            text = await response.text()
            raise SpotifyAuthError(f"Failed to refresh access token: {response.status} - {text}")
        token_data = await response.json()
        _user_token_cache["value"] = token_data["access_token"]
        _user_token_cache["exp"] = time.time() + int(token_data.get("expires_in", 3600))
        return _user_token_cache["value"]

async def get_user_access_token(force_refresh: bool = False) -> str:
    if not force_refresh and _user_token_cache["value"] and time.time() < _user_token_cache["exp"] - 60:
        return _user_token_cache["value"]
    if SPOTIFY_REFRESH_TOKEN:
        return await refresh_user_token()
    if SPOTIFY_ACCESS_TOKEN:
        _user_token_cache["value"] = SPOTIFY_ACCESS_TOKEN
        _user_token_cache["exp"] = time.time() + 300  # treat as short-lived
        return _user_token_cache["value"]
    raise SpotifyAuthError("No user access token or refresh token available")

# Enhanced safe version of user token getter
async def get_user_access_token_safe(force_refresh: bool = False) -> str:
    """Safe version that handles auth failures gracefully"""
    try:
        return await get_user_access_token(force_refresh)
    except SpotifyAuthError as e:
        if "invalid_grant" in str(e).lower() or "revoked" in str(e).lower():
            logger.warning("Refresh token is invalid/revoked. User authentication unavailable.")
            raise SpotifyAuthError(
                "User authentication is unavailable. The refresh token has been revoked or expired. "
                "Please run 'python refresh_tokens.py' to get new tokens."
            )
        raise

# Check if user auth is available
async def check_user_auth_available() -> bool:
    """Check if user authentication is available"""
    try:
        await get_user_access_token_safe()
        return True
    except SpotifyAuthError:
        return False


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")

def _new_code_verifier() -> str:
    # 43–128 chars
    return _b64url(secrets.token_bytes(64))

def _code_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return _b64url(digest)

# -----------------------------------------------------------------------------
# Enhanced Core request with retry/backoff and better error handling
# -----------------------------------------------------------------------------

async def make_spotify_request(
    endpoint: str,
    method: str = "GET",
    requires_user_auth: bool = False,
    params: Optional[Dict[str, Any]] = None,
    json_data: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """
    Enhanced version with better auth handling and error messages
    """
    session = await get_http_session()

    try:
        if requires_user_auth:
            token = await get_user_access_token_safe()
        else:
            token = await get_client_credentials_token()
    except SpotifyAuthError as e:
        # Provide helpful error message for users
        if requires_user_auth and ("invalid_grant" in str(e) or "revoked" in str(e)):
            raise SpotifyAuthError(
                "This feature requires user authentication, but your tokens are invalid. "
                "Please run 'python refresh_tokens.py' to get new authentication tokens, "
                "then update your .env file with the new SPOTIFY_REFRESH_TOKEN."
            )
        raise

    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    url = f"{SPOTIFY_API_BASE}/{endpoint.lstrip('/')}"

    attempts = 0
    max_attempts = 4  # normal + 401 retry + one 429 retry + one 5xx retry
    base_backoff = 0.75

    while True:
        attempts += 1
        try:
            async with session.request(method=method, url=url, headers=headers, params=params, json=json_data) as response:
                # Fast path: no content
                if response.status == 204:
                    return {}

                # Handle 401 once (token refresh)
                if response.status == 401 and attempts == 1:
                    logger.info("401 received; refreshing token and retrying once")
                    if requires_user_auth:
                        token = await get_user_access_token_safe(force_refresh=True)
                    else:
                        token = await get_client_credentials_token(force_refresh=True)
                    headers["Authorization"] = f"Bearer {token}"
                    continue

                # Basic 429 backoff (Retry-After honored if present)
                if response.status == 429 and attempts < max_attempts:
                    retry_after = response.headers.get("Retry-After")
                    delay = float(retry_after) if retry_after else (base_backoff + random.random())
                    logger.warning(f"429 rate limited; backing off {delay:.2f}s (attempt {attempts}/{max_attempts})")
                    await asyncio.sleep(delay)
                    continue

                # Transient 5xx retry (once, with jittered backoff)
                if response.status in (502, 503, 504) and attempts < max_attempts:
                    delay = (base_backoff * attempts) + random.random()
                    logger.warning(f"{response.status} from Spotify; retrying in {delay:.2f}s "
                                   f"(attempt {attempts}/{max_attempts})")
                    await asyncio.sleep(delay)
                    continue

                # Error path: capture body + key headers for diagnostics
                if response.status >= 400:
                    body_text = await response.text()
                    diag_headers = {k: v for k, v in response.headers.items()
                                    if k.lower() in ("retry-after", "content-type", "cache-control")}
                    logger.error(
                        "Spotify API error %s\n"
                        "→ %s %s\n"
                        "→ params=%s json=%s\n"
                        "→ headers=%s\n"
                        "→ body=%s",
                        response.status, method, url, params, json_data, diag_headers, body_text[:800]
                    )
                    if response.status == 403:
                        if requires_user_auth:
                            raise SpotifyAuthError(
                                f"Insufficient permissions (403). This feature may require additional scopes. "
                                f"Try running 'python refresh_tokens.py' to re-authorize with full permissions."
                            )
                        else:
                            raise SpotifyAuthError(f"Insufficient permissions (403): {body_text}")
                    raise SpotifyAPIError(f"API error {response.status}: {body_text}")

                # Success: try JSON, tolerate empty
                try:
                    return await response.json()
                except Exception:
                    text = await response.text()
                    # Some endpoints can return empty body with 2xx (rare). Normalize to {}
                    return {} if not text.strip() else {"_raw": text}

        except asyncio.TimeoutError:
            # Optional: treat timeouts as transient; retry once if room
            if attempts < max_attempts:
                delay = (base_backoff * attempts) + random.random()
                logger.warning(f"Timeout calling {method} {url}; retrying in {delay:.2f}s "
                               f"(attempt {attempts}/{max_attempts})")
                await asyncio.sleep(delay)
                continue
            raise


# -----------------------------------------------------------------------------
# Tool definitions (exportable for tests)
# -----------------------------------------------------------------------------

def get_tool_definitions() -> List[types.Tool]:
    return [
        types.Tool(
            name="search_music",
            description="Search for tracks, artists, albums, or playlists on Spotify. Specify the query and type of content to search for. Returns detailed results including IDs, names, popularity, and other metadata.",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query (e.g., 'Bohemian Rhapsody', 'The Beatles', 'jazz')"},
                    "type": {"type": "string", "enum": ["track", "artist", "album", "playlist"], "description": "Type of content to search for"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 50, "default": 10, "description": "Number of results to return (1-50)"},
                    "market": {"type": "string", "description": "Market/country code (e.g., 'US', 'GB') for track availability"}
                },
                "required": ["query", "type"]
            }
        ),
        types.Tool(
            name="get_track_details",
            description="Get detailed information about a specific track including audio features, popularity, duration, and album information. Requires a Spotify track ID.",
            inputSchema={
                "type": "object",
                "properties": {
                    "track_id": {"type": "string", "description": "Spotify track ID (e.g., '4iV5W9uYEdYUVa79Axb7Rh')"},
                    "market": {"type": "string", "description": "Market/country code for track availability"}
                },
                "required": ["track_id"]
            }
        ),
        types.Tool(
            name="get_track_audio_features",
            description="Get detailed audio features for a track including tempo, key, danceability, energy, valence, and other musical characteristics.",
            inputSchema={"type":"object","properties":{"track_id":{"type":"string","description":"Spotify track ID"}},"required":["track_id"]}
        ),
        types.Tool(
            name="get_artist_details",
            description="Get comprehensive information about an artist including genres, popularity, follower count, and images.",
            inputSchema={"type":"object","properties":{"artist_id":{"type":"string","description":"Spotify artist ID"}},"required":["artist_id"]}
        ),
        types.Tool(
            name="get_artist_top_tracks",
            description="Get an artist's most popular tracks in a specific market. Returns up to 10 tracks.",
            inputSchema={"type":"object","properties":{"artist_id":{"type":"string"},"market":{"type":"string","default":"US"}},"required":["artist_id"]}
        ),
        types.Tool(
            name="get_artist_albums",
            description="Get all albums by an artist including studio albums, singles, compilations, and appears-on releases.",
            inputSchema={
                "type":"object",
                "properties":{
                    "artist_id":{"type":"string"},
                    "include_groups":{"type":"string","default":"album,single"},
                    "market":{"type":"string"},
                    "limit":{"type":"integer","minimum":1,"maximum":50,"default":20},
                    "offset":{"type":"integer","minimum":0,"default":0}
                },
                "required":["artist_id"]
            }
        ),
        types.Tool(
            name="get_album_details",
            description="Get complete album information including tracks, release date, label, genres, and images.",
            inputSchema={"type":"object","properties":{"album_id":{"type":"string"},"market":{"type":"string"}},"required":["album_id"]}
        ),
        types.Tool(
            name="get_album_tracks",
            description="Get all tracks from a specific album with detailed information.",
            inputSchema={
                "type":"object",
                "properties":{
                    "album_id":{"type":"string"},
                    "market":{"type":"string"},
                    "limit":{"type":"integer","minimum":1,"maximum":50,"default":50},
                    "offset":{"type":"integer","minimum":0,"default":0}
                },
                "required":["album_id"]
            }
        ),
        types.Tool(
            name="get_playlist_details",
            description="Get comprehensive playlist information including description, follower count, tracks count, and owner details.",
            inputSchema={"type":"object","properties":{"playlist_id":{"type":"string"},"market":{"type":"string"}},"required":["playlist_id"]}
        ),
        types.Tool(
            name="get_playlist_tracks",
            description="Get all tracks from a playlist with complete track and artist information.",
            inputSchema={
                "type":"object",
                "properties":{
                    "playlist_id":{"type":"string"},
                    "market":{"type":"string"},
                    "limit":{"type":"integer","minimum":1,"maximum":100,"default":50},
                    "offset":{"type":"integer","minimum":0,"default":0}
                },
                "required":["playlist_id"]
            }
        ),
        types.Tool(
            name="get_music_recommendations",
            description="Get personalized music recommendations based on seed tracks, artists, or genres. Supports tunable attributes.",
            inputSchema={
                "type":"object",
                "properties":{
                    "seed_tracks":{"type":"string","description":"Comma-separated track IDs (max 5 seeds total)"},
                    "seed_artists":{"type":"string","description":"Comma-separated artist IDs (max 5)"},
                    "seed_genres":{"type":"string","description":"Comma-separated genres (max 5)"},
                    "limit":{"type":"integer","minimum":1,"maximum":100,"default":20},
                    "market":{"type":"string"},
                    "min_energy":{"type":"number","minimum":0,"maximum":1},
                    "max_energy":{"type":"number","minimum":0,"maximum":1},
                    "min_danceability":{"type":"number","minimum":0,"maximum":1},
                    "max_danceability":{"type":"number","minimum":0,"maximum":1},
                    "min_valence":{"type":"number","minimum":0,"maximum":1},
                    "max_valence":{"type":"number","minimum":0,"maximum":1},
                    "target_tempo":{"type":"number","minimum":0}
                },
                "required":[]
            }
        ),
        types.Tool(
            name="get_available_genres",
            description="Get the list of all available genre seeds for recommendations.",
            inputSchema={"type":"object","properties":{},"required":[]}
        ),
        types.Tool(
            name="get_new_releases",
            description="Get a list of new album releases featured on Spotify.",
            inputSchema={
                "type":"object",
                "properties":{
                    "country":{"type":"string"},
                    "limit":{"type":"integer","minimum":1,"maximum":50,"default":20},
                    "offset":{"type":"integer","minimum":0,"default":0}
                },
                "required":[]
            }
        ),
        types.Tool(
            name="get_featured_playlists",
            description="Get featured playlists from Spotify's editorial team.",
            inputSchema={
                "type":"object",
                "properties":{
                    "country":{"type":"string"},
                    "limit":{"type":"integer","minimum":1,"maximum":50,"default":20},
                    "offset":{"type":"integer","minimum":0,"default":0},
                    "timestamp":{"type":"string","description":"ISO 8601 timestamp"}
                },
                "required":[]
            }
        ),
        types.Tool(
            name="get_categories",
            description="Get all available music categories/genres used by Spotify.",
            inputSchema={
                "type":"object",
                "properties":{
                    "country":{"type":"string"},
                    "locale":{"type":"string"},
                    "limit":{"type":"integer","minimum":1,"maximum":50,"default":20},
                    "offset":{"type":"integer","minimum":0,"default":0}
                },
                "required":[]
            }
        ),
        types.Tool(
            name="get_category_playlists",
            description="Get playlists from a specific category.",
            inputSchema={
                "type":"object",
                "properties":{
                    "category_id":{"type":"string"},
                    "country":{"type":"string"},
                    "limit":{"type":"integer","minimum":1,"maximum":50,"default":20},
                    "offset":{"type":"integer","minimum":0,"default":0}
                },
                "required":["category_id"]
            }
        ),
    ]

# -----------------------------------------------------------------------------
# Build server and implement tool handlers with enhanced error handling
# -----------------------------------------------------------------------------

def build_server() -> Server:
    server = Server("spotify-mcp-server")

    @server.list_tools()
    async def handle_list_tools() -> List[types.Tool]:
        return get_tool_definitions()

    @server.call_tool()
    async def handle_call_tool(name: str, arguments: Dict[str, Any]) -> List[types.TextContent]:
        logger.info(f"Tool called: {name} with args: {arguments}")
        
        try:
            # Tools that require user authentication
            user_auth_tools = {
                "get_track_audio_features", 
                "get_music_recommendations", 
                "get_available_genres"
            }
            
            if name in user_auth_tools:
                # Check if user auth is available
                if not await check_user_auth_available():
                    return [types.TextContent(
                        type="text",
                        text=(
                            f"❌ The '{name}' tool requires user authentication, but your tokens are invalid.\n\n"
                            "🔧 To fix this:\n"
                            "1. Run: python refresh_tokens.py\n"
                            "2. Follow the authorization steps\n"
                            "3. Update your .env file with the new tokens\n"
                            "4. Restart the server\n\n"
                            "This will restore access to audio features, recommendations, and genre data."
                        )
                    )]

            # -----------------------------------------------------------------
            if name == "search_music":
                query = arguments["query"]
                search_type = arguments["type"]
                limit = arguments.get("limit", 10)
                market = arguments.get("market")

                params = {"q": query, "type": search_type, "limit": limit}
                if market:
                    params["market"] = market

                result = await make_spotify_request("search", params=params)
                search_key = f"{search_type}s"
                items = result.get(search_key, {}).get("items", [])

                formatted = []
                for item in items:
                    if search_type == "track":
                        formatted.append({
                            "id": item.get("id"),
                            "name": item.get("name"),
                            "artists": [a.get("name") for a in item.get("artists", [])],
                            "album": (item.get("album") or {}).get("name"),
                            "duration_ms": item.get("duration_ms"),
                            "popularity": item.get("popularity"),
                            "preview_url": item.get("preview_url"),
                            "external_urls": item.get("external_urls"),
                        })
                    elif search_type == "artist":
                        followers = (item.get("followers") or {}).get("total")
                        formatted.append({
                            "id": item.get("id"),
                            "name": item.get("name"),
                            "genres": item.get("genres", []),
                            "popularity": item.get("popularity"),
                            "followers": followers,
                            "external_urls": item.get("external_urls"),
                        })
                    elif search_type == "album":
                        formatted.append({
                            "id": item.get("id"),
                            "name": item.get("name"),
                            "artists": [a.get("name") for a in item.get("artists", [])],
                            "release_date": item.get("release_date"),
                            "total_tracks": item.get("total_tracks"),
                            "external_urls": item.get("external_urls"),
                        })
                    elif search_type == "playlist":
                        owner = item.get("owner") or {}
                        formatted.append({
                            "id": item.get("id"),
                            "name": item.get("name"),
                            "description": item.get("description", "") or "",
                            "owner": owner.get("display_name") or owner.get("id"),
                            "tracks_total": (item.get("tracks") or {}).get("total"),
                            "public": item.get("public"),
                            "external_urls": item.get("external_urls"),
                        })

                return [types.TextContent(type="text", text=f"Found {len(formatted)} {search_type}(s) for '{query}':\n\n" + json.dumps(formatted, indent=2))]

            # -----------------------------------------------------------------
            elif name == "get_track_details":
                track_id = arguments["track_id"]
                market = arguments.get("market")
                params = {"market": market} if market else None
                track = await make_spotify_request(f"tracks/{track_id}", params=params)

                album = track.get("album") or {}
                result = {
                    "id": track.get("id"),
                    "name": track.get("name"),
                    "artists": [{"id": a.get("id"), "name": a.get("name")} for a in track.get("artists", [])],
                    "album": {
                        "id": album.get("id"),
                        "name": album.get("name"),
                        "release_date": album.get("release_date"),
                        "images": album.get("images", []),
                    },
                    "duration_ms": track.get("duration_ms"),
                    "popularity": track.get("popularity"),
                    "preview_url": track.get("preview_url"),
                    "explicit": track.get("explicit"),
                    "external_urls": track.get("external_urls"),
                    "available_markets": track.get("available_markets", []),
                }
                return [types.TextContent(type="text", text=f"Track Details:\n\n{json.dumps(result, indent=2)}")]

            # -----------------------------------------------------------------
            elif name == "get_track_audio_features":
                track_id = arguments["track_id"]
                features = await make_spotify_request(f"audio-features/{track_id}", requires_user_auth=True)
                return [types.TextContent(type="text", text=f"Audio Features for Track {track_id}:\n\n{json.dumps(features, indent=2)}")]

            # -----------------------------------------------------------------
            elif name == "get_artist_details":
                artist_id = arguments["artist_id"]
                artist = await make_spotify_request(f"artists/{artist_id}")
                result = {
                    "id": artist.get("id"),
                    "name": artist.get("name"),
                    "genres": artist.get("genres", []),
                    "popularity": artist.get("popularity"),
                    "followers": (artist.get("followers") or {}).get("total"),
                    "images": artist.get("images", []),
                    "external_urls": artist.get("external_urls"),
                }
                return [types.TextContent(type="text", text=f"Artist Details:\n\n{json.dumps(result, indent=2)}")]

            # -----------------------------------------------------------------
            elif name == "get_artist_top_tracks":
                artist_id = arguments["artist_id"]
                market = arguments.get("market", "US")
                params = {"market": market}
                result = await make_spotify_request(f"artists/{artist_id}/top-tracks", params=params)
                tracks_out = []
                for track in result.get("tracks", []):
                    tracks_out.append({
                        "id": track.get("id"),
                        "name": track.get("name"),
                        "album": (track.get("album") or {}).get("name"),
                        "popularity": track.get("popularity"),
                        "preview_url": track.get("preview_url"),
                        "external_urls": track.get("external_urls"),
                    })
                return [types.TextContent(type="text", text=f"Top Tracks for Artist {artist_id} in {market}:\n\n{json.dumps(tracks_out, indent=2)}")]

            # -----------------------------------------------------------------
            elif name == "get_artist_albums":
                artist_id = arguments["artist_id"]
                include_groups = arguments.get("include_groups", "album,single")
                market = arguments.get("market")
                limit = arguments.get("limit", 20)
                offset = arguments.get("offset", 0)

                params = {"include_groups": include_groups, "limit": limit, "offset": offset}
                if market:
                    params["market"] = market

                result = await make_spotify_request(f"artists/{artist_id}/albums", params=params)
                albums = []
                for album in result.get("items", []):
                    albums.append({
                        "id": album.get("id"),
                        "name": album.get("name"),
                        "album_type": album.get("album_type"),
                        "release_date": album.get("release_date"),
                        "total_tracks": album.get("total_tracks"),
                        "external_urls": album.get("external_urls"),
                    })
                return [types.TextContent(type="text", text=f"Albums by Artist {artist_id}:\n\n{json.dumps(albums, indent=2)}")]

            # -----------------------------------------------------------------
            elif name == "get_album_details":
                album_id = arguments["album_id"]
                market = arguments.get("market")
                params = {"market": market} if market else None
                album = await make_spotify_request(f"albums/{album_id}", params=params)
                result = {
                    "id": album.get("id"),
                    "name": album.get("name"),
                    "artists": [{"id": a.get("id"), "name": a.get("name")} for a in album.get("artists", [])],
                    "release_date": album.get("release_date"),
                    "total_tracks": album.get("total_tracks"),
                    "genres": album.get("genres", []),
                    "label": album.get("label"),
                    "popularity": album.get("popularity"),
                    "images": album.get("images", []),
                    "external_urls": album.get("external_urls"),
                    "copyrights": album.get("copyrights", [])
                }
                return [types.TextContent(type="text", text=f"Album Details:\n\n{json.dumps(result, indent=2)}")]

            # -----------------------------------------------------------------
            elif name == "get_album_tracks":
                album_id = arguments["album_id"]
                market = arguments.get("market")
                limit = arguments.get("limit", 50)
                offset = arguments.get("offset", 0)

                params = {"limit": limit, "offset": offset}
                if market:
                    params["market"] = market

                result = await make_spotify_request(f"albums/{album_id}/tracks", params=params)
                tracks = []
                for track in result.get("items", []):
                    tracks.append({
                        "id": track.get("id"),
                        "name": track.get("name"),
                        "track_number": track.get("track_number"),
                        "duration_ms": track.get("duration_ms"),
                        "explicit": track.get("explicit"),
                        "preview_url": track.get("preview_url"),
                        "external_urls": track.get("external_urls"),
                    })
                return [types.TextContent(type="text", text=f"Tracks from Album {album_id}:\n\n{json.dumps(tracks, indent=2)}")]

            # -----------------------------------------------------------------
            elif name == "get_playlist_details":
                playlist_id = arguments["playlist_id"]
                market = arguments.get("market")
                params = {"market": market} if market else None

                playlist = await make_spotify_request(f"playlists/{playlist_id}", params=params)
                owner = playlist.get("owner") or {}
                result = {
                    "id": playlist.get("id"),
                    "name": playlist.get("name"),
                    "description": playlist.get("description", "") or "",
                    "owner": {
                        "id": owner.get("id"),
                        "display_name": owner.get("display_name") or owner.get("id")
                    },
                    "public": playlist.get("public"),
                    "collaborative": playlist.get("collaborative"),
                    "followers": (playlist.get("followers") or {}).get("total"),
                    "tracks_total": (playlist.get("tracks") or {}).get("total"),
                    "images": playlist.get("images", []),
                    "external_urls": playlist.get("external_urls"),
                }
                return [types.TextContent(type="text", text=f"Playlist Details:\n\n{json.dumps(result, indent=2)}")]

            # -----------------------------------------------------------------
            elif name == "get_playlist_tracks":
                playlist_id = arguments["playlist_id"]
                market = arguments.get("market")
                limit = arguments.get("limit", 50)
                offset = arguments.get("offset", 0)

                params = {"limit": limit, "offset": offset}
                if market:
                    params["market"] = market

                result = await make_spotify_request(f"playlists/{playlist_id}/tracks", params=params)
                tracks = []
                for item in result.get("items", []):
                    track = (item or {}).get("track") or {}
                    if track.get("type") == "track":
                        tracks.append({
                            "id": track.get("id"),
                            "name": track.get("name"),
                            "artists": [a.get("name") for a in track.get("artists", [])],
                            "album": (track.get("album") or {}).get("name"),
                            "duration_ms": track.get("duration_ms"),
                            "popularity": track.get("popularity"),
                            "added_at": item.get("added_at"),
                            "external_urls": track.get("external_urls"),
                        })
                return [types.TextContent(type="text", text=f"Tracks from Playlist {playlist_id}:\n\n{json.dumps(tracks, indent=2)}")]

            # -----------------------------------------------------------------
            elif name == "get_music_recommendations":
                params: Dict[str, Any] = {}
                if arguments.get("seed_tracks"): params["seed_tracks"] = arguments["seed_tracks"]
                if arguments.get("seed_artists"): params["seed_artists"] = arguments["seed_artists"]
                if arguments.get("seed_genres"): params["seed_genres"] = arguments["seed_genres"]

                if not any(k.startswith("seed_") for k in params.keys()):
                    return [types.TextContent(type="text", text="Error: At least one seed (tracks, artists, or genres) is required for recommendations.")]

                params["limit"] = arguments.get("limit", 20)
                if arguments.get("market"): params["market"] = arguments["market"]

                for attr in ["min_energy","max_energy","min_danceability","max_danceability","min_valence","max_valence","target_tempo"]:
                    if arguments.get(attr) is not None:
                        params[attr] = arguments[attr]

                result = await make_spotify_request("recommendations", params=params, requires_user_auth=True)
                recommendations = []
                for track in result.get("tracks", []):
                    recommendations.append({
                        "id": track.get("id"),
                        "name": track.get("name"),
                        "artists": [a.get("name") for a in track.get("artists", [])],
                        "album": (track.get("album") or {}).get("name"),
                        "popularity": track.get("popularity"),
                        "preview_url": track.get("preview_url"),
                        "external_urls": track.get("external_urls"),
                    })
                return [types.TextContent(type="text", text=f"Music Recommendations:\n\n{json.dumps(recommendations, indent=2)}")]

            # -----------------------------------------------------------------
            elif name == "get_available_genres":
                result = await make_spotify_request("recommendations/available-genre-seeds", requires_user_auth=True)
                return [types.TextContent(type="text", text=f"Available Genre Seeds:\n\n{json.dumps(result.get('genres', []), indent=2)}")]

            # -----------------------------------------------------------------
            elif name == "get_new_releases":
                country = arguments.get("country")
                limit = arguments.get("limit", 20)
                offset = arguments.get("offset", 0)

                params = {"limit": limit, "offset": offset}
                if country:
                    params["country"] = country

                result = await make_spotify_request("browse/new-releases", params=params)
                albums = []
                for album in (result.get("albums") or {}).get("items", []):
                    albums.append({
                        "id": album.get("id"),
                        "name": album.get("name"),
                        "artists": [a.get("name") for a in album.get("artists", [])],
                        "release_date": album.get("release_date"),
                        "total_tracks": album.get("total_tracks"),
                        "external_urls": album.get("external_urls"),
                    })
                return [types.TextContent(type="text", text=f"New Releases:\n\n{json.dumps(albums, indent=2)}")]

            # -----------------------------------------------------------------
            elif name == "get_featured_playlists":
                country = arguments.get("country")
                limit = arguments.get("limit", 20)
                offset = arguments.get("offset", 0)
                timestamp = arguments.get("timestamp")

                params = {"limit": limit, "offset": offset}
                if country: params["country"] = country
                if timestamp: params["timestamp"] = timestamp

                result = await make_spotify_request("browse/featured-playlists", params=params)
                playlists = []
                for playlist in (result.get("playlists") or {}).get("items", []):
                    playlists.append({
                        "id": playlist.get("id"),
                        "name": playlist.get("name"),
                        "description": playlist.get("description", "") or "",
                        "owner": (playlist.get("owner") or {}).get("display_name"),
                        "tracks_total": (playlist.get("tracks") or {}).get("total"),
                        "external_urls": playlist.get("external_urls"),
                    })
                return [types.TextContent(type="text", text=f"Featured Playlists:\n\n{json.dumps(playlists, indent=2)}")]

            # -----------------------------------------------------------------
            elif name == "get_categories":
                country = arguments.get("country")
                locale = arguments.get("locale")
                limit = arguments.get("limit", 20)
                offset = arguments.get("offset", 0)

                params = {"limit": limit, "offset": offset}
                if country: params["country"] = country
                if locale: params["locale"] = locale

                result = await make_spotify_request("browse/categories", params=params)
                categories = []
                for category in (result.get("categories") or {}).get("items", []):
                    categories.append({
                        "id": category.get("id"),
                        "name": category.get("name"),
                        "icons": category.get("icons", []),
                    })
                return [types.TextContent(type="text", text=f"Music Categories:\n\n{json.dumps(categories, indent=2)}")]

            # -----------------------------------------------------------------
            elif name == "get_category_playlists":
                category_id = arguments["category_id"]
                country = arguments.get("country")
                limit = arguments.get("limit", 20)
                offset = arguments.get("offset", 0)

                params = {"limit": limit, "offset": offset}
                if country: params["country"] = country

                result = await make_spotify_request(f"browse/categories/{category_id}/playlists", params=params)
                playlists = []
                for playlist in (result.get("playlists") or {}).get("items", []):
                    playlists.append({
                        "id": playlist.get("id"),
                        "name": playlist.get("name"),
                        "description": playlist.get("description", "") or "",
                        "owner": (playlist.get("owner") or {}).get("display_name"),
                        "tracks_total": (playlist.get("tracks") or {}).get("total"),
                        "external_urls": playlist.get("external_urls"),
                    })
                return [types.TextContent(type="text", text=f"Playlists in Category '{category_id}':\n\n{json.dumps(playlists, indent=2)}")]

            # -----------------------------------------------------------------
            else:
                return [types.TextContent(type="text", text=f"Unknown tool: {name}")]

        except SpotifyAuthError as e:
            logger.error(f"Authentication error in {name}: {e}")
            return [types.TextContent(
                type="text",
                text=f"🔐 Authentication Error: {e}\n\nIf this persists, try running 'python refresh_tokens.py' to get fresh tokens."
            )]
        except SpotifyAPIError as e:
            logger.error(f"Spotify API error in {name}: {e}")
            return [types.TextContent(
                type="text",
                text=f"🎵 Spotify API Error: {e}"
            )]
        except Exception as e:
            logger.exception(f"Unexpected error in {name}: {e}")
            return [types.TextContent(
                type="text",
                text=f"❌ An unexpected error occurred: {e}"
            )]

    return server

# -----------------------------------------------------------------------------
# Startup validation and health checks
# -----------------------------------------------------------------------------

async def validate_spotify_setup():
    """Validate Spotify setup and display authentication status"""
    
    print("🔍 Validating Spotify setup...")
    
    # Check environment variables
    if not SPOTIFY_CLIENT_ID or not SPOTIFY_CLIENT_SECRET:
        print("❌ Missing required Spotify credentials")
        print("   Please set SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET")
        return False
    
    print(f"✅ Client credentials found (ID: {SPOTIFY_CLIENT_ID[:8]}...)")
    
    # Test client credentials flow
    try:
        await get_client_credentials_token()
        print("✅ Client credentials authentication working")
    except Exception as e:
        print(f"❌ Client credentials authentication failed: {e}")
        return False
    
    # Test user authentication
    try:
        await get_user_access_token_safe()
        print("✅ User authentication available")
        print("   All features including audio-features, recommendations, and genres will work")
    except SpotifyAuthError:
        print("⚠️  User authentication unavailable")
        print("   Basic features (search, track info, artist info) will work")
        print("   For full features, run: python refresh_tokens.py")
    
    return True

# -----------------------------------------------------------------------------
# App wiring (HTTP SSE and stdio)
# -----------------------------------------------------------------------------

async def run_stdio_server(server: Server) -> None:
    import mcp.server.stdio
    async with mcp.server.stdio.stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())

@contextlib.asynccontextmanager
async def create_session_manager() -> AsyncIterator[StreamableHTTPSessionManager]:
    async with StreamableHTTPSessionManager() as session_manager:
        yield session_manager

async def handle_sse(scope: Scope, receive: Receive, send: Send) -> None:
    async with create_session_manager() as session_manager:
        sse_transport = SseServerTransport("/messages", session_manager)
        await sse_transport(scope, receive, send, _SERVER_SINGLETON)

async def handle_root(request) -> Response:
    return Response(
        "🎵 Spotify MCP Server is running!\n\n"
        "This server provides tools to interact with Spotify's music streaming platform.\n\n"
        "Available endpoints:\n"
        "• GET /health - Health check\n"
        "• GET /tokens - Token status\n"
        "• GET /oauth/login - Start OAuth flow\n"
        "• GET /sse/messages - MCP communication endpoint",
        media_type="text/plain"
    )

async def handle_health(request):
    """Health check endpoint"""
    try:
        # Quick test of Spotify API
        await get_client_credentials_token()
        
        # Check user auth
        user_auth_available = False
        try:
            await get_user_access_token_safe()
            user_auth_available = True
        except:
            pass
        
        return Response(
            json.dumps({
                "status": "healthy", 
                "timestamp": time.time(),
                "app_auth": True,
                "user_auth": user_auth_available
            }),
            media_type="application/json"
        )
    except Exception as e:
        return Response(
            json.dumps({"status": "unhealthy", "error": str(e)}),
            media_type="application/json",
            status_code=503
        )

async def handle_token_status(request):
    """Show current token status"""
    
    html_parts = ["<h2>🎵 Spotify MCP Server - Token Status</h2>"]
    
    # Check app token
    try:
        await get_client_credentials_token()
        html_parts.append("<p>✅ <strong>App Authentication:</strong> Working</p>")
    except Exception as e:
        html_parts.append(f"<p>❌ <strong>App Authentication:</strong> Failed - {e}</p>")
    
    # Check user token
    try:
        await get_user_access_token_safe()
        html_parts.append("<p>✅ <strong>User Authentication:</strong> Working</p>")
        html_parts.append("<p>All features available including audio-features, recommendations, and genres.</p>")
    except Exception as e:
        html_parts.append(f"<p>⚠️ <strong>User Authentication:</strong> Unavailable - {str(e)[:100]}...</p>")
        html_parts.append("<p>Basic features work. For full features, <a href='/oauth/login'>click here to re-authorize</a>.</p>")
    
    # Token refresh instructions
    html_parts.append("<hr>")
    html_parts.append("<h3>🔧 Need to refresh tokens?</h3>")
    html_parts.append("<ol>")
    html_parts.append("<li>Run: <code>python refresh_tokens.py</code></li>")
    html_parts.append("<li>Follow the authorization steps</li>")  
    html_parts.append("<li>Update your .env file with new tokens</li>")
    html_parts.append("<li>Restart the server</li>")
    html_parts.append("</ol>")
    
    return HTMLResponse("".join(html_parts))

async def oauth_login(request):
    if not SPOTIFY_CLIENT_ID:
        return PlainTextResponse("Missing SPOTIFY_CLIENT_ID", status_code=500)

    state = _b64url(secrets.token_bytes(24))
    verifier = _new_code_verifier()
    challenge = _code_challenge(verifier)
    _PKCE_STORE[state] = {"verifier": verifier, "ts": str(time.time())}

    scope = "user-read-private user-read-email playlist-read-private playlist-read-collaborative user-library-read"
    params = {
        "response_type": "code",
        "client_id": SPOTIFY_CLIENT_ID,
        "redirect_uri": SPOTIFY_REDIRECT_URI,
        "scope": scope,
        "code_challenge_method": "S256",
        "code_challenge": challenge,
        "state": state,
    }
    url = "https://accounts.spotify.com/authorize?" + urlencode(params)
    return RedirectResponse(url)

async def oauth_callback(request):
    q = request.query_params
    code = q.get("code")
    state = q.get("state")
    error = q.get("error")
    if error:
        return PlainTextResponse(f"OAuth error: {error}", status_code=400)
    if not code or not state or state not in _PKCE_STORE:
        return PlainTextResponse("Invalid OAuth response/state", status_code=400)

    verifier = _PKCE_STORE.pop(state)["verifier"]

    session = await get_http_session()
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": SPOTIFY_REDIRECT_URI,
        "client_id": SPOTIFY_CLIENT_ID,
        "code_verifier": verifier,
    }
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    async with session.post(SPOTIFY_AUTH_URL, data=data, headers=headers) as resp:
        body = await resp.json()
        if resp.status != 200:
            return PlainTextResponse(f"Token exchange failed: {resp.status} | {body}", status_code=resp.status)

    access = body.get("access_token")
    refresh = body.get("refresh_token")
    expires_in = body.get("expires_in", 3600)

    _user_token_cache["value"] = access
    _user_token_cache["exp"] = time.time() + int(expires_in)

    if refresh:
        os.makedirs(".secrets", exist_ok=True)
        with open(".secrets/spotify_tokens.json", "w") as f:
            json.dump({"refresh_token": refresh}, f, indent=2)
        hint = f"<code>SPOTIFY_REFRESH_TOKEN={refresh}</code>"
    else:
        hint = "<i>(no refresh_token returned; check scopes)</i>"

    html = f"""
    <h2>Spotify OAuth complete ✅</h2>
    <p>Access token cached in memory. Refresh token saved to <code>.secrets/spotify_tokens.json</code>.</p>
    <p>Add this to your .env for future runs: {hint}</p>
    <p>You can close this window and check <a href="/tokens">token status</a>.</p>
    """
    return HTMLResponse(html)

_SERVER_SINGLETON: Server = build_server()
app = Starlette(routes=[
    Route("/", handle_root),
    Route("/health", handle_health),
    Route("/tokens", handle_token_status),
    Mount("/sse", handle_sse),
    Route("/oauth/login", oauth_login),
    Route("/oauth/callback", oauth_callback),
])

@app.on_event("shutdown")
async def _on_shutdown():
    await close_http_session()

# -----------------------------------------------------------------------------
# Enhanced CLI with validation
# -----------------------------------------------------------------------------

@click.command()
@click.option("--port", default=SPOTIFY_MCP_SERVER_PORT, help="Port to listen on for HTTP")
@click.option("--log-level", default="INFO", help="Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)")
@click.option("--stdio", is_flag=True, help="Run MCP server over stdio instead of HTTP")
def main(port: int, log_level: str, stdio: bool):
    """Spotify MCP Server - Interact with Spotify's music streaming platform."""
    logging.basicConfig(
        level=getattr(logging, log_level.upper()), 
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )

    print("🎵 Spotify MCP Server")
    print("=" * 50)
    
    # Validate setup before starting
    setup_valid = asyncio.run(validate_spotify_setup())
    if not setup_valid:
        print("\n❌ Setup validation failed. Please fix the issues above.")
        sys.exit(1)
    
    print("\n🚀 Starting server...")

    if stdio:
        logger.info("Starting Spotify MCP Server (stdio mode)")
        asyncio.run(run_stdio_server(_SERVER_SINGLETON))
        return

    logger.info(f"Spotify MCP Server starting on http://localhost:{port}")
    print(f"\n📡 Server endpoints:")
    print(f"   • Status:  http://localhost:{port}/")
    print(f"   • Health:  http://localhost:{port}/health")
    print(f"   • Tokens:  http://localhost:{port}/tokens")
    print(f"   • MCP:     http://localhost:{port}/sse/messages")
    print(f"   • OAuth:   http://localhost:{port}/oauth/login")
    
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=port, log_level=log_level.lower())

if __name__ == "__main__":
    main()