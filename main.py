from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
import yt_dlp
import static_ffmpeg
import tempfile
import os
import uuid
import shutil
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

STRATEGIES = [
    {
        "extractor_args": {"youtube": {"player_client": ["tv_embedded"]}},
        "http_headers": {"User-Agent": "Mozilla/5.0 (SMART-TV; Linux; Tizen 6.0) AppleWebKit/538.1 (KHTML, like Gecko) Version/6.0 TV Safari/538.1"},
    },
    {
        "extractor_args": {"youtube": {"player_client": ["ios"]}},
        "http_headers": {"User-Agent": "com.google.ios.youtube/19.29.1 (iPhone16,2; U; CPU iOS 17_5_1 like Mac OS X;)"},
    },
    {
        "extractor_args": {"youtube": {"player_client": ["android_vr"]}},
        "http_headers": {"User-Agent": "com.google.android.apps.youtube.vr.oculus/1.56.120 (Linux; U; Android 12L; eureka-user Build/SQ3A.220605.009.A1) gzip"},
    },
    {
        "extractor_args": {"youtube": {"player_client": ["android"]}},
        "http_headers": {"User-Agent": "com.google.android.youtube/19.30.36 (Linux; U; Android 14) gzip"},
    },
    {
        "extractor_args": {"youtube": {"player_client": ["web"]}},
        "http_headers": {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"},
    },
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

        for strategy in STRATEGIES:
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
                    "extractor_args": strategy["extractor_args"],
                    "http_headers": strategy["http_headers"],
                    "socket_timeout": 30,
                    "retries": 2,
                }

                if cookies_file:
                    ydl_opts["cookiefile"] = cookies_file

                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    ydl.download([request.url])

                for f in os.listdir(tmp_dir):
                    if f.startswith(unique_id) and f.endswith(".mp3"):
                        response = FileResponse(
                            os.path.join(tmp_dir, f),
                            media_type="audio/mpeg",
                            filename="audio.mp3"
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
        "yt_dlp_version": yt_dlp.version.__version__
    }
