#!/usr/bin/env python3
"""
Live Event Flow Test

This script performs a comprehensive end-to-end test of the event-driven architecture:
1. Generates a small dummy WAV file
2. Sends it to the live Railway endpoint
3. Captures the interaction_id from logs
4. Polls SQS queue for the event
5. Verifies the event schema
6. Cleans up test data
"""

import os
import sys
import json
import time
import wave
import struct
import requests
import boto3
from datetime import datetime

# Configuration
RAILWAY_URL = "https://live-transcription-fastapi-production.up.railway.app"
SQS_QUEUE_NAME = "meeting-transcripts-queue"
AWS_REGION = os.getenv("AWS_REGION", "us-east-1")

# Colors for output
GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
BLUE = "\033[94m"
RESET = "\033[0m"


def generate_dummy_wav(filename="test_audio.wav", duration_seconds=1):
    """
    Generate a small dummy WAV file with silence.
    
    Args:
        filename: Output filename
        duration_seconds: Duration of the audio file
    """
    print(f"{BLUE}[1/6] Generating dummy WAV file...{RESET}")
    
    sample_rate = 16000  # 16kHz
    num_channels = 1  # Mono
    sample_width = 2  # 16-bit
    num_frames = sample_rate * duration_seconds
    
    with wave.open(filename, 'w') as wav_file:
        wav_file.setnchannels(num_channels)
        wav_file.setsampwidth(sample_width)
        wav_file.setframerate(sample_rate)
        
        # Write silence (zeros)
        for _ in range(num_frames):
            wav_file.writeframes(struct.pack('<h', 0))
    
    file_size = os.path.getsize(filename)
    print(f"{GREEN}✓ Generated {filename} ({file_size} bytes){RESET}")
    return filename


def send_to_endpoint(filename):
    """
    Send the audio file to the live Railway endpoint.
    
    Args:
        filename: Path to the audio file
        
    Returns:
        Response object and interaction_id (if found in logs)
    """
    print(f"\n{BLUE}[2/6] Sending file to live endpoint...{RESET}")
    print(f"  URL: {RAILWAY_URL}/batch/process")
    
    with open(filename, 'rb') as f:
        files = {'file': (filename, f, 'audio/wav')}
        
        try:
            response = requests.post(
                f"{RAILWAY_URL}/batch/process",
                files=files,
                timeout=60
            )
            
            if response.status_code == 200:
                print(f"{GREEN}✓ Request successful (200 OK){RESET}")
                data = response.json()
                print(f"  Raw transcript length: {len(data.get('raw_transcript', ''))} chars")
                print(f"  Cleaned transcript length: {len(data.get('cleaned_transcript', ''))} chars")
                return response, None
            else:
                print(f"{RED}✗ Request failed: {response.status_code}{RESET}")
                print(f"  Response: {response.text}")
                return response, None
                
        except Exception as e:
            print(f"{RED}✗ Request error: {e}{RESET}")
            return None, None


def get_sqs_queue_url():
    """Get the SQS queue URL."""
    print(f"\n{BLUE}[3/6] Getting SQS queue URL...{RESET}")
    
    try:
        sqs = boto3.client('sqs', region_name=AWS_REGION)
        response = sqs.get_queue_url(QueueName=SQS_QUEUE_NAME)
        queue_url = response['QueueUrl']
        print(f"{GREEN}✓ Queue URL: {queue_url}{RESET}")
        return queue_url
    except Exception as e:
        print(f"{RED}✗ Failed to get queue URL: {e}{RESET}")
        return None


def poll_sqs_for_message(queue_url, max_attempts=10, wait_seconds=3):
    """
    Poll SQS queue for messages.
    
    Args:
        queue_url: SQS queue URL
        max_attempts: Maximum number of polling attempts
        wait_seconds: Seconds to wait between attempts
        
    Returns:
        Message dict or None
    """
    print(f"\n{BLUE}[4/6] Polling SQS queue for event...{RESET}")
    print(f"  Max attempts: {max_attempts}")
    print(f"  Wait between attempts: {wait_seconds}s")
    
    sqs = boto3.client('sqs', region_name=AWS_REGION)
    
    for attempt in range(1, max_attempts + 1):
        print(f"  Attempt {attempt}/{max_attempts}...", end=" ")
        
        try:
            response = sqs.receive_message(
                QueueUrl=queue_url,
                MaxNumberOfMessages=10,
                WaitTimeSeconds=2,
                AttributeNames=['All'],
                MessageAttributeNames=['All']
            )
            
            messages = response.get('Messages', [])
            
            if messages:
                print(f"{GREEN}Found {len(messages)} message(s){RESET}")
                # Return the most recent message
                return messages[0]
            else:
                print(f"{YELLOW}No messages yet{RESET}")
                
        except Exception as e:
            print(f"{RED}Error: {e}{RESET}")
        
        if attempt < max_attempts:
            time.sleep(wait_seconds)
    
    print(f"{RED}✗ No messages received after {max_attempts} attempts{RESET}")
    return None


