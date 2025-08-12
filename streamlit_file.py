#!/usr/bin/env python3
"""
Spotify MCP Server - Standalone Streamlit Web Interface
Self-contained web app that connects to your MCP server via HTTP
"""

import streamlit as st
import asyncio
import aiohttp
import json
import os
from dotenv import load_dotenv
import base64
import time
from typing import Dict, List, Any

# Load environment variables
load_dotenv()

# Page configuration
st.set_page_config(
    page_title="🎵 Spotify Music Discovery",
    page_icon="🎵",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Custom CSS for better styling
st.markdown("""
<style>
    .main-header {
        font-size: 3rem;
        color: #1DB954;
        text-align: center;
        margin-bottom: 2rem;
    }
    .feature-card {
        background: linear-gradient(135deg, #1DB954, #1ed760);
        padding: 1rem;
        border-radius: 10px;
        color: white;
        margin: 0.5rem 0;
    }
    .track-item {
        background: #f8f9fa;
        padding: 0.8rem;
        margin: 0.3rem 0;
        border-radius: 6px;
        border-left: 3px solid #1DB954;
    }
    .artist-card {
        background: white;
        padding: 1rem;
        border-radius: 10px;
        box-shadow: 0 2px 4px rgba(0,0,0,0.1);
        margin: 0.5rem 0;
    }
    .album-card {
        background: white;
        padding: 1rem;
        border-radius: 10px;
        box-shadow: 0 2px 4px rgba(0,0,0,0.1);
        margin: 0.5rem;
        text-align: center;
    }
</style>
""", unsafe_allow_html=True)

# Initialize session state
if 'search_results' not in st.session_state:
    st.session_state.search_results = {}

# Spotify API Configuration
SPOTIFY_CLIENT_ID = os.getenv("SPOTIFY_CLIENT_ID")
SPOTIFY_CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET")
SPOTIFY_API_BASE = "https://api.spotify.com/v1"
SPOTIFY_AUTH_URL = "https://accounts.spotify.com/api/token"

# Token cache
_token_cache = {"value": None, "exp": 0}

# Async wrapper for Streamlit
def run_async(coro):
    """Run async function in Streamlit"""
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    
    return loop.run_until_complete(coro)

# Direct Spotify API functions
async def get_client_credentials_token():
    """Get access token using client credentials flow"""
    global _token_cache
    
    if _token_cache["value"] and time.time() < _token_cache["exp"] - 60:
        return _token_cache["value"]

    if not SPOTIFY_CLIENT_ID or not SPOTIFY_CLIENT_SECRET:
        raise Exception("Missing SPOTIFY_CLIENT_ID or SPOTIFY_CLIENT_SECRET")

    auth_string = f"{SPOTIFY_CLIENT_ID}:{SPOTIFY_CLIENT_SECRET}"
    auth_b64 = base64.b64encode(auth_string.encode("ascii")).decode("ascii")

    headers = {
        "Authorization": f"Basic {auth_b64}", 
        "Content-Type": "application/x-www-form-urlencoded"
    }
    data = {"grant_type": "client_credentials"}

    async with aiohttp.ClientSession() as session:
        async with session.post(SPOTIFY_AUTH_URL, headers=headers, data=data) as response:
            if response.status != 200:
                text = await response.text()
                raise Exception(f"Failed to get access token: {response.status} - {text}")
            
            token_data = await response.json()
            _token_cache["value"] = token_data["access_token"]
            _token_cache["exp"] = time.time() + int(token_data.get("expires_in", 3600))
            return _token_cache["value"]

async def make_spotify_request(endpoint: str, params: Dict = None):
    """Make a request to the Spotify API"""
    token = await get_client_credentials_token()
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }
    
    url = f"{SPOTIFY_API_BASE}/{endpoint.lstrip('/')}"
    
    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers=headers, params=params) as response:
            if response.status == 200:
                return await response.json()
            else:
                text = await response.text()
                raise Exception(f"API error {response.status}: {text}")

