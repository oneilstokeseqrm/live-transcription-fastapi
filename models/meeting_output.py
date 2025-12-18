"""MeetingOutput model for structured transcript cleaning results."""
from pydantic import BaseModel, Field
from typing import List


class MeetingOutput(BaseModel):
    """Structured output from transcript cleaning process.
    
    This model is used with OpenAI's Structured Outputs feature
    to ensure reliable parsing of LLM responses.
    """
    summary: str = Field(
        description="A concise summary of the meeting or conversation (2-3 sentences)"
    )
    action_items: List[str] = Field(
        description="List of actionable tasks extracted from the conversation"
    )
    cleaned_transcript: str = Field(
        description="The cleaned transcript with filler words removed, "
                    "grammar fixed, and punctuation added"
    )
