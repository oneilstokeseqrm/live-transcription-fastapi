#!/usr/bin/env python3
"""
Manual E2E Test Script

Reads test_payload.json and POSTs to the production /text/clean endpoint.
Captures and displays the interaction_id and trace_id for verification.
"""

import json
import sys
import httpx
from pathlib import Path
from datetime import datetime


PRODUCTION_URL = "https://live-transcription-fastapi-production.up.railway.app/text/clean"
PAYLOAD_FILE = Path(__file__).parent.parent / "test_payload.json"


def load_payload() -> dict:
    """Load the test payload from JSON file."""
    if not PAYLOAD_FILE.exists():
        print(f"ERROR: Payload file not found: {PAYLOAD_FILE}")
        sys.exit(1)
    
    with open(PAYLOAD_FILE, "r") as f:
        return json.load(f)


def run_e2e_test():
    """Execute the E2E test against production."""
    print("=" * 60)
    print("MANUAL E2E TEST - Production Endpoint")
    print("=" * 60)
    print(f"Timestamp: {datetime.utcnow().isoformat()}Z")
    print(f"Target URL: {PRODUCTION_URL}")
    print()
    
    # Load payload
    print("Loading test payload...")
    payload_data = load_payload()
    
    # Extract components
    headers = payload_data.get("headers", {})
    text = payload_data.get("text", "")
    metadata = payload_data.get("metadata", {})
    
    print(f"  Text length: {len(text)} characters")
    print(f"  Headers: {json.dumps(headers, indent=4)}")
    print(f"  Metadata: {json.dumps(metadata, indent=4)}")
    print()
    
    # Build request body (text and metadata only, headers go in HTTP headers)
    request_body = {
        "text": text,
        "metadata": metadata
    }
    
    # Make the POST request
    print("Sending POST request to production...")
    print("-" * 60)
    
    try:
        # Large transcripts can take 3-5 minutes to process with OpenAI
        with httpx.Client(timeout=300.0) as client:
            response = client.post(
                PRODUCTION_URL,
                json=request_body,
                headers=headers
            )
        
        print(f"Status Code: {response.status_code}")
        print()
        
        if response.status_code == 200:
            response_json = response.json()
            
            # Extract critical IDs
            interaction_id = response_json.get("interaction_id", "NOT FOUND")
            trace_id = response_json.get("trace_id", headers.get("X-Trace-Id", "NOT FOUND"))
            
            print("=" * 60)
            print("CRITICAL IDs FOR VERIFICATION")
            print("=" * 60)
            print(f"  interaction_id: {interaction_id}")
            print(f"  trace_id: {trace_id}")
            print("=" * 60)
            print()
            
            print("FULL RESPONSE:")
            print("-" * 60)
            print(json.dumps(response_json, indent=2))
            
            # Return IDs for downstream verification
            return {
                "success": True,
                "interaction_id": interaction_id,
                "trace_id": trace_id,
                "response": response_json
            }
        else:
            print(f"ERROR: Request failed with status {response.status_code}")
            print(f"Response: {response.text}")
            return {
                "success": False,
                "status_code": response.status_code,
                "error": response.text
            }
            
    except httpx.TimeoutException:
        print("ERROR: Request timed out after 300 seconds")
        return {"success": False, "error": "timeout"}
    except Exception as e:
        print(f"ERROR: {type(e).__name__}: {e}")
        return {"success": False, "error": str(e)}


if __name__ == "__main__":
    result = run_e2e_test()
    
    print()
    print("=" * 60)
    if result.get("success"):
        print("TEST COMPLETED SUCCESSFULLY")
        print()
        print("Next Steps - Use these IDs for verification:")
        print(f"  1. Kinesis: Look for trace_id = {result.get('trace_id')}")
        print(f"  2. Step Functions: Check for new execution")
        print(f"  3. Neon: Query interaction_id = {result.get('interaction_id')}")
    else:
        print("TEST FAILED")
        sys.exit(1)
    print("=" * 60)
