"""CleanerService for transforming raw transcripts into polished, structured documents.

This service uses OpenAI's GPT-4o with Structured Outputs to clean transcripts
following the RoboScribe philosophy: edit, don't author.
"""
import os
import logging
from typing import Optional
from openai import AsyncOpenAI
from models.meeting_output import MeetingOutput


logger = logging.getLogger(__name__)


class CleanerService:
    """Service for cleaning and structuring raw transcripts using OpenAI.
    
    Design Philosophy (from RoboScribe):
    1. Editor, Not Author: The LLM cleans existing content without adding new words
    2. Preserve Authenticity: Maintains speaker voice and natural patterns
    3. Improve Readability: Removes filler words, fixes grammar, adds punctuation
    4. Structured Output: Returns summary, action items, and cleaned text
    """
    
    def __init__(self):
        """Initialize the CleanerService with OpenAI client."""
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise ValueError("OPENAI_API_KEY environment variable is required")
        
        self.model = os.getenv("OPENAI_MODEL", "gpt-4o")
        self.client = AsyncOpenAI(api_key=api_key)
        logger.info(f"CleanerService initialized with model: {self.model}")
    
    async def clean_transcript(
        self,
        raw_transcript: str,
        session_id: str
    ) -> MeetingOutput:
        """Clean and structure a raw transcript.
        
        Args:
            raw_transcript: The raw, unprocessed transcript text
            session_id: Session identifier for logging
            
        Returns:
            MeetingOutput with summary, action items, and cleaned transcript
            
        Raises:
            Exception: If OpenAI API call fails (caller should handle gracefully)
        """
        if not raw_transcript or not raw_transcript.strip():
            logger.warning(f"Empty transcript for session {session_id}")
            return MeetingOutput(
                summary="No content to summarize.",
                action_items=[],
                cleaned_transcript=""
            )
        
        logger.info(
            f"Cleaning transcript: session_id={session_id}, "
            f"length={len(raw_transcript)} chars"
        )
        
        try:
            # Use OpenAI's Structured Outputs with response_format
            completion = await self.client.beta.chat.completions.parse(
                model=self.model,
                messages=[
                    {
                        "role": "system",
                        "content": self._get_system_prompt()
                    },
                    {
                        "role": "user",
                        "content": f"Please clean and structure this transcript:\n\n{raw_transcript}"
                    }
                ],
                response_format=MeetingOutput,
                temperature=0.3  # Lower temperature for more consistent editing
            )
            
            result = completion.choices[0].message.parsed
            
            logger.info(
                f"Transcript cleaned successfully: session_id={session_id}, "
                f"summary_length={len(result.summary)}, "
                f"action_items={len(result.action_items)}, "
                f"cleaned_length={len(result.cleaned_transcript)}"
            )
            
            return result
            
        except Exception as e:
            logger.error(
                f"Failed to clean transcript: session_id={session_id}, error={str(e)}",
                exc_info=True
            )
            # Return raw transcript with error message
            return MeetingOutput(
                summary=f"Error processing transcript: {str(e)}",
                action_items=[],
                cleaned_transcript=raw_transcript
            )
    
    def _get_system_prompt(self) -> str:
        """Get the system prompt for transcript cleaning.
        
        This prompt follows the RoboScribe philosophy of editing, not authoring.
        """
        return """You are an expert transcript editor. Your job is to clean and improve transcripts while preserving the speaker's authentic voice and meaning.

**Your Role: Editor, Not Author**
- Clean existing content without adding new words or ideas
- Preserve the speaker's natural voice and patterns
- Maintain authenticity and original meaning

**Cleaning Tasks:**
1. Remove filler words (um, uh, like, you know, etc.)
2. Fix grammar and sentence structure
3. Add proper punctuation and capitalization
4. Remove false starts and repetitions
5. Organize into clear paragraphs

**Output Requirements:**
1. **Summary**: Write a concise 2-3 sentence summary of the main points
2. **Action Items**: Extract any actionable tasks, decisions, or next steps mentioned
3. **Cleaned Transcript**: The polished transcript with improvements applied

**Important Guidelines:**
- Do NOT add information that wasn't in the original
- Do NOT change the meaning or intent
- Do NOT remove important context or details
- DO preserve technical terms and specific names exactly as spoken
- DO maintain the conversational tone where appropriate"""
