from fastapi import FastAPI, WebSocket
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
import asyncio
from intent_handler.run_intent_runtime import main as run_intent_runtime_main
from intent_handler.run_intent_runtime import _build_parser, _run_once
from utils import get_logger
logger = get_logger(__name__)
import json

import uvicorn
from pathlib import Path

app = FastAPI()
global eisy_args
eisy_args=None
# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # In production, replace with specific origins
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

root_dir = Path(__file__).resolve().parent

global INDEX_HTML, CERT_FILE, KEY_FILE
STATIC_DIR = root_dir / "static" 
CERT_FILE = root_dir / "certs" / "certificate.pem" 
KEY_FILE = root_dir / "certs" / "private_key.pem" 
INDEX_HTML = STATIC_DIR / "index.html"

# Mount the static files directory
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

# Store active connections
active_connections = []

@app.get("/")
async def get():
    global INDEX_HTML
    with open(INDEX_HTML) as f:
        return HTMLResponse(f.read())

async def _process_message_queue(eisy_ai, message_queue: asyncio.Queue):
    while True:
        user_message = await message_queue.get()
        if user_message is None:
            message_queue.task_done()
            break
        try:
            await _run_once(eisy_ai, user_message)
        finally:
            message_queue.task_done()

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    active_connections.append(websocket)
    global eisy_args
    eisy_ai = run_intent_runtime_main(args=eisy_args, websocket=websocket)
    message_queue: asyncio.Queue = asyncio.Queue()
    processor_task = asyncio.create_task(_process_message_queue(eisy_ai, message_queue))
    
    try:
        while True:
            data = await websocket.receive_text()    
            await message_queue.put(data)
            
    except Exception as e:
        print(f"Error: {e}")
    finally:
        await message_queue.put(None)
        await processor_task
        active_connections.remove(websocket)

def NuCoreChat(args=None):
    # Run the server with HTTPS
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=8000,  # Standard HTTPS port
        ssl_keyfile=KEY_FILE if KEY_FILE else None,
        ssl_certfile=CERT_FILE if CERT_FILE else None,
        #reload=True
    )
    
if __name__ == "__main__":
    eisy_args = _build_parser().parse_args()
    NuCoreChat()