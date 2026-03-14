from fastapi import FastAPI, Request, WebSocket
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from typing import Dict, Callable, Optional, Any
from deepgram import Deepgram
from dotenv import load_dotenv
import os
import sys
import uuid
import asyncio
import logging
import json
from datetime import datetime, timezone
from services.event_publisher import EventPublisher
from services.cleaner_service import CleanerService
from services.aws_event_publisher import AWSEventPublisher
from services.intelligence_service import IntelligenceService
from services.transcript_enrichment import TranscriptEnrichmentService
from models.envelope import EnvelopeV1, ContentModel
from models.request_context import RequestContext
from middleware.jwt_auth import verify_internal_jwt, extract_bearer_token, JWTVerificationError
from routers import batch
from routers import text
from routers import upload
from routers.upload import reap_stuck_jobs

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Validate required environment variables
REQUIRED_ENV_VARS = ["DEEPGRAM_API_KEY", "REDIS_URL", "OPENAI_API_KEY", "DATABASE_URL"]

def validate_environment():
    """Validate that all required environment variables are set."""
    missing = [var for var in REQUIRED_ENV_VARS if not os.getenv(var)]
    if missing:
        logger.error(f"Missing required environment variables: {missing}")
        sys.exit(1)
    logger.info("Environment validation passed")

def validate_aws_credentials():
    """
    Validate AWS credentials and log EventBridge integration status.
    
    This function checks for AWS credentials and logs the configuration.
    If credentials are missing, EventBridge integration will be disabled
    but the application will continue to run.
    """
    aws_access_key = os.getenv("AWS_ACCESS_KEY_ID")
    aws_secret_key = os.getenv("AWS_SECRET_ACCESS_KEY")
    
    if aws_access_key and aws_secret_key:
        # Credentials present - log enabled status
        aws_region = os.getenv("AWS_REGION", "us-east-1")
        eventbridge_bus = os.getenv("EVENTBRIDGE_BUS_NAME", "default")
        event_source = os.getenv("EVENT_SOURCE", "com.yourapp.transcription")
        
        logger.info("=" * 60)
        logger.info("EventBridge integration ENABLED")
        logger.info(f"  AWS Region: {aws_region}")
        logger.info(f"  EventBridge Bus: {eventbridge_bus}")
        logger.info(f"  Event Source: {event_source}")
        logger.info("=" * 60)
    else:
        # Credentials missing - log warning
        logger.warning("=" * 60)
        logger.warning("EventBridge integration DISABLED")
        logger.warning("Missing AWS credentials (AWS_ACCESS_KEY_ID and/or AWS_SECRET_ACCESS_KEY)")
        logger.warning("Batch processing will continue without event publishing")
        logger.warning("=" * 60)

# Call validation at startup
validate_environment()
validate_aws_credentials()

app = FastAPI()


@app.on_event("startup")
async def startup_event():
    """Run startup tasks including stuck job cleanup."""
    logger.info("Running startup tasks...")
    await reap_stuck_jobs()
    logger.info("Startup tasks completed")


@app.get("/health")
async def health_check():
    return {"status": "ok"}


# Include routers
app.include_router(batch.router)
app.include_router(text.router, prefix="/text", tags=["text"])
app.include_router(upload.router)

dg_client = Deepgram(os.getenv('DEEPGRAM_API_KEY'))
event_publisher = EventPublisher()
cleaner_service = CleanerService()

templates = Jinja2Templates(directory="templates")

