"""Intelligence Service for extracting and persisting structured insights.

This service implements the "Sidecar" Intelligence Layer that extracts structured
insights from cleaned transcripts using LLM (GPT-4o via instructor) and persists
them to Postgres (Neon).
"""
import os
import hashlib
import logging
from datetime import datetime
from typing import Optional
from uuid import UUID

import instructor
from sqlmodel import select

from models.extraction_models import InteractionAnalysis
from models.db_models import (
    PersonaModel,
    InteractionSummaryEntryModel,
    InteractionInsightModel,
    SummaryLevelEnum,
    ProfileTypeEnum,
    InsightTypeEnum,
    RiskSeverityDBEnum,
)
from services.database import get_async_session

logger = logging.getLogger(__name__)


class IntelligenceService:
    """Service for extracting and persisting structured intelligence from transcripts.
    
    This service implements Lane 2 of the Async Fork pattern, running concurrently
    with the existing event publishing flow (Lane 1).
    """
    
    def __init__(self):
        """Initialize instructor client with async OpenAI."""
        self.model = os.getenv("OPENAI_MODEL", "gpt-4o")
        
        # Create async instructor client using from_provider
        self.client = instructor.from_provider(
            f"openai/{self.model}",
            async_client=True,
        )
        
        logger.info(f"IntelligenceService initialized with model: {self.model}")
    
    async def process_transcript(
        self,
        cleaned_transcript: str,
        interaction_id: str,
        tenant_id: str,
        trace_id: str,
        interaction_type: str = "meeting",
        account_id: Optional[str] = None,
        interaction_timestamp: Optional[datetime] = None,
        persona_code: str = "gtm"
    ) -> Optional[InteractionAnalysis]:
        """Extract intelligence and persist to database.
        
        Args:
            cleaned_transcript: The cleaned transcript text to analyze.
            interaction_id: Unique identifier for this interaction.
            tenant_id: Tenant identifier for multi-tenancy.
            trace_id: Trace ID for observability.
            interaction_type: Type of interaction (meeting, note, etc.).
            account_id: Optional account identifier.
            interaction_timestamp: When the interaction occurred.
            persona_code: Persona code for extraction context (default: gtm).
            
        Returns:
            InteractionAnalysis if successful, None on any failure.
        """
        logger.info(
            f"Processing transcript: interaction_id={interaction_id}, "
            f"tenant_id={tenant_id}, trace_id={trace_id}"
        )
        
        try:
            # Step 1: Extract intelligence using LLM
            analysis = await self._extract_intelligence(cleaned_transcript)
            
            if analysis is None:
                logger.warning(
                    f"Extraction returned None: interaction_id={interaction_id}"
                )
                return None
            
            # Step 2: Persist to database
            await self._persist_intelligence(
                analysis=analysis,
                interaction_id=interaction_id,
                tenant_id=tenant_id,
                trace_id=trace_id,
                persona_code=persona_code,
                interaction_type=interaction_type,
                account_id=account_id,
                interaction_timestamp=interaction_timestamp or datetime.utcnow()
            )
            
            logger.info(
                f"Intelligence processing complete: interaction_id={interaction_id}, "
                f"summaries=5, action_items={len(analysis.action_items)}, "
                f"decisions={len(analysis.decisions)}, risks={len(analysis.risks)}"
            )
            
            return analysis
            
        except Exception as e:
            logger.error(
                f"Intelligence processing failed: interaction_id={interaction_id}, "
                f"tenant_id={tenant_id}, error={str(e)}",
                exc_info=True
            )
            return None

    
    async def _extract_intelligence(
        self,
        cleaned_transcript: str
    ) -> Optional[InteractionAnalysis]:
        """Use instructor to extract structured data from transcript.
        
        Args:
            cleaned_transcript: The cleaned transcript text.
            
        Returns:
            InteractionAnalysis if successful, None on any failure.
        """
        try:
            result = await self.client.create(
                response_model=InteractionAnalysis,
                messages=[
                    {"role": "system", "content": self._get_system_prompt()},
                    {"role": "user", "content": f"Analyze this transcript:\n\n{cleaned_transcript}"}
                ],
                max_retries=2
            )
            return result
            
        except Exception as e:
            logger.error(f"Intelligence extraction failed: {e}", exc_info=True)
            return None
    
    def _get_system_prompt(self) -> str:
        """GTM-focused system prompt for intelligence extraction."""
        return """You are an expert Go-To-Market (GTM) analyst reviewing customer interaction transcripts.

Your role is to extract actionable intelligence that helps GTM teams:
- Identify sales opportunities and deal risks
- Track customer commitments and action items
- Capture competitive intelligence and market signals
- Surface product feedback for roadmap prioritization

**Extraction Guidelines:**

1. **Summaries**: Write from a GTM leader's perspective, focusing on business impact
   - title: 5-10 word title capturing the essence
   - headline: 1-2 sentence headline for quick scanning
   - brief: 2-3 paragraph executive summary
   - detailed: Comprehensive summary with all key points
   - spotlight: The single most important takeaway

2. **Action Items**: Capture commitments, follow-ups, and next steps with owners when mentioned

3. **Decisions**: Document any agreements, approvals, or strategic choices made

4. **Risks**: Identify deal risks, relationship concerns, or competitive threats with severity levels

5. **Key Takeaways**: Highlight insights valuable for account strategy

6. **Product Feedback**: Note feature requests, pain points, bugs, or UX issues mentioned

7. **Market Intelligence**: Capture competitor mentions, market trends, or industry themes

Be thorough but precise. Only extract information explicitly present in the transcript.
Do not invent or assume information not stated."""
    
    async def _get_persona_id(
        self,
        session,
        persona_code: str = "gtm"
    ) -> UUID:
        """Look up persona UUID by code.
        
        Args:
            session: The database session.
            persona_code: The persona code to look up.
            
        Returns:
            The UUID of the persona.
            
        Raises:
            ValueError: If persona not found.
        """
        result = await session.execute(
            select(PersonaModel).where(PersonaModel.code == persona_code)
        )
        persona = result.scalar_one_or_none()
        
        if not persona:
            raise ValueError(f"Persona '{persona_code}' not found in database")
        
        return persona.id
    
    def _generate_content_hash(self, insight_type: str, content: str) -> str:
        """Generate SHA-256 hash for insight deduplication.
        
        Args:
            insight_type: The type of insight.
            content: The content to hash.
            
        Returns:
            Hex digest of the hash.
        """
        hash_input = f"{insight_type}:{content}"
        return hashlib.sha256(hash_input.encode()).hexdigest()

    
    async def _persist_intelligence(
        self,
        analysis: InteractionAnalysis,
        interaction_id: str,
        tenant_id: str,
        trace_id: str,
        persona_code: str,
        interaction_type: str,
        account_id: Optional[str],
        interaction_timestamp: datetime
    ) -> None:
        """Persist summaries and insights to Postgres in a single transaction.
        
        Creates exactly 5 summary entries (one per level) and one insight row
        per extracted insight item.
        
        Args:
            analysis: The extracted InteractionAnalysis.
            interaction_id: Unique identifier for this interaction.
            tenant_id: Tenant identifier.
            trace_id: Trace ID for observability.
            persona_code: Persona code for lookup.
            interaction_type: Type of interaction.
            account_id: Optional account identifier.
            interaction_timestamp: When the interaction occurred.
        """
        async with get_async_session() as session:
            try:
                # Look up persona ID
                persona_id = await self._get_persona_id(session, persona_code)
                
                # Convert string IDs to UUIDs
                interaction_uuid = UUID(interaction_id)
                tenant_uuid = UUID(tenant_id)
                trace_uuid = UUID(trace_id)
                account_uuid = UUID(account_id) if account_id else None
                
                # Create summary entries (exactly 5, one per level)
                summary_mappings = [
                    (SummaryLevelEnum.title, analysis.summaries.title),
                    (SummaryLevelEnum.headline, analysis.summaries.headline),
                    (SummaryLevelEnum.brief, analysis.summaries.brief),
                    (SummaryLevelEnum.detailed, analysis.summaries.detailed),
                    (SummaryLevelEnum.spotlight, analysis.summaries.spotlight),
                ]
                
                for level, text in summary_mappings:
                    summary_entry = InteractionSummaryEntryModel(
                        tenant_id=tenant_uuid,
                        interaction_id=interaction_uuid,
                        persona_id=persona_id,
                        level=level,
                        text=text,
                        word_count=len(text.split()),
                        profile_type=ProfileTypeEnum.rich,
                        source=f"openai:{self.model}",
                        trace_id=trace_uuid,
                        interaction_type=interaction_type,
                        account_id=account_uuid,
                        interaction_timestamp=interaction_timestamp,
                    )
                    session.add(summary_entry)
                
                # Create insight entries for action items
                for item in analysis.action_items:
                    insight = InteractionInsightModel(
                        tenant_id=tenant_uuid,
                        interaction_id=interaction_uuid,
                        persona_id=persona_id,
                        type=InsightTypeEnum.action_item,
                        description=item.description,
                        owner=item.owner,
                        due_date=datetime.combine(item.due_date, datetime.min.time()) if item.due_date else None,
                        content_hash=self._generate_content_hash("action_item", item.description),
                        trace_id=trace_uuid,
                        interaction_type=interaction_type,
                        account_id=account_uuid,
                        interaction_timestamp=interaction_timestamp,
                    )
                    session.add(insight)
                
                # Create insight entries for decisions
                for item in analysis.decisions:
                    insight = InteractionInsightModel(
                        tenant_id=tenant_uuid,
                        interaction_id=interaction_uuid,
                        persona_id=persona_id,
                        type=InsightTypeEnum.decision_made,
                        decision=item.decision,
                        rationale=item.rationale,
                        content_hash=self._generate_content_hash("decision_made", item.decision),
                        trace_id=trace_uuid,
                        interaction_type=interaction_type,
                        account_id=account_uuid,
                        interaction_timestamp=interaction_timestamp,
                    )
                    session.add(insight)
                
                # Create insight entries for risks
                for item in analysis.risks:
                    severity_db = RiskSeverityDBEnum(item.severity.value) if item.severity else None
                    insight = InteractionInsightModel(
                        tenant_id=tenant_uuid,
                        interaction_id=interaction_uuid,
                        persona_id=persona_id,
                        type=InsightTypeEnum.risk,
                        risk=item.risk,
                        severity=severity_db,
                        mitigation=item.mitigation,
                        content_hash=self._generate_content_hash("risk", item.risk),
                        trace_id=trace_uuid,
                        interaction_type=interaction_type,
                        account_id=account_uuid,
                        interaction_timestamp=interaction_timestamp,
                    )
                    session.add(insight)
                
                # Create insight entries for key takeaways
                for text in analysis.key_takeaways:
                    insight = InteractionInsightModel(
                        tenant_id=tenant_uuid,
                        interaction_id=interaction_uuid,
                        persona_id=persona_id,
                        type=InsightTypeEnum.key_takeaway,
                        text=text,
                        content_hash=self._generate_content_hash("key_takeaway", text),
                        trace_id=trace_uuid,
                        interaction_type=interaction_type,
                        account_id=account_uuid,
                        interaction_timestamp=interaction_timestamp,
                    )
                    session.add(insight)
                
                # Create insight entries for product feedback (DIRECT mapping)
                for item in analysis.product_feedback:
                    insight = InteractionInsightModel(
                        tenant_id=tenant_uuid,
                        interaction_id=interaction_uuid,
                        persona_id=persona_id,
                        type=InsightTypeEnum.product_feedback,
                        text=item.text,
                        content_hash=self._generate_content_hash("product_feedback", item.text),
                        trace_id=trace_uuid,
                        interaction_type=interaction_type,
                        account_id=account_uuid,
                        interaction_timestamp=interaction_timestamp,
                    )
                    session.add(insight)
                
                # Create insight entries for market intelligence (DIRECT mapping)
                for item in analysis.market_intelligence:
                    insight = InteractionInsightModel(
                        tenant_id=tenant_uuid,
                        interaction_id=interaction_uuid,
                        persona_id=persona_id,
                        type=InsightTypeEnum.market_intelligence,
                        text=item.text,
                        content_hash=self._generate_content_hash("market_intelligence", item.text),
                        trace_id=trace_uuid,
                        interaction_type=interaction_type,
                        account_id=account_uuid,
                        interaction_timestamp=interaction_timestamp,
                    )
                    session.add(insight)
                
                # Commit all in single transaction
                await session.commit()
                
                logger.info(
                    f"Persisted intelligence: interaction_id={interaction_id}, "
                    f"summaries=5, insights={len(analysis.action_items) + len(analysis.decisions) + len(analysis.risks) + len(analysis.key_takeaways) + len(analysis.product_feedback) + len(analysis.market_intelligence)}"
                )
                
            except Exception as e:
                await session.rollback()
                logger.error(
                    f"Database persistence failed: interaction_id={interaction_id}, "
                    f"error={str(e)}",
                    exc_info=True
                )
                raise
