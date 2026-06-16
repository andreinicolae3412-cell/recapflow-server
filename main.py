from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
import yt_dlp
import static_ffmpeg
import tempfile
import os
import shutil
import subprocess
from pydantic import BaseModel

static_ffmpeg.add_paths()
FFMPEG_PATH = shutil.which("ffmpeg")

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

def download_tiktok_audio(url: str, tmp_dir: str, cookies_file: str | None) -> str:
    """
    TikTok returnează adesea video-only ca 'bestaudio'.
    Inspectăm formatele și alegem explicit unul cu acodec != none.
    Dacă există format audio-only îl preferăm, altfel luăm video+audio
    și extragem audio cu ffmpeg.
    """
    headers = {
        "User-Agent": "Mozilla/5.0 (Linux; Android 14; Pixel 9) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.6422.165 Mobile Safari/537.36",
        "Referer": "https://www.tiktok.com/",
    }

    base_opts = {
        "quiet": True,
        "no_warnings": True,
        "socket_timeout": 30,
        "http_headers": headers,
    }
    if cookies_file:
        base_opts["cookiefile"] = cookies_file

    # Pas 1: inspectează formatele
    with yt_dlp.YoutubeDL(base_opts) as ydl:
        info = ydl.extract_info(url, download=False)

    audio_only_formats = []
    video_audio_formats = []

    for f in info.get("formats", []):
        acodec = f.get("acodec") or "none"
        vcodec = f.get("vcodec") or "none"
        fmt_id = f.get("format_id", "")
        abr = f.get("abr") or 0
        tbr = f.get("tbr") or 0
        score = abr or tbr

        if acodec == "none":
            continue  # video-only, skip

        if vcodec == "none":
            audio_only_formats.append((score, fmt_id))
        else:
            video_audio_formats.append((score, fmt_id))

    if audio_only_formats:
        audio_only_formats.sort(reverse=True)
        chosen = audio_only_formats[0][1]
    elif video_audio_formats:
        video_audio_formats.sort(reverse=True)
        chosen = video_audio_formats[0][1]
    else:
        fmt_debug = [
            f"id={f.get('format_id')} acodec={f.get('acodec')} vcodec={f.get('vcodec')} abr={f.get('abr')}"
            for f in info.get("formats", [])
        ]
        raise HTTPException(
            status_code=500,
            detail="TikTok: niciun format cu audio.\n" + "\n".join(fmt_debug)
        )

    # Pas 2: descarcă formatul ales
    raw_path = os.path.join(tmp_dir, "tiktok_raw")
    dl_opts = {
        **base_opts,
        "format": chosen,
        "outtmpl": raw_path,
        "retries": 5,
        "noplaylist": True,
    }

    with yt_dlp.YoutubeDL(dl_opts) as ydl:
        ydl.download([url])

    # Găsește fișierul descărcat (yt-dlp adaugă extensia automat)
    downloaded = None
    for f in os.listdir(tmp_dir):
        full = os.path.join(tmp_dir, f)
        if os.path.isfile(full) and f != "cookies.txt":
            downloaded = full
            break

    if not downloaded:
        raise HTTPException(status_code=500, detail="TikTok: fișierul nu a fost descărcat")

    return downloaded


def download_youtube_audio(url: str, tmp_dir: str, cookies_file: str | None) -> str:
    """
    YouTube: folosim mai mulți player clients ca fallback.
    """
    raw_path = os.path.join(tmp_dir, "yt_raw")

    ydl_opts = {
        # Selector robust: încearcă m4a, webm, orice audio, fallback la best
        "format": "bestaudio[ext=m4a]/bestaudio[ext=webm]/bestaudio/best",
        "outtmpl": raw_path,
        "quiet": True,
        "no_warnings": True,
        "socket_timeout": 30,
        "retries": 5,
        "noplaylist": True,
        "extractor_args": {
            "youtube": {
                # web are cel mai multe formate disponibile
                "player_client": ["web", "ios", "android"],
            }
        },
        "http_headers": {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
        },
    }
    if cookies_file:
        ydl_opts["cookiefile"] = cookies_file

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.download([url])

    downloaded = None
    for f in os.listdir(tmp_dir):
        full = os.path.join(tmp_dir, f)
        if os.path.isfile(full) and f != "cookies.txt":
            downloaded = full
            break

    if not downloaded:
        raise HTTPException(status_code=500, detail="YouTube: fișierul nu a fost descărcat")

    return downloaded


def convert_to_mp3(input_path: str, output_path: str):
    """Convertește orice fișier audio/video la MP3 192k."""
    cmd = [
        str(FFMPEG_PATH),
        "-i", input_path,
        "-vn",           # ignoră video
        "-ar", "44100",
        "-ac", "2",
        "-b:a", "192k",
        "-f", "mp3",
        output_path,
        "-y"
    ]
    result = subprocess.run(cmd, capture_output=True, timeout=120)

    if result.returncode != 0 or not os.path.exists(output_path) or os.path.getsize(output_path) == 0:
        raise HTTPException(
            status_code=500,
            detail=f"Conversie ffmpeg eșuată:\n{result.stderr.decode(errors='replace')[-2000:]}"
        )


@app.post("/download-audio")
async def download_audio(request: DownloadRequest):
    tmp_dir = tempfile.mkdtemp()
    mp3_output = os.path.join(tmp_dir, "output.mp3")
    cookies_file = None

    try:
        if request.cookies and request.cookies.strip():
            cookies_file = os.path.join(tmp_dir, "cookies.txt")
            with open(cookies_file, "w") as f:
                f.write(request.cookies)

        if "tiktok.com" in request.url:
            downloaded = download_tiktok_audio(request.url, tmp_dir, cookies_file)
        else:
            downloaded = download_youtube_audio(request.url, tmp_dir, cookies_file)

        convert_to_mp3(downloaded, mp3_output)

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
