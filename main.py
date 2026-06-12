from fastapi import FastAPI, HTTPException, Request, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
import yt_dlp
import static_ffmpeg
import tempfile
import shutil
import os
import uuid
from pydantic import BaseModel

static_ffmpeg.add_paths()

# Găsește calea exactă către ffmpeg instalat de static_ffmpeg
import shutil as shutil_which
FFMPEG_PATH = shutil_which.which("ffmpeg") or ""
print(f"[STARTUP] ffmpeg path: {FFMPEG_PATH}")
print(f"[STARTUP] ffprobe path: {shutil_which.which('ffprobe')}")

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


YOUTUBE_STRATEGIES = [
    {
        "name": "android_embedded",
        "extractor_args": {"youtube": {"player_client": ["android_embedded"]}},
        "http_headers": {
            "User-Agent": "com.google.android.youtube/19.09.37 (Linux; U; Android 11) gzip",
        },
    },
    {
        "name": "android",
        "extractor_args": {"youtube": {"player_client": ["android"]}},
        "http_headers": {
            "User-Agent": "com.google.android.youtube/19.30.36 (Linux; U; Android 14) gzip",
        },
    },
    {
        "name": "tvhtml5",
        "extractor_args": {"youtube": {"player_client": ["tvhtml5"]}},
        "http_headers": {
            "User-Agent": "Mozilla/5.0 (SMART-TV; Linux; Tizen 5.0) AppleWebKit/538.1 (KHTML, like Gecko) Version/5.0 TV Safari/538.1",
        },
    },
    {
        "name": "web_creator",
        "extractor_args": {"youtube": {"player_client": ["web_creator"]}},
        "http_headers": {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
        },
    },
]

TIKTOK_STRATEGIES = [
    {
        "name": "tiktok_mobile",
        "extractor_args": {},
        "http_headers": {
            "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1",
            "Referer": "https://www.tiktok.com/",
        },
    },
    {
        "name": "tiktok_desktop",
        "extractor_args": {},
        "http_headers": {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
            "Referer": "https://www.tiktok.com/",
        },
    },
]


def is_tiktok(url: str) -> bool:
    return "tiktok.com" in url.lower()


def cleanup_dir(path: str):
    try:
        shutil.rmtree(path, ignore_errors=True)
    except Exception:
        pass


@app.post("/download-audio")
async def download_audio(request: DownloadRequest, background_tasks: BackgroundTasks):
    tmp_dir = tempfile.mkdtemp()
    background_tasks.add_task(cleanup_dir, tmp_dir)

    cookies_file = None
    last_error = "Nicio strategie nu a funcționat."

    try:
        if request.cookies and request.cookies.strip():
            cookies_file = os.path.join(tmp_dir, "cookies.txt")
            with open(cookies_file, "w") as f:
                f.write(request.cookies)

        strategies = TIKTOK_STRATEGIES if is_tiktok(request.url) else YOUTUBE_STRATEGIES

        for strategy in strategies:
            unique_id = str(uuid.uuid4())
            output_template = os.path.join(tmp_dir, f"{unique_id}.%(ext)s")

            try:
                ydl_opts = {
                    "format": "bestaudio/best",
                    "outtmpl": output_template,
                    # NU mai folosim FFmpegExtractAudio — cauzează problema cu ffprobe
                    # În schimb convertim manual după download
                    "quiet": False,  # temporar verbose ca să vedem ce se întâmplă
                    "no_warnings": False,
                    "noprogress": True,
                    "noplaylist": True,
                    "http_headers": strategy["http_headers"],
                    "socket_timeout": 30,
                    "retries": 2,
                    "fragment_retries": 2,
                    "ignoreerrors": False,
                }

                if strategy.get("extractor_args"):
                    ydl_opts["extractor_args"] = strategy["extractor_args"]

                if cookies_file:
                    ydl_opts["cookiefile"] = cookies_file

                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    ydl.download([request.url])

                # Găsește fișierul descărcat (orice extensie)
                downloaded_file = None
                for f in os.listdir(tmp_dir):
                    if f.startswith(unique_id) and not f.endswith(".mp3") and f != "cookies.txt":
                        downloaded_file = os.path.join(tmp_dir, f)
                        break

                if not downloaded_file or not os.path.exists(downloaded_file):
                    last_error = "Fișierul descărcat nu a fost găsit"
                    continue

                print(f"[DEBUG] Fișier descărcat: {downloaded_file}, size: {os.path.getsize(downloaded_file)}")

                # Convertim manual cu ffmpeg, fără ffprobe
                mp3_file = os.path.join(tmp_dir, f"{unique_id}.mp3")
                import subprocess
                ffmpeg_cmd = [
                    FFMPEG_PATH or "ffmpeg",
                    "-y",                    # suprascrie dacă există
                    "-i", downloaded_file,   # input
                    "-vn",                   # fără video
                    "-acodec", "libmp3lame", # codec audio
                    "-ab", "192k",           # bitrate
                    "-ar", "44100",          # sample rate
                    mp3_file
                ]

                result = subprocess.run(
                    ffmpeg_cmd,
                    capture_output=True,
                    text=True,
                    timeout=120
                )

                print(f"[DEBUG] ffmpeg returncode: {result.returncode}")
                if result.returncode != 0:
                    print(f"[DEBUG] ffmpeg stderr: {result.stderr[-500:]}")
                    last_error = f"ffmpeg conversion failed: {result.stderr[-200:]}"
                    continue

                if os.path.exists(mp3_file) and os.path.getsize(mp3_file) > 0:
                    response = FileResponse(
                        mp3_file,
                        media_type="audio/mpeg",
                        filename="audio.mp3",
                        background=None,
                    )
                    response.headers["Access-Control-Allow-Origin"] = "*"
                    return response

            except yt_dlp.utils.DownloadError as e:
                last_error = str(e)
                print(f"[DEBUG] DownloadError strategy {strategy['name']}: {last_error}")
                for f in os.listdir(tmp_dir):
                    if f.startswith(unique_id) and f != "cookies.txt":
                        try:
                            os.remove(os.path.join(tmp_dir, f))
                        except Exception:
                            pass
                continue

            except Exception as e:
                last_error = str(e)
                print(f"[DEBUG] Exception strategy {strategy['name']}: {last_error}")
                continue

        raise HTTPException(
            status_code=500,
            detail=f"Toate strategiile ({len(strategies)}) au eșuat. Ultima eroare: {last_error}"
        )

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/health")
async def health():
    return {"status": "ok", "yt_dlp_version": yt_dlp.version.__version__}
