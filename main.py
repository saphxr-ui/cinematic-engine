import os
import uuid
import json
import random
import asyncio
import httpx
import subprocess
import tempfile
from pathlib import Path
from fastapi import FastAPI, BackgroundTasks, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

app = FastAPI(title="Cinematic Engine")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

PEXELS_API_KEY = os.environ.get("PEXELS_API_KEY", "")
OUTPUT_DIR = Path("/tmp/cinematic_outputs")
OUTPUT_DIR.mkdir(exist_ok=True)

# Cinematic search queries for beautiful locations
CINEMATIC_QUERIES = [
    "tokyo night street rain",
    "new york city skyline sunset",
    "paris city lights evening",
    "london fog bridge",
    "venice canal morning",
    "kyoto bamboo forest",
    "santorini sunset ocean",
    "nordic mountains snow",
    "dubai skyscrapers night",
    "amsterdam canal rain",
    "hong kong city night",
    "swiss alps mountains fog",
    "bali rice terraces sunrise",
    "prague old town evening",
    "icelandic waterfall nature",
]

# Lofi music URLs from Pixabay (royalty-free, direct mp3 links)
LOFI_TRACKS = [
    "https://cdn.pixabay.com/download/audio/2022/05/27/audio_1808fbf07a.mp3",
    "https://cdn.pixabay.com/download/audio/2022/03/10/audio_270f49c0e3.mp3",
    "https://cdn.pixabay.com/download/audio/2021/11/25/audio_91b32e02df.mp3",
    "https://cdn.pixabay.com/download/audio/2022/01/18/audio_d0c6ff1bab.mp3",
    "https://cdn.pixabay.com/download/audio/2022/10/25/audio_946f989e43.mp3",
]

class GenerateRequest(BaseModel):
    query: str | None = None
    duration: int = 30  # seconds, max 60

JOBS_DIR = OUTPUT_DIR / "jobs"
JOBS_DIR.mkdir(exist_ok=True)

def save_job(job_id: str, data: dict):
    (JOBS_DIR / f"{job_id}.json").write_text(json.dumps(data))

def load_job(job_id: str) -> dict | None:
    p = JOBS_DIR / f"{job_id}.json"
    if p.exists():
        return json.loads(p.read_text())
    return None

async def fetch_pexels_video(query: str) -> str | None:
    """Fetch a random cinematic video from Pexels."""
    headers = {"Authorization": PEXELS_API_KEY}
    url = f"https://api.pexels.com/videos/search?query={query}&per_page=15&orientation=landscape&size=medium"
    
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(url, headers=headers)
        if resp.status_code != 200:
            return None
        data = resp.json()
        videos = data.get("videos", [])
        if not videos:
            return None
        
        # Pick a random video, prefer HD
        video = random.choice(videos)
        files = video.get("video_files", [])
        
        # Sort by quality preference
        hd_files = [f for f in files if f.get("quality") in ("hd", "sd") and f.get("width", 0) >= 1080]
        chosen = hd_files[0] if hd_files else files[0]
        return chosen.get("link")

async def download_file(url: str, dest: Path) -> bool:
    """Download a file from URL."""
    async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
        resp = await client.get(url)
        if resp.status_code == 200:
            dest.write_bytes(resp.content)
            return True
    return False

def apply_cinematic_grade(input_video: Path, input_audio: Path, output: Path, duration: int):
    """Apply cinematic color grading with FFmpeg."""
    
    # Cinematic filter chain:
    # - Scale to 9:16 (Shorts format) with cropping
    # - Teal & Orange color grade via curves
    # - Subtle vignette
    # - Film grain
    # - Slight contrast boost
    
    video_filter = (
        # Scale and crop to 1080x1920 (9:16 vertical for Shorts)
        "scale=1920:1080,crop=607:1080:656:0,"  # crop to 9:16 from landscape
        # Teal & Orange grade: lift shadows to teal, push highlights to orange
        "curves=r='0/0 0.5/0.55 1/1':g='0/0 0.5/0.48 1/0.95':b='0/0.05 0.5/0.52 1/0.85',"
        # Slight saturation boost
        "eq=saturation=1.15:contrast=1.08:brightness=-0.02,"
        # Vignette effect
        "vignette=PI/4,"
        # Film grain noise
        "noise=alls=8:allf=t+u"
    )
    
    cmd = [
        "ffmpeg", "-y",
        "-i", str(input_video),
        "-i", str(input_audio),
        "-t", str(duration),
        "-vf", video_filter,
        "-af", f"afade=t=in:st=0:d=2,afade=t=out:st={duration-3}:d=3,volume=0.4",
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "23",
        "-c:a", "aac",
        "-b:a", "128k",
        "-shortest",
        "-movflags", "+faststart",
        str(output)
    ]
    
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if result.returncode != 0:
        raise RuntimeError(f"FFmpeg error: {result.stderr[-500:]}")

