# Flask Photo & Video Gallery (no JS) with exiftool integration

This project is a minimal Flask-based photo and video gallery that deliberately uses no JavaScript.
It reads albums from the `photo/` folder, extracts metadata using `exiftool` (when available) and falls back to Pillow and sidecar JSON files.

Features:
- Albums are subdirectories in `photo/`
- Sidebar with albums and years
- View images & videos fullscreen, with prev/next navigation and slideshow via meta-refresh
- Admin area protected by a simple password (`1234` by default) to create albums, upload files, delete files and edit metadata
- EXIF/GPS/size/width/height extraction via `exiftool` subprocess when available

Quick start:
1. Install dependencies:
   pip install -r requirements.txt
2. Install exiftool on your system (recommended):
   - Debian/Ubuntu: sudo apt install libimage-exiftool-perl
   - macOS: brew install exiftool
   - Windows: choco install exiftool
3. Run the app:
   python app.py
4. Open http://127.0.0.1:5000/
5. Admin: http://127.0.0.1:5000/admin (password: 1234)

Notes:
- The admin password and secret key are insecure defaults; change them for any public deployment.
- The app stores per-album sidecar metadata in `.meta.json` inside each album folder.
