# VideoSniffer - Website Video Sniffer & Downloader

A Python-based video URL sniffer and download tool with proxy support, m3u8 stream parsing, multi-threaded segmented downloading, and resume capability.

## Features
- HTTP/HTTPS proxy sniffing for video URLs
- m3u8 stream detection and parsing
- Multi-threaded segmented download for speed
- Resume interrupted downloads
- AI-powered search integration
- Qt-based GUI

## Tech Stack
- Python 3.11+
- Qt (GUI framework)
- mitmproxy (HTTP sniffing)
- m3u8 parser
- DeepSeek API (AI search)

## Project Structure
`
VideoSniffer/
  main.py            # Entry point (launches GUI)
  gui/               # Qt GUI components
  core/               # Sniffing & download engine
  config/             # Configuration & settings
  tools/              # External tools (excluded from git)
`

## Setup
`ash
pip install -r requirements.txt
python main.py
`

## Configuration
API settings are in config/settings.py. Set your DeepSeek API key via environment variable:
`ash
set DEEPSEEK_API_KEY=your_key_here
`

## Usage
1. Launch the app
2. Configure proxy settings (default: localhost:8080)
3. Browse target website through the proxy
4. Video URLs are automatically detected
5. Select and download videos

## License
MIT
