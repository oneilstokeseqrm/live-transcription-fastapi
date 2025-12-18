"""Live verification script for CleanerService using real OpenAI API.

This script tests the CleanerService with sample transcripts to verify
the prompt quality and output structure. It uses the REAL OpenAI API
(no mocks) to show actual before/after results.

Usage:
    python scripts/verify_cleaning_live.py
"""
import asyncio
import os
import sys
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from services.cleaner_service import CleanerService
from dotenv import load_dotenv


# Sample raw transcripts for testing
SAMPLE_TRANSCRIPTS = [
    {
        "name": "Short Meeting",
        "text": """um so like we need to uh you know finish the project by friday 
        uh john can you handle the database migration and uh sarah will work on 
        the frontend um we should probably meet again on thursday to check progress 
        you know just to make sure everything's on track"""
    },
    {
        "name": "Technical Discussion",
        "text": """okay so the the issue is that um the API is returning like 
        inconsistent data types sometimes it's a string sometimes it's an integer 
        uh we need to add validation on the backend um maybe use pydantic or something 
        and uh also we should add error handling so it doesn't crash the whole app 
        you know when bad data comes through"""
    },
    {
        "name": "Action Items Heavy",
        "text": """alright team so here's what we need to do um first we need to 
        update the documentation that's on mike second uh we need to fix the bug 
        in the payment flow sarah can you take that and um third we should schedule 
        a demo with the client for next week uh john can you coordinate that 
        oh and we also need to review the security audit findings before friday"""
    }
]


def print_separator(char="=", length=80):
    """Print a separator line."""
    print(char * length)


def print_section(title: str):
    """Print a section header."""
    print_separator()
    print(f"  {title}")
    print_separator()


async def verify_cleaning():
    """Run verification tests on CleanerService."""
    # Load environment variables
    load_dotenv()
    
    # Check for required API key
    if not os.getenv("OPENAI_API_KEY"):
        print("ERROR: OPENAI_API_KEY environment variable not set")
        print("Please set it in your .env file")
        sys.exit(1)
    
    print_section("CleanerService Live Verification")
    print(f"Model: {os.getenv('OPENAI_MODEL', 'gpt-4o')}")
    print(f"Testing {len(SAMPLE_TRANSCRIPTS)} sample transcripts...\n")
    
    # Initialize service
    try:
        cleaner = CleanerService()
    except Exception as e:
        print(f"ERROR: Failed to initialize CleanerService: {e}")
        sys.exit(1)
    
    # Test each sample
    for i, sample in enumerate(SAMPLE_TRANSCRIPTS, 1):
        print_section(f"Test {i}: {sample['name']}")
        
        # Show original
        print("\n📝 BEFORE (Raw Transcript):")
        print("-" * 80)
        print(sample['text'].strip())
        print()
        
        # Clean the transcript
        try:
            session_id = f"test-{i}"
            result = await cleaner.clean_transcript(sample['text'], session_id)
            
            # Show results
            print("\n✨ AFTER (Cleaned Output):")
            print("-" * 80)
            
            print("\n📋 Summary:")
            print(result.summary)
            
            print("\n✅ Action Items:")
            if result.action_items:
                for idx, item in enumerate(result.action_items, 1):
                    print(f"  {idx}. {item}")
            else:
                print("  (none)")
            
            print("\n📄 Cleaned Transcript:")
            print(result.cleaned_transcript)
            
            print("\n" + "=" * 80)
            print(f"✓ Test {i} completed successfully")
            print("=" * 80)
            print()
            
        except Exception as e:
            print(f"\n❌ ERROR: Test {i} failed: {e}")
            print("=" * 80)
            print()
            continue
    
    print_section("Verification Complete")
    print("\n✓ All tests completed")
    print("\nReview the before/after outputs above to verify:")
    print("  1. Filler words are removed (um, uh, like, you know)")
    print("  2. Grammar and punctuation are improved")
    print("  3. Summary captures main points (2-3 sentences)")
    print("  4. Action items are correctly extracted")
    print("  5. Original meaning and voice are preserved")
    print()


if __name__ == "__main__":
    asyncio.run(verify_cleaning())
