#!/usr/bin/env python3
"""
Verification script for batch diarization pipeline.

This script tests the complete batch processing flow:
1. Downloads a sample audio file with multiple speakers
2. Transcribes it using BatchService (Deepgram)
3. Cleans the transcript using BatchCleanerService (OpenAI)
4. Validates the output format and quality
"""

import asyncio
import os
import sys
import time
from pathlib import Path

# Add parent directory to path to import services
sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
import requests

from services.batch_service import BatchService
from services.batch_cleaner_service import BatchCleanerService


# Sample audio URL with multiple speakers (Deepgram's sample)
SAMPLE_AUDIO_URL = "https://static.deepgram.com/examples/interview_speech-analytics.wav"


def download_sample_audio() -> bytes:
    """Download a sample audio file for testing."""
    print(f"Downloading sample audio from: {SAMPLE_AUDIO_URL}")
    response = requests.get(SAMPLE_AUDIO_URL, timeout=30)
    response.raise_for_status()
    print(f"Downloaded {len(response.content)} bytes")
    return response.content


async def main():
    """Run the verification workflow."""
    # Load environment variables
    load_dotenv()
    
    # Validate required environment variables
    required_vars = ["DEEPGRAM_API_KEY", "OPENAI_API_KEY"]
    missing = [var for var in required_vars if not os.getenv(var)]
    if missing:
        print(f"ERROR: Missing required environment variables: {missing}")
        print("Please ensure .env file contains DEEPGRAM_API_KEY and OPENAI_API_KEY")
        sys.exit(1)
    
    print("=" * 80)
    print("BATCH DIARIZATION VERIFICATION SCRIPT")
    print("=" * 80)
    print()
    
    try:
        # Step 1: Download sample audio
        print("Step 1: Downloading sample audio file...")
        audio_bytes = download_sample_audio()
        print("✓ Audio file downloaded successfully")
        print()
        
        # Step 2: Transcribe with Deepgram
        print("Step 2: Transcribing audio with Deepgram (with diarization)...")
        batch_service = BatchService()
        
        start_time = time.time()
        raw_transcript = await batch_service.transcribe_audio(
            audio_bytes=audio_bytes,
            mimetype="audio/wav"
        )
        transcription_duration = time.time() - start_time
        
        print(f"✓ Transcription completed in {transcription_duration:.2f} seconds")
        print()
        print("-" * 80)
        print("RAW TRANSCRIPT (with speaker labels):")
        print("-" * 80)
        print(raw_transcript)
        print("-" * 80)
        print()
        
        # Step 3: Validate raw transcript format
        print("Step 3: Validating raw transcript format...")
        
        # Check for speaker labels
        if "SPEAKER_" not in raw_transcript:
            print("✗ ERROR: No speaker labels found in raw transcript!")
            print("Expected format: 'SPEAKER_0: text'")
            sys.exit(1)
        
        # Count speakers
        speaker_count = len(set(
            line.split(":")[0] 
            for line in raw_transcript.split("\n") 
            if line.strip() and "SPEAKER_" in line
        ))
        print(f"✓ Found {speaker_count} unique speakers")
        
        # Check for content
        if len(raw_transcript.strip()) == 0:
            print("✗ ERROR: Raw transcript is empty!")
            sys.exit(1)
        print(f"✓ Raw transcript contains {len(raw_transcript)} characters")
        print()
        
        # Step 4: Clean transcript with OpenAI
        print("Step 4: Cleaning transcript with OpenAI...")
        batch_cleaner = BatchCleanerService()
        
        start_time = time.time()
        cleaned_transcript = await batch_cleaner.clean_transcript(raw_transcript)
        cleaning_duration = time.time() - start_time
        
        print(f"✓ Cleaning completed in {cleaning_duration:.2f} seconds")
        print()
        print("-" * 80)
        print("CLEANED TRANSCRIPT:")
        print("-" * 80)
        print(cleaned_transcript)
        print("-" * 80)
        print()
        
        # Step 5: Validate cleaned transcript
        print("Step 5: Validating cleaned transcript...")
        
        # Check that speaker labels are preserved
        if "SPEAKER_" not in cleaned_transcript:
            print("✗ ERROR: Speaker labels were removed during cleaning!")
            sys.exit(1)
        print("✓ Speaker labels preserved in cleaned transcript")
        
        # Check that content exists
        if len(cleaned_transcript.strip()) == 0:
            print("✗ ERROR: Cleaned transcript is empty!")
            sys.exit(1)
        print(f"✓ Cleaned transcript contains {len(cleaned_transcript)} characters")
        
        # Compare lengths (cleaned should be similar or slightly shorter)
        length_ratio = len(cleaned_transcript) / len(raw_transcript)
        print(f"✓ Length ratio (cleaned/raw): {length_ratio:.2f}")
        
        if length_ratio < 0.5:
            print("⚠ WARNING: Cleaned transcript is significantly shorter than raw")
        
        print()
        
        # Step 6: Summary
        print("=" * 80)
        print("VERIFICATION SUMMARY")
        print("=" * 80)
        print(f"✓ Transcription: {transcription_duration:.2f}s")
        print(f"✓ Cleaning: {cleaning_duration:.2f}s")
        print(f"✓ Total processing: {transcription_duration + cleaning_duration:.2f}s")
        print(f"✓ Speakers detected: {speaker_count}")
        print(f"✓ Raw transcript: {len(raw_transcript)} chars")
        print(f"✓ Cleaned transcript: {len(cleaned_transcript)} chars")
        print()
        print("✓ ALL CHECKS PASSED")
        print("=" * 80)
        
    except Exception as e:
        print()
        print("=" * 80)
        print("✗ VERIFICATION FAILED")
        print("=" * 80)
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
