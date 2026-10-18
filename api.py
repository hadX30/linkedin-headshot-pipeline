"""HTTP API for running the headshot pipeline from a web application.

Run locally with: uvicorn api:app --host 0.0.0.0 --port 8000
"""

from pathlib import Path
from uuid import UUID, uuid4

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse

from config import OUTPUT_DIR
from pipeline import run_pipeline

app = FastAPI(
    title="LinkedIn Headshot Pipeline API",
    description="Upload a portrait and run the complete analysis, generation, and validation pipeline.",
    version="1.0.0",
)

ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}


@app.get("/health")
def health() -> dict:
    """Basic readiness check."""
    return {"status": "ok"}


@app.post("/v1/headshots", status_code=201)
def create_headshot(image: UploadFile = File(...)) -> dict:
    """Run the entire pipeline for an uploaded image and return its result."""
    suffix = Path(image.filename or "").suffix.lower()
    if suffix not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=415,
            detail="Unsupported image type. Upload a JPEG, PNG, or WebP image.",
        )

    job_id = uuid4()
    job_dir = Path(OUTPUT_DIR) / str(job_id)
    job_dir.mkdir(parents=True, exist_ok=False)
    input_path = job_dir / f"input{suffix}"

    try:
        with input_path.open("wb") as destination:
            while chunk := image.file.read(1024 * 1024):
                destination.write(chunk)
    finally:
        image.file.close()

    result = run_pipeline(
        image_path=str(input_path),
        output_dir=str(job_dir),
        verbose=False,
    )

    generated_path = result.get("generated_image")
    image_url = None
    if generated_path and Path(generated_path).is_file():
        image_url = f"/v1/headshots/{job_id}/image"

    # Return an application-usable URL instead of exposing a server filesystem path.
    result["generated_image"] = image_url
    response = {"job_id": str(job_id), **result}
    if result.get("status") == "error":
        raise HTTPException(status_code=500, detail=response)
    return response


@app.get("/v1/headshots/{job_id}/image")
def get_headshot_image(job_id: str) -> FileResponse:
    """Retrieve the generated image for a pipeline run."""
    try:
        safe_job_id = str(UUID(job_id))
    except ValueError:
        raise HTTPException(status_code=404, detail="Headshot not found") from None

    job_dir = Path(OUTPUT_DIR) / safe_job_id
    candidates = sorted(job_dir.glob("*_headshot_v*.png"))
    if not candidates:
        raise HTTPException(status_code=404, detail="Generated headshot not found")

    # Return the latest attempt, including the last output when manual review is needed.
    image_path = candidates[-1]
    return FileResponse(
        image_path,
        media_type="image/png",
        filename="headshot.png",
    )
