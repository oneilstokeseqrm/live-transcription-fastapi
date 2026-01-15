#!/usr/bin/env python3
"""
Black Box Verification Script for Live Railway Deployment.

This script performs end-to-end verification of the Intelligence Layer
by sending an HTTP request to the live Railway deployment and verifying
that the expected database rows are created in Neon.

Usage:
    python scripts/verify_deployment_http.py <BASE_URL>
    
Example:
    python scripts/verify_deployment_http.py https://inspiring-upliftment-production.up.railway.app
"""
import asyncio
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from uuid import uuid4

import httpx
from dotenv import load_dotenv

# Add project root to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from services.database import get_async_session

# Load environment variables
load_dotenv()


def log(message: str):
    """Log with timestamp."""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] {message}")


async def verify_db_rows(tenant_id: str) -> tuple[int, int]:
    """Query Neon to count summary entries and insights for the tenant."""
    async with get_async_session() as session:
        from sqlalchemy import text
        
        # Count summary entries
        result = await session.execute(
            text("SELECT COUNT(*) FROM interaction_summary_entries WHERE tenant_id = :tenant_id"),
            {"tenant_id": tenant_id}
        )
        summary_count = result.scalar()
        
        # Count insights
        result = await session.execute(
            text("SELECT COUNT(*) FROM interaction_insights WHERE tenant_id = :tenant_id"),
            {"tenant_id": tenant_id}
        )
        insight_count = result.scalar()
        
        return summary_count, insight_count


async def get_insight_types(tenant_id: str) -> list[str]:
    """Get the distinct insight types created for the tenant."""
    async with get_async_session() as session:
        from sqlalchemy import text
        
        result = await session.execute(
            text("SELECT DISTINCT type FROM interaction_insights WHERE tenant_id = :tenant_id ORDER BY type"),
            {"tenant_id": tenant_id}
        )
        return [row[0] for row in result.fetchall()]


async def main():
    if len(sys.argv) < 2:
        print("Usage: python scripts/verify_deployment_http.py <BASE_URL>")
        print("Example: python scripts/verify_deployment_http.py https://inspiring-upliftment-production.up.railway.app")
        sys.exit(1)
    
    base_url = sys.argv[1].rstrip("/")
    
    log("=" * 60)
    log("BLACK BOX VERIFICATION - Live Railway Deployment")
    log("=" * 60)
    log(f"Target URL: {base_url}")
    
    # Load test payload
    payload_path = Path(__file__).parent.parent / "test_payload.json"
    if not payload_path.exists():
        log(f"ERROR: test_payload.json not found at {payload_path}")
        sys.exit(1)
    
    with open(payload_path) as f:
        payload = json.load(f)
    
    tenant_id = payload["headers"]["X-Tenant-ID"]
    log(f"Tenant ID: {tenant_id}")
    log(f"Transcript length: {len(payload['text'])} chars")
    
    # Step 1: Verify clean state
    log("\n--- Step 1: Verify Clean State ---")
    summary_count, insight_count = await verify_db_rows(tenant_id)
    log(f"Pre-test summary entries: {summary_count}")
    log(f"Pre-test insights: {insight_count}")
    
    if summary_count > 0 or insight_count > 0:
        log("WARNING: Database not clean. Proceeding anyway...")
    
    # Step 2: Send HTTP request to /text/clean endpoint
    log("\n--- Step 2: Send HTTP Request ---")
    endpoint = f"{base_url}/text/clean"
    log(f"POST {endpoint}")
    
    # Build request body matching TextCleanRequest model
    request_body = {
        "text": payload["text"],
        "metadata": payload.get("metadata", {}),
        "interaction_id": str(uuid4()),  # Generate new interaction_id
        "trace_id": str(uuid4()),  # Generate new trace_id
    }
    
    headers = {
        "Content-Type": "application/json",
        "X-Tenant-ID": tenant_id,
        "X-User-ID": payload["headers"].get("X-User-ID", "test_user"),
        "X-Trace-Id": request_body["trace_id"],
    }
    
    log(f"Interaction ID: {request_body['interaction_id']}")
    log(f"Trace ID: {request_body['trace_id']}")
    
    start_time = time.time()
    
    # Use longer timeout for large transcripts (OpenAI processing can take 2-3 minutes)
    async with httpx.AsyncClient(timeout=300.0) as client:
        try:
            response = await client.post(endpoint, json=request_body, headers=headers)
            elapsed = time.time() - start_time
            
            log(f"Response Status: {response.status_code}")
            log(f"Response Time: {elapsed:.2f}s")
            
            if response.status_code != 200:
                log(f"ERROR: Unexpected status code")
                log(f"Response Body: {response.text[:500]}")
                sys.exit(1)
            
            response_data = response.json()
            log(f"Response contains cleaned_text: {'cleaned_text' in response_data}")
            
        except httpx.RequestError as e:
            log(f"ERROR: Request failed - {e}")
            sys.exit(1)
    
    # Step 3: Wait for async background task to complete
    log("\n--- Step 3: Wait for Background Processing ---")
    wait_seconds = 15
    log(f"Waiting {wait_seconds} seconds for Lane 2 (Intelligence) to complete...")
    await asyncio.sleep(wait_seconds)
    
    # Step 4: Verify database rows created
    log("\n--- Step 4: Verify Database Rows ---")
    summary_count, insight_count = await verify_db_rows(tenant_id)
    insight_types = await get_insight_types(tenant_id)
    
    log(f"Summary entries created: {summary_count}")
    log(f"Insights created: {insight_count}")
    log(f"Insight types: {insight_types}")
    
    # Validation
    success = True
    
    if summary_count != 5:
        log(f"FAIL: Expected 5 summary entries, got {summary_count}")
        success = False
    else:
        log("PASS: 5 summary entries created (title, headline, brief, detailed, spotlight)")
    
    if insight_count < 5:
        log(f"FAIL: Expected at least 5 insights, got {insight_count}")
        success = False
    else:
        log(f"PASS: {insight_count} insights created")
    
    # Check for expected insight types
    expected_types = {"action_item", "decision_made", "key_takeaway"}
    found_types = set(insight_types)
    missing_types = expected_types - found_types
    
    if missing_types:
        log(f"WARNING: Missing expected insight types: {missing_types}")
    else:
        log("PASS: All expected insight types present")
    
    # Step 5: Summary
    log("\n" + "=" * 60)
    if success:
        log("VERIFICATION PASSED")
        log("The Intelligence Layer is working correctly on Railway.")
    else:
        log("VERIFICATION FAILED")
        log("Check the logs above for details.")
    log("=" * 60)
    
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    asyncio.run(main())
