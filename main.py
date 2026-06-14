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

def convert_to_mp3(input_path: str, output_path: str) -> bool:
    try:
        cmd = [
            FFMPEG_PATH,
            "-i", input_path,
            "-vn",          # ignoră video, doar audio
            "-ar", "44100",
            "-ac", "2",
            "-b:a", "192k",
            output_path,
            "-y"
        ]
        result = subprocess.run(cmd, capture_output=True, timeout=120)
        return result.returncode == 0 and os.path.exists(output_path) and os.path.getsize(output_path) > 0
    except Exception as e:
        return False

YOUTUBE_STRATEGIES = [
    {"player_client": ["ios"], "user_agent": "com.google.ios.youtube/19.29.1 (iPhone16,2; U; CPU iOS 17_5_1 like Mac OS X;)"},
    {"player_client": ["android_vr"], "user_agent": "com.google.android.apps.youtube.vr.oculus/1.56.120 (Linux; U; Android 12L; eureka-user Build/SQ3A.220605.009.A1) gzip"},
    {"player_client": ["android"], "user_agent": "com.google.android.youtube/19.30.36 (Linux; U; Android 14) gzip"},
    {"player_client": ["mweb"], "user_agent": "Mozilla/5.0 (Linux; Android 14; Pixel 9) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.6422.165 Mobile Safari/537.36"},
    {"player_client": ["web_creator"], "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"},
]

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

        is_youtube = "youtube.com" in request.url or "youtu.be" in request.url
        is_tiktok = "tiktok.com" in request.url

        strategies = []

        if is_youtube:
            for s in YOUTUBE_STRATEGIES:
                strategies.append({
                    "url": request.url,
                    "ydl_opts_extra": {
                        "extractor_args": {"youtube": {"player_client": s["player_client"]}},
                        "http_headers": {"User-Agent": s["user_agent"]},
                    }
                })
        else:
            strategies.append({
                "url": request.url,
                "ydl_opts_extra": {
                    "http_headers": {
                        "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 14_4_2 like Mac OS X) AppleWebKit/605.1.15",
                        "Referer": "https://www.tiktok.com/" if is_tiktok else "",
                    }
                }
            })

        for strategy in strategies:
            unique_id = str(uuid.uuid4())
            output_template = os.path.join(tmp_dir, f"{unique_id}.%(ext)s")
            mp3_output = os.path.join(tmp_dir, f"{unique_id}_final.mp3")

            try:
                ydl_opts = {
                    "format": "bestaudio/best",
                    "outtmpl": output_template,
                    "quiet": True,
                    "no_warnings": True,
                    "socket_timeout": 30,
                    "retries": 2,
                    "noplaylist": True,
                }
                ydl_opts.update(strategy["ydl_opts_extra"])

                if cookies_file:
                    ydl_opts["cookiefile"] = cookies_file

                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    ydl.download([strategy["url"]])

                # Găsește fișierul descărcat
                downloaded = None
                for f in os.listdir(tmp_dir):
                    if f.startswith(unique_id) and not f.endswith("_final.mp3") and f != "cookies.txt":
                        downloaded = os.path.join(tmp_dir, f)
                        break

                if not downloaded or os.path.getsize(downloaded) == 0:
                    last_error = "Fișier descărcat gol sau lipsă"
                    continue

                # Convertește la mp3 cu ffmpeg (-vn extrage doar audio din orice format)
                if convert_to_mp3(downloaded, mp3_output):
                    response = FileResponse(
                        mp3_output,
                        media_type="audio/mpeg",
                        filename="audio.mp3"
                    )
                    response.headers["Access-Control-Allow-Origin"] = "*"
                    return response
                else:
                    last_error = "Conversia la mp3 a eșuat"
                    continue

            except Exception as e:
                last_error = str(e)
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
        "ffmpeg": FFMPEG_PATH,
        "ffprobe": shutil.which("ffprobe"),
        "yt_dlp_version": yt_dlp.version.__version__
    }
