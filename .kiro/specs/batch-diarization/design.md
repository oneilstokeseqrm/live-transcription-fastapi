# Design Document: Batch Diarization Pipeline

## Overview

The Batch Diarization Pipeline is a parallel processing system that complements the existing real-time WebSocket streaming architecture. It processes pre-recorded audio files through Deepgram's prerecorded API with speaker diarization, then cleans the transcript using OpenAI's GPT-4o with the proven RoboScribe cleaning prompt.

The design prioritizes complete isolation from the streaming pipeline, reuse of proven RoboScribe patterns, and minimal implementation complexity. The system follows a simple request-response model: upload audio → transcribe with diarization → format speaker labels → chunk long turns → clean with LLM → return results.

## Architecture

### High-Level Flow

```
User Browser
    ↓ (Record audio via MediaRecorder)
    ↓ (POST /batch/process with audio Blob)
FastAPI Router (/batch/process)
    ↓ (Validate file)
    ↓ (Pass audio bytes)
BatchService
    ↓ (Call Deepgram prerecorded API)
    ↓ (Parse response, format as SPEAKER_X:)
    ↓ (Return raw transcript)
Text Utilities (split_long_lines)
    ↓ (Chunk long speaker turns)
BatchCleanerService
    ↓ (Process each chunk with OpenAI)
    ↓ (Join cleaned chunks)
    ↓ (Return cleaned transcript)
FastAPI Router
    ↓ (Return JSON response)
User Browser
    ↓ (Display cleaned transcript)
```

### Component Isolation

The batch pipeline is completely isolated from the streaming pipeline:

- **New Files Only**: All batch components are in new files (no modifications to existing streaming code)
- **No Shared State**: Batch processing is stateless and synchronous
- **Separate Router**: Batch endpoints are in a dedicated router module
- **Independent Services**: BatchService and BatchCleanerService are separate from streaming services

### File Structure

```
/
├── main.py                          # Existing streaming app (unchanged)
├── utils/
│   └── text_utils.py               # NEW: RoboScribe text utilities
├── services/
│   ├── event_publisher.py          # Existing streaming service
│   ├── cleaner_service.py          # Existing streaming service
│   ├── batch_service.py            # NEW: Deepgram batch processing
│   └── batch_cleaner_service.py    # NEW: OpenAI batch cleaning
├── routers/
│   └── batch.py                    # NEW: Batch processing endpoints
├── templates/
│   └── index.html                  # MODIFIED: Add batch recording UI
└── scripts/
    └── verify_batch_local.py       # NEW: Verification script
```

## Components and Interfaces

### 1. Text Utilities Module (`utils/text_utils.py`)

Lifted directly from RoboScribe, this module provides functions for splitting long speaker turns into manageable chunks.

**Functions:**

```python
def split_long_lines(segments: List[str], max_words: int = 500) -> List[str]:
    """Split lines that exceed max word limit while preserving speaker labels and sentence boundaries."""
    
def _split_into_sentences(text: str, sentence_endings: List[str]) -> List[str]:
    """Split text into sentences based on common sentence endings."""
    
def _group_sentences(sentences: List[str], max_words: int, speaker_label: str) -> List[str]:
    """Group sentences into chunks that don't exceed max_words."""
```

**Key Behaviors:**
- Preserves speaker labels (e.g., "SPEAKER_0:") at the start of each chunk
- Respects sentence boundaries (periods, question marks, exclamation points)
- Limits chunks to 500 words maximum
- Does not split turns under 500 words

### 2. BatchService (`services/batch_service.py`)

Handles Deepgram prerecorded API calls and formats responses into the RoboScribe speaker label format.

**Interface:**

```python
class BatchService:
    def __init__(self):
        """Initialize with Deepgram API key from environment."""
        
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
        
    def _format_deepgram_response(self, response: dict) -> str:
        """
        Parse Deepgram response and format as SPEAKER_X: text.
        
        Iterates through words array, groups by speaker, returns one line per turn.
        """
        
    def _get_mimetype_from_extension(self, filename: str) -> str:
        """Map file extension to MIME type."""
```

**Deepgram Configuration:**
- `smart_format: True` - Automatic punctuation and capitalization
- `diarize: True` - Speaker identification
- `mimetype: <detected>` - Based on file extension
- `timeout: 120 seconds` - Reasonable limit for batch processing

