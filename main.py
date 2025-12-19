from fastapi import FastAPI, Request, WebSocket
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from typing import Dict, Callable
from deepgram import Deepgram
from dotenv import load_dotenv
import os
import sys
import uuid
import logging
import json
from services.event_publisher import EventPublisher
from services.cleaner_service import CleanerService
from services.aws_event_publisher import AWSEventPublisher
from routers import batch

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Validate required environment variables
REQUIRED_ENV_VARS = ["DEEPGRAM_API_KEY", "REDIS_URL", "OPENAI_API_KEY"]

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

# Include routers
app.include_router(batch.router)

dg_client = Deepgram(os.getenv('DEEPGRAM_API_KEY'))
event_publisher = EventPublisher()
cleaner_service = CleanerService()

templates = Jinja2Templates(directory="templates")

async def process_audio(fast_socket: WebSocket, session_id: str):
    async def get_transcript(data: Dict) -> None:
        if 'channel' in data:
            transcript = data['channel']['alternatives'][0]['transcript']
        
            if transcript:
                await fast_socket.send_text(transcript)
                
                if data.get('is_final', False):
                    tenant_id = os.getenv('MOCK_TENANT_ID', 'default_org')
                    await event_publisher.publish_transcript_event(
                        transcript=transcript,
                        metadata=data,
                        tenant_id=tenant_id,
                        session_id=session_id
                    )

    deepgram_socket = await connect_to_deepgram(get_transcript)

    return deepgram_socket

async def connect_to_deepgram(transcript_received_handler: Callable[[Dict], None]):
    try:
        socket = await dg_client.transcription.live({'punctuate': True, 'interim_results': False})
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
    
    await websocket.accept()
    logger.info(f"WebSocket connection established: session_id={session_id}")

    deepgram_socket = None
    
    try:
        deepgram_socket = await process_audio(websocket, session_id) 

        while True:
            # Use receive() instead of receive_bytes() to handle both audio and control messages
            message = await websocket.receive()
            
            if "bytes" in message:
                # It's audio data -> Send to Deepgram
                deepgram_socket.send(message["bytes"])
            elif "text" in message:
                # It's a control signal -> Check for stop
                try:
                    data = json.loads(message["text"])
                    if data.get("type") == "stop_recording":
                        logger.info(f"Stop signal received: session_id={session_id}")
                        break  # Exit loop -> Enters 'finally' block -> Triggers Cleaning
                except json.JSONDecodeError:
                    logger.warning(f"Received non-JSON text message: {message['text']}")
                    
    except Exception as e:
        logger.error(f"WebSocket error: session_id={session_id}, error={e}")
    finally:
        # Close Deepgram connection
        if deepgram_socket:
            try:
                deepgram_socket.finish()
                logger.info(f"Deepgram connection closed: session_id={session_id}")
            except Exception as e:
                logger.warning(f"Error closing Deepgram socket: {e}")
        
        # Step 1: Retrieve raw transcript
        try:
            raw_transcript = await event_publisher.get_final_transcript(session_id)
            
            if raw_transcript:
                logger.info(
                    f"Retrieved raw transcript: session_id={session_id}, "
                    f"length={len(raw_transcript)} chars"
                )
                
                # Step 2: Clean and structure the transcript
                logger.info(f"Starting transcript cleaning: session_id={session_id}")
                meeting_output = await cleaner_service.clean_transcript(
                    raw_transcript,
                    session_id
                )
                logger.info(f"Transcript cleaning complete: session_id={session_id}")
                
                # Step 3: Send structured output to client (only if socket is still open)
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
