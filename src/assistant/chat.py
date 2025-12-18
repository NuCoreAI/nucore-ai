from fastapi import FastAPI, WebSocket
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from base_assistant import get_parser_args
from generic_assistant import NuCoreAssistant as eisyAI
#from openai_assistant import NuCoreAssistant as eisyAI

import uvicorn
import json

app = FastAPI()
eisy_ai=None
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
    global eisy_ai
    await websocket.accept()
    active_connections.append(websocket)
    
    
    try:
        while True:
            data = await websocket.receive_text()    
            
            # Parse the received JSON data
            message_data = json.loads(data)
            user_message = message_data.get("message", "")

            if user_message:
                await eisy_ai.process_customer_input(user_message, websocket=websocket)
            
            
    except Exception as e:
        print(f"Error: {e}")
    finally:
        active_connections.remove(websocket)

def NuCoreChat(args):
    global eisy_ai
    eisy_ai=eisyAI(args)
    
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
    args = get_parser_args()
    NuCoreChat(args)