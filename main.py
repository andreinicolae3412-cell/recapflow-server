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
    mp3_output = os.path.join(tmp_dir, f"{unique_id}.mp3")
    cookies_file = None

    try:
        if request.cookies and request.cookies.strip():
            cookies_file = os.path.join(tmp_dir, "cookies.txt")
            with open(cookies_file, "w") as f:
                f.write(request.cookies)

        is_tiktok = "tiktok.com" in request.url

        if is_tiktok:
            # Pentru TikTok: descarcă video+audio combinat (format mp4)
            output_template = os.path.join(tmp_dir, f"{unique_id}.mp4")
            ydl_opts = {
                "format": "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
                "outtmpl": output_template,
                "quiet": True,
                "no_warnings": True,
                "socket_timeout": 30,
                "retries": 3,
                "noplaylist": True,
                "merge_output_format": "mp4",
                "http_headers": {
                    "User-Agent": "Mozilla/5.0 (Linux; Android 14; Pixel 9) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.6422.165 Mobile Safari/537.36",
                    "Referer": "https://www.tiktok.com/",
                },
            }
            if cookies_file:
                ydl_opts["cookiefile"] = cookies_file

            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([request.url])

            # Găsește fișierul mp4 descărcat
            downloaded = None
            for f in os.listdir(tmp_dir):
                if f.startswith(unique_id) and f != "cookies.txt":
                    downloaded = os.path.join(tmp_dir, f)
                    break

            if not downloaded or not os.path.exists(downloaded):
                raise HTTPException(status_code=500, detail="Fișierul TikTok nu a fost descărcat")

            # Extrage audio din mp4 cu ffmpeg
            cmd = [
                FFMPEG_PATH,
                "-i", downloaded,
                "-map", "0:a:0",  # extrage primul stream audio
                "-ar", "44100",
                "-ac", "2",
                "-b:a", "192k",
                mp3_output,
                "-y"
            ]
            result = subprocess.run(cmd, capture_output=True, timeout=120)

            if result.returncode != 0 or not os.path.exists(mp3_output) or os.path.getsize(mp3_output) == 0:
                raise HTTPException(
                    status_code=500,
                    detail=f"Conversia TikTok eșuată: {result.stderr.decode()[:500]}"
                )

        else:
            # Pentru YouTube și alte platforme
            output_template = os.path.join(tmp_dir, f"{unique_id}.%(ext)s")
            ydl_opts = {
                "format": "bestaudio/best",
                "outtmpl": output_template,
                "quiet": True,
                "no_warnings": True,
                "socket_timeout": 30,
                "retries": 3,
                "noplaylist": True,
                "extractor_args": {
                    "youtube": {
                        "player_client": ["android"],
                    }
                },
                "http_headers": {
                    "User-Agent": "com.google.android.youtube/19.30.36 (Linux; U; Android 14) gzip",
                },
            }
            if cookies_file:
                ydl_opts["cookiefile"] = cookies_file

            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([request.url])

            # Găsește fișierul descărcat
            downloaded = None
            for f in os.listdir(tmp_dir):
                if f.startswith(unique_id) and f != "cookies.txt":
                    downloaded = os.path.join(tmp_dir, f)
                    break

            if not downloaded or not os.path.exists(downloaded):
                raise HTTPException(status_code=500, detail="Fișierul nu a fost descărcat")

            # Convertește la mp3
            cmd = [
                FFMPEG_PATH,
                "-i", downloaded,
                "-vn",
                "-ar", "44100",
                "-ac", "2",
                "-b:a", "192k",
                mp3_output,
                "-y"
            ]
            result = subprocess.run(cmd, capture_output=True, timeout=120)

            if result.returncode != 0 or not os.path.exists(mp3_output) or os.path.getsize(mp3_output) == 0:
                raise HTTPException(
                    status_code=500,
                    detail=f"Conversia eșuată: {result.stderr.decode()[:500]}"
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
        "ffmpeg": FFMPEG_PATH,
        "ffprobe": shutil.which("ffprobe"),
        "yt_dlp_version": yt_dlp.version.__version__
    }
