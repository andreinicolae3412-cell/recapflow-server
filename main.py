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
    TikTok: toate formatele sunt video+audio combinate in mp4.
    Alegem h264 (mai compatibil decat h265) la calitate medie,
    descarcam cu yt-dlp (el gestioneaza token-urile),
    apoi extragem audio cu ffmpeg local.
    """
    headers = {
        "User-Agent": "Mozilla/5.0 (Linux; Android 14; Pixel 9) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.6422.165 Mobile Safari/537.36",
        "Referer": "https://www.tiktok.com/",
    }

    base_opts = {
        "quiet": False,
        "no_warnings": True,
        "socket_timeout": 30,
        "http_headers": headers,
    }
    if cookies_file:
        base_opts["cookiefile"] = cookies_file

    # Pas 1: inspectam formatele disponibile
    with yt_dlp.YoutubeDL({**base_opts, "quiet": True}) as ydl:
        info = ydl.extract_info(url, download=False)

    all_formats = info.get("formats", [])

    # Pas 2: alegem cel mai bun format h264 (garantat video+audio)
    # Evitam h265/bytevc1 care pot fi video-only pe unele servere
    # Evitam formatul "download" (watermarked)
    h264_formats = []
    any_formats = []

    for f in all_formats:
        fmt_id = f.get("format_id", "")
        vcodec = f.get("vcodec", "") or ""
        acodec = f.get("acodec", "") or ""
        tbr = f.get("tbr") or 0

        if fmt_id == "download":
            continue  # skip watermarked

        if "h264" in vcodec and acodec != "none" and acodec:
            h264_formats.append((tbr, fmt_id))

        if acodec != "none" and acodec:
            any_formats.append((tbr, fmt_id))

    if h264_formats:
        h264_formats.sort(reverse=True)
        # Alegem calitate medie (nu cea mai mare) pentru viteza
        # daca avem mai mult de 2 formate, luam al doilea
        idx = min(1, len(h264_formats) - 1)
        chosen = h264_formats[idx][1]
        print(f"Ales h264 format: {chosen} (din {h264_formats})")
    elif any_formats:
        any_formats.sort(reverse=True)
        idx = min(1, len(any_formats) - 1)
        chosen = any_formats[idx][1]
        print(f"Ales format fallback: {chosen} (din {any_formats})")
    else:
        fmt_debug = [
            f"id={f.get('format_id')} acodec={f.get('acodec')} vcodec={f.get('vcodec')}"
            for f in all_formats
        ]
        raise HTTPException(
            status_code=500,
            detail="TikTok: niciun format cu audio.\n" + "\n".join(fmt_debug)
        )

    # Pas 3: descarcam cu yt-dlp (el gestioneaza token-urile si headerele)
    raw_path = os.path.join(tmp_dir, "tiktok_raw")
    dl_opts = {
        **base_opts,
        "format": chosen,
        "outtmpl": raw_path,
        "retries": 5,
        "noplaylist": True,
        "quiet": False,
    }

    with yt_dlp.YoutubeDL(dl_opts) as ydl:
        ydl.download([url])

    # Gasim fisierul descarcat
    downloaded = None
    for fname in os.listdir(tmp_dir):
        full = os.path.join(tmp_dir, fname)
        if os.path.isfile(full) and fname not in ("cookies.txt", "output.mp3"):
            downloaded = full
            break

    if not downloaded:
        raise HTTPException(status_code=500, detail="TikTok: fisierul nu a fost descarcat")

    print(f"Descarcat: {downloaded}, size: {os.path.getsize(downloaded)} bytes")

    # Pas 4: verificam ca fisierul are stream audio
    probe = subprocess.run(
        [str(FFMPEG_PATH), "-i", downloaded],
        capture_output=True, timeout=30
    )
    probe_out = probe.stderr.decode(errors="replace")

    print(f"Probe streams: {'Audio' in probe_out}")

    if "Audio:" not in probe_out:
        # Incercam alt format - cel mai mic h264 disponibil
        if len(h264_formats) > 1:
            fallback = h264_formats[-1][1]  # cel mai mic bitrate
            print(f"Audio absent, incercam fallback: {fallback}")
            dl_opts["format"] = fallback

            # Stergem fisierul descarcat anterior
            os.remove(downloaded)

            with yt_dlp.YoutubeDL(dl_opts) as ydl:
                ydl.download([url])

            for fname in os.listdir(tmp_dir):
                full = os.path.join(tmp_dir, fname)
                if os.path.isfile(full) and fname not in ("cookies.txt", "output.mp3"):
                    downloaded = full
                    break

            probe2 = subprocess.run(
                [str(FFMPEG_PATH), "-i", downloaded],
                capture_output=True, timeout=30
            )
            if "Audio:" not in probe2.stderr.decode(errors="replace"):
                raise HTTPException(
                    status_code=500,
                    detail=f"TikTok: niciun format nu contine audio. Probe: {probe_out[-500:]}"
                )
        else:
            raise HTTPException(
                status_code=500,
                detail=f"TikTok: fisierul descarcat nu contine audio.\nProbe: {probe_out[-500:]}"
            )

    return downloaded


def download_youtube_audio(url: str, tmp_dir: str, cookies_file: str | None) -> str:
    raw_path = os.path.join(tmp_dir, "yt_raw")

    ydl_opts = {
        "format": "bestaudio[ext=m4a]/bestaudio[ext=webm]/bestaudio/best",
        "outtmpl": raw_path,
        "quiet": True,
        "no_warnings": True,
        "socket_timeout": 30,
        "retries": 5,
        "noplaylist": True,
        "extractor_args": {
            "youtube": {
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
    for fname in os.listdir(tmp_dir):
        full = os.path.join(tmp_dir, fname)
        if os.path.isfile(full) and fname not in ("cookies.txt", "output.mp3"):
            downloaded = full
            break

    if not downloaded:
        raise HTTPException(status_code=500, detail="YouTube: fisierul nu a fost descarcat")

    return downloaded


def convert_to_mp3(input_path: str, output_path: str):
    cmd = [
        str(FFMPEG_PATH),
        "-i", input_path,
        "-vn",
        "-ar", "44100",
        "-ac", "2",
        "-b:a", "192k",
        "-f", "mp3",
        output_path,
        "-y"
    ]
    result = subprocess.run(cmd, capture_output=True, timeout=120)
    stderr = result.stderr.decode(errors="replace")

    print(f"FFmpeg returncode: {result.returncode}")

    if result.returncode != 0 or not os.path.exists(output_path) or os.path.getsize(output_path) == 0:
        raise HTTPException(
            status_code=500,
            detail=f"Conversie ffmpeg esuata:\n{stderr[-2000:]}"
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
