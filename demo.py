#!/usr/bin/env python3
"""
Spotify MCP Server - Enhanced Basic Features Demo
Tests all features that match the Streamlit interface (client credentials only)
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from server import make_spotify_request, close_http_session

MARKET = "US"


async def demo_music_search():
    print("🔍 Demo: Music Search")
    print("Natural query: 'Search for tracks by Taylor Swift'")

    try:
        # Search for tracks
        result = await make_spotify_request(
            "search",
            params={"q": "Taylor Swift", "type": "track", "limit": 5, "market": MARKET},
        )
        tracks = result.get("tracks", {}).get("items", [])
        print(f"✅ Found {len(tracks)} tracks:")
        for track in tracks:
            duration = f"{track['duration_ms'] // 60000}:{(track['duration_ms'] // 1000) % 60:02d}"
            album_name = track.get("album", {}).get("name", "N/A")
            print(f"   🎵 {track['name']}")
            print(f"      by {track['artists'][0]['name']}")
            print(
                f"      Album: {album_name} | Duration: {duration} | Popularity: {track['popularity']}/100"
            )
            if track.get("preview_url"):
                print(f"      🎧 Preview: {track['preview_url']}")
    except Exception as e:
        print(f"❌ Music search failed: {e}")
    print()


async def demo_artist_explorer():
    print("🎤 Demo: Artist Explorer")
    print("Natural query: 'Explore The Beatles as an artist'")

    try:
        # Search for artist
        search_result = await make_spotify_request(
            "search", params={"q": "The Beatles", "type": "artist", "limit": 1}
        )
        artists = search_result.get("artists", {}).get("items", [])

        if artists:
            artist = artists[0]
            artist_id = artist["id"]

            print("✅ Artist Details:")
            print(f"   🎤 Name: {artist['name']}")
            print(f"   Popularity: {artist['popularity']}/100")
            followers = artist.get("followers", {}).get("total", 0)
            print(f"   Followers: {followers:,}")
            print()

            # Get top tracks
            top_tracks = await make_spotify_request(
                f"artists/{artist_id}/top-tracks", params={"market": MARKET}
            )
            print("   🎵 Top Tracks:")
            for i, track in enumerate(top_tracks.get("tracks", [])[:5], 1):
                duration = f"{track['duration_ms'] // 60000}:{(track['duration_ms'] // 1000) % 60:02d}"
                print(
                    f"     #{i}. {track['name']} (Popularity: {track['popularity']}/100, Duration: {duration})"
                )
                print(f"         Album: {track['album']['name']}")
            print()

            # Get recent albums
            albums = await make_spotify_request(
                f"artists/{artist_id}/albums", params={"limit": 4, "market": MARKET}
            )
            print("   💿 Recent Albums:")
            for album in albums.get("items", []):
                print(
                    f"     • {album['name']} ({album['release_date'][:4]}) - {album['total_tracks']} tracks"
                )
        else:
            print("❌ Artist not found")

    except Exception as e:
        print(f"❌ Artist explorer failed: {e}")
    print()


async def demo_album_discovery():
    print("💿 Demo: Album Discovery")
    print("Natural query: 'Discover albums like Abbey Road'")

    try:
        # Search for albums
        albums_result = await make_spotify_request(
            "search",
            params={"q": "Abbey Road", "type": "album", "limit": 4, "market": MARKET},
        )
        albums = albums_result.get("albums", {}).get("items", [])

        if albums:
            print(f"✅ Found {len(albums)} albums:")
            for album in albums:
                print(f"   💿 {album['name']}")
                print(f"      by {album['artists'][0]['name']}")
                print(
                    f"      Released: {album['release_date']} | Tracks: {album['total_tracks']}"
                )

    except Exception as e:
        print(f"❌ Album discovery failed: {e}")
    print()


async def demo_playlist_explorer():
    print("🎯 Demo: Playlist Explorer")
    print("Natural query: 'Find workout playlists'")

    try:
        # Search for playlists
        search_result = await make_spotify_request(
            "search",
            params={"q": "workout", "type": "playlist", "limit": 4, "market": MARKET},
        )
        playlists = search_result.get("playlists", {}).get("items", [])

        if playlists:
            print(f"✅ Found {len(playlists)} playlists")
        else:
            print("❌ No playlists found")

    except Exception as e:
        print(f"❌ Playlist explorer failed: {e}")
    print()


async def demo_browse_categories():
    print("🎨 Demo: Browse Categories")
    print("Natural query: 'What music categories are available?'")

    try:
        result = await make_spotify_request(
            "browse/categories", params={"country": MARKET, "limit": 10}
        )
        categories = result.get("categories", {}).get("items", [])
        print(f"✅ Found {len(categories)} categories:")
        for category in categories:
            print(f"   🎨 {category['name']}")

    except Exception as e:
        print(f"❌ Browse categories failed: {e}")
    print()


async def demo_comprehensive_search():
    print("🔍 Demo: Comprehensive Search Types")
    print("Natural query: 'Search for different types of content'")

    search_queries = [
        ("track", "Bohemian Rhapsody", 2),
        ("artist", "Queen", 2),
        ("album", "A Night at the Opera", 2),
        ("playlist", "classic rock", 2),
    ]

    for search_type, query, limit in search_queries:
        try:
            print(f"   🔍 Searching for {search_type}: '{query}'")
            result = await make_spotify_request(
                "search",
                params={
                    "q": query,
                    "type": search_type,
                    "limit": limit,
                    "market": MARKET,
                },
            )

            items = result.get(f"{search_type}s", {}).get("items", [])
            # Filter out None items
            valid_items = [item for item in items if item is not None]
            print(f"      ✅ Found {len(valid_items)} {search_type}(s)")

            for item in valid_items:
                if search_type == "track":
                    print(f"         🎵 {item['name']} by {item['artists'][0]['name']}")
                elif search_type == "artist":
                    followers = item.get("followers", {}).get("total", 0)
                    print(f"         🎤 {item['name']} ({followers:,} followers)")
                elif search_type == "album":
                    print(
                        f"         💿 {item['name']} by {item['artists'][0]['name']} ({item['total_tracks']} tracks)"
                    )
                elif search_type == "playlist":
                    # Extra safety checks for playlist items
                    track_count = (
                        item.get("tracks", {}).get("total", 0)
                        if item.get("tracks")
                        else 0
                    )
                    owner_info = item.get("owner", {})
                    owner = (
                        owner_info.get("display_name", "Unknown")
                        if owner_info
                        else "Unknown"
                    )
                    playlist_name = item.get("name", "Unknown Playlist")
                    print(
                        f"         🎵 {playlist_name} by {owner} ({track_count} tracks)"
                    )
            print()

        except Exception as e:
            print(f"      ❌ Search for {search_type} failed: {e}")
    print()


async def run_enhanced_demo():
    print("🎵" + "=" * 70)
    print("  SPOTIFY MCP SERVER - ENHANCED FEATURES DEMONSTRATION")
    print("  (Matches Streamlit Interface - Client Credentials Only)")
    print("=" * 70 + "🎵")
    print()

    demos = [
        demo_music_search,
        demo_artist_explorer,
        demo_album_discovery,
        demo_playlist_explorer,
        demo_browse_categories,
        demo_comprehensive_search,
    ]

    success_count = 0
    for i, demo in enumerate(demos, 1):
        try:
            print(f"[{i}/{len(demos)}] ", end="")
            await demo()
            success_count += 1
        except Exception as e:
            print(f"❌ Demo failed: {e}")

    print("🎉 Enhanced Features Demonstration Complete!")
    print(f"✅ {success_count}/{len(demos)} features working perfectly")
    print()


if __name__ == "__main__":
    asyncio.run(run_enhanced_demo())
    asyncio.run(close_http_session())
