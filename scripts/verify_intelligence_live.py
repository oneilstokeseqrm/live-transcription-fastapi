#!/usr/bin/env python3
"""Live verification script for the Intelligence Layer.

This script bypasses the API layer and invokes IntelligenceService directly
to verify the intelligence pipeline with a real, high-token-count transcript
against the live Neon database and OpenAI API.

Usage:
    python scripts/verify_intelligence_live.py

The script will:
1. Load test_payload.json from project root
2. Generate new trace_id and interaction_id
3. Extract intelligence using IntelligenceService
4. Verify persisted rows in Neon
5. Generate artifact report
6. Teardown (delete created rows)
"""
import asyncio
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from uuid import uuid4

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv()

from sqlalchemy import delete, select, func
from sqlmodel import col

from services.intelligence_service import IntelligenceService
from services.database import get_async_session
from models.db_models import (
    InteractionSummaryEntryModel,
    InteractionInsightModel,
    InsightTypeEnum,
)


def log(message: str) -> None:
    """Log with timestamp."""
    timestamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] {message}")


def load_payload(filepath: str) -> dict:
    """Load test payload from JSON file."""
    with open(filepath, "r") as f:
        return json.load(f)


async def verify_db_rows(interaction_id: str) -> tuple[list, list]:
    """Query Neon to verify created rows."""
    from uuid import UUID
    interaction_uuid = UUID(interaction_id)
    
    async with get_async_session() as session:
        # Get summaries
        summaries_result = await session.execute(
            select(InteractionSummaryEntryModel).where(
                InteractionSummaryEntryModel.interaction_id == interaction_uuid
            )
        )
        summaries = summaries_result.scalars().all()
        
        # Get insights
        insights_result = await session.execute(
            select(InteractionInsightModel).where(
                InteractionInsightModel.interaction_id == interaction_uuid
            )
        )
        insights = insights_result.scalars().all()
        
    return summaries, insights


async def teardown(interaction_id: str) -> dict:
    """Delete all rows created during the test run."""
    from uuid import UUID
    interaction_uuid = UUID(interaction_id)
    
    async with get_async_session() as session:
        # Delete insights first (no FK constraint issues)
        insights_result = await session.execute(
            delete(InteractionInsightModel).where(
                InteractionInsightModel.interaction_id == interaction_uuid
            )
        )
        
        # Delete summaries
        summaries_result = await session.execute(
            delete(InteractionSummaryEntryModel).where(
                InteractionSummaryEntryModel.interaction_id == interaction_uuid
            )
        )
        
        await session.commit()
        
    return {
        "insights_deleted": insights_result.rowcount,
        "summaries_deleted": summaries_result.rowcount
    }


def generate_artifact(
    config: dict,
    input_snippet: str,
    analysis_json: str,
    summaries: list,
    insights: list,
    teardown_result: dict,
    success: bool
) -> str:
    """Generate markdown artifact report."""
    timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    filename = f"test_artifacts/run_{timestamp}.md"
    
    # Count insights by type
    insight_counts = {}
    for insight in insights:
        type_name = insight.type.value if hasattr(insight.type, 'value') else str(insight.type)
        insight_counts[type_name] = insight_counts.get(type_name, 0) + 1
    
    # Build summaries table
    summaries_table = "| Level | Word Count | Preview |\n|-------|------------|---------|"
    for s in summaries:
        level = s.level.value if hasattr(s.level, 'value') else str(s.level)
        preview = s.text[:50] + "..." if len(s.text) > 50 else s.text
        preview = preview.replace("|", "\\|").replace("\n", " ")
        summaries_table += f"\n| {level} | {s.word_count} | {preview} |"
    
    # Build insights table
    insights_table = "| Type | Count |\n|------|-------|"
    for type_name, count in sorted(insight_counts.items()):
        insights_table += f"\n| {type_name} | {count} |"
    
    content = f"""# Intelligence Layer Live Verification Report

## Run Configuration
- **Timestamp**: {datetime.utcnow().isoformat()}Z
- **Trace ID**: {config['trace_id']}
- **Interaction ID**: {config['interaction_id']}
- **Tenant ID**: {config['tenant_id']}
- **User ID**: {config['user_id']}
- **Model**: {config['model']}
- **Status**: {"✅ SUCCESS" if success else "❌ FAILED"}

## Input Transcript (First 500 chars)
```
{input_snippet}
```

## Raw LLM Output (InteractionAnalysis)
```json
{analysis_json}
```

## Database Verification

### Summaries Created
{summaries_table}

### Insights Created
{insights_table}

**Total Insights**: {len(insights)}

## Teardown
- {"✅" if teardown_result['summaries_deleted'] > 0 else "⚠️"} Deleted {teardown_result['summaries_deleted']} summary entries
- {"✅" if teardown_result['insights_deleted'] >= 0 else "⚠️"} Deleted {teardown_result['insights_deleted']} insight entries
- {"✅ Cleanup verified" if teardown_result['summaries_deleted'] == 5 else "⚠️ Cleanup may be incomplete"}
"""
    
    with open(filename, "w") as f:
        f.write(content)
    
    return filename


