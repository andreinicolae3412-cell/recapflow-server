from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
import yt_dlp
import static_ffmpeg
import tempfile
import os
import uuid
import shutil
import re
from pydantic import BaseModel

static_ffmpeg.add_paths()
FFMPEG_PATH = shutil.which("ffmpeg")
FFMPEG_DIR = os.path.dirname(FFMPEG_PATH) if FFMPEG_PATH else None

# Setează PATH să includă directorul ffmpeg
if FFMPEG_DIR and FFMPEG_DIR not in os.environ.get("PATH", ""):
    os.environ["PATH"] = FFMPEG_DIR + os.pathsep + os.environ.get("PATH", "")

app = FastAPI()

@app.options("/{rest_of_path:path}")
async def preflight_handler(request: Request, rest_of_path: str):
    return JSONResponse(
        content={},
        headers={
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "POST, GET, OPTIONS",
            "Access-Control-Allow-Headers": "*",
        }
    )

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
    allow_credentials=False,
    expose_headers=["*"],
)

class DownloadRequest(BaseModel):
    url: str
    cookies: str = ""

INVIDIOUS_INSTANCES = [
    "https://invidious.privacyredirect.com",
    "https://inv.nadeko.net",
    "https://invidious.nerdvpn.de",
    "https://yt.artemislena.eu",
    "https://invidious.lunar.icu",
    "https://iv.datura.network",
    "https://invidious.fdn.fr",
]

def get_video_id(url: str):
    match = re.search(
        r"(?:youtube\.com/shorts/|youtu\.be/|youtube\.com/watch\?v=)([a-zA-Z0-9_-]{11})",
        url
    )
    return match.group(1) if match else None

def is_youtube(url: str) -> bool:
    return "youtube.com" in url or "youtu.be" in url

def is_tiktok(url: str) -> bool:
    return "tiktok.com" in url

@app.post("/download-audio")
async def download_audio(request: DownloadRequest):
    tmp_dir = tempfile.mkdtemp()
    cookies_file = None
    last_error = "Nicio strategie nu a funcționat"

    try:
        if request.cookies and request.cookies.strip():
            cookies_file = os.path.join(tmp_dir, "cookies.txt")
            with open(cookies_file, "w") as f:
                f.write(request.cookies)

        # Construiește lista de URL-uri de încercat
        urls_to_try = []

        if is_youtube(request.url):
            # Încearcă direct mai întâi cu mai mulți player clients
            urls_to_try.append(("youtube_direct", request.url))
            # Apoi prin Invidious
            video_id = get_video_id(request.url)
            if video_id:
                for instance in INVIDIOUS_INSTANCES:
                    urls_to_try.append(("invidious", f"{instance}/watch?v={video_id}"))
        else:
            urls_to_try.append(("other", request.url))

        for (url_type, url) in urls_to_try:
            unique_id = str(uuid.uuid4())
            output_template = os.path.join(tmp_dir, f"{unique_id}.%(ext)s")

            try:
                ydl_opts = {
                    "format": "bestaudio[ext=m4a]/bestaudio[ext=webm]/bestaudio/best",
                    "outtmpl": output_template,
                    "postprocessors": [{
                        "key": "FFmpegExtractAudio",
                        "preferredcodec": "mp3",
                        "preferredquality": "192",
                    }],
                    "ffmpeg_location": FFMPEG_DIR,
                    "quiet": True,
                    "no_warnings": True,
                    "socket_timeout": 30,
                    "retries": 2,
                }

                if url_type == "youtube_direct":
                    ydl_opts["extractor_args"] = {
                        "youtube": {
                            "player_client": ["tv_embedded", "ios", "android_vr"],
                        }
                    }
                    ydl_opts["http_headers"] = {
                        "User-Agent": "Mozilla/5.0 (SMART-TV; Linux; Tizen 6.0) AppleWebKit/538.1 (KHTML, like Gecko) Version/6.0 TV Safari/538.1",
                    }

                if is_tiktok(request.url):
                    ydl_opts["format"] = "bestaudio/best"
                    ydl_opts["http_headers"] = {
                        "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 14_4_2 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/14.0 Mobile/15E148 Safari/604.1",
                        "Referer": "https://www.tiktok.com/",
                    }
                    # Pentru TikTok nu folosi postprocessor — descarcă direct
                    ydl_opts["postprocessors"] = []
                    ydl_opts["format"] = "bestaudio/best"

                if cookies_file:
                    ydl_opts["cookiefile"] = cookies_file

                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    info = ydl.extract_info(url, download=True)

                # Caută fișierul descărcat
                for f in os.listdir(tmp_dir):
                    if f.startswith(unique_id) and f != "cookies.txt":
                        filepath = os.path.join(tmp_dir, f)
                        if os.path.getsize(filepath) > 0:
                            # Detectează media type
                            ext = f.split(".")[-1].lower()
                            media_type = "audio/mpeg" if ext == "mp3" else "audio/mp4" if ext == "m4a" else "audio/webm" if ext == "webm" else "audio/mpeg"
                            response = FileResponse(
                                filepath,
                                media_type=media_type,
                                filename=f"audio.{ext}"
                            )
                            response.headers["Access-Control-Allow-Origin"] = "*"
                            return response

            except Exception as e:
                last_error = str(e)
                for f in os.listdir(tmp_dir):
                    if f.startswith(unique_id):
                        try:
                            os.remove(os.path.join(tmp_dir, f))
                        except Exception:
                            pass
                continue

        raise HTTPException(status_code=500, detail=last_error)

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/health")
async def health():
    return {
        "status": "ok",
        "ffmpeg": shutil.which("ffmpeg"),
        "ffprobe": shutil.which("ffprobe"),
        "ffmpeg_dir": FFMPEG_DIR,
        "path": os.environ.get("PATH", "")[:200],
        "yt_dlp_version": yt_dlp.version.__version__
    }