**Speaker Label Format:**
```
SPEAKER_0: Hello, how are you today?
SPEAKER_1: I'm doing great, thanks for asking.
SPEAKER_0: That's wonderful to hear.
```

### 3. BatchCleanerService (`services/batch_cleaner_service.py`)

Cleans diarized transcripts using OpenAI GPT-4o with the RoboScribe prompt.

**Interface:**

```python
class BatchCleanerService:
    def __init__(self):
        """Initialize with OpenAI API key and model from environment."""
        
    async def clean_transcript(self, raw_transcript: str) -> str:
        """
        Clean a diarized transcript using OpenAI.
        
        Args:
            raw_transcript: Formatted transcript with SPEAKER_X labels
            
        Returns:
            Cleaned transcript with preserved speaker labels
            
        Raises:
            Exception: If OpenAI API call fails
        """
        
    def _get_system_prompt(self) -> str:
        """
        Return the RoboScribe system prompt with speaker label preservation instruction.
        
        The prompt is copied verbatim from RoboScribe's transcript_processor.py,
        with an additional instruction added:
        "The input WILL contain speaker labels (e.g., 'SPEAKER_0:'). 
        You MUST preserve these labels exactly at the start of each turn. 
        Do not merge turns from different speakers."
        """
        
    async def _clean_chunk(self, chunk: str) -> str:
        """Clean a single chunk using OpenAI."""
```

**OpenAI Configuration:**
- `model: gpt-4o` - From environment variable
- `temperature: 0.5` - Consistent results
- `timeout: 60 seconds per chunk` - Reasonable limit
- `messages: [system_prompt, user_chunk]` - Standard chat format

**Cleaning Process:**
1. Split raw transcript using `split_long_lines` (500 word chunks)
2. For each chunk, call OpenAI with RoboScribe prompt
3. Parse JSON response to extract `cleaned_text` field
4. Join all cleaned chunks with newlines
5. Return complete cleaned transcript

### 4. Batch Router (`routers/batch.py`)

FastAPI router providing the batch processing endpoint.

**Interface:**

```python
router = APIRouter(prefix="/batch", tags=["batch"])

@router.post("/process")
async def process_batch_audio(file: UploadFile) -> dict:
    """
    Process uploaded audio file with diarization and cleaning.
    
    Args:
        file: Uploaded audio file (WAV, MP3, FLAC, M4A)
        
    Returns:
        {
            "raw_transcript": "SPEAKER_0: ...",
            "cleaned_transcript": "SPEAKER_0: ..."
        }
        
    Raises:
        HTTPException(400): Invalid file format or size
        HTTPException(500): Processing error
    """
```

**Validation:**
- File format: WAV, MP3, FLAC, M4A
- File size: Maximum 100MB
- Content type: Verify MIME type matches extension

**Error Handling:**
- 400 Bad Request: Invalid file format or size
- 500 Internal Server Error: Deepgram or OpenAI failures
- Include error details in response for debugging

### 5. Frontend Integration (`templates/index.html`)

Add a second recording mode below the existing live recorder.

**New UI Elements:**

```html
<div id="batch-section">
    <h2>Batch Recording Mode</h2>
    <button id="batch-record-btn">Record & Process (Batch)</button>
    <div id="batch-status"></div>
    <textarea id="batch-results" readonly></textarea>
</div>
```

**JavaScript Logic:**

```javascript
// Reuse MediaRecorder from existing live recorder
let batchRecorder;
let batchChunks = [];

// Start recording
batchRecordBtn.onclick = () => {
    if (!batchRecorder || batchRecorder.state === 'inactive') {
        startBatchRecording();
    } else {
        stopBatchRecording();
    }
};

async function startBatchRecording() {
    // Initialize MediaRecorder
    // Accumulate chunks in batchChunks array
    // Update button text to "Stop Recording"
}

async function stopBatchRecording() {
    // Stop recorder
    // Create Blob from batchChunks
    // POST to /batch/process
    // Display cleaned_transcript in textarea
}
```

**User Experience:**
1. User clicks "Record & Process (Batch)"
2. Button changes to "Stop Recording"
3. Audio is recorded locally (no WebSocket)
4. User clicks "Stop Recording"
5. Status shows "Processing..."
6. Cleaned transcript appears in text box

## Data Models

### Deepgram Response Structure

