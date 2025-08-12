# Spotify MCP Server

A comprehensive Model Context Protocol (MCP) server that provides seamless integration with the Spotify Web API. This project includes a powerful MCP server with comprehensive music data access and a beautiful Streamlit web interface for music discovery and exploration.

## Features

### Core MCP Server Capabilities
- **🔍 Music Search**: Search for tracks, artists, albums, and playlists with detailed metadata
- **🎤 Artist Explorer**: Get comprehensive artist information, top tracks, albums, and related data
- **💿 Album Discovery**: Explore albums with complete track listings and metadata
- **🎯 Playlist Management**: Access and analyze playlists with full track information
- **🎨 Browse Categories**: Explore Spotify's music categories and genre-based playlists
- **🆕 New Releases**: Discover latest albums and featured content
- **🔐 Client Credentials Authentication**: Secure app-level authentication for public data access

### Advanced Server Features
- **Token Management**: Automatic token refresh and caching system
- **Error Handling**: Comprehensive error handling with retry logic and rate limit management
- **Health Monitoring**: Built-in health checks and status endpoints (`/health`, `/tokens`)
- **Market Support**: Geographic market selection for content availability
- **Detailed Logging**: Comprehensive logging and debugging capabilities

### Streamlit Web Interface
- **Interactive Music Discovery**: Beautiful, responsive web interface
- **Real-time Search**: Instant search results with rich metadata and previews
- **Visual Layout**: Album artwork, artist images, and modern UI design
- **Audio Previews**: Listen to track previews directly in the browser
- **Multi-market Support**: Content discovery across different geographic regions
- **Feature-rich Navigation**: Easy switching between different discovery modes

## MCP Tools Available

The server provides **13 comprehensive tools** using client credentials authentication:

1. **search_music** - Search tracks, artists, albums, playlists
2. **get_track_details** - Detailed track information and metadata
3. **get_artist_details** - Complete artist profiles
4. **get_artist_top_tracks** - Artist's most popular tracks
5. **get_artist_albums** - Artist discography
6. **get_album_details** - Complete album information
7. **get_album_tracks** - All tracks from an album
8. **get_playlist_details** - Playlist metadata and information
9. **get_playlist_tracks** - All tracks from a playlist
10. **get_new_releases** - Latest album releases
11. **get_featured_playlists** - Spotify's featured playlists
12. **get_categories** - Browse music categories
13. **get_category_playlists** - Playlists from specific categories

## 🚀 Quick Start

### Prerequisites
- Python 3.8 or higher
- Spotify Developer Account
- Spotify App with Client ID and Client Secret

### Installation

```bash
# Clone the repository
git clone https://github.com/Rchintalapati0111/mcp_server_spotify.git
cd mcp_server_spotify

# Create virtual environment
python -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt
```

### Configuration

Create a `.env` file in the project root:

```env
# Required: Spotify API Credentials
SPOTIFY_CLIENT_ID=your_client_id_here
SPOTIFY_CLIENT_SECRET=your_client_secret_here

# Server configuration
SPOTIFY_MCP_SERVER_PORT=5050
```

### Start the Server

```bash
# Activate virtual environment
source .venv/bin/activate

# Start the server
python server.py --port 5050
```

### Verify Server Health

```bash
# Check server health
curl http://localhost:5050/health
```

## Usage Examples

### Option A: Run Demo Script
```bash
python demo.py
```

### Option B: Streamlit Web Interface
```bash
streamlit run streamlit_file.py
```

### Option C: Use as MCP Server
Connect your MCP client to: `http://localhost:5050/sse/messages`

## 📁 Project Structure

```
mcp_server_spotify/
├── server.py                 # Main MCP server
├── demo.py                   # Demo script
├── streamlit_file.py         # Streamlit web interface
├── requirements.txt          # Python dependencies
├── .env.example             # Environment template
└── README.md                # This file
```

## Troubleshooting

### Server Won't Start
```bash
# Check if port is in use
lsof -i :5050

# Use different port
python server.py --port 5051
```

### Authentication Issues
- Ensure `.env` file exists with valid credentials
- Check Spotify Developer Dashboard for correct Client ID/Secret

## 📄 License

This project is licensed under the MIT License.

---

**🎵 Happy Music Discovery with MCP Integration! 🎵**
