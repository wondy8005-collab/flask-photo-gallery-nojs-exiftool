import os
import json
import shutil
import subprocess
from datetime import datetime
from flask import (
    Flask, render_template, request, redirect, url_for,
    send_from_directory, flash, session, abort
)
from werkzeug.utils import secure_filename
from PIL import Image, ExifTags

try:
    import piexif
    PIEXIF_AVAILABLE = True
except Exception:
    PIEXIF_AVAILABLE = False

APP_DIR = os.path.dirname(os.path.abspath(__file__))
PHOTO_DIR = os.path.join(APP_DIR, "photo")
ALLOWED_IMAGE_EXT = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".tiff", ".tif"}
ALLOWED_VIDEO_EXT = {".mp4", ".webm", ".ogg", ".mov", ".mkv"}

SECRET_KEY = "please-change-this-in-production"
ADMIN_PASSWORD = "1234"

app = Flask(__name__)
app.config["SECRET_KEY"] = SECRET_KEY
app.config["MAX_CONTENT_LENGTH"] = 200 * 1024 * 1024  # 200 MB upload limit

# exiftool detection
EXIFTOOL_PATH = shutil.which("exiftool")

def exiftool_available():
    return EXIFTOOL_PATH is not None


def run_exiftool_args(args):
    if not EXIFTOOL_PATH:
        return None
    cmd = [EXIFTOOL_PATH] + args
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if res.returncode == 0:
            return res.stdout
        else:
            return None
    except Exception:
        return None


def read_exiftool_json(path):
    out = run_exiftool_args(["-j", "-n", "-charset", "utf8", path])
    if not out:
        return {}
    try:
        arr = json.loads(out)
        if isinstance(arr, list) and arr:
            return arr[0]
    except Exception:
        pass
    return {}


def parse_exif_datetime(value):
    if not value:
        return None
    try:
        # remove timezone parts like +02:00 or Z
        base = str(value).split('+')[0].split('Z')[0].strip()
        # some exiftool outputs already in ISO, attempt both
        try:
            return datetime.strptime(base, "%Y:%m:%d %H:%M:%S")
        except Exception:
            return datetime.fromisoformat(base)
    except Exception:
        return None


def get_exiftool_metadata(path):
    result = {"datetime": None, "lat": None, "lon": None, "width": None, "height": None, "size": None, "raw": {}}
    try:
        result["size"] = os.path.getsize(path)
    except Exception:
        result["size"] = None
    if not exiftool_available():
        return result
    data = read_exiftool_json(path)
    if not data:
        return result
    result["raw"] = data
    # date
    for key in ("DateTimeOriginal", "CreateDate", "ModifyDate", "FileModifyDate"):
        if key in data and data.get(key):
            dt = parse_exif_datetime(data.get(key))
            if dt:
                result["datetime"] = dt.isoformat()
                break
    # GPS (numeric when -n used)
    lat = data.get("GPSLatitude")
    lon = data.get("GPSLongitude")
    if lat is not None and lon is not None:
        try:
            result["lat"] = float(lat)
            result["lon"] = float(lon)
        except Exception:
            result["lat"] = None
            result["lon"] = None
    else:
        gpspos = data.get("Composite:GPSPosition") or data.get("GPSPosition")
        if gpspos:
            try:
                s = str(gpspos).replace(',', ' ')
                parts = [p for p in s.split() if p.replace('.', '', 1).lstrip('-').isdigit()]
                if len(parts) >= 2:
                    result["lat"] = float(parts[0])
                    result["lon"] = float(parts[1])
            except Exception:
                pass
    # width/height
    for w_key, h_key in (("ImageWidth", "ImageHeight"), ("ExifImageWidth", "ExifImageHeight"), ("PixelXDimension", "PixelYDimension")):
        if w_key in data and h_key in data and data.get(w_key) and data.get(h_key):
            try:
                result["width"] = int(data.get(w_key))
                result["height"] = int(data.get(h_key))
                break
            except Exception:
                continue
    # ImageSize fallback
    if (result["width"] is None or result["height"] is None) and data.get("ImageSize"):
        try:
            s = str(data.get("ImageSize")).lower().replace(' ', '')
            if 'x' in s:
                w, h = s.split('x', 1)
                result["width"] = int(w)
                result["height"] = int(h)
        except Exception:
            pass
    return result


def ensure_photo_dir():
    if not os.path.exists(PHOTO_DIR):
        os.makedirs(PHOTO_DIR)


