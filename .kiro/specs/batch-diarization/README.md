# Batch Diarization Pipeline Specification

## Overview

This specification defines a parallel batch processing pipeline for the live-transcription-fastapi service. The pipeline processes pre-recorded audio files through Deepgram's prerecorded API with speaker diarization, then cleans the transcript using OpenAI's GPT-4o with the proven RoboScribe cleaning prompt.

## Key Design Principles

1. **Complete Isolation**: The batch pipeline is entirely separate from the existing streaming pipeline
2. **RoboScribe Reuse**: Text utilities and cleaning prompt are lifted directly from the proven RoboScribe project
3. **Minimal Complexity**: Simple request-response model with no queuing or persistence
4. **Zero Impact**: No modifications to existing streaming code (main.py, event_publisher.py, cleaner_service.py)

## Specification Documents

### requirements.md

Defines 15 requirements with 75 acceptance criteria covering:
- Audio file upload and validation
- Deepgram prerecorded API integration with diarization
- Speaker label formatting (SPEAKER_X: format)
- Text chunking for LLM processing
- OpenAI cleaning with RoboScribe prompt
- REST API endpoint design
- Frontend batch recording interface
- Error handling and timeouts
- Environment configuration reuse

### design.md

Provides comprehensive design including:
- High-level architecture and data flow
- Component interfaces and responsibilities
- File structure (new files only, no modifications)
- Data models and formats
- 12 correctness properties for testing
- Error handling strategies
- Testing strategy (unit, integration, property-based)
- Performance and security considerations

### tasks.md

Breaks implementation into 7 main tasks:
1. Text Utilities (RoboScribe split_long_lines)
2. BatchService (Deepgram integration)
3. BatchCleanerService (OpenAI integration)
4. Batch Router (FastAPI endpoint)
5. Frontend (Batch recording UI)
6. Verification Script
7. Final Checkpoint

Optional test tasks are marked with * for faster MVP delivery.

## Implementation Approach

### Phase 1: Core Services (Tasks 1-3)

Implement the foundational services:
- Text utilities for chunking long speaker turns
- BatchService for Deepgram API calls and speaker label formatting
- BatchCleanerService for OpenAI cleaning with RoboScribe prompt

### Phase 2: API and Frontend (Tasks 4-5)

Build the user-facing components:
- REST API endpoint for batch processing
- Frontend batch recording interface
- Integration with existing web UI

### Phase 3: Verification (Task 6)

Create verification script to validate:
- Deepgram diarization accuracy
- Speaker label formatting
- OpenAI cleaning quality
- End-to-end flow

## Key Technical Decisions

### RoboScribe Integration

**Text Utilities**: Lifted verbatim from `roboscribe/text_utils.py`
- `split_long_lines`: Splits speaker turns into 500-word chunks
- Preserves speaker labels at chunk boundaries
- Respects sentence boundaries

**Cleaning Prompt**: Copied verbatim from `roboscribe/transcript_processor.py`
- Proven prompt that preserves speaker authenticity
- Enhanced with speaker label preservation instruction
- Uses JSON output format for reliable parsing

### Deepgram Configuration

- **smart_format: True** - Automatic punctuation and capitalization
- **diarize: True** - Speaker identification
- **timeout: 120 seconds** - Reasonable for files up to 100MB

### OpenAI Configuration

- **model: gpt-4o** - Cost-effective and fast
- **temperature: 0.5** - Consistent results
- **timeout: 60 seconds per chunk** - Reasonable for 500-word chunks

## File Structure

```
/
├── utils/
│   └── text_utils.py               # NEW: RoboScribe utilities
├── services/
│   ├── batch_service.py            # NEW: Deepgram integration
│   └── batch_cleaner_service.py    # NEW: OpenAI cleaning
├── routers/
│   └── batch.py                    # NEW: Batch endpoints
├── templates/
│   └── index.html                  # MODIFIED: Add batch UI
└── scripts/
    └── verify_batch_local.py       # NEW: Verification
```

## Environment Variables

Reuses existing variables:
- `DEEPGRAM_API_KEY` - Required
- `OPENAI_API_KEY` - Required
- `OPENAI_MODEL` - Optional (default: gpt-4o)

No new environment variables needed.

## Testing Strategy

### Property-Based Tests (Optional)

- File format validation
- File size validation
- UUID generation
- MIME type mapping
- Speaker label formatting
- Text chunking behavior
- JSON parsing

### Unit Tests (Optional)

- BatchService methods
- BatchCleanerService methods
- Text utility functions
- Error handling

### Integration Tests (Optional)

- End-to-end batch processing flow
- File upload validation
- Error scenarios

### Manual Verification (Required)

- Run verification script with real APIs
- Validate diarization accuracy
- Validate cleaning quality
- Verify speaker label preservation

## Success Criteria

The implementation is successful when:

1. ✅ Users can record audio in the browser and receive cleaned transcripts
2. ✅ Speaker labels are correctly identified and preserved
3. ✅ Long speaker turns are properly chunked and cleaned
4. ✅ The RoboScribe cleaning prompt produces high-quality output
5. ✅ The batch pipeline is completely isolated from streaming pipeline
6. ✅ No modifications to existing streaming code
7. ✅ Verification script validates end-to-end functionality

## Next Steps

To begin implementation:

1. Open `.kiro/specs/batch-diarization/tasks.md`
2. Click "Start task" next to Task 1
3. Follow the implementation steps for each task
4. Run verification script after completing core services
5. Test in browser after completing frontend integration

## References

- **RoboScribe**: https://github.com/dend/roboscribe
- **Deepgram Python SDK**: https://github.com/deepgram/deepgram-python-sdk
- **Existing Spec**: `.kiro/specs/stateless-stitcher/`
