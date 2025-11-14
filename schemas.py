"""
Database Schemas

Define your MongoDB collection schemas here using Pydantic models.
These schemas are used for data validation in your application.

Each Pydantic model represents a collection in your database.
Model name is converted to lowercase for the collection name:
- User -> "user" collection
- Product -> "product" collection
- BlogPost -> "blogs" collection
"""

from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any

# Example schemas (you can keep these for reference):

class User(BaseModel):
    name: str = Field(..., description="Full name")
    email: str = Field(..., description="Email address")
    address: str = Field(..., description="Address")
    age: Optional[int] = Field(None, ge=0, le=120, description="Age in years")
    is_active: bool = Field(True, description="Whether user is active")

class Product(BaseModel):
    title: str = Field(..., description="Product title")
    description: Optional[str] = Field(None, description="Product description")
    price: float = Field(..., ge=0, description="Price in dollars")
    category: str = Field(..., description="Product category")
    in_stock: bool = Field(True, description="Whether product is in stock")

# App-specific schemas

class SpeakerSegment(BaseModel):
    speaker: Optional[str] = Field(None, description="Speaker label like Speaker A")
    text: str = Field(..., description="Spoken text for the segment")
    start: Optional[float] = Field(None, description="Start time in seconds")
    end: Optional[float] = Field(None, description="End time in seconds")

class Meeting(BaseModel):
    title: Optional[str] = Field(None, description="Human friendly title for the meeting")
    source: str = Field("upload", description="Where the audio/video came from (upload/recording)")
    transcript_id: Optional[str] = Field(None, description="Provider transcript ID (e.g., AssemblyAI)")
    provider: Optional[str] = Field("assemblyai", description="Transcription provider")
    status: str = Field("queued", description="processing|completed|error|queued")
    language: Optional[str] = Field(None, description="Detected language code")
    transcript: Optional[str] = Field(None, description="Full transcript text")
    summary: Optional[str] = Field(None, description="Auto-generated meeting notes/summary")
    speakers: Optional[List[SpeakerSegment]] = None
    raw_provider_response: Optional[Dict[str, Any]] = None