def list_albums():
    ensure_photo_dir()
    items = []
    for name in sorted(os.listdir(PHOTO_DIR)):
        path = os.path.join(PHOTO_DIR, name)
        if os.path.isdir(path):
            items.append(name)
    return items


def is_image(filename):
    ext = os.path.splitext(filename.lower())[1]
    return ext in ALLOWED_IMAGE_EXT


def is_video(filename):
    ext = os.path.splitext(filename.lower())[1]
    return ext in ALLOWED_VIDEO_EXT


def load_album_sidecar(album):
    meta_path = os.path.join(PHOTO_DIR, album, ".meta.json")
    if os.path.exists(meta_path):
        try:
            with open(meta_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def save_album_sidecar(album, data):
    meta_path = os.path.join(PHOTO_DIR, album, ".meta.json")
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def extract_image_datetime(path):
    # prefer exiftool
    if exiftool_available():
        md = get_exiftool_metadata(path)
        if md.get("datetime"):
            try:
                return datetime.fromisoformat(md.get("datetime"))
            except Exception:
                pass
    # pillow fallback
    try:
        img = Image.open(path)
        exif = img._getexif()
        if exif:
            for tag, val in exif.items():
                decoded = ExifTags.TAGS.get(tag, tag)
                if decoded in ("DateTimeOriginal", "DateTime"):
                    try:
                        dt = datetime.strptime(val, "%Y:%m:%d %H:%M:%S")
                        return dt
                    except Exception:
                        pass
    except Exception:
        pass
    # mtime fallback
    try:
        ts = os.path.getmtime(path)
        return datetime.fromtimestamp(ts)
    except Exception:
        return None


def get_album_files(album):
    album_dir = os.path.join(PHOTO_DIR, album)
    if not os.path.isdir(album_dir):
        return []
    meta = load_album_sidecar(album)
    files = []
    for fname in sorted(os.listdir(album_dir)):
        if fname.startswith('.'):
            continue
        fpath = os.path.join(album_dir, fname)
        if os.path.isfile(fpath) and (is_image(fname) or is_video(fname)):
            item = {"filename": fname}
            if is_image(fname):
                dt = extract_image_datetime(fpath)
            else:
                try:
                    ts = os.path.getmtime(fpath)
                    dt = datetime.fromtimestamp(ts)
                except Exception:
                    dt = None
            fm = meta.get(fname, {})
            if fm.get("datetime"):
                try:
                    dt = datetime.fromisoformat(fm["datetime"])
                except Exception:
                    pass
            item["datetime"] = dt
            item["year"] = dt.year if dt else None
            item["is_image"] = is_image(fname)
            item["is_video"] = is_video(fname)
            item["meta"] = fm
            # include exiftool quick metadata if available
            try:
                md = get_exiftool_metadata(fpath)
                item["exif"] = md
            except Exception:
                item["exif"] = {}
            files.append(item)
    files.sort(key=lambda x: x["datetime"] or datetime.fromtimestamp(0))
    return files


def gather_years():
    years = {}
    for album in list_albums():
        for f in get_album_files(album):
            y = f["year"] or "Unknown"
            years.setdefault(y, []).append((album, f["filename"]))
    keys = sorted([k for k in years.keys() if isinstance(k, int)], reverse=True)
    if "Unknown" in years:
        keys.append("Unknown")
    return [(k, years[k]) for k in keys]


@app.route("/media/<path:filename>")
def media(filename):
    safe = os.path.normpath(filename)
    if ".." in safe.split(os.path.sep):
        abort(404)
    full = os.path.join(PHOTO_DIR, safe)
    if not os.path.exists(full):
        abort(404)
    parts = safe.split(os.path.sep)
    if len(parts) >= 2:
        album = parts[0]
        return send_from_directory(os.path.join(PHOTO_DIR, album), os.path.sep.join(parts[1:]))
    else:
        return send_from_directory(PHOTO_DIR, safe)


@app.route("/")
def index():
    albums = list_albums()
    years = gather_years()
    return render_template("gallery.html", albums=albums, years=years)


@app.route("/album/<album>")
def album_view(album):
    if album not in list_albums():
        abort(404)
    files = get_album_files(album)
    return render_template("album.html", album=album, files=files, albums=list_albums(), years=gather_years())


@app.route("/year/<int:year>")
def year_view(year):
    years = gather_years()
    selected = None
    for y, items in years:
        if y == year:
            selected = items
            break
    if selected is None:
        abort(404)
    results = []
    for album, fname in selected:
        files = get_album_files(album)
        item = next((f for f in files if f["filename"] == fname), None)
        if item:
            results.append({"album": album, **item})
    return render_template("year.html", year=year, items=results, albums=list_albums(), years=years)


@app.route("/view/<album>/<filename>")
def view_item(album, filename):
    slideshow = request.args.get("slideshow") == "1"
    interval = int(request.args.get("interval") or 5)
    files = get_album_files(album)
    idx = next((i for i, f in enumerate(files) if f["filename"] == filename), None)
    if idx is None:
        abort(404)
    prev_idx = (idx - 1) % len(files)
    next_idx = (idx + 1) % len(files)
    prev_file = files[prev_idx]["filename"]
    next_file = files[next_idx]["filename"]
    current = files[idx]
    return render_template(
        "view.html",
        album=album,
        file=current,
        filename=filename,
        prev_file=prev_file,
        next_file=next_file,
        slideshow=slideshow,
        interval=interval,
        albums=list_albums(),
        years=gather_years()
    )


# Admin
def admin_required(func):
    def wrapper(*args, **kwargs):
        if not session.get("admin"):
            return redirect(url_for("admin_login", next=request.path))
        return func(*args, **kwargs)
    wrapper.__name__ = func.__name__
    return wrapper


@app.route("/admin", methods=["GET", "POST"])
def admin_login():
    if request.method == "POST":
        pw = request.form.get("password", "")
        if pw == ADMIN_PASSWORD:
            session["admin"] = True
            flash("Eingeloggt als Admin.", "success")
            return redirect(url_for("admin_index"))
        else:
            flash("Falsches Passwort.", "danger")
            return redirect(url_for("admin_login"))
    return render_template("admin_login.html")


@app.route("/admin/index")
@admin_required
def admin_index():
    albums = list_albums()
    return render_template("admin_index.html", albums=albums)


@app.route("/admin/create_album", methods=["POST"])
@admin_required
def admin_create_album():
    name = request.form.get("album_name", "").strip()
    if not name:
        flash("Albumname darf nicht leer sein.", "danger")
        return redirect(url_for("admin_index"))
    safe = secure_filename(name)
    path = os.path.join(PHOTO_DIR, safe)
    if not os.path.exists(path):
        os.makedirs(path)
        flash(f"Album '{safe}' erstellt.", "success")
    else:
        flash("Album existiert bereits.", "warning")
    return redirect(url_for("admin_index"))


@app.route("/admin/upload", methods=["POST"])
@admin_required
def admin_upload():
    album = request.form.get("album")
    if not album:
        flash("Kein Album ausgewählt.", "danger")
        return redirect(url_for("admin_index"))
    if album not in list_albums():
        flash("Album existiert nicht.", "danger")
        return redirect(url_for("admin_index"))
    file = request.files.get("file")
    if not file or file.filename == "":
        flash("Keine Datei ausgewählt.", "danger")
        return redirect(url_for("admin_index"))
    filename = secure_filename(file.filename)
    dest = os.path.join(PHOTO_DIR, album, filename)
    file.save(dest)
    # Try to extract metadata via exiftool and save in sidecar
    meta = load_album_sidecar(album)
    try:
        md = get_exiftool_metadata(dest)
        if md.get("datetime"):
            meta[filename] = meta.get(filename, {})
            meta[filename]["datetime"] = md.get("datetime")
        # store size/width/height/lat/lon if present
        if md.get("size") is not None:
            meta[filename] = meta.get(filename, {})
            meta[filename]["filesize"] = md.get("size")
        if md.get("width"):
            meta[filename]["width"] = md.get("width")
        if md.get("height"):
            meta[filename]["height"] = md.get("height")
        if md.get("lat") is not None:
            meta[filename]["lat"] = md.get("lat")
        if md.get("lon") is not None:
            meta[filename]["lon"] = md.get("lon")
    except Exception:
        # fallback to pillow/mtime
        dt = None
        if is_image(filename):
            dtv = extract_image_datetime(dest)
            if dtv:
                dt = dtv.isoformat()
        if not dt:
            try:
                dt = datetime.fromtimestamp(os.path.getmtime(dest)).isoformat()
            except Exception:
                dt = None
        if dt:
            meta[filename] = meta.get(filename, {})
            meta[filename]["datetime"] = dt
    save_album_sidecar(album, meta)
    flash("Datei hochgeladen.", "success")
    return redirect(url_for("album_view", album=album))


@app.route("/admin/list_files/<album>")
@admin_required
def admin_list_files(album):
    if album not in list_albums():
        abort(404)
    files = get_album_files(album)
    return render_template("admin_list_files.html", album=album, files=files)


@app.route("/admin/delete/<album>/<filename>", methods=["GET", "POST"])
@admin_required
def admin_delete(album, filename):
    fpath = os.path.join(PHOTO_DIR, album, filename)
    if request.method == "POST":
        confirm = request.form.get("confirm")
        if confirm == "yes":
            try:
                os.remove(fpath)
                meta = load_album_sidecar(album)
                if filename in meta:
                    meta.pop(filename)
                    save_album_sidecar(album, meta)
                flash("Datei gelöscht.", "success")
            except Exception as e:
                flash(f"Löschen fehlgeschlagen: {e}", "danger")
            return redirect(url_for("admin_list_files", album=album))
        else:
            return redirect(url_for("admin_list_files", album=album))
    return render_template("admin_delete_confirm.html", album=album, filename=filename)


@app.route("/admin/edit_metadata/<album>/<filename>", methods=["GET", "POST"])
@admin_required
def admin_edit_metadata(album, filename):
    fpath = os.path.join(PHOTO_DIR, album, filename)
    if not os.path.exists(fpath):
        abort(404)
    meta = load_album_sidecar(album)
    entry = meta.get(filename, {})
    if request.method == "POST":
        title = request.form.get("title", "").strip()
        desc = request.form.get("description", "").strip()
        dt = request.form.get("datetime", "").strip()
        try:
            if dt:
                parsed = datetime.fromisoformat(dt)
                dtout = parsed.isoformat()
            else:
                dtout = None
        except Exception:
            flash("Ungültiges Datum (verwende ISO-Format YYYY-MM-DDTHH:MM:SS).", "danger")
            return redirect(url_for("admin_edit_metadata", album=album, filename=filename))
        entry["title"] = title
        entry["description"] = desc
        entry["datetime"] = dtout
        meta[filename] = entry
        save_album_sidecar(album, meta)
        # Try writing EXIF with exiftool first, fallback to piexif for JPEG
        wrote = False
        if exiftool_available() and is_image(filename) and filename.lower().endswith((".jpg", ".jpeg", ".tif", ".tiff")):
            try:
                # build exiftool args
                args = ["-overwrite_original", "-charset", "utf8"]
                if dtout:
                    parsed = datetime.fromisoformat(dtout)
                    dt_exif = parsed.strftime("%Y:%m:%d %H:%M:%S")
                    args.append(f"-DateTimeOriginal={dt_exif}")
                    args.append(f"-CreateDate={dt_exif}")
                if title:
                    args.append(f"-Title={title}")
                    args.append(f"-Artist={title}")
                if desc:
                    args.append(f"-ImageDescription={desc}")
                args.append(fpath)
                out = run_exiftool_args(args)
                if out is not None:
                    flash("Metadaten gespeichert und EXIF aktualisiert (exiftool).", "success")
                    wrote = True
            except Exception:
                wrote = False
        if not wrote and PIEXIF_AVAILABLE and is_image(filename) and filename.lower().endswith((".jpg", ".jpeg")) and entry.get("datetime"):
            try:
                dt_for_exif = entry["datetime"].split("T")[0].replace("-", ":") + " " + entry["datetime"].split("T")[1].split(".")[0]
                exif_dict = piexif.load(fpath)
                exif_dict["0th"][piexif.ImageIFD.ImageDescription] = entry.get("description", "").encode("utf-8")
                exif_dict["0th"][piexif.ImageIFD.Artist] = entry.get("title", "").encode("utf-8")
                exif_dict["Exif"][piexif.ExifIFD.DateTimeOriginal] = dt_for_exif.encode("utf-8")
                exif_bytes = piexif.dump(exif_dict)
                piexif.insert(exif_bytes, fpath)
                flash("Metadaten gespeichert und EXIF aktualisiert (JPEG).", "success")
                wrote = True
            except Exception:
                flash("Metadaten gespeichert (EXIF-Update fehlgeschlagen).", "warning")
        if not wrote:
            flash("Metadaten gespeichert.", "success")
        return redirect(url_for("admin_list_files", album=album))
    return render_template("admin_edit_metadata.html", album=album, filename=filename, entry=entry)


@app.route("/logout")
def logout():
    session.clear()
    flash("Abgemeldet.", "info")
    return redirect(url_for("index"))


if __name__ == "__main__":
    ensure_photo_dir()
    app.run(debug=True)