async def process_audio(
    fast_socket: WebSocket,
    session_id: str,
    context: Optional[RequestContext] = None,
    desktop_config: Optional[Dict[str, Any]] = None,
):
    """Set up Deepgram connection and transcript callback.

    Args:
        fast_socket: Client WebSocket to stream transcripts back to.
        session_id: Unique session identifier.
        context: Authenticated request context (from JWT). None for legacy browser sessions.
        desktop_config: Desktop session_config payload. None for legacy browser sessions.
    """
    is_desktop = desktop_config is not None
    user_name = (context.user_name if context else None) or "You"
    tenant_id = context.tenant_id if context else os.getenv('MOCK_TENANT_ID', 'default_org')

    async def get_transcript(data: Dict) -> None:
        if 'channel' not in data:
            return
        transcript = data['channel']['alternatives'][0]['transcript']
        if not transcript:
            return

        if is_desktop and data.get('is_final', False):
            # Desktop multichannel: store structured segment
            channel_idx = data.get('channel_index', [0, 1])[0]
            speaker = user_name if channel_idx == 0 else "Others"
            start_time = data.get('start', 0.0)
            confidence = data['channel']['alternatives'][0].get('confidence', 0.0)

            await event_publisher.publish_structured_segment(
                channel=channel_idx,
                speaker=speaker,
                text=transcript,
                timestamp=start_time,
                confidence=confidence,
                metadata=data,
                tenant_id=tenant_id,
                session_id=session_id,
            )

            # Stream labeled chunk to desktop client
            await fast_socket.send_json({
                "type": "transcript_chunk",
                "speaker": speaker,
                "channel": channel_idx,
                "text": transcript,
                "timestamp": start_time,
            })
        else:
            # Legacy browser: send plain text, store plain string
            await fast_socket.send_text(transcript)

            if data.get('is_final', False):
                await event_publisher.publish_transcript_event(
                    transcript=transcript,
                    metadata=data,
                    tenant_id=tenant_id,
                    session_id=session_id,
                )

    # Build Deepgram options based on session type
    dg_options: Dict[str, Any] = {
        'punctuate': True,
        'interim_results': False,
        'smart_format': True,
    }
    if is_desktop:
        audio_cfg = desktop_config.get('audio', {})
        dg_options.update({
            'model': 'nova-3',
            'multichannel': True,
            'channels': audio_cfg.get('channels', 2),
            'sample_rate': audio_cfg.get('sample_rate', 16000),
            'encoding': audio_cfg.get('encoding', 'linear16'),
            'endpointing': 300,          # 300ms silence before finalizing (prevents word splitting at pauses)
            'filler_words': True,        # Include um, uh, mhm (otherwise perceived as skipped words)
        })

    deepgram_socket = await connect_to_deepgram(get_transcript, dg_options)
    return deepgram_socket


async def connect_to_deepgram(
    transcript_received_handler: Callable[[Dict], None],
    options: Optional[Dict[str, Any]] = None,
):
    try:
        dg_opts = options or {'punctuate': True, 'interim_results': False}
        socket = await dg_client.transcription.live(dg_opts)
        socket.registerHandler(socket.event.CLOSE, lambda c: print(f'Connection closed with code {c}.'))
        socket.registerHandler(socket.event.TRANSCRIPT_RECEIVED, transcript_received_handler)
        return socket
    except Exception as e:
        raise Exception(f'Could not open socket: {e}')
 
