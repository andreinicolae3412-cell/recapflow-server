from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
import yt_dlp
import static_ffmpeg
import tempfile
import os
import uuid
import shutil
import subprocess
from pydantic import BaseModel

static_ffmpeg.add_paths()

# Găsește ffmpeg după ce static_ffmpeg a adăugat path-urile
FFMPEG_PATH = shutil.which("ffmpeg")
FFMPEG_DIR = os.path.dirname(FFMPEG_PATH) if FFMPEG_PATH else None

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

@app.post("/download-audio")
async def download_audio(request: DownloadRequest):
    tmp_dir = tempfile.mkdtemp()
    unique_id = str(uuid.uuid4())
    output_template = os.path.join(tmp_dir, f"{unique_id}.%(ext)s")
    mp3_output = os.path.join(tmp_dir, f"{unique_id}.mp3")
    cookies_file = None

    try:
        if request.cookies and request.cookies.strip():
            cookies_file = os.path.join(tmp_dir, "cookies.txt")
            with open(cookies_file, "w") as f:
                f.write(request.cookies)

        # Descarcă fără conversie — doar audio brut
        ydl_opts = {
            "format": "bestaudio/best",
            "outtmpl": output_template,
            "quiet": True,
            "no_warnings": True,
            "socket_timeout": 30,
            "retries": 3,
        }

        if "youtube.com" in request.url or "youtu.be" in request.url:
            ydl_opts["extractor_args"] = {
                "youtube": {
                    "player_client": ["tv_embedded", "ios", "android_vr"],
                }
            }

        if "tiktok.com" in request.url:
            ydl_opts["http_headers"] = {
                "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 14_4_2 like Mac OS X) AppleWebKit/605.1.15",
                "Referer": "https://www.tiktok.com/",
            }

        if cookies_file:
            ydl_opts["cookiefile"] = cookies_file

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([request.url])

        # Găsește fișierul descărcat (orice format)
        downloaded_file = None
        for f in os.listdir(tmp_dir):
            if f.startswith(unique_id) and f != "cookies.txt":
                downloaded_file = os.path.join(tmp_dir, f)
                break

        if not downloaded_file:
            raise HTTPException(status_code=500, detail="Fișierul nu a fost descărcat")

        # Convertește la mp3 cu ffmpeg direct
        convert_cmd = [
            FFMPEG_PATH,
            "-i", downloaded_file,
            "-vn",
            "-ar", "44100",
            "-ac", "2",
            "-b:a", "192k",
            "-f", "mp3",
            mp3_output,
            "-y"
        ]
        result = subprocess.run(convert_cmd, capture_output=True, timeout=120)

        if result.returncode != 0 or not os.path.exists(mp3_output):
            raise HTTPException(
                status_code=500,
                detail=f"Conversia la mp3 a eșuat: {result.stderr.decode()}"
            )

        response = FileResponse(
            mp3_output,
            media_type="audio/mpeg",
            filename="audio.mp3"
        )
        response.headers["Access-Control-Allow-Origin"] = "*"
        return response

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
        "yt_dlp_version": yt_dlp.version.__version__
    }
