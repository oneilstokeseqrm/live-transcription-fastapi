"""CleanedChunk model for batch transcript cleaning."""
from pydantic import BaseModel, Field


class CleanedChunk(BaseModel):
    """Structured output for a single cleaned transcript chunk.
    
    This model is used with OpenAI's Structured Outputs feature
    to ensure reliable parsing of LLM responses.
    """
    cleaned_text: str = Field(
        description="The cleaned transcript chunk with filler words removed, "
                    "grammar fixed, punctuation added, and speaker labels preserved"
    )
