# Implementation Plan: Batch Diarization Pipeline

- [x] 1. Implement text utilities from RoboScribe
  - Create `utils/text_utils.py` with functions lifted directly from RoboScribe
  - Implement `split_long_lines` function that splits speaker turns exceeding 500 words
  - Implement `_split_into_sentences` helper that respects sentence boundaries
  - Implement `_group_sentences` helper that groups sentences into chunks
  - Ensure speaker labels (SPEAKER_X:) are preserved at the start of each chunk
  - Requirements: 4.2, 4.3, 4.4, 4.5

- [ ]* 1.1 Write property test for speaker label preservation
  - **Property 8: Speaker labels are preserved after chunking**
  - **Validates: Requirements 4.2**

- [ ]* 1.2 Write property test for sentence boundary respect
  - **Property 9: Chunks respect sentence boundaries**
  - **Validates: Requirements 4.3**

- [ ]* 1.3 Write property test for word limit enforcement
  - **Property 10: Chunks do not exceed word limit**
  - **Validates: Requirements 4.4**

- [ ]* 1.4 Write property test for short turn handling
  - **Property 11: Short turns are not split**
  - **Validates: Requirements 4.5**

- [x] 2. Implement BatchService for Deepgram integration
  - Create `services/batch_service.py` with BatchService class
  - Implement `__init__` method that loads DEEPGRAM_API_KEY from environment
  - Implement `transcribe_audio` method that calls Deepgram prerecorded API
  - Configure Deepgram with `smart_format: True` and `diarize: True`
  - Set timeout to 120 seconds for API calls
  - Implement `_format_deepgram_response` method that parses word-level speaker labels
  - Format output as "SPEAKER_X: text" with one line per speaker turn
  - Implement `_get_mimetype_from_extension` helper for MIME type mapping
  - Handle missing speaker labels with "SPEAKER_UNKNOWN:" fallback
  - Requirements: 2.1, 2.2, 2.3, 2.5, 3.2, 3.3, 3.4, 3.5

- [ ]* 2.1 Write property test for MIME type mapping
  - **Property 4: MIME types are correctly mapped from extensions**
  - **Validates: Requirements 2.3**

- [ ]* 2.2 Write property test for speaker label format
  - **Property 5: Speaker label format is preserved**
  - **Validates: Requirements 3.2**

- [ ]* 2.3 Write property test for word joining
  - **Property 6: Same speaker words are joined with spaces**
  - **Validates: Requirements 3.3**

- [ ]* 2.4 Write property test for turn-per-line structure
  - **Property 7: One speaker turn per line**
  - **Validates: Requirements 3.4**

- [ ]* 2.5 Write unit tests for BatchService
  - Test Deepgram API call with mocked responses
  - Test error handling for API failures
  - Test timeout handling
  - Requirements: 2.1, 2.2, 2.3, 2.5

- [x] 3. Implement BatchCleanerService for OpenAI integration
  - Create `services/batch_cleaner_service.py` with BatchCleanerService class
  - Implement `__init__` method that loads OPENAI_API_KEY and OPENAI_MODEL from environment
  - Implement `_get_system_prompt` method that returns RoboScribe prompt verbatim
  - Add speaker label preservation instruction to system prompt: "The input WILL contain speaker labels (e.g., 'SPEAKER_0:'). You MUST preserve these labels exactly at the start of each turn. Do not merge turns from different speakers."
  - Implement `clean_transcript` method that processes full transcripts
  - Use `split_long_lines` from text utilities to chunk the transcript
  - Implement `_clean_chunk` method that calls OpenAI for individual chunks
  - Configure OpenAI with `model: gpt-4o` and `temperature: 0.5`
  - Set timeout to 60 seconds per chunk
  - Parse JSON responses to extract `cleaned_text` field
  - Join cleaned chunks with newline characters
  - Handle OpenAI failures by returning raw transcript with error flag
  - Requirements: 5.1, 5.2, 5.3, 5.4, 5.5, 6.2, 6.3, 6.4

- [ ]* 3.1 Write property test for JSON parsing
  - **Property 12: JSON responses are correctly parsed**
  - **Validates: Requirements 6.3**