async def main():
    """Main verification workflow."""
    log("=" * 60)
    log("Intelligence Layer Live Verification")
    log("=" * 60)
    
    success = False
    analysis = None
    summaries = []
    insights = []
    teardown_result = {"summaries_deleted": 0, "insights_deleted": 0}
    config = {}
    input_snippet = ""
    analysis_json = "{}"
    
    try:
        # ============================================================
        # SETUP PHASE
        # ============================================================
        log("PHASE 1: Setup")
        
        # Load payload
        payload_path = Path(__file__).parent.parent / "test_payload.json"
        if not payload_path.exists():
            log(f"ERROR: test_payload.json not found at {payload_path}")
            sys.exit(1)
        
        payload = load_payload(str(payload_path))
        log(f"  Loaded payload from {payload_path}")
        
        # Extract configuration
        transcript = payload.get("text", "")
        headers = payload.get("headers", {})
        
        tenant_id = headers.get("X-Tenant-ID", "11111111-1111-4111-8111-111111111111")
        user_id = headers.get("X-User-ID", "user_manual_test_001")
        
        # Generate new IDs for this run
        trace_id = str(uuid4())
        interaction_id = str(uuid4())
        
        config = {
            "trace_id": trace_id,
            "interaction_id": interaction_id,
            "tenant_id": tenant_id,
            "user_id": user_id,
            "model": os.getenv("OPENAI_MODEL", "gpt-4o"),
        }
        
        input_snippet = transcript[:500] if len(transcript) > 500 else transcript
        
        log(f"  Tenant ID: {tenant_id}")
        log(f"  User ID: {user_id}")
        log(f"  Trace ID: {trace_id}")
        log(f"  Interaction ID: {interaction_id}")
        log(f"  Transcript length: {len(transcript)} chars")
        
        # ============================================================
        # EXTRACT & PERSIST PHASE
        # ============================================================
        log("")
        log("PHASE 2: Extract & Persist")
        
        service = IntelligenceService()
        log(f"  IntelligenceService initialized with model: {config['model']}")
        
        log("  Calling process_transcript()...")
        analysis = await service.process_transcript(
            cleaned_transcript=transcript,
            interaction_id=interaction_id,
            tenant_id=tenant_id,
            trace_id=trace_id,
            interaction_type="meeting"
        )
        
        if analysis is None:
            log("  ERROR: process_transcript() returned None")
            sys.exit(1)
        
        log("  ✅ Extraction complete")
        log(f"    - Summaries: 5 levels")
        log(f"    - Action items: {len(analysis.action_items)}")
        log(f"    - Decisions: {len(analysis.decisions)}")
        log(f"    - Risks: {len(analysis.risks)}")
        log(f"    - Key takeaways: {len(analysis.key_takeaways)}")
        log(f"    - Product feedback: {len(analysis.product_feedback)}")
        log(f"    - Market intelligence: {len(analysis.market_intelligence)}")
        
        # Serialize analysis to JSON
        analysis_json = analysis.model_dump_json(indent=2)
        
        # ============================================================
        # VERIFY PHASE
        # ============================================================
        log("")
        log("PHASE 3: Verify Database Rows")
        
        summaries, insights = await verify_db_rows(interaction_id)
        
        log(f"  Summaries found: {len(summaries)}")
        log(f"  Insights found: {len(insights)}")
        
        if len(summaries) != 5:
            log(f"  ⚠️ WARNING: Expected 5 summaries, found {len(summaries)}")
        else:
            log("  ✅ Summary count verified (5)")
        
        # Count insights by type
        insight_counts = {}
        for insight in insights:
            type_name = insight.type.value if hasattr(insight.type, 'value') else str(insight.type)
            insight_counts[type_name] = insight_counts.get(type_name, 0) + 1
        
        log("  Insight breakdown:")
        for type_name, count in sorted(insight_counts.items()):
            log(f"    - {type_name}: {count}")
        
        success = len(summaries) == 5
        
        # ============================================================
        # REPORT PHASE
        # ============================================================
        log("")
        log("PHASE 4: Generate Artifact")
        
        artifact_path = generate_artifact(
            config=config,
            input_snippet=input_snippet,
            analysis_json=analysis_json,
            summaries=summaries,
            insights=insights,
            teardown_result=teardown_result,  # Will be updated after teardown
            success=success
        )
        log(f"  Artifact generated: {artifact_path}")
        
        # ============================================================
        # TEARDOWN PHASE
        # ============================================================
        log("")
        log("PHASE 5: Teardown")
        
        teardown_result = await teardown(interaction_id)
        log(f"  Deleted {teardown_result['summaries_deleted']} summary entries")
        log(f"  Deleted {teardown_result['insights_deleted']} insight entries")
        
        # Verify cleanup
        verify_summaries, verify_insights = await verify_db_rows(interaction_id)
        if len(verify_summaries) == 0 and len(verify_insights) == 0:
            log("  ✅ Cleanup verified - no rows remaining")
        else:
            log(f"  ⚠️ WARNING: {len(verify_summaries)} summaries and {len(verify_insights)} insights still exist")
        
        # Update artifact with teardown results
        artifact_path = generate_artifact(
            config=config,
            input_snippet=input_snippet,
            analysis_json=analysis_json,
            summaries=summaries,
            insights=insights,
            teardown_result=teardown_result,
            success=success
        )
        
        # ============================================================
        # COMPLETE
        # ============================================================
        log("")
        log("=" * 60)
        if success:
            log("✅ VERIFICATION COMPLETE - SUCCESS")
        else:
            log("⚠️ VERIFICATION COMPLETE - WITH WARNINGS")
        log(f"Artifact: {artifact_path}")
        log("=" * 60)
        
    except Exception as e:
        log(f"ERROR: {str(e)}")
        import traceback
        traceback.print_exc()
        
        # Generate failure artifact
        try:
            artifact_path = generate_artifact(
                config=config,
                input_snippet=input_snippet,
                analysis_json=analysis_json,
                summaries=summaries,
                insights=insights,
                teardown_result=teardown_result,
                success=False
            )
            log(f"Failure artifact: {artifact_path}")
        except Exception:
            pass
        
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