def verify_message_schema(message):
    """
    Verify the message matches the expected schema.
    
    Args:
        message: SQS message dict
        
    Returns:
        True if valid, False otherwise
    """
    print(f"\n{BLUE}[5/6] Verifying message schema...{RESET}")
    
    try:
        # Parse the message body
        body = json.loads(message['Body'])
        
        # EventBridge wraps the event in a specific structure
        if 'detail' in body:
            detail = body['detail']
        else:
            detail = body
        
        print(f"  Message ID: {message['MessageId']}")
        print(f"  Event detail keys: {list(detail.keys())}")
        
        # Verify required fields
        required_fields = [
            'version',
            'interaction_id',
            'tenant_id',
            'user_id',
            'timestamp',
            'status',
            'data'
        ]
        
        missing_fields = [field for field in required_fields if field not in detail]
        
        if missing_fields:
            print(f"{RED}✗ Missing required fields: {missing_fields}{RESET}")
            return False
        
        # Verify data structure
        if 'data' in detail:
            data = detail['data']
            if 'cleaned_transcript' not in data or 'raw_transcript' not in data:
                print(f"{RED}✗ Missing transcript fields in data{RESET}")
                return False
        
        # Print key details
        print(f"{GREEN}✓ Schema validation passed{RESET}")
        print(f"\n  Event Details:")
        print(f"    Version: {detail.get('version')}")
        print(f"    Interaction ID: {detail.get('interaction_id')}")
        print(f"    Tenant ID: {detail.get('tenant_id')}")
        print(f"    User ID: {detail.get('user_id')}")
        print(f"    Status: {detail.get('status')}")
        print(f"    Timestamp: {detail.get('timestamp')}")
        print(f"    Raw transcript length: {len(detail.get('data', {}).get('raw_transcript', ''))} chars")
        print(f"    Cleaned transcript length: {len(detail.get('data', {}).get('cleaned_transcript', ''))} chars")
        
        return True
        
    except json.JSONDecodeError as e:
        print(f"{RED}✗ Failed to parse message body: {e}{RESET}")
        return False
    except Exception as e:
        print(f"{RED}✗ Verification error: {e}{RESET}")
        return False


def cleanup(queue_url, message, filename):
    """
    Clean up test data.
    
    Args:
        queue_url: SQS queue URL
        message: SQS message to delete
        filename: Audio file to delete
    """
    print(f"\n{BLUE}[6/6] Cleaning up...{RESET}")
    
    # Delete SQS message
    if queue_url and message:
        try:
            sqs = boto3.client('sqs', region_name=AWS_REGION)
            sqs.delete_message(
                QueueUrl=queue_url,
                ReceiptHandle=message['ReceiptHandle']
            )
            print(f"{GREEN}✓ Deleted test message from SQS{RESET}")
        except Exception as e:
            print(f"{RED}✗ Failed to delete SQS message: {e}{RESET}")
    
    # Delete audio file
    if filename and os.path.exists(filename):
        try:
            os.remove(filename)
            print(f"{GREEN}✓ Deleted {filename}{RESET}")
        except Exception as e:
            print(f"{RED}✗ Failed to delete file: {e}{RESET}")


def main():
    """Main test execution."""
    print(f"\n{BLUE}{'='*60}{RESET}")
    print(f"{BLUE}Live Event Flow Test{RESET}")
    print(f"{BLUE}{'='*60}{RESET}\n")
    
    filename = None
    queue_url = None
    message = None
    
    try:
        # Step 1: Generate dummy WAV file
        filename = generate_dummy_wav()
        
        # Step 2: Send to endpoint
        response, interaction_id = send_to_endpoint(filename)
        
        if not response or response.status_code != 200:
            print(f"\n{RED}✗ Test failed: Endpoint request unsuccessful{RESET}")
            sys.exit(1)
        
        # Step 3: Get SQS queue URL
        queue_url = get_sqs_queue_url()
        
        if not queue_url:
            print(f"\n{RED}✗ Test failed: Could not get SQS queue URL{RESET}")
            sys.exit(1)
        
        # Step 4: Poll SQS for message
        message = poll_sqs_for_message(queue_url)
        
        if not message:
            print(f"\n{RED}✗ Test failed: No message received in SQS{RESET}")
            sys.exit(1)
        
        # Step 5: Verify message schema
        if not verify_message_schema(message):
            print(f"\n{RED}✗ Test failed: Message schema validation failed{RESET}")
            sys.exit(1)
        
        # Step 6: Cleanup
        cleanup(queue_url, message, filename)
        
        # Success!
        print(f"\n{GREEN}{'='*60}{RESET}")
        print(f"{GREEN}✓ ALL TESTS PASSED{RESET}")
        print(f"{GREEN}{'='*60}{RESET}")
        print(f"\n{GREEN}Event flow verified:{RESET}")
        print(f"  Client → Railway Endpoint → EventBridge → SQS ✓")
        
        sys.exit(0)
        
    except KeyboardInterrupt:
        print(f"\n{YELLOW}Test interrupted by user{RESET}")
        cleanup(queue_url, message, filename)
        sys.exit(1)
    except Exception as e:
        print(f"\n{RED}✗ Unexpected error: {e}{RESET}")
        import traceback
        traceback.print_exc()
        cleanup(queue_url, message, filename)
        sys.exit(1)


if __name__ == "__main__":
    main()
