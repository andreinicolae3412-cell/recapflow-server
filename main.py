from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
import yt_dlp
import static_ffmpeg
import tempfile
import os
import uuid
import subprocess
import sys
from pydantic import BaseModel

static_ffmpeg.add_paths()

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

def update_yt_dlp():
    """Auto-update yt-dlp la cea mai nouă versiune"""
    try:
        subprocess.run(
            [sys.executable, "-m", "pip", "install", "--upgrade", "yt-dlp", "-q"],
            timeout=60,
            check=False
        )
    except Exception:
        pass

# Actualizează yt-dlp la pornire
update_yt_dlp()

# Strategii de download în ordine de prioritate
DOWNLOAD_STRATEGIES = [
    {
        "name": "ios",
        "extractor_args": {"youtube": {"player_client": ["ios"]}},
        "format": "bestaudio[ext=m4a]/bestaudio/best",
        "http_headers": {
            "User-Agent": "com.google.ios.youtube/19.29.1 (iPhone16,2; U; CPU iOS 17_5_1 like Mac OS X;)",
        }
    },
    {
        "name": "android",
        "extractor_args": {"youtube": {"player_client": ["android"]}},
        "format": "bestaudio[ext=m4a]/bestaudio/best",
        "http_headers": {
            "User-Agent": "com.google.android.youtube/19.30.36 (Linux; U; Android 14) gzip",
        }
    },
    {
        "name": "android_vr",
        "extractor_args": {"youtube": {"player_client": ["android_vr"]}},
        "format": "bestaudio/best",
        "http_headers": {
            "User-Agent": "com.google.android.apps.youtube.vr.oculus/1.56.120 (Linux; U; Android 12L; eureka-user Build/SQ3A.220605.009.A1) gzip",
        }
    },
    {
        "name": "web_creator",
        "extractor_args": {"youtube": {"player_client": ["web_creator"]}},
        "format": "bestaudio/best",
        "http_headers": {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
        }
    },
    {
        "name": "mweb",
        "extractor_args": {"youtube": {"player_client": ["mweb"]}},
        "format": "bestaudio/best",
        "http_headers": {
            "User-Agent": "Mozilla/5.0 (Linux; Android 14; Pixel 9) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.6422.165 Mobile Safari/537.36",
        }
    },
    {
        "name": "web_fallback",
        "extractor_args": {"youtube": {"player_client": ["web"]}},
        "format": "bestaudio/best",
        "http_headers": {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
        }
    },
]

@app.post("/download-audio")
async def download_audio(request: DownloadRequest):
    tmp_dir = tempfile.mkdtemp()
    unique_id = str(uuid.uuid4())
    output_template = os.path.join(tmp_dir, f"{unique_id}.%(ext)s")
    cookies_file = None
    last_error = None

    try:
        if request.cookies and request.cookies.strip():
            cookies_file = os.path.join(tmp_dir, "cookies.txt")
            with open(cookies_file, "w") as f:
                f.write(request.cookies)

        for strategy in DOWNLOAD_STRATEGIES:
            try:
                ydl_opts = {
                    "format": strategy["format"],
                    "outtmpl": output_template,
                    "postprocessors": [{
                        "key": "FFmpegExtractAudio",
                        "preferredcodec": "mp3",
                        "preferredquality": "192",
                    }],
                    "quiet": True,
                    "no_warnings": True,
                    "extractor_args": strategy["extractor_args"],
                    "http_headers": strategy["http_headers"],
                    "socket_timeout": 30,
                    "retries": 2,
                    # Ignoră erorile de format și încearcă oricum
                    "ignoreerrors": False,
                }

                if cookies_file:
                    ydl_opts["cookiefile"] = cookies_file

                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    ydl.download([request.url])

                # Caută fișierul mp3 generat
                for f in os.listdir(tmp_dir):
                    if f.endswith(".mp3"):
                        response = FileResponse(
                            os.path.join(tmp_dir, f),
                            media_type="audio/mpeg",
                            filename="audio.mp3"
                        )
                        response.headers["Access-Control-Allow-Origin"] = "*"
                        return response

            except Exception as e:
                last_error = str(e)
                # Curăță fișierele parțiale între încercări
                for f in os.listdir(tmp_dir):
                    if f != "cookies.txt":
                        try:
                            os.remove(os.path.join(tmp_dir, f))
                        except Exception:
                            pass
                # Regenerează output template cu nou UUID
                unique_id = str(uuid.uuid4())
                output_template = os.path.join(tmp_dir, f"{unique_id}.%(ext)s")
                continue

        raise HTTPException(
            status_code=500,
            detail=f"Toate strategiile au eșuat. Ultima eroare: {last_error}"
        )

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/health")
async def health():
    return {"status": "ok"}
