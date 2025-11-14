import os
from typing import List, Optional
from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import requests
from database import db, create_document, get_documents
from schemas import Meeting, SpeakerSegment
from bson import ObjectId

ASSEMBLYAI_API_KEY = os.getenv("ASSEMBLYAI_API_KEY")
ASSEMBLYAI_BASE = "https://api.assemblyai.com/v2"

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class MeetingCreateResponse(BaseModel):
    id: str
    status: str


def _aai_headers():
    if not ASSEMBLYAI_API_KEY:
        raise HTTPException(status_code=500, detail="ASSEMBLYAI_API_KEY not set in environment")
    return {"authorization": ASSEMBLYAI_API_KEY}


def upload_to_assemblyai(file_bytes: bytes) -> str:
    """Upload binary file to AssemblyAI and return upload_url"""
    url = f"{ASSEMBLYAI_BASE}/upload"
    headers = _aai_headers()
    # streaming upload in chunks
    def data_gen():
        chunk_size = 5 * 1024 * 1024
        for i in range(0, len(file_bytes), chunk_size):
            yield file_bytes[i : i + chunk_size]
    resp = requests.post(url, headers=headers, data=data_gen())
    if resp.status_code >= 400:
        raise HTTPException(status_code=502, detail=f"AssemblyAI upload error: {resp.text}")
    upload_url = resp.json().get("upload_url")
    if not upload_url:
        raise HTTPException(status_code=502, detail="Failed to obtain upload_url from AssemblyAI")
    return upload_url


def create_transcript(upload_url: str) -> dict:
    url = f"{ASSEMBLYAI_BASE}/transcript"
    headers = {**_aai_headers(), "content-type": "application/json"}
    payload = {
        "audio_url": upload_url,
        "speaker_labels": True,
        "auto_highlights": True,
        "entity_detection": True,
        "language_detection": True,
        "summarization": True,
        "summary_type": "bullets",
        "summary_model": "conversational",
        "punctuate": True,
        "format_text": True,
        "disfluencies": False,
    }
    resp = requests.post(url, headers=headers, json=payload)
    if resp.status_code >= 400:
        raise HTTPException(status_code=502, detail=f"AssemblyAI transcript error: {resp.text}")
    return resp.json()


def fetch_transcript(transcript_id: str) -> dict:
    url = f"{ASSEMBLYAI_BASE}/transcript/{transcript_id}"
    headers = _aai_headers()
    resp = requests.get(url, headers=headers)
    if resp.status_code >= 400:
        raise HTTPException(status_code=502, detail=f"AssemblyAI fetch error: {resp.text}")
    return resp.json()


def to_speaker_segments(provider_json: dict) -> List[dict]:
    segments = []
    utterances = provider_json.get("utterances") or []
    for utt in utterances:
        segments.append(
            SpeakerSegment(
                speaker=utt.get("speaker"),
                text=utt.get("text", ""),
                start=(utt.get("start") or 0) / 1000.0 if isinstance(utt.get("start"), (int, float)) else None,
                end=(utt.get("end") or 0) / 1000.0 if isinstance(utt.get("end"), (int, float)) else None,
            ).model_dump()
        )
    if not segments:
        # fallback: single segment with full text
        text = provider_json.get("text", "")
        if text:
            segments.append(SpeakerSegment(speaker="Speaker 1", text=text).model_dump())
    return segments


@app.get("/")
def read_root():
    return {"message": "Meeting Recorder API is running"}


@app.get("/test")
def test_database():
    response = {
        "backend": "✅ Running",
        "database": "❌ Not Available",
        "database_url": None,
        "database_name": None,
        "connection_status": "Not Connected",
        "collections": [],
    }
    try:
        if db is not None:
            response["database"] = "✅ Available"
            response["database_url"] = "✅ Configured"
            response["database_name"] = db.name if hasattr(db, "name") else "✅ Connected"
            response["connection_status"] = "Connected"
            try:
                collections = db.list_collection_names()
                response["collections"] = collections[:10]
                response["database"] = "✅ Connected & Working"
            except Exception as e:
                response["database"] = f"⚠️  Connected but Error: {str(e)[:50]}"
        else:
            response["database"] = "⚠️  Available but not initialized"
    except Exception as e:
        response["database"] = f"❌ Error: {str(e)[:50]}"

    response["database_url"] = "✅ Set" if os.getenv("DATABASE_URL") else "❌ Not Set"
    response["database_name"] = "✅ Set" if os.getenv("DATABASE_NAME") else "❌ Not Set"
    return response


@app.post("/api/meetings/upload", response_model=MeetingCreateResponse)
async def upload_meeting(file: UploadFile = File(...)):
    """Accept audio/video file, forward to AssemblyAI, create transcription job, and store meeting record."""
    if file.content_type not in (
        "audio/mpeg",
        "audio/wav",
        "audio/x-wav",
        "audio/webm",
        "video/webm",
        "video/mp4",
        "audio/mp4",
        "audio/ogg",
        "video/quicktime",
    ):
        # allow anyway; provider accepts many
        pass

    file_bytes = await file.read()
    if len(file_bytes) == 0:
        raise HTTPException(status_code=400, detail="Empty file uploaded")

    upload_url = upload_to_assemblyai(file_bytes)
    transcript = create_transcript(upload_url)

    meeting_doc = Meeting(
        title=file.filename,
        source="upload",
        transcript_id=transcript.get("id"),
        provider="assemblyai",
        status=transcript.get("status", "processing"),
    )
    inserted_id = create_document("meeting", meeting_doc)

    return MeetingCreateResponse(id=inserted_id, status=meeting_doc.status)


@app.get("/api/meetings")
def list_meetings(limit: int = 20):
    docs = get_documents("meeting", {}, limit)
    def to_public(d):
        d["id"] = str(d.pop("_id"))
        return d
    return [to_public(d) for d in docs]


@app.get("/api/meetings/{meeting_id}")
def get_meeting(meeting_id: str):
    doc = db["meeting"].find_one({"_id": ObjectId(meeting_id)})
    if not doc:
        raise HTTPException(status_code=404, detail="Meeting not found")

    # If not completed, try to refresh from provider
    status = doc.get("status")
    transcript_id = doc.get("transcript_id")
    if transcript_id and status not in ("completed", "error"):
        provider_json = fetch_transcript(transcript_id)
        new_status = provider_json.get("status", status)
        update_fields = {"status": new_status, "raw_provider_response": provider_json}
        if new_status == "completed":
            update_fields["transcript"] = provider_json.get("text")
            update_fields["language"] = provider_json.get("language_code") or provider_json.get("language")
            update_fields["speakers"] = to_speaker_segments(provider_json)
            # summarization result can be in "summary" or "summaries" depending on API
            summary = provider_json.get("summary")
            if not summary and isinstance(provider_json.get("summaries"), list) and provider_json["summaries"]:
                summary = provider_json["summaries"][0].get("summary")
            update_fields["summary"] = summary
        db["meeting"].update_one({"_id": ObjectId(meeting_id)}, {"$set": update_fields})
        doc.update(update_fields)

    doc["id"] = str(doc.pop("_id"))
    return doc


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
