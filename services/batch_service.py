"""BatchService for Deepgram prerecorded API integration with speaker diarization."""
import os
import logging
from dataclasses import dataclass
from typing import Optional
from deepgram import Deepgram

logger = logging.getLogger(__name__)


@dataclass
class TranscriptionResult:
    """Result from Deepgram transcription with diagnostic metadata."""
    transcript: str
    duration_seconds: Optional[float] = None
    channels: Optional[int] = None
    words: int = 0


class BatchService:
    """Service for processing audio files with Deepgram's prerecorded API."""
    
    def __init__(self):
        """Initialize with Deepgram API key from environment."""
        api_key = os.getenv("DEEPGRAM_API_KEY")
        if not api_key:
            raise ValueError("DEEPGRAM_API_KEY environment variable is required")
        
        self.client = Deepgram(api_key)
        logger.info("BatchService initialized")
    
    async def transcribe_audio(self, audio_bytes: bytes, mimetype: str) -> str:
        """
        Transcribe audio with diarization and return formatted transcript.

        Args:
            audio_bytes: Raw audio file bytes
            mimetype: MIME type (audio/wav, audio/mpeg, etc.)

        Returns:
            Formatted transcript with speaker labels (SPEAKER_X: text)

        Raises:
            Exception: If Deepgram API call fails
        """
        try:
            logger.info(f"Starting Deepgram transcription, mimetype={mimetype}, size={len(audio_bytes)} bytes")

            # Configure source and options for SDK v2
            source = {
                'buffer': audio_bytes,
                'mimetype': mimetype
            }

            options = {
                'smart_format': True,
                'diarize': True,
                'punctuate': True
            }

            # Call Deepgram API (SDK v2 syntax)
            response = await self.client.transcription.prerecorded(source, options)

            # Log Deepgram response metadata for diagnostics
            self._log_deepgram_metadata(response, source_label="buffer")

            # Format response into SPEAKER_X: text format
            formatted_transcript = self._format_deepgram_response(response)

            return formatted_transcript

        except Exception as e:
            logger.error(f"Deepgram transcription failed: {e}", exc_info=True)
            raise

    async def transcribe_from_url(self, audio_url: str, mimetype: str = "audio/wav") -> TranscriptionResult:
        """
        Transcribe audio from a URL (e.g., presigned S3 URL).

        This method uses Deepgram's URL-based ingestion, which is more efficient
        for large files as Deepgram fetches the file directly from the URL.

        Args:
            audio_url: Publicly accessible URL to the audio file (e.g., presigned S3 URL)
            mimetype: MIME type hint (optional, Deepgram can auto-detect)

        Returns:
            TranscriptionResult with transcript text and Deepgram metadata

        Raises:
            Exception: If Deepgram API call fails
        """
        try:
            logger.info(f"Starting Deepgram URL transcription, mimetype={mimetype}")

            # Configure source as URL for SDK v2
            source = {
                'url': audio_url
            }

            options = {
                'smart_format': True,
                'diarize': True,
                'punctuate': True
            }

            # Call Deepgram API with URL source (SDK v2 syntax)
            response = await self.client.transcription.prerecorded(source, options)

            # Log and extract Deepgram response metadata
            meta = self._log_deepgram_metadata(response, source_label="url")

            # Format response into SPEAKER_X: text format
            formatted_transcript = self._format_deepgram_response(response)

            return TranscriptionResult(
                transcript=formatted_transcript,
                duration_seconds=meta.get("duration_seconds"),
                channels=meta.get("channels"),
                words=meta.get("words", 0),
            )

        except Exception as e:
            logger.error(f"Deepgram URL transcription failed: {e}", exc_info=True)
            raise
    
    def _log_deepgram_metadata(self, response: dict, source_label: str = "unknown") -> dict:
        """Log Deepgram response metadata for diagnostics and return it.

        Extracts duration, channel count, and word count from the response
        so empty-transcript issues can be diagnosed from logs alone.

        Returns:
            Dict with keys: duration_seconds, channels, words
        """
        meta = {"duration_seconds": None, "channels": None, "words": 0}
        try:
            metadata = response.get("metadata", {})
            duration = metadata.get("duration")
            channels = response.get("results", {}).get("channels", [])
            num_channels = len(channels)
            num_words = 0
            transcript_preview = ""
            if channels:
                alt = channels[0].get("alternatives", [{}])[0]
                words = alt.get("words", [])
                num_words = len(words)
                transcript_preview = alt.get("transcript", "")[:80]

            meta = {
                "duration_seconds": duration,
                "channels": num_channels,
                "words": num_words,
            }

            logger.info(
                f"Deepgram response ({source_label}): "
                f"duration={duration}s, channels={num_channels}, "
                f"words={num_words}, preview={transcript_preview!r}"
            )
        except Exception as e:
            logger.warning(f"Failed to log Deepgram metadata: {e}")
        return meta

    def _format_deepgram_response(self, response: dict) -> str:
        """
        Parse Deepgram response and format as SPEAKER_X: text.
        
        This is the critical "glue" logic that converts Deepgram's word-level
        speaker labels into the RoboScribe format that the cleaning prompt expects.
        
        Args:
            response: Deepgram API response dictionary
            
        Returns:
            Formatted transcript with one line per speaker turn
        """
        try:
            # Navigate to words array
            words = response['results']['channels'][0]['alternatives'][0]['words']
            
            if not words:
                logger.warning("No words found in Deepgram response")
                return ""
            
            lines = []
            current_speaker: Optional[int] = None
            current_line_words = []
            
            for word_obj in words:
                # Get speaker ID (may be missing)
                speaker = word_obj.get('speaker')
                
                # Use punctuated_word if available (from smart_format), otherwise fall back to word
                word_text = word_obj.get('punctuated_word') or word_obj.get('word', '')
                
                if not word_text:
                    continue
                
                # Handle missing speaker information
                if speaker is None:
                    # If we have a current speaker, continue with them
                    # Otherwise, use UNKNOWN
                    if current_speaker is None:
                        speaker = 'UNKNOWN'
                    else:
                        speaker = current_speaker
                
                # Check if speaker changed
                if speaker != current_speaker:
                    # Save previous line if it exists
                    if current_line_words:
                        line_text = ' '.join(current_line_words)
                        speaker_label = f"SPEAKER_{current_speaker}" if current_speaker != 'UNKNOWN' else "SPEAKER_UNKNOWN"
                        lines.append(f"{speaker_label}: {line_text}")
                    
                    # Start new line
                    current_speaker = speaker
                    current_line_words = [word_text]
                else:
                    # Same speaker, append word
                    current_line_words.append(word_text)
            
            # Don't forget the last line
            if current_line_words:
                speaker_label = f"SPEAKER_{current_speaker}" if current_speaker != 'UNKNOWN' else "SPEAKER_UNKNOWN"
                line_text = ' '.join(current_line_words)
                lines.append(f"{speaker_label}: {line_text}")
            
            formatted_transcript = '\n'.join(lines)
            logger.info(f"Formatted transcript: {len(lines)} speaker turns")
            
            return formatted_transcript
            
        except (KeyError, IndexError) as e:
            logger.error(f"Failed to parse Deepgram response: {e}", exc_info=True)
            raise ValueError(f"Invalid Deepgram response structure: {e}")
    
    def _get_mimetype_from_extension(self, filename: str) -> str:
        """
        Map file extension to MIME type.
        
        Args:
            filename: File name with extension
            
        Returns:
            MIME type string
        """
        extension = filename.lower().split('.')[-1]
        
        mime_map = {
            'wav': 'audio/wav',
            'mp3': 'audio/mpeg',
            'flac': 'audio/flac',
            'm4a': 'audio/mp4',
            'webm': 'audio/webm',
            'mp4': 'audio/mp4'
        }
        
        return mime_map.get(extension, 'audio/wav')
