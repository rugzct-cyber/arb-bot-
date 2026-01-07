"""
FastAPI server for HFT multi-bot dashboard
Real-time WebSocket broadcasting and full orderbook API
"""
import asyncio
import json
from typing import List, Dict
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
from pathlib import Path

from ..bot import manager


app = FastAPI(title="HFT Arb Bot API")


class ConnectionManager:
    """Manages WebSocket connections for real-time updates"""
    
    def __init__(self):
        self.active_connections: List[WebSocket] = []
        self._broadcast_queue: asyncio.Queue = asyncio.Queue()
        self._broadcaster_task = None
    
    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)
        print(f"📱 WebSocket connected ({len(self.active_connections)} clients)")
    
    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)
        print(f"📱 WebSocket disconnected ({len(self.active_connections)} clients)")
    
    async def broadcast(self, message: dict):
        """Broadcast message to all connected clients"""
        if not self.active_connections:
            return
        
        message_str = json.dumps(message)
        disconnected = []
        
        for connection in self.active_connections:
            try:
                await connection.send_text(message_str)
            except Exception:
                disconnected.append(connection)
        
        # Clean up disconnected
        for conn in disconnected:
            self.disconnect(conn)
    
    def queue_broadcast(self, message: dict):
        """Queue a message for async broadcasting"""
        try:
            self._broadcast_queue.put_nowait(message)
        except asyncio.QueueFull:
            pass  # Drop if queue is full
    
    async def start_broadcaster(self):
        """Background task for processing broadcast queue"""
        while True:
            try:
                message = await asyncio.wait_for(
                    self._broadcast_queue.get(), 
                    timeout=0.1
                )
                await self.broadcast(message)
            except asyncio.TimeoutError:
                continue
            except Exception as e:
                print(f"Broadcaster error: {e}")


ws_manager = ConnectionManager()


def on_bot_update(bot_data: dict):
    """Callback for bot updates - queue for WebSocket broadcast"""
    ws_manager.queue_broadcast({
        "type": "bot_update",
        "data": bot_data
    })


# Register callback with bot manager
manager.add_update_callback(on_bot_update)


class CreateBotRequest(BaseModel):
    symbol: str
    exchange_a: str
    exchange_b: str
    min_spread: float = 0.15
    max_size: float = 20.0
    poll_interval: int = 50
    use_websocket: bool = False
    dry_run: bool = True


@app.on_event("startup")
async def startup_event():
    """Start background tasks on server startup"""
    asyncio.create_task(ws_manager.start_broadcaster())
    asyncio.create_task(periodic_status_broadcast())


async def periodic_status_broadcast():
    """Periodically broadcast full status to all clients"""
    while True:
        try:
            await asyncio.sleep(1)  # Broadcast every 1 second
            
            if ws_manager.active_connections:
                bots = manager.get_all_bots()
                latencies = manager.get_exchange_latencies()
                
                await ws_manager.broadcast({
                    "type": "status",
                    "data": {
                        "bots": bots,
                        "latencies": latencies,
                        "timestamp": int(asyncio.get_event_loop().time() * 1000),
                    }
                })
        except Exception as e:
            print(f"Status broadcast error: {e}")


@app.get("/")
async def root():
    """Serve the dashboard"""
    frontend_path = Path(__file__).parent.parent.parent / "frontend" / "index.html"
    return FileResponse(frontend_path)


@app.get("/api/status")
async def get_status():
    """Get full system status"""
    return {
        "bots": manager.get_all_bots(),
        "latencies": manager.get_exchange_latencies(),
        "exchanges": ["lighter", "extended", "paradex", "vest"],
    }


@app.get("/api/bots")
async def get_all_bots():
    """Get all bots"""
    return {"bots": manager.get_all_bots()}


@app.get("/api/bots/{bot_id}")
async def get_bot(bot_id: str):
    """Get a single bot"""
    bot = manager.get_bot(bot_id)
    if bot:
        return {"bot": bot}
    return {"error": "Bot not found"}


@app.get("/api/bots/{bot_id}/orderbook")
async def get_bot_orderbook(bot_id: str):
    """Get current orderbooks for a bot"""
    if bot_id not in manager.bots:
        return {"error": "Bot not found"}
    
    bot = manager.bots[bot_id]
    return {
        "exchange_a": bot.orderbooks.exchange_a.to_dict() if bot.orderbooks.exchange_a else None,
        "exchange_b": bot.orderbooks.exchange_b.to_dict() if bot.orderbooks.exchange_b else None,
    }


@app.post("/api/bots")
async def create_bot(req: CreateBotRequest):
    """Create and start a new bot"""
    result = await manager.create_bot(
        symbol=req.symbol,
        exchange_a=req.exchange_a,
        exchange_b=req.exchange_b,
        min_spread=req.min_spread,
        max_size=req.max_size,
        poll_interval=req.poll_interval,
        use_websocket=req.use_websocket,
        dry_run=req.dry_run,
    )
    
    # Broadcast new bot to all clients
    if result.get("success"):
        bots = manager.get_all_bots()
        await ws_manager.broadcast({
            "type": "bots_list",
            "data": {"bots": bots}
        })
    
    return result


@app.post("/api/bots/{bot_id}/stop")
async def stop_bot(bot_id: str):
    """Stop a bot"""
    result = manager.stop_bot(bot_id)
    
    if result.get("success"):
        await ws_manager.broadcast({
            "type": "bots_list",
            "data": {"bots": manager.get_all_bots()}
        })
    
    return result


@app.post("/api/bots/{bot_id}/start")
async def start_bot(bot_id: str):
    """Restart a stopped bot"""
    bot = manager.bots.get(bot_id)
    if not bot:
        return {"success": False, "error": "Bot not found"}
    if bot.running:
        return {"success": False, "error": "Bot already running"}
    
    bot.running = True
    bot.stats.start_time = int(asyncio.get_event_loop().time())
    asyncio.create_task(bot.run())
    
    await ws_manager.broadcast({
        "type": "bots_list",
        "data": {"bots": manager.get_all_bots()}
    })
    
    return {"success": True}


@app.delete("/api/bots/{bot_id}")
async def remove_bot(bot_id: str):
    """Remove a bot completely"""
    result = manager.remove_bot(bot_id)
    
    if result.get("success"):
        await ws_manager.broadcast({
            "type": "bots_list",
            "data": {"bots": manager.get_all_bots()}
        })
    
    return result


@app.get("/api/exchanges")
async def get_exchanges():
    """Get available exchanges"""
    return {"exchanges": ["lighter", "extended", "paradex", "vest"]}


@app.get("/api/latencies")
async def get_latencies():
    """Get exchange latency statistics"""
    return {"latencies": manager.get_exchange_latencies()}


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """WebSocket for real-time updates"""
    await ws_manager.connect(websocket)
    
    # Send initial state
    try:
        await websocket.send_json({
            "type": "init",
            "data": {
                "bots": manager.get_all_bots(),
                "latencies": manager.get_exchange_latencies(),
            }
        })
    except Exception:
        pass
    
    try:
        while True:
            # Receive messages from client (for future commands)
            data = await websocket.receive_text()
            try:
                msg = json.loads(data)
                if msg.get("type") == "ping":
                    await websocket.send_json({"type": "pong"})
            except json.JSONDecodeError:
                pass
    except WebSocketDisconnect:
        ws_manager.disconnect(websocket)


# Mount frontend static files
frontend_path = Path(__file__).parent.parent.parent / "frontend"
if frontend_path.exists():
    app.mount("/static", StaticFiles(directory=str(frontend_path)), name="static")
