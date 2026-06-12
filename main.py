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
        "name": "web_creator",
        "extractor_args": {"youtube": {"player_client": ["web_creator"]}},
        "http_headers": {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
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

# Strategii separate pentru TikTok
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
    {
        "name": "tiktok_android",
        "extractor_args": {},
        "http_headers": {
            "User-Agent": "Mozilla/5.0 (Linux; Android 14; Pixel 9) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.6422.165 Mobile Safari/537.36",
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


def build_ydl_opts(output_template: str, strategy: dict, cookies_file: str = None) -> dict:
    opts = {
        "format": "bestaudio/best",
        "outtmpl": output_template,
        "postprocessors": [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": "192",
        }],
        "postprocessor_args": {
            "FFmpegExtractAudio": ["-vn", "-acodec", "libmp3lame", "-q:a", "2"]
        },
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "noplaylist": True,
        "http_headers": strategy["http_headers"],
        "socket_timeout": 30,
        "retries": 2,
        "fragment_retries": 2,
        "ignoreerrors": False,
    }

    if strategy.get("extractor_args"):
        opts["extractor_args"] = strategy["extractor_args"]

    if cookies_file:
        opts["cookiefile"] = cookies_file

    return opts


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

        # Alege strategiile în funcție de platformă
        strategies = TIKTOK_STRATEGIES if is_tiktok(request.url) else YOUTUBE_STRATEGIES

        for strategy in strategies:
            unique_id = str(uuid.uuid4())
            output_template = os.path.join(tmp_dir, f"{unique_id}.%(ext)s")

            try:
                ydl_opts = build_ydl_opts(output_template, strategy, cookies_file)

                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    ydl.download([request.url])

                mp3_file = None
                for f in os.listdir(tmp_dir):
                    if f.startswith(unique_id) and f.endswith(".mp3"):
                        mp3_file = os.path.join(tmp_dir, f)
                        break

                if mp3_file and os.path.exists(mp3_file) and os.path.getsize(mp3_file) > 0:
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
                for f in os.listdir(tmp_dir):
                    if f.startswith(unique_id) and f != "cookies.txt":
                        try:
                            os.remove(os.path.join(tmp_dir, f))
                        except Exception:
                            pass
                continue

            except Exception as e:
                last_error = str(e)
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