```python
{
    "results": {
        "channels": [{
            "alternatives": [{
                "words": [
                    {
                        "word": "hello",
                        "start": 0.5,
                        "end": 0.8,
                        "confidence": 0.99,
                        "speaker": 0
                    },
                    # ... more words
                ]
            }]
        }]
    }
}
```

### Batch Processing Response

```python
{
    "raw_transcript": "SPEAKER_0: Hello how are you\nSPEAKER_1: I'm great thanks",
    "cleaned_transcript": "SPEAKER_0: Hello, how are you?\nSPEAKER_1: I'm great, thanks."
}
```

### Internal Data Flow

```python
# 1. Upload
audio_bytes: bytes
mimetype: str

# 2. Deepgram Response
deepgram_response: dict

# 3. Formatted Transcript
raw_transcript: str  # "SPEAKER_0: text\nSPEAKER_1: text"

# 4. Chunked Transcript
chunks: List[str]  # ["SPEAKER_0: chunk1", "SPEAKER_0: chunk2", ...]

# 5. Cleaned Chunks
cleaned_chunks: List[str]

# 6. Final Output
cleaned_transcript: str
```

## Correctness Properties

*A property is a characteristic or behavior that should hold true across all valid executions of a system-essentially, a formal statement about what the system should do. Properties serve as the bridge between human-readable specifications and machine-verifiable correctness guarantees.*

### Property 1: Valid file formats are accepted, invalid formats are rejected

*For any* file extension, if it is in the set {wav, mp3, flac, m4a}, then the system should accept it; otherwise, the system should reject it with a 400 error.

**Validates: Requirements 1.1**

### Property 2: Files over size limit are rejected

*For any* file size, if it exceeds 100MB, then the system should reject it with a 400 error.

**Validates: Requirements 1.2**

### Property 3: Processing IDs are valid UUIDs

*For any* batch processing request, the generated processing_id should match the UUID v4 format pattern.

**Validates: Requirements 1.3**

### Property 4: MIME types are correctly mapped from extensions

*For any* file extension in {wav, mp3, flac, m4a}, the system should return the correct corresponding MIME type {audio/wav, audio/mpeg, audio/flac, audio/mp4}.

**Validates: Requirements 2.3**

### Property 5: Speaker label format is preserved

*For any* Deepgram response with speaker changes, each new speaker should start a new line with the format "SPEAKER_X:" where X is the speaker number.

**Validates: Requirements 3.2**

### Property 6: Same speaker words are joined with spaces

*For any* sequence of words from the same speaker, they should be joined with single space characters on the same line.

**Validates: Requirements 3.3**

### Property 7: One speaker turn per line

*For any* formatted transcript, each line should represent exactly one continuous turn from a single speaker.

**Validates: Requirements 3.4**

### Property 8: Speaker labels are preserved after chunking

*For any* speaker turn that is split into chunks, each chunk should start with the original speaker label.

**Validates: Requirements 4.2**

### Property 9: Chunks respect sentence boundaries

*For any* split operation, the split should occur at a sentence boundary (period, question mark, or exclamation point), not mid-sentence.

**Validates: Requirements 4.3**

### Property 10: Chunks do not exceed word limit

*For any* chunk produced by split_long_lines, the word count should not exceed 500 words.

**Validates: Requirements 4.4**

### Property 11: Short turns are not split

*For any* speaker turn with fewer than 500 words, it should remain as a single chunk without splitting.

**Validates: Requirements 4.5**

### Property 12: JSON responses are correctly parsed

*For any* valid JSON response from OpenAI containing a "cleaned_text" field, the system should successfully extract the text value.

**Validates: Requirements 6.3**

## Error Handling

### Deepgram API Errors

**Scenarios:**
- Network timeout (120 seconds)
- Invalid audio format
- API authentication failure
- Rate limiting

**Handling:**
- Log error with processing_id
- Return HTTP 500 with error message
- Include actionable guidance in error message

### OpenAI API Errors

**Scenarios:**
- Network timeout (60 seconds per chunk)
- Invalid API key
- Rate limiting
- JSON parsing failure

**Handling:**
- Log error with processing_id and chunk index
- Return raw transcript with error flag
- Continue processing remaining chunks if possible

### File Validation Errors

**Scenarios:**
- Invalid file format
- File size exceeds 100MB
- Corrupted file data

