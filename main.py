from fastapi import FastAPI, HTTPException, Request, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
import yt_dlp
import tempfile
import shutil
import os
import uuid
from pydantic import BaseModel

# ffmpeg e instalat direct în sistem prin Dockerfile — nu mai avem nevoie de static_ffmpeg
import shutil as _shutil
FFMPEG_PATH = _shutil.which("ffmpeg") or "ffmpeg"
FFPROBE_PATH = _shutil.which("ffprobe") or "ffprobe"

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


def is_youtube_url(url: str) -> bool:
    return any(x in url for x in ["youtube.com", "youtu.be", "youtube-nocookie.com"])


def is_tiktok_url(url: str) -> bool:
    return "tiktok.com" in url


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
        "name": "android_vr",
        "extractor_args": {"youtube": {"player_client": ["android_vr"]}},
        "http_headers": {
            "User-Agent": "com.google.android.apps.youtube.vr.oculus/1.56.120 (Linux; U; Android 12L; eureka-user Build/SQ3A.220605.009.A1) gzip",
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
        "name": "mweb",
        "extractor_args": {"youtube": {"player_client": ["mweb"]}},
        "http_headers": {
            "User-Agent": "Mozilla/5.0 (Linux; Android 14; Pixel 9) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.6422.165 Mobile Safari/537.36",
        },
    },
    {
        "name": "ios",
        "extractor_args": {"youtube": {"player_client": ["ios"]}},
        "http_headers": {
            "User-Agent": "com.google.ios.youtube/19.29.1 (iPhone16,2; U; CPU iOS 17_5_1 like Mac OS X;)",
        },
    },
]

TIKTOK_STRATEGIES = [
    {
        "name": "tiktok_android",
        "http_headers": {
            "User-Agent": "com.zhiliaoapp.musically/2022600030 (Linux; U; Android 10; en_US; Pixel 4; Build/QQ3A.200805.001; Cronet/58.0.2991.0)",
            "Referer": "https://www.tiktok.com/",
        },
    },
    {
        "name": "tiktok_iphone",
        "http_headers": {
            "User-Agent": "TikTok 26.2.0 rv:262018 (iPhone; iOS 14.4.2; en_US) Cronet",
            "Referer": "https://www.tiktok.com/",
        },
    },
    {
        "name": "tiktok_web",
        "http_headers": {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
            "Referer": "https://www.tiktok.com/",
        },
    },
]


def get_base_ydl_opts(output_template: str, cookies_file: str = None) -> dict:
    opts = {
        "format": "bestaudio[ext=m4a]/bestaudio[ext=webm]/bestaudio/best",
        "outtmpl": output_template,
        "postprocessors": [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": "192",
        }],
        "ffmpeg_location": FFMPEG_PATH,
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "noplaylist": True,
        "socket_timeout": 30,
        "retries": 2,
        "fragment_retries": 2,
        "ignoreerrors": False,
    }
    if cookies_file:
        opts["cookiefile"] = cookies_file
    return opts


def find_mp3(tmp_dir: str, unique_id: str) -> str | None:
    for f in os.listdir(tmp_dir):
        if f.startswith(unique_id) and f.endswith(".mp3"):
            full_path = os.path.join(tmp_dir, f)
            if os.path.exists(full_path) and os.path.getsize(full_path) > 0:
                return full_path
    return None


def cleanup_partial(tmp_dir: str, unique_id: str):
    for f in os.listdir(tmp_dir):
        if f.startswith(unique_id):
            try:
                os.remove(os.path.join(tmp_dir, f))
            except Exception:
                pass


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
    last_error = "Nicio strategie nu a functionat."

    try:
        if request.cookies and request.cookies.strip():
            cookies_file = os.path.join(tmp_dir, "cookies.txt")
            with open(cookies_file, "w") as f:
                f.write(request.cookies)

        if is_youtube_url(request.url):
            strategies = YOUTUBE_STRATEGIES
            use_extractor_args = True
        elif is_tiktok_url(request.url):
            strategies = TIKTOK_STRATEGIES
            use_extractor_args = False
        else:
            strategies = TIKTOK_STRATEGIES
            use_extractor_args = False

        for strategy in strategies:
            unique_id = str(uuid.uuid4())
            output_template = os.path.join(tmp_dir, f"{unique_id}.%(ext)s")

            try:
                ydl_opts = get_base_ydl_opts(output_template, cookies_file)
                ydl_opts["http_headers"] = strategy["http_headers"]

                if use_extractor_args and "extractor_args" in strategy:
                    ydl_opts["extractor_args"] = strategy["extractor_args"]

                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    ydl.download([request.url])

                mp3_file = find_mp3(tmp_dir, unique_id)
                if mp3_file:
                    response = FileResponse(
                        mp3_file,
                        media_type="audio/mpeg",
                        filename="audio.mp3",
                    )
                    response.headers["Access-Control-Allow-Origin"] = "*"
                    return response

            except yt_dlp.utils.DownloadError as e:
                last_error = str(e)
                cleanup_partial(tmp_dir, unique_id)
                continue

            except Exception as e:
                last_error = str(e)
                cleanup_partial(tmp_dir, unique_id)
                continue

        raise HTTPException(
            status_code=500,
            detail=f"Toate strategiile ({len(strategies)}) au esuat. Ultima eroare: {last_error}"
        )

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "yt_dlp_version": yt_dlp.version.__version__,
        "ffmpeg": FFMPEG_PATH,
        "ffprobe": FFPROBE_PATH,
    }
