from fastapi import FastAPI, WebSocket
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from intent_handler.run_intent_runtime import main as run_intent_runtime_main
from intent_handler.run_intent_runtime import _build_parser, _run_once
from utils import get_logger
logger = get_logger(__name__)

import uvicorn
import json

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


# Mount the static files directory
app.mount("/static", StaticFiles(directory="/usr/home/admin/workspace/nucore/nucore-ai/src/assistant/static"), name="static")

# Store active connections
active_connections = []

@app.get("/")
async def get():
    with open("/usr/home/admin/workspace/nucore/nucore-ai/src/assistant/static/index.html") as f:
        return HTMLResponse(f.read())

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    active_connections.append(websocket)
    global eisy_args
    eisy_ai = run_intent_runtime_main(args=eisy_args, websocket=websocket)
    
    try:
        while True:
            data = await websocket.receive_text()    
            
            # Parse the received JSON data
            message_data = json.loads(data)
            user_message = message_data.get("message", "")

            if user_message:
                eisy_ai.reset_stream_handler()
                await _run_once(eisy_ai, user_message)
            
    except Exception as e:
        print(f"Error: {e}")
    finally:
        active_connections.remove(websocket)

def NuCoreChat():
    
    # Run the server with HTTPS
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=8000,  # Standard HTTPS port
        ssl_keyfile="/usr/home/admin/workspace/nucore/nucore-ai/src/assistant/certs/private_key.pem",
        ssl_certfile="/usr/home/admin/workspace/nucore/nucore-ai/src/assistant/certs/certificate.pem",
        #reload=True
    )
    
if __name__ == "__main__":
    eisy_args = _build_parser().parse_args()
    NuCoreChat()