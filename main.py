from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
import yt_dlp
import static_ffmpeg
import tempfile
import os
import uuid
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

@app.post("/download-audio")
async def download_audio(request: DownloadRequest):
    tmp_dir = tempfile.mkdtemp()
    unique_id = str(uuid.uuid4())
    output_template = os.path.join(tmp_dir, f"{unique_id}.%(ext)s")
    cookies_file = None
    try:
        if request.cookies and request.cookies.strip():
            cookies_file = os.path.join(tmp_dir, "cookies.txt")
            with open(cookies_file, "w") as f:
                f.write(request.cookies)

        ydl_opts = {
            "format": "bestaudio[ext=m4a]/bestaudio/best",
            "outtmpl": output_template,
            "postprocessors": [{
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "192",
            }],
            "quiet": True,
            "no_warnings": True,
        }

        if cookies_file:
            ydl_opts["cookiefile"] = cookies_file

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([request.url])

        for f in os.listdir(tmp_dir):
            if f.endswith(".mp3"):
                response = FileResponse(
                    os.path.join(tmp_dir, f),
                    media_type="audio/mpeg",
                    filename="audio.mp3"
                )
                response.headers["Access-Control-Allow-Origin"] = "*"
                return response

        raise HTTPException(status_code=500, detail="Fișierul audio nu a fost găsit")

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/health")
async def health():
    return {"status": "ok"}