async def generate_video_job(job_id: str, query: str, duration: int):
    """Background job: fetch, grade, and produce the video."""
    job_dir = OUTPUT_DIR / job_id
    job_dir.mkdir(exist_ok=True)
    
    try:
        job = load_job(job_id)
        job["status"] = "fetching_video"
        save_job(job_id, job)
        
        # Get video URL from Pexels
        video_url = await fetch_pexels_video(query)
        if not video_url:
            raise RuntimeError("No video found for query")
        
        # Download video
        raw_video = job_dir / "raw_video.mp4"
        ok = await download_file(video_url, raw_video)
        if not ok:
            raise RuntimeError("Failed to download video")
        
        job["status"] = "fetching_audio"
        save_job(job_id, job)
        
        # Download random lofi track
        audio_url = random.choice(LOFI_TRACKS)
        raw_audio = job_dir / "lofi.mp3"
        ok = await download_file(audio_url, raw_audio)
        if not ok:
            subprocess.run([
                "ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
                "-t", str(duration), str(raw_audio)
            ], capture_output=True)
        
        job["status"] = "rendering"
        save_job(job_id, job)
        
        # Apply cinematic grade
        output_file = job_dir / "cinematic.mp4"
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(
            None, apply_cinematic_grade, raw_video, raw_audio, output_file, duration
        )
        
        job["status"] = "done"
        job["file"] = str(output_file)
        job["filename"] = f"cinematic_{query.replace(' ', '_')}.mp4"
        save_job(job_id, job)
        
    except Exception as e:
        job = load_job(job_id) or {}
        job["status"] = "error"
        job["error"] = str(e)
        save_job(job_id, job)

@app.get("/")
async def root():
    return {"status": "Cinematic Engine running 🎬"}

@app.post("/generate")
async def generate(req: GenerateRequest, background_tasks: BackgroundTasks):
    """Start a cinematic video generation job."""
    if not PEXELS_API_KEY:
        raise HTTPException(500, "PEXELS_API_KEY not set")
    
    query = req.query or random.choice(CINEMATIC_QUERIES)
    duration = max(15, min(60, req.duration))
    job_id = str(uuid.uuid4())
    
    job_data = {
        "status": "queued",
        "query": query,
        "duration": duration,
    }
    save_job(job_id, job_data)
    
    background_tasks.add_task(generate_video_job, job_id, query, duration)
    
    return {
        "job_id": job_id,
        "query": query,
        "duration": duration,
        "status_url": f"/status/{job_id}",
    }

@app.get("/status/{job_id}")
async def status(job_id: str):
    job = load_job(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    
    response = {"job_id": job_id, "status": job["status"]}
    if job["status"] == "done":
        response["download_url"] = f"/download/{job_id}"
    elif job["status"] == "error":
        response["error"] = job.get("error", "Unknown error")
    return response

@app.get("/download/{job_id}")
async def download(job_id: str):
    job = load_job(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    if job["status"] != "done":
        raise HTTPException(400, f"Job not ready: {job['status']}")
    
    file_path = Path(job["file"])
    if not file_path.exists():
        raise HTTPException(404, "File not found")
    
    return FileResponse(
        file_path,
        media_type="video/mp4",
        filename=job.get("filename", "cinematic.mp4")
    )

@app.get("/queries")
async def list_queries():
    """List available cinematic queries."""
    return {"queries": CINEMATIC_QUERIES}