# Helper function to format duration
def format_duration(duration_ms):
    minutes = duration_ms // 60000
    seconds = (duration_ms // 1000) % 60
    return f"{minutes}:{seconds:02d}"

# Header
st.markdown('<h1 class="main-header">🎵 Spotify Music Discovery Platform</h1>', unsafe_allow_html=True)

# Check if credentials are configured
if not SPOTIFY_CLIENT_ID or not SPOTIFY_CLIENT_SECRET:
    st.error("🔧 Please configure your Spotify API credentials in the .env file")
    st.code("""
# Add these to your .env file:
SPOTIFY_CLIENT_ID=your_client_id_here
SPOTIFY_CLIENT_SECRET=your_client_secret_here
    """)
    st.stop()

# Test connection
if 'connection_tested' not in st.session_state:
    with st.spinner("Testing Spotify API connection..."):
        try:
            run_async(get_client_credentials_token())
            st.session_state.connection_tested = True
        except Exception as e:
            st.error(f"❌ Failed to connect to Spotify API: {e}")
            st.stop()

# Sidebar
st.sidebar.markdown("## 🎛️ Control Panel")
st.sidebar.markdown("---")

# Connection Status
st.sidebar.success("✅ Connected to Spotify API")

# Feature Selection
selected_feature = st.sidebar.selectbox(
    "🎯 Select Feature",
    [
        "🔍 Music Search",
        "🎤 Artist Explorer", 
        "💿 Album Discovery",
        "🎯 Playlist Explorer",
        "🎨 Browse Categories"
    ]
)

# Market Selection
market = st.sidebar.selectbox(
    "🌍 Market",
    ["US", "GB", "CA", "AU", "DE", "FR", "ES", "IT", "NL", "SE"],
    index=0
)

st.sidebar.markdown("---")
st.sidebar.markdown("### 🎵 About")
st.sidebar.markdown("Direct Spotify Web API Integration")

# Main Content Area
if selected_feature == "🔍 Music Search":
    st.header("🔍 Music Search")
    
    col1, col2 = st.columns([3, 1])
    
    with col1:
        search_query = st.text_input("🎵 Search for music", placeholder="Enter artist, song, or album name...")
    
    with col2:
        search_type = st.selectbox("Type", ["track", "artist", "album", "playlist"])
    
    if search_query:
        with st.spinner(f"Searching for {search_type}s..."):
            try:
                search_result = run_async(make_spotify_request("search", {
                    "q": search_query,
                    "type": search_type,
                    "limit": 10,
                    "market": market
                }))
                
                items = search_result.get(f"{search_type}s", {}).get("items", [])
                
                if items:
                    st.success(f"Found {len(items)} {search_type}(s)")
                    
                    if search_type == "track":
                        for track in items:
                            # Use Streamlit components instead of HTML
                            with st.container():
                                col1, col2, col3 = st.columns([3, 2, 1])
                                
                                with col1:
                                    st.markdown(f"**🎵 {track['name']}**")
                                    st.caption(f"by {track['artists'][0]['name']}")
                                
                                with col2:
                                    album_name = track.get('album', {}).get('name', 'N/A')
                                    st.caption(f"Album: {album_name}")
                                    st.caption(f"Duration: {format_duration(track['duration_ms'])}")
                                
                                with col3:
                                    st.metric("Popularity", f"{track['popularity']}/100")
                                
                                if track.get('preview_url'):
                                    st.audio(track['preview_url'])
                                
                                st.divider()
                    
                    elif search_type == "artist":
                        for artist in items:
                            genres = ", ".join(artist.get('genres', [])[:3]) or "N/A"
                            followers = artist.get('followers', {}).get('total', 0)
                            
                            with st.container():
                                col1, col2 = st.columns([2, 1])
                                
                                with col1:
                                    st.markdown(f"**🎤 {artist['name']}**")
                                    st.caption(f"Genres: {genres}")
                                    st.caption(f"Followers: {followers:,}")
                                
                                with col2:
                                    st.metric("Popularity", f"{artist['popularity']}/100")
                                
                                st.divider()
                    
                    elif search_type == "album":
                        for album in items:
                            with st.container():
                                col1, col2 = st.columns([1, 2])
                                
                                with col1:
                                    if album.get('images') and len(album['images']) > 0:
                                        st.image(album['images'][0]['url'], width=150)
                                
                                with col2:
                                    st.markdown(f"**💿 {album['name']}**")
                                    st.caption(f"by {album['artists'][0]['name']}")
                                    st.caption(f"Released: {album['release_date']}")
                                    st.caption(f"Tracks: {album['total_tracks']}")
                                
                                st.divider()
                else:
                    st.warning("No results found")
                    
            except Exception as e:
                st.error(f"Search failed: {e}")

elif selected_feature == "🎤 Artist Explorer":
    st.header("🎤 Artist Explorer")
    
    artist_name = st.text_input("🎤 Enter artist name", placeholder="e.g., The Beatles, Taylor Swift...")
    
    if artist_name:
        with st.spinner("Loading artist information..."):
            try:
                # Search for artist
                search_result = run_async(make_spotify_request("search", {
                    "q": artist_name,
                    "type": "artist",
                    "limit": 1
                }))
                
                artists = search_result.get("artists", {}).get("items", [])
                
                if artists:
                    artist = artists[0]
                    artist_id = artist["id"]
                    
                    # Get top tracks
                    top_tracks = run_async(make_spotify_request(f"artists/{artist_id}/top-tracks", {
                        "market": market
                    }))
                    
                    # Get albums
                    albums = run_async(make_spotify_request(f"artists/{artist_id}/albums", {
                        "limit": 6, 
                        "market": market
                    }))
                    
                    # Artist Header
                    col1, col2 = st.columns([1, 2])
                    
                    with col1:
                        if artist.get('images'):
                            st.image(artist['images'][0]['url'], width=250)
                    
                    with col2:
                        st.markdown(f"# {artist['name']}")
                        
                        # Metrics
                        col2_1, col2_2 = st.columns(2)
                        with col2_1:
                            st.metric("Popularity", f"{artist['popularity']}/100")
                        with col2_2:
                            followers = artist.get('followers', {}).get('total', 0)
                            st.metric("Followers", f"{followers:,}")
                    
                    st.markdown("---")
                    
                    # Top Tracks
                    if top_tracks:
                        st.subheader("🎵 Top Tracks")
                        tracks = top_tracks.get("tracks", [])[:8]
                        
                        for i, track in enumerate(tracks, 1):
                            col1, col2, col3 = st.columns([1, 3, 1])
                            
                            with col1:
                                st.markdown(f"**#{i}**")
                            
                            with col2:
                                st.markdown(f"**{track['name']}**")
                                st.caption(f"Album: {track['album']['name']}")
                            
                            with col3:
                                st.markdown(f"**{track['popularity']}**/100")
                                st.caption(format_duration(track['duration_ms']))
                    
                    # Albums
                    if albums:
                        st.subheader("💿 Recent Albums")
                        album_items = albums.get("items", [])[:6]
                        
                        cols = st.columns(3)
                        for i, album in enumerate(album_items):
                            with cols[i % 3]:
                                if album.get('images') and len(album['images']) > 0:
                                    st.image(album['images'][0]['url'], width=150)
                                st.markdown(f"**{album['name']}**")
                                st.caption(f"{album['release_date'][:4]} • {album['total_tracks']} tracks")
                else:
                    st.warning("Artist not found")
                    
            except Exception as e:
                st.error(f"Error loading artist: {e}")

elif selected_feature == "💿 Album Discovery":
    st.header("💿 Album Discovery")
    
    album_query = st.text_input("💿 Search for albums", placeholder="e.g., Abbey Road, Thriller...")
    
    if album_query:
        with st.spinner("Searching for albums..."):
            try:
                albums = run_async(make_spotify_request("search", {
                    "q": album_query,
                    "type": "album",
                    "limit": 9,
                    "market": market
                })).get("albums", {}).get("items", [])
                
                if albums:
                    st.success(f"Found {len(albums)} albums")
                    
                    # Display albums in grid
                    cols = st.columns(3)
                    for i, album in enumerate(albums):
                        with cols[i % 3]:
                            if album.get('images') and len(album['images']) > 0:
                                st.image(album['images'][0]['url'], width=150)
                            
                            st.markdown(f"**{album['name']}**")
                            st.caption(f"by {album['artists'][0]['name']}")
                            st.caption(f"Released: {album['release_date']}")
                            st.caption(f"Tracks: {album['total_tracks']}")
                else:
                    st.warning("No albums found")
                    
            except Exception as e:
                st.error(f"Album search failed: {e}")

elif selected_feature == "🎯 Playlist Explorer":
    st.header("🎯 Playlist Explorer")
    
    playlist_query = st.text_input("🎵 Search for playlists", placeholder="e.g., workout, chill, pop hits...")
    
    if playlist_query:
        with st.spinner("Searching for playlists..."):
            try:
                search_result = run_async(make_spotify_request("search", {
                    "q": playlist_query,
                    "type": "playlist",
                    "limit": 12,
                    "market": market
                }))
                
                playlists = search_result.get("playlists", {}).get("items", []) if search_result else []
                
                if playlists:
                    st.success(f"Found {len(playlists)} playlists")
                    
                    # Display playlists in grid
                    cols = st.columns(3)
                    for i, playlist in enumerate(playlists):
                        if not playlist:  # Skip if playlist is None
                            continue
                            
                        with cols[i % 3]:
                            # Playlist image
                            images = playlist.get('images', [])
                            if images and len(images) > 0 and images[0]:
                                st.image(images[0].get('url', ''), width=150)
                            
                            # Playlist info
                            playlist_name = playlist.get('name', 'Unknown Playlist')
                            st.markdown(f"**🎵 {playlist_name}**")
                            
                            # Owner info
                            owner = playlist.get('owner', {})
                            if owner:
                                owner_name = owner.get('display_name') or owner.get('id', 'Unknown')
                                st.caption(f"by {owner_name}")
                            
                            # Track count
                            tracks_info = playlist.get('tracks', {})
                            track_count = tracks_info.get('total', 0) if tracks_info else 0
                            st.caption(f"🎧 {track_count:,} tracks")
                            
                            # Description (if available)
                            description = playlist.get('description', '')
                            if description and description.strip():
                                # Remove HTML tags and truncate
                                import re
                                clean_desc = re.sub('<[^<]+?>', '', description)
                                short_desc = clean_desc[:60] + "..." if len(clean_desc) > 60 else clean_desc
                                if short_desc.strip():
                                    st.caption(f"📝 {short_desc}")
                            
                            # Show playlist details button
                            playlist_id = playlist.get('id')
                            if playlist_id and st.button(f"View Tracks", key=f"playlist_{i}"):
                                # Get playlist tracks
                                with st.spinner("Loading tracks..."):
                                    try:
                                        tracks_data = run_async(make_spotify_request(f"playlists/{playlist_id}/tracks", {
                                            "limit": 10,
                                            "market": market
                                        }))
                                        
                                        tracks = tracks_data.get("items", []) if tracks_data else []
                                        if tracks:
                                            st.subheader(f"🎵 Tracks from '{playlist_name}'")
                                            
                                            for j, item in enumerate(tracks[:10], 1):
                                                if not item:
                                                    continue
                                                    
                                                track = item.get('track')
                                                if track and track.get('type') == 'track':
                                                    col1, col2 = st.columns([1, 3])
                                                    
                                                    with col1:
                                                        st.caption(f"#{j}")
                                                    
                                                    with col2:
                                                        track_name = track.get('name', 'Unknown Track')
                                                        st.markdown(f"**{track_name}**")
                                                        
                                                        artists = track.get('artists', [])
                                                        if artists:
                                                            artist_names = ", ".join([a.get('name', 'Unknown') for a in artists if a])
                                                            st.caption(f"by {artist_names}")
                                            
                                            if len(tracks) == 10:
                                                st.caption("... and more tracks")
                                        else:
                                            st.info("No tracks found in this playlist")
                                            
                                    except Exception as e:
                                        st.error(f"Could not load tracks: {e}")
                            
                            st.divider()
                else:
                    st.warning("No playlists found")
                    
            except Exception as e:
                st.error(f"Playlist search failed: {e}")
                # Add debug info
                st.caption(f"Debug: Search query was '{playlist_query}' in market '{market}'")
    else:
        # Show some example searches
        st.info("💡 **Try searching for:**")
        col1, col2, col3 = st.columns(3)
        
        with col1:
            st.markdown("**🏃‍♀️ Workout**")
            st.caption("• workout")
            st.caption("• gym") 
            st.caption("• running")
        
        with col2:
            st.markdown("**😌 Chill**")
            st.caption("• chill")
            st.caption("• study")
            st.caption("• coffee")
        
        with col3:
            st.markdown("**🎉 Party**")
            st.caption("• party")
            st.caption("• dance")
            st.caption("• hits")

elif selected_feature == "🎨 Browse Categories":
    st.header("🎨 Browse Categories")
    
    with st.spinner("Loading categories..."):
        try:
            categories = run_async(make_spotify_request("browse/categories", {
                "country": market,
                "limit": 20
            })).get("categories", {}).get("items", [])
            
            if categories:
                st.success(f"Found {len(categories)} categories")
                
                # Display categories
                cols = st.columns(4)
                for i, category in enumerate(categories):
                    with cols[i % 4]:
                        if category.get('icons') and len(category['icons']) > 0:
                            st.image(category['icons'][0]['url'], width=100)
                        st.markdown(f"**{category['name']}**")
            else:
                st.warning("No categories found")
                
        except Exception as e:
            st.error(f"Failed to load categories: {e}")

# Footer
st.markdown("---")
st.markdown("### 🎵 Spotify Music Discovery Platform")
st.markdown("Built with Streamlit • Direct Spotify Web API Integration")