- [ ]* 3.2 Write unit tests for BatchCleanerService
  - Test system prompt construction
  - Test chunk processing with mocked OpenAI responses
  - Test error handling for API failures
  - Test timeout handling
  - Test fallback to raw transcript on errors
  - Requirements: 5.1, 5.2, 5.3, 5.4, 5.5, 6.2

- [x] 4. Implement batch processing router
  - Create `routers/batch.py` with FastAPI router
  - Define POST endpoint at `/batch/process`
  - Accept `file: UploadFile` parameter
  - Implement file validation: check format (WAV, MP3, FLAC, M4A, WebM, MP4) and size (max 100MB)
  - Generate unique processing_id using UUID v4
  - Read audio bytes from uploaded file
  - Call BatchService.transcribe_audio with audio bytes and MIME type
  - Call BatchCleanerService.clean_transcript with raw transcript
  - Return JSON response with `raw_transcript` and `cleaned_transcript` fields
  - Handle validation errors with HTTP 400 responses
  - Handle processing errors with HTTP 500 responses
  - Log all operations with processing_id for traceability
  - Update `main.py` to include the batch router
  - Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 8.1, 8.2, 8.3, 8.4, 8.5

- [ ]* 4.1 Write property test for file format validation
  - **Property 1: Valid file formats are accepted, invalid formats are rejected**
  - **Validates: Requirements 1.1**

- [ ]* 4.2 Write property test for file size validation
  - **Property 2: Files over size limit are rejected**
  - **Validates: Requirements 1.2**

- [ ]* 4.3 Write property test for UUID generation
  - **Property 3: Processing IDs are valid UUIDs**
  - **Validates: Requirements 1.3**

- [ ]* 4.4 Write integration tests for batch router
  - Test complete flow: upload → transcribe → clean → response
  - Test file validation errors
  - Test processing errors
  - Test response format
  - Requirements: 8.1, 8.2, 8.3, 8.4, 8.5

- [x] 5. Implement frontend batch recording interface
  - Open `templates/index.html` for modification
  - Add new section below existing live recorder: "Batch Recording Mode"
  - Add button with id "batch-record-btn" and text "Record & Process (Batch)"
  - Add status div with id "batch-status" for displaying processing state
  - Add textarea with id "batch-results" for displaying cleaned transcript
  - Implement JavaScript to reuse MediaRecorder from existing live recorder
  - Create `batchChunks` array to accumulate audio chunks locally
  - Implement `startBatchRecording` function that initializes MediaRecorder
  - Implement `stopBatchRecording` function that creates Blob and POSTs to /batch/process
  - Update button text to "Stop Recording" when recording is active
  - Display "Processing..." in status div while waiting for response
  - Extract `cleaned_transcript` from response and display in textarea
  - Handle errors by displaying error messages in status div
  - Requirements: 9.1, 9.2, 9.3, 9.4, 9.5, 10.1, 10.2, 10.3, 10.4, 10.5, 11.1, 11.2, 11.3, 11.4, 11.5

- [x] 6. Create verification script
  - Create `scripts/verify_batch_local.py`
  - Load DEEPGRAM_API_KEY and OPENAI_API_KEY from .env file
  - Create or use a test audio file with multiple speakers (2-3 speakers)
  - Call BatchService.transcribe_audio with test audio
  - Verify response contains speaker labels in SPEAKER_X format
  - Print raw transcript to console
  - Call BatchCleanerService.clean_transcript with raw transcript
  - Verify response is a cleaned string
  - Print cleaned transcript to console
  - Compare raw and cleaned transcripts to verify cleaning quality
  - Log processing duration for performance monitoring
  - Requirements: 13.1, 13.2, 13.3, 13.4, 13.5, 13.6

- [ ]* 6.1 Run verification script and validate output
  - Execute verification script locally
  - Verify Deepgram returns diarized transcript
  - Verify speaker labels are in correct format
  - Verify OpenAI cleaning preserves speaker labels
  - Verify cleaning removes filler words and improves punctuation
  - Requirements: 13.1, 13.2, 13.3, 13.4, 13.5, 13.6

- [x] 7. Final checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise
