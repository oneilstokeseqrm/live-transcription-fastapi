# Requirements Document: Batch Diarization Pipeline

## Introduction

This specification defines a parallel batch processing pipeline for the live-transcription-fastapi service. Unlike the existing real-time WebSocket streaming architecture, this pipeline processes pre-recorded audio files through Deepgram's prerecorded API with speaker diarization, then cleans the transcript using OpenAI's GPT-4o. The system is inspired by the RoboScribe project and reuses its proven text processing utilities and cleaning prompt strategies while adapting them for a web service architecture.

## Glossary

- **Batch Processing**: Processing complete audio files in a single operation, as opposed to streaming chunks
- **Diarization**: The process of identifying and labeling different speakers in an audio recording
- **Speaker Label**: A tag in the format "SPEAKER_X:" where X is a numeric identifier (e.g., "SPEAKER_0:", "SPEAKER_1:")
- **Prerecorded API**: Deepgram's API endpoint for processing complete audio files (as opposed to streaming)
- **Smart Format**: Deepgram's feature that automatically adds punctuation and capitalization to transcripts
- **BatchService**: The service component responsible for calling Deepgram's prerecorded API and formatting the response
- **BatchCleanerService**: The service component responsible for cleaning diarized transcripts using OpenAI
- **Text Utilities**: Helper functions from RoboScribe for splitting long speaker turns into manageable chunks
- **RoboScribe Prompt**: The proven system prompt from RoboScribe that instructs the LLM to clean transcripts while preserving speaker authenticity
- **Turn**: A continuous segment of speech from a single speaker before another speaker begins
- **Chunking**: The process of splitting long speaker turns into smaller segments for LLM processing

## Requirements

### Requirement 1: Audio File Upload and Processing

**User Story:** As a user, I want to upload a pre-recorded audio file for transcription and diarization, so that I can get a cleaned transcript with speaker labels.

#### Acceptance Criteria

1. WHEN a user uploads an audio file, THE system SHALL accept files in WAV, MP3, FLAC, and M4A formats
2. WHEN receiving an uploaded file, THE system SHALL validate the file size does not exceed 100MB
3. WHEN processing begins, THE system SHALL generate a unique processing_id using UUID v4 format
4. WHEN the file is valid, THE system SHALL pass the audio bytes to the BatchService for processing
5. WHEN processing completes, THE system SHALL return both raw and cleaned transcripts to the client

### Requirement 2: Deepgram Prerecorded Transcription

**User Story:** As a developer, I want to use Deepgram's prerecorded API with diarization enabled, so that I can identify different speakers in the audio file.

#### Acceptance Criteria

1. WHEN calling Deepgram's prerecorded API, THE BatchService SHALL enable the smart_format feature
2. WHEN calling Deepgram's prerecorded API, THE BatchService SHALL enable the diarize feature
3. WHEN calling Deepgram's prerecorded API, THE BatchService SHALL specify the correct mimetype based on file extension
4. WHEN the API call completes, THE BatchService SHALL receive a JSON response containing word-level speaker labels
5. WHEN the API call fails, THE BatchService SHALL raise an exception with the error details

### Requirement 3: Speaker Label Formatting

**User Story:** As a system architect, I want Deepgram responses formatted in the RoboScribe speaker label format, so that the cleaning prompt can process them correctly.

#### Acceptance Criteria

1. WHEN parsing Deepgram's response, THE BatchService SHALL iterate through the words array
2. WHEN encountering a new speaker, THE BatchService SHALL start a new line with the format "SPEAKER_X:"
3. WHEN the same speaker continues, THE BatchService SHALL append words to the current line with spaces
4. WHEN formatting is complete, THE BatchService SHALL return a string with one speaker turn per line
5. WHEN speaker information is missing, THE BatchService SHALL use "SPEAKER_UNKNOWN:" as the label

### Requirement 4: Text Chunking for LLM Processing

**User Story:** As a developer, I want long speaker turns split into manageable chunks, so that they fit within LLM context limits and maintain sentence boundaries.

#### Acceptance Criteria

1. WHEN processing the formatted transcript, THE system SHALL use the split_long_lines utility from RoboScribe
2. WHEN splitting lines, THE system SHALL preserve speaker labels at the start of each chunk
3. WHEN splitting lines, THE system SHALL respect sentence boundaries (periods, question marks, exclamation points)
4. WHEN splitting lines, THE system SHALL limit chunks to 500 words maximum
5. WHEN a speaker turn is under 500 words, THE system SHALL NOT split it

### Requirement 5: RoboScribe Cleaning Prompt Integration

**User Story:** As a content editor, I want the cleaning process to use the proven RoboScribe prompt, so that transcripts are cleaned consistently with established quality standards.

#### Acceptance Criteria

1. WHEN initializing the BatchCleanerService, THE system SHALL load the RoboScribe system prompt verbatim
2. WHEN the system prompt is loaded, THE system SHALL add the instruction: "The input WILL contain speaker labels (e.g., 'SPEAKER_0:'). You MUST preserve these labels exactly at the start of each turn. Do not merge turns from different speakers."
3. WHEN calling OpenAI, THE BatchCleanerService SHALL include the system prompt in the messages array
4. WHEN calling OpenAI, THE BatchCleanerService SHALL use the gpt-4o model
5. WHEN calling OpenAI, THE BatchCleanerService SHALL set temperature to 0.5 for consistent results

### Requirement 6: Batch Transcript Cleaning

**User Story:** As a user, I want my diarized transcript automatically cleaned, so that I receive polished output with proper punctuation and without filler words.

#### Acceptance Criteria

1. WHEN cleaning begins, THE BatchCleanerService SHALL process each chunked line sequentially
2. WHEN processing a line, THE system SHALL send it to OpenAI with the RoboScribe prompt
3. WHEN OpenAI returns a response, THE system SHALL parse the JSON to extract the cleaned_text field
4. WHEN all lines are processed, THE system SHALL join them with newline characters
5. WHEN cleaning completes, THE system SHALL return the complete cleaned transcript as a string

### Requirement 7: Cleaning Quality Standards

**User Story:** As a content editor, I want the cleaning process to preserve speaker authenticity while improving readability, so that transcripts remain accurate and quotable.

#### Acceptance Criteria

1. WHEN cleaning a transcript, THE system SHALL remove filler words such as "um", "uh", and "like"
2. WHEN cleaning a transcript, THE system SHALL remove word duplications such as "the the"
3. WHEN cleaning a transcript, THE system SHALL add appropriate punctuation for readability
4. WHEN cleaning a transcript, THE system SHALL fix basic grammar errors while preserving speaker voice
5. WHEN cleaning a transcript, THE system SHALL NOT add words or content not present in the original
6. WHEN cleaning a transcript, THE system SHALL preserve speaker labels exactly as written
7. WHEN cleaning a transcript, THE system SHALL NOT merge turns from different speakers

### Requirement 8: REST API Endpoint

**User Story:** As a frontend developer, I want a REST API endpoint for batch processing, so that I can integrate it into the web interface.

#### Acceptance Criteria

1. WHEN defining the endpoint, THE system SHALL create a POST route at /batch/process
2. WHEN the endpoint receives a request, THE system SHALL accept a file parameter of type UploadFile
3. WHEN processing completes successfully, THE system SHALL return a JSON response with raw_transcript and cleaned_transcript fields
4. WHEN processing fails, THE system SHALL return an HTTP 500 error with an error message
5. WHEN the endpoint is registered, THE system SHALL include it in the main FastAPI application

### Requirement 9: Frontend Batch Recording Interface

**User Story:** As a user, I want a batch recording mode in the web interface, so that I can record audio and process it without maintaining a WebSocket connection.

#### Acceptance Criteria

1. WHEN the page loads, THE system SHALL display a "Batch Recording Mode" section below the existing live recorder
2. WHEN the section is displayed, THE system SHALL include a "Record & Process (Batch)" button
3. WHEN the user clicks the button, THE system SHALL start recording using MediaRecorder
4. WHEN recording is active, THE system SHALL accumulate audio chunks in memory
5. WHEN the user stops recording, THE system SHALL create a Blob from the accumulated chunks

### Requirement 10: Batch Processing Submission

**User Story:** As a user, I want my recorded audio automatically submitted for processing, so that I receive cleaned transcripts without manual file uploads.

#### Acceptance Criteria

1. WHEN recording stops, THE system SHALL create a FormData object with the audio Blob
2. WHEN the FormData is created, THE system SHALL POST it to /batch/process
3. WHEN the request is sent, THE system SHALL display a loading indicator
4. WHEN the response is received, THE system SHALL extract the cleaned_transcript field
5. WHEN the cleaned transcript is extracted, THE system SHALL display it in a new text box

### Requirement 11: Error Handling and User Feedback

**User Story:** As a user, I want clear feedback during batch processing, so that I understand the system status and any errors that occur.

#### Acceptance Criteria

1. WHEN recording starts, THE system SHALL change the button text to "Stop Recording"
2. WHEN processing begins, THE system SHALL display "Processing..." in the results area
3. WHEN processing completes, THE system SHALL display the cleaned transcript
4. IF an error occurs, THEN THE system SHALL display an error message in the results area
5. WHEN displaying errors, THE system SHALL include actionable guidance (e.g., "Please try a shorter recording")

### Requirement 12: Isolation from Streaming Pipeline

**User Story:** As a system architect, I want the batch pipeline completely isolated from the streaming pipeline, so that changes to one do not affect the other.

#### Acceptance Criteria

1. WHEN implementing batch services, THE system SHALL create new files in the services/ directory
2. WHEN implementing the batch router, THE system SHALL create a new file in a routers/ directory
3. WHEN implementing text utilities, THE system SHALL create a new file in a utils/ directory
4. WHEN registering the batch router, THE system SHALL NOT modify the existing WebSocket endpoint in main.py
5. WHEN the batch pipeline is deployed, THE existing streaming functionality SHALL continue operating unchanged

### Requirement 13: Local Verification Script

**User Story:** As a developer, I want a verification script to test the batch pipeline locally, so that I can validate functionality before deployment.

#### Acceptance Criteria

1. WHEN running the verification script, THE system SHALL load credentials from the local .env file
2. WHEN the script executes, THE system SHALL make a real API call to Deepgram without mocks
3. WHEN calling Deepgram, THE system SHALL use a test audio file with multiple speakers
4. WHEN Deepgram returns results, THE system SHALL verify the response contains speaker labels in SPEAKER_X format
5. WHEN the BatchCleanerService processes the transcript, THE system SHALL verify it returns a cleaned string
6. WHEN verification completes, THE system SHALL print both raw and cleaned transcripts to the console

### Requirement 14: Environment Configuration Reuse

**User Story:** As a developer, I want to reuse existing environment variables, so that I don't need to configure new credentials for the batch pipeline.

#### Acceptance Criteria

1. WHEN the BatchService initializes, THE system SHALL read DEEPGRAM_API_KEY from environment variables
2. WHEN the BatchCleanerService initializes, THE system SHALL read OPENAI_API_KEY from environment variables
3. WHEN the BatchCleanerService initializes, THE system SHALL read OPENAI_MODEL from environment variables with default "gpt-4o"
4. WHEN environment variables are missing, THE system SHALL raise an exception at initialization
5. WHEN the batch pipeline runs, THE system SHALL NOT require any new environment variables

### Requirement 15: Performance and Timeout Handling

**User Story:** As a system operator, I want reasonable timeout limits for batch processing, so that long-running requests don't block the service.

#### Acceptance Criteria

1. WHEN calling Deepgram's API, THE system SHALL set a timeout of 120 seconds
2. WHEN calling OpenAI's API, THE system SHALL set a timeout of 60 seconds per chunk
3. IF Deepgram times out, THEN THE system SHALL return an error to the client
4. IF OpenAI times out, THEN THE system SHALL return the raw transcript with an error flag
5. WHEN processing completes within timeout limits, THE system SHALL log the total processing duration
