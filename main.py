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
from services.event_publisher import EventPublisher
from services.cleaner_service import CleanerService

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

# Call validation at startup
validate_environment()

app = FastAPI()

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

    try:
        deepgram_socket = await process_audio(websocket, session_id) 

        while True:
            data = await websocket.receive_bytes()
            deepgram_socket.send(data)
    except Exception as e:
        logger.error(f"WebSocket error: session_id={session_id}, error={e}")
        raise Exception(f'Could not process audio: {e}')
    finally:
        # Step 1: Retrieve raw transcript
        try:
            raw_transcript = await event_publisher.get_final_transcript(session_id)
            
            if raw_transcript:
                logger.info(
                    f"Retrieved raw transcript: session_id={session_id}, "
                    f"length={len(raw_transcript)} chars"
                )
                
                # Step 2: Clean and structure the transcript
                meeting_output = await cleaner_service.clean_transcript(
                    raw_transcript,
                    session_id
                )
                
                # Step 3: Send structured output to client
                await websocket.send_json({
                    "type": "session_complete",
                    "summary": meeting_output.summary,
                    "action_items": meeting_output.action_items,
                    "cleaned_transcript": meeting_output.cleaned_transcript,
                    "raw_transcript": raw_transcript
                })
                
                logger.info(
                    f"Session complete: session_id={session_id}, "
                    f"action_items={len(meeting_output.action_items)}"
                )
            else:
                logger.warning(f"Session {session_id} had no transcript to retrieve")
                
        except Exception as e:
            logger.error(
                f"Failed to process final transcript: session_id={session_id}, error={e}",
                exc_info=True
            )
        
        logger.info(f"WebSocket disconnected: session_id={session_id}")
        await websocket.close()
