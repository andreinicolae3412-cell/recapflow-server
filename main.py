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
    TikTok are video+audio combinate in acelasi fisier mp4.
    Lasam yt-dlp sa aleaga cel mai bun format disponibil,
    dar ii spunem explicit sa nu faca merge (e deja combinat).
    Apoi extragem audio cu ffmpeg.
    """
    headers = {
        "User-Agent": "Mozilla/5.0 (Linux; Android 14; Pixel 9) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.6422.165 Mobile Safari/537.36",
        "Referer": "https://www.tiktok.com/",
    }

    # Extragem URL-ul direct al fisierului video+audio
    info_opts = {
        "quiet": True,
        "no_warnings": True,
        "socket_timeout": 30,
        "http_headers": headers,
    }
    if cookies_file:
        info_opts["cookiefile"] = cookies_file

    with yt_dlp.YoutubeDL(info_opts) as ydl:
        info = ydl.extract_info(url, download=False)

    all_formats = info.get("formats", [])

    # Alegem h264 540p sau 720p - sunt stabile si au audio inclus
    # Evitam bytevc1 (h265) pentru compatibilitate ffmpeg
    preferred_ids = [
        "h264_720p_872067-0",
        "h264_720p_872067-1",
        "h264_540p_331703-0",
        "h264_540p_331703-1",
    ]

    chosen_fmt = None
    chosen_url = None

    # Incearca formatele preferate mai intai
    fmt_by_id = {f.get("format_id"): f for f in all_formats}
    for pid in preferred_ids:
        if pid in fmt_by_id:
            f = fmt_by_id[pid]
            if f.get("url"):
                chosen_fmt = pid
                chosen_url = f["url"]
                print(f"Ales format preferat: {chosen_fmt}")
                break

    # Fallback: orice format h264 cu url direct
    if not chosen_url:
        for f in all_formats:
            vcodec = f.get("vcodec", "")
            if "h264" in vcodec and f.get("url"):
                chosen_fmt = f.get("format_id")
                chosen_url = f["url"]
                print(f"Ales format h264 fallback: {chosen_fmt}")
                break

    # Fallback final: orice format cu url direct
    if not chosen_url:
        for f in all_formats:
            if f.get("url") and f.get("format_id") != "download":
                chosen_fmt = f.get("format_id")
                chosen_url = f["url"]
                print(f"Ales format final fallback: {chosen_fmt}")
                break

    if not chosen_url:
        raise HTTPException(
            status_code=500,
            detail="TikTok: nu s-a putut obtine URL direct pentru niciun format"
        )

    # Descarcam direct cu ffmpeg din URL-ul raw
    # Aceasta metoda garanteaza ca extragem audio corect
    mp3_output = os.path.join(tmp_dir, "output.mp3")

    print(f"Descarcam audio direct cu ffmpeg din format: {chosen_fmt}")
    print(f"URL: {chosen_url[:80]}...")

    cmd = [
        str(FFMPEG_PATH),
        "-headers", f"Referer: https://www.tiktok.com/\r\nUser-Agent: {headers['User-Agent']}\r\n",
        "-i", chosen_url,
        "-vn",           # skip video
        "-ar", "44100",
        "-ac", "2",
        "-b:a", "192k",
        "-f", "mp3",
        mp3_output,
        "-y"
    ]

    result = subprocess.run(cmd, capture_output=True, timeout=180)
    stderr = result.stderr.decode(errors="replace")

    print(f"FFmpeg TikTok returncode: {result.returncode}")
    if result.returncode != 0:
        print(f"FFmpeg stderr: {stderr[-1000:]}")

    if result.returncode != 0 or not os.path.exists(mp3_output) or os.path.getsize(mp3_output) == 0:
        raise HTTPException(
            status_code=500,
            detail=f"TikTok conversie esuata:\n{stderr[-2000:]}"
        )

    return mp3_output


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
    print(f"FFmpeg stderr: {stderr[-500:]}")

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
            # TikTok: returneaza direct mp3_output (ffmpeg descarca si converteste)
            final_mp3 = download_tiktok_audio(request.url, tmp_dir, cookies_file)
        else:
            # YouTube: descarcam fisier, apoi convertim
            downloaded = download_youtube_audio(request.url, tmp_dir, cookies_file)
            convert_to_mp3(downloaded, mp3_output)
            final_mp3 = mp3_output

        response = FileResponse(
            final_mp3,
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