**Handling:**
- Return HTTP 400 with specific error message
- Do not attempt processing
- Log validation failure

### Graceful Degradation

If OpenAI cleaning fails:
1. Log the error
2. Return the raw transcript from Deepgram
3. Include an error flag in the response
4. User still receives usable (though uncleaned) output

## Testing Strategy

### Unit Tests

**Text Utilities:**
- Test split_long_lines with various input lengths
- Test sentence boundary detection
- Test speaker label preservation
- Test word count limits

**BatchService:**
- Test MIME type mapping
- Test Deepgram response formatting
- Test speaker label generation
- Mock Deepgram API calls

**BatchCleanerService:**
- Test system prompt construction
- Test chunk processing
- Test JSON parsing
- Mock OpenAI API calls

### Integration Tests

**End-to-End Flow:**
- Upload audio file → receive cleaned transcript
- Test with multi-speaker audio
- Test with long audio files (chunking)
- Test error scenarios (invalid files, API failures)

**Router Tests:**
- Test /batch/process endpoint
- Test file upload validation
- Test response format
- Test error responses

### Property-Based Tests

Use Hypothesis for property-based testing:

**File Validation Properties:**
- Generate random file extensions, verify correct accept/reject
- Generate random file sizes, verify size limit enforcement

**Formatting Properties:**
- Generate random speaker sequences, verify format correctness
- Generate random text lengths, verify chunking behavior

**Chunking Properties:**
- Generate random speaker turns, verify word limits
- Generate random text, verify sentence boundary respect

### Manual Verification

**Verification Script (`scripts/verify_batch_local.py`):**
- Load real credentials from .env
- Call Deepgram with test audio file
- Verify speaker labels in SPEAKER_X format
- Call BatchCleanerService with raw transcript
- Print both raw and cleaned transcripts
- Verify cleaning quality manually

**Test Audio:**
- Use a short multi-speaker audio file (2-3 speakers)
- Include filler words and poor punctuation
- Verify diarization accuracy
- Verify cleaning quality

## Performance Considerations

### Timeouts

- **Deepgram API**: 120 seconds (reasonable for batch files up to 100MB)
- **OpenAI API**: 60 seconds per chunk (500 words should process quickly)
- **Total Processing**: Depends on audio length and number of chunks

### Scalability

- **Synchronous Processing**: Each request blocks until complete
- **No Queuing**: Simple request-response model
- **Future Enhancement**: Add async job queue for long files

### Cost Optimization

- **Deepgram**: Pay per minute of audio
- **OpenAI**: Pay per token (chunking reduces context size)
- **Caching**: Not implemented (each request is unique)

## Security Considerations

### File Upload Security

- Validate file extensions (whitelist only)
- Limit file size to 100MB
- Validate MIME types match extensions
- Do not execute or interpret uploaded files

### API Key Security

- Load from environment variables only
- Never log full API keys
- Use existing DEEPGRAM_API_KEY and OPENAI_API_KEY
- No new credentials required

### Data Privacy

- Audio files are not stored on disk
- Transcripts are returned immediately
- No persistence layer (stateless)
- Consider adding optional transcript storage in future

## Deployment Considerations

### Environment Variables

Reuse existing variables:
- `DEEPGRAM_API_KEY` - Required
- `OPENAI_API_KEY` - Required
- `OPENAI_MODEL` - Optional (default: gpt-4o)

### Railway Deployment

- No changes to existing deployment configuration
- Batch router is automatically included when main.py imports it
- No new environment variables needed
- No database or Redis changes

### Monitoring

Log the following:
- Processing start/complete with processing_id
- Deepgram API call duration
- OpenAI API call duration per chunk
- Total processing time
- Error rates and types

## Future Enhancements

### Potential Improvements

1. **Async Job Queue**: For long audio files, return immediately and notify when complete
2. **Transcript Storage**: Save transcripts to database for later retrieval
3. **Speaker Identification**: Allow users to name speakers (SPEAKER_0 → "John")
4. **Multiple Output Formats**: Export as PDF, DOCX, SRT subtitles
5. **Batch Upload**: Process multiple files in one request
6. **Progress Updates**: WebSocket progress notifications during processing

### Not in Scope

- Real-time processing (use existing streaming pipeline)
- Speaker recognition (identifying specific people)
- Audio storage or playback
- Transcript editing interface
- Multi-language support (Deepgram supports it, but not tested)
