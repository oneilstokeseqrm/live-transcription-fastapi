#!/usr/bin/env python3
"""Utility script to test CleanerService with a local transcript file.

This script:
1. Reads raw_transcript.txt from the project root
2. Processes it using CleanerService with real OpenAI API
3. Saves formatted results to cleaned_result.md
"""
import asyncio
import sys
import os
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Add parent directory to path to import services
sys.path.insert(0, str(Path(__file__).parent.parent))

from services.cleaner_service import CleanerService


async def main():
    """Main execution function."""
    # Define file paths
    input_file = Path(__file__).parent.parent / "raw_transcript.txt"
    output_file = Path(__file__).parent.parent / "cleaned_result.md"
    
    # Check if input file exists
    if not input_file.exists():
        print(f"Error: Input file not found: {input_file}")
        sys.exit(1)
    
    print(f"Reading transcript from: {input_file}")
    
    # Read the raw transcript
    with open(input_file, 'r', encoding='utf-8') as f:
        raw_transcript = f.read()
    
    print(f"Transcript length: {len(raw_transcript)} characters")
    print("Processing with CleanerService...")
    
    # Initialize CleanerService and process
    try:
        cleaner = CleanerService()
        result = await cleaner.clean_transcript(
            raw_transcript=raw_transcript,
            session_id="local-test"
        )
        
        print("✓ Processing complete!")
        
        # Format the output as Markdown
        markdown_output = f"""# Cleaned Transcript Results

## Summary

{result.summary}

## Action Items

"""
        
        if result.action_items:
            for i, item in enumerate(result.action_items, 1):
                markdown_output += f"{i}. {item}\n"
        else:
            markdown_output += "*No action items identified*\n"
        
        markdown_output += f"""

## Cleaned Transcript

{result.cleaned_transcript}

---

## Original Transcript (for comparison)

{raw_transcript}
"""
        
        # Write to output file
        with open(output_file, 'w', encoding='utf-8') as f:
            f.write(markdown_output)
        
        print(f"✓ Results saved to: {output_file}")
        print(f"\nSummary preview: {result.summary[:100]}...")
        print(f"Action items found: {len(result.action_items)}")
        print(f"Cleaned transcript length: {len(result.cleaned_transcript)} characters")
        
    except Exception as e:
        print(f"Error processing transcript: {e}")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
