"""Google Cloud Storage helpers. Falls back to local filesystem if GCS_BUCKET is not set."""
import os, uuid

BUCKET = os.environ.get("GCS_BUCKET", "")
_gcs = None

def enabled() -> bool:
    return bool(BUCKET)

def _client():
    global _gcs
    if _gcs is None:
        from google.cloud import storage
        _gcs = storage.Client()
    return _gcs

def upload_video(local_path: str) -> str:
    """Upload video to GCS. Returns gs:// URI for Vertex AI to fetch internally."""
    blob_name = f"videos/{uuid.uuid4().hex[:8]}_{os.path.basename(local_path)}"
    blob = _client().bucket(BUCKET).blob(blob_name)
    blob.upload_from_filename(local_path, timeout=120)
    return f"gs://{BUCKET}/{blob_name}"

def save_evidence(data: bytes, filename: str) -> str:
    """Save evidence image locally (instant) and upload to GCS in background (non-blocking)."""
    path = f"data/evidence/{filename}"
    os.makedirs("data/evidence", exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)
    if enabled():
        import threading
        def _upload():
            try:
                blob = _client().bucket(BUCKET).blob(f"evidence/{filename}")
                blob.upload_from_string(data, content_type="image/png")
            except Exception:
                pass
        threading.Thread(target=_upload, daemon=True).start()
    return path

def img_ok(path: str) -> bool:
    """True if path is a URL or an existing local file."""
    return bool(path and (path.startswith("http") or os.path.exists(path)))
