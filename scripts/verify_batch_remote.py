#!/usr/bin/env python3
"""
Remote verification script for batch diarization production deployment.

This script tests the live production endpoint to ensure the batch processing
pipeline is working correctly in the deployed environment.
"""

import requests
import sys

# Production URL
PRODUCTION_URL = "https://live-transcription-fastapi-production.up.railway.app"
ENDPOINT = f"{PRODUCTION_URL}/batch/process"

# Sample audio URL
SAMPLE_AUDIO_URL = "https://static.deepgram.com/examples/interview_speech-analytics.wav"


def main():
    """Verify the production batch processing endpoint."""
    print("=" * 80)
    print("REMOTE BATCH DIARIZATION VERIFICATION")
    print("=" * 80)
    print(f"Target: {PRODUCTION_URL}")
    print(f"Endpoint: POST /batch/process")
    print()
    
    try:
        # Step 1: Download sample audio
        print("Step 1: Downloading sample audio file...")
        audio_response = requests.get(SAMPLE_AUDIO_URL, timeout=30)
        audio_response.raise_for_status()
        audio_bytes = audio_response.content
        print(f"✓ Downloaded {len(audio_bytes)} bytes")
        print()
        
        # Step 2: Send POST request to production endpoint
        print("Step 2: Sending audio to production endpoint...")
        print(f"POST {ENDPOINT}")
        
        files = {
            'file': ('interview_speech-analytics.wav', audio_bytes, 'audio/wav')
        }
        
        response = requests.post(
            ENDPOINT,
            files=files,
            timeout=180  # 3 minutes timeout for processing
        )
        
        print(f"✓ Response received")
        print()
        
        # Step 3: Print HTTP Status Code
        print("-" * 80)
        print(f"HTTP Status Code: {response.status_code}")
        print("-" * 80)
        print()
        
        # Step 4: Check if successful
        if response.status_code == 200:
            print("✓ Remote Diarization Success!")
            print()
            
            # Step 5: Parse and display cleaned transcript
            try:
                data = response.json()
                
                if 'cleaned_transcript' in data:
                    cleaned_transcript = data['cleaned_transcript']
                    
                    print("-" * 80)
                    print("CLEANED TRANSCRIPT (first 500 characters):")
                    print("-" * 80)
                    print(cleaned_transcript[:500])
                    if len(cleaned_transcript) > 500:
                        print("...")
                    print("-" * 80)
                    print()
                    
                    # Additional validation
                    if "SPEAKER_" in cleaned_transcript:
                        print("✓ Speaker labels preserved")
                    else:
                        print("⚠ WARNING: No speaker labels found in cleaned transcript")
                    
                    print(f"✓ Total transcript length: {len(cleaned_transcript)} characters")
                    print()
                    
                    # Check for raw transcript too
                    if 'raw_transcript' in data:
                        raw_transcript = data['raw_transcript']
                        print(f"✓ Raw transcript length: {len(raw_transcript)} characters")
                        print()
                else:
                    print("⚠ WARNING: No 'cleaned_transcript' field in response")
                    print(f"Response keys: {list(data.keys())}")
                    
            except Exception as e:
                print(f"✗ Failed to parse JSON response: {e}")
                print(f"Response text: {response.text[:500]}")
                sys.exit(1)
            
            print("=" * 80)
            print("✓ PRODUCTION DEPLOYMENT VERIFIED")
            print("=" * 80)
            
        else:
            print(f"✗ Request failed with status {response.status_code}")
            print(f"Response: {response.text}")
            sys.exit(1)
            
    except requests.exceptions.Timeout:
        print("✗ Request timed out")
        print("The processing may take longer than expected or the service is unresponsive")
        sys.exit(1)
    except requests.exceptions.RequestException as e:
        print(f"✗ Request failed: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"✗ Unexpected error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