@app.get("/", response_class=HTMLResponse)
def get(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

@app.websocket("/listen")
async def websocket_endpoint(websocket: WebSocket):
    # Generate unique session ID
    session_id = str(uuid.uuid4())

    # --- JWT Authentication (optional, backward compatible) ---
    context: Optional[RequestContext] = None
    auth_header = websocket.headers.get("authorization")
    token = extract_bearer_token(auth_header)

    if token:
        try:
            claims = verify_internal_jwt(token)
            context = RequestContext(
                tenant_id=claims.tenant_id,
                user_id=claims.user_id,
                pg_user_id=claims.pg_user_id,
                user_name=claims.user_name,
                account_id=None,
                interaction_id=session_id,
                trace_id=str(uuid.uuid4()),
            )
            logger.info(
                f"WebSocket authenticated via JWT: session_id={session_id}, "
                f"tenant={claims.tenant_id[:8]}..."
            )
        except JWTVerificationError as e:
            logger.warning(f"WebSocket JWT auth failed: {e.code}, session_id={session_id}")
            await websocket.close(code=4001, reason=e.message)
            return

    await websocket.accept()
    logger.info(f"WebSocket connection established: session_id={session_id}")

    deepgram_socket = None
    desktop_config: Optional[Dict[str, Any]] = None

    try:
        # --- Wait for first message: could be session_config (desktop) or audio (browser) ---
        first_message = await websocket.receive()

        if "text" in first_message:
            try:
                data = json.loads(first_message["text"])
                if data.get("type") == "session_config":
                    desktop_config = data
                    logger.info(
                        f"Desktop session configured: session_id={session_id}, "
                        f"source={data.get('source')}, platform={data.get('platform')}, "
                        f"channels={data.get('audio', {}).get('channels', 'N/A')}"
                    )
            except json.JSONDecodeError:
                pass

        # --- Create Deepgram connection with appropriate config ---
        deepgram_socket = await process_audio(
            websocket, session_id, context=context, desktop_config=desktop_config,
        )

        # If the first message was audio (not session_config), forward it now
        if desktop_config is None and "bytes" in first_message:
            deepgram_socket.send(first_message["bytes"])

        # --- Main message loop ---
        while True:
            message = await websocket.receive()

            if "bytes" in message:
                deepgram_socket.send(message["bytes"])
            elif "text" in message:
                try:
                    data = json.loads(message["text"])
                    msg_type = data.get("type")

                    if msg_type == "stop_recording":
                        logger.info(f"Stop signal received: session_id={session_id}")
                        break

                    elif msg_type == "session_reauth" and context is not None:
                        # Desktop token refresh: validate new JWT, update context
                        new_token = data.get("token")
                        if new_token:
                            try:
                                new_claims = verify_internal_jwt(new_token)
                                context = RequestContext(
                                    tenant_id=new_claims.tenant_id,
                                    user_id=new_claims.user_id,
                                    pg_user_id=new_claims.pg_user_id,
                                    user_name=new_claims.user_name,
                                    account_id=context.account_id,
                                    interaction_id=context.interaction_id,
                                    trace_id=context.trace_id,
                                )
                                logger.info(f"Session reauth successful: session_id={session_id}")
                            except JWTVerificationError as e:
                                logger.warning(
                                    f"Session reauth failed: {e.code}, session_id={session_id}"
                                )
                                # Non-fatal: continue with previous context

                except json.JSONDecodeError:
                    logger.warning(f"Received non-JSON text message: {message['text']}")

    except Exception as e:
        logger.error(f"WebSocket error: session_id={session_id}, error={e}")
    finally:
        # Close Deepgram connection
        if deepgram_socket:
            try:
                await deepgram_socket.finish()
                logger.info(f"Deepgram connection closed: session_id={session_id}")
            except Exception as e:
                logger.warning(f"Error closing Deepgram socket: {e}")

        # --- Finalization: retrieve transcript, clean, publish ---
        try:
            raw_transcript = await event_publisher.get_final_transcript(session_id)

            if raw_transcript:
                logger.info(
                    f"Retrieved raw transcript: session_id={session_id}, "
                    f"length={len(raw_transcript)} chars"
                )

                # Send raw transcript to client immediately (Phase 1)
                # This arrives in ~2s. Client can save + display while GPT-4o works.
                try:
                    await websocket.send_json({
                        "type": "session_transcript_ready",
                        "raw_transcript": raw_transcript,
                        "session_id": session_id,
                    })
                    logger.info(f"session_transcript_ready sent: session_id={session_id}")
                except Exception as e:
                    logger.warning(
                        f"Could not send session_transcript_ready: "
                        f"session_id={session_id}, error={e}"
                    )

                # Step 2: Enrich transcript with calendar event contacts
                enrichment_service = TranscriptEnrichmentService()
                transcript_ts = datetime.now(timezone.utc)
                conference_url_val = desktop_config.get("conference_url") if desktop_config else None
                enrichment = await enrichment_service.enrich(
                    tenant_id=context.tenant_id if context else os.getenv('MOCK_TENANT_ID', 'default_org'),
                    transcript_timestamp=transcript_ts,
                    raw_transcript=raw_transcript,
                    conference_url=conference_url_val,
                    user_name=context.user_name if context else None,
                    account_id=context.account_id if context else None,
                )

                # Prepend front-matter before cleaning
                text_for_cleaning = raw_transcript
                if enrichment.front_matter:
                    text_for_cleaning = enrichment.front_matter + "\n\n" + raw_transcript

                # Step 3: Clean and structure the transcript
                logger.info(f"Starting transcript cleaning: session_id={session_id}")
                meeting_output = await cleaner_service.clean_transcript(
                    text_for_cleaning,
                    session_id
                )
                logger.info(f"Transcript cleaning complete: session_id={session_id}")

                # Step 3: Send structured output to client
                try:
                    await websocket.send_json({
                        "type": "session_complete",
                        "summary": meeting_output.summary,
                        "action_items": meeting_output.action_items,
                        "cleaned_transcript": meeting_output.cleaned_transcript,
                        "raw_transcript": raw_transcript
                    })
                    logger.info(
                        f"Session complete message sent: session_id={session_id}, "
                        f"action_items={len(meeting_output.action_items)}"
                    )
                except Exception as e:
                    logger.warning(
                        f"Could not send session_complete (socket may be closed): "
                        f"session_id={session_id}, error={e}"
                    )

                # Step 5: Async Fork — Lane 1 (publish) + Lane 2 (intelligence)
                ws_tenant_id = context.tenant_id if context else os.getenv('MOCK_TENANT_ID', 'default_org')
                ws_user_id = context.user_id if context else "websocket_user"
                ws_trace_id = context.trace_id if context else str(uuid.uuid4())
                source = "desktop-companion" if desktop_config else "websocket"
                extras: Dict[str, Any] = {}
                if desktop_config:
                    extras["platform"] = desktop_config.get("platform", "unknown")
                    extras["device_id"] = desktop_config.get("device_id", "unknown")
                if context and context.user_name:
                    extras["user_name"] = context.user_name

                # Add enrichment metadata to extras
                extras.update(enrichment.to_extras_dict())

                # Include front-matter in content.text for downstream LLMs
                content_text = meeting_output.cleaned_transcript
                if enrichment.front_matter:
                    content_text = enrichment.front_matter + "\n\n" + meeting_output.cleaned_transcript

                async def _lane1_publish() -> Optional[dict]:
                    """Lane 1: Publish envelope to Kinesis/EventBridge."""
                    try:
                        envelope = EnvelopeV1(
                            tenant_id=uuid.UUID(ws_tenant_id) if len(ws_tenant_id) == 36 else uuid.uuid4(),
                            user_id=ws_user_id,
                            interaction_type="meeting",
                            content=ContentModel(text=content_text, format="diarized"),
                            timestamp=transcript_ts,
                            source=source,
                            extras=extras,
                            interaction_id=uuid.UUID(session_id),
                            trace_id=ws_trace_id,
                            account_id=None,
                        )
                        aws_publisher = AWSEventPublisher()
                        return await aws_publisher.publish_envelope(envelope)
                    except Exception as e:
                        logger.error(f"Lane 1 (publishing) error: session_id={session_id}, error={e}")
                        raise

                async def _lane2_intelligence() -> Optional[object]:
                    """Lane 2: Extract and persist intelligence."""
                    try:
                        intelligence_service = IntelligenceService()
                        return await intelligence_service.process_transcript(
                            cleaned_transcript=meeting_output.cleaned_transcript,
                            interaction_id=session_id,
                            tenant_id=ws_tenant_id,
                            trace_id=ws_trace_id,
                            interaction_type="meeting",
                            contact_ids=enrichment.contact_ids or None,
                            calendar_event_id=enrichment.calendar_event_id,
                            enrichment_confidence=enrichment.match_confidence,
                            enrichment_match_method=enrichment.match_method,
                        )
                    except Exception as e:
                        logger.error(f"Lane 2 (intelligence) error: session_id={session_id}, error={e}")
                        raise

                results = await asyncio.gather(
                    _lane1_publish(),
                    _lane2_intelligence(),
                    return_exceptions=True
                )

                for i, result in enumerate(results):
                    if isinstance(result, Exception):
                        lane_name = "Lane 1 (publishing)" if i == 0 else "Lane 2 (intelligence)"
                        logger.error(
                            f"{lane_name} failed: session_id={session_id}, error={result}",
                            exc_info=result
                        )
                    else:
                        lane_name = "Lane 1 (publishing)" if i == 0 else "Lane 2 (intelligence)"
                        logger.info(f"{lane_name} completed: session_id={session_id}")

            else:
                logger.warning(f"Session {session_id} had no transcript to retrieve")

        except Exception as e:
            logger.error(
                f"Failed to process final transcript: session_id={session_id}, error={e}",
                exc_info=True
            )

        # Close WebSocket
        try:
            await websocket.close()
            logger.info(f"WebSocket closed: session_id={session_id}")
        except Exception as e:
            logger.debug(f"WebSocket already closed: session_id={session_id}")
