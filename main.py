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

    # Pas 1: extrage info cu logging activ ca sa vedem formatele
    with yt_dlp.YoutubeDL({**base_opts, "quiet": False}) as ydl:
        info = ydl.extract_info(url, download=False)

    # Pas 2: loghează TOATE formatele
    all_formats = info.get("formats", [])
    format_log = []
    for f in all_formats:
        format_log.append(
            f"id={f.get('format_id')} "
            f"ext={f.get('ext')} "
            f"acodec={f.get('acodec')} "
            f"vcodec={f.get('vcodec')} "
            f"abr={f.get('abr')} "
            f"tbr={f.get('tbr')} "
            f"note={f.get('format_note', '')}"
        )

    print("=== TIKTOK FORMATE ===")
    for line in format_log:
        print(line)
    print("=== END FORMATE ===")

    # Pas 3: caută formate cu audio - verificare strictă
    audio_only = []
    video_audio = []

    for f in all_formats:
        acodec = f.get("acodec")
        vcodec = f.get("vcodec")
        fmt_id = f.get("format_id", "")

        # Skip dacă acodec e None, "none", gol sau lipsă
        if not acodec or acodec == "none":
            continue

        abr = f.get("abr") or f.get("tbr") or 0

        if not vcodec or vcodec == "none":
            audio_only.append((abr, fmt_id))
        else:
            video_audio.append((abr, fmt_id))

    print(f"Audio-only formats: {audio_only}")
    print(f"Video+Audio formats: {video_audio}")

    if audio_only:
        audio_only.sort(reverse=True)
        chosen = audio_only[0][1]
        print(f"Ales audio-only: {chosen}")
    elif video_audio:
        video_audio.sort(reverse=True)
        chosen = video_audio[0][1]
        print(f"Ales video+audio: {chosen}")
    else:
        raise HTTPException(
            status_code=500,
            detail="TikTok: niciun format cu audio gasit.\nFormate disponibile:\n" + "\n".join(format_log)
        )

    # Pas 4: descarca formatul ales
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

    # Gaseste fisierul descarcat
    downloaded = None
    for f in os.listdir(tmp_dir):
        full = os.path.join(tmp_dir, f)
        if os.path.isfile(full) and f not in ("cookies.txt", "output.mp3"):
            downloaded = full
            break

    if not downloaded:
        raise HTTPException(status_code=500, detail="TikTok: fisierul nu a fost descarcat")

    print(f"Descarcat: {downloaded}, size: {os.path.getsize(downloaded)}")

    # Pas 5: verifica ca fisierul chiar are audio inainte de conversie
    probe_cmd = [str(FFMPEG_PATH), "-i", downloaded]
    probe = subprocess.run(probe_cmd, capture_output=True, timeout=30)
    probe_output = probe.stderr.decode(errors="replace")
    print(f"FFprobe output: {probe_output}")

    if "Audio:" not in probe_output:
        raise HTTPException(
            status_code=500,
            detail=(
                "Fisierul descarcat NU contine audio!\n"
                "Formate disponibile:\n" + "\n".join(format_log) +
                f"\nFormat ales: {chosen}"
                f"\nFFmpeg info: {probe_output[-500:]}"
            )
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
    for f in os.listdir(tmp_dir):
        full = os.path.join(tmp_dir, f)
        if os.path.isfile(full) and f not in ("cookies.txt", "output.mp3"):
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
    print(f"FFmpeg stderr: {stderr[-1000:]}")

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
