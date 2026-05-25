"""
ChatSphere - Python FastAPI Backend
Real-time WebSocket Chat Application
Handles: WebSockets, REST API, File Uploads, Message Storage
"""

import asyncio
import json
import os
import uuid
import hashlib
import mimetypes
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Set
from pathlib import Path

from fastapi import (
    FastAPI, WebSocket, WebSocketDisconnect,
    HTTPException, UploadFile, File, Form, Depends, status
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse, FileResponse
from pydantic import BaseModel, validator
import uvicorn

# ─────────────────────────────────────────
#  App Configuration
# ─────────────────────────────────────────
app = FastAPI(
    title="ChatSphere API",
    description="Real-time collaboration & chat platform",
    version="1.0.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Ensure directories exist
UPLOAD_DIR = Path("uploads")
UPLOAD_DIR.mkdir(exist_ok=True)
app.mount("/uploads", StaticFiles(directory="uploads"), name="uploads")

# ─────────────────────────────────────────
#  In-Memory Data Store (scalable to Redis)
# ─────────────────────────────────────────
users_db: Dict[str, dict] = {}          # user_id -> user info
rooms_db: Dict[str, dict] = {}          # room_id -> room info
messages_db: Dict[str, List[dict]] = {} # room_id -> [messages]
typing_status: Dict[str, Set[str]] = {} # room_id -> {user_ids typing}
online_users: Set[str] = set()

# Pre-create default rooms
DEFAULT_ROOMS = [
    {"id": "general", "name": "# general", "type": "group", "description": "General discussions"},
    {"id": "random",  "name": "# random",  "type": "group", "description": "Random chats"},
    {"id": "dev",     "name": "# dev",     "type": "group", "description": "Developer talk"},
]
for r in DEFAULT_ROOMS:
    rooms_db[r["id"]] = {**r, "members": [], "created_at": datetime.utcnow().isoformat()}
    messages_db[r["id"]] = []

# ─────────────────────────────────────────
#  Pydantic Models
# ─────────────────────────────────────────
class UserRegister(BaseModel):
    username: str
    password: str
    display_name: Optional[str] = None
    avatar_color: Optional[str] = "#6366f1"

class UserLogin(BaseModel):
    username: str
    password: str

class MessageCreate(BaseModel):
    room_id: str
    content: str
    message_type: str = "text"  # text | emoji | file | image
    reply_to: Optional[str] = None

class RoomCreate(BaseModel):
    name: str
    description: Optional[str] = ""
    members: Optional[List[str]] = []

# ─────────────────────────────────────────
#  Connection Manager (WebSocket Hub)
# ─────────────────────────────────────────
class ConnectionManager:
    def __init__(self):
        # room_id -> {user_id: WebSocket}
        self.room_connections: Dict[str, Dict[str, WebSocket]] = {}
        # user_id -> WebSocket (for direct notifications)
        self.user_connections: Dict[str, WebSocket] = {}

    async def connect(self, websocket: WebSocket, user_id: str, room_id: str):
        await websocket.accept()
        if room_id not in self.room_connections:
            self.room_connections[room_id] = {}
        self.room_connections[room_id][user_id] = websocket
        self.user_connections[user_id] = websocket
        online_users.add(user_id)

    def disconnect(self, user_id: str, room_id: str):
        if room_id in self.room_connections:
            self.room_connections[room_id].pop(user_id, None)
        self.user_connections.pop(user_id, None)
        online_users.discard(user_id)
        # Remove from typing
        if room_id in typing_status:
            typing_status[room_id].discard(user_id)

    async def broadcast_to_room(self, room_id: str, message: dict, exclude: str = None):
        """Broadcast to all connections in a room."""
        if room_id not in self.room_connections:
            return
        dead = []
        for uid, ws in self.room_connections[room_id].items():
            if uid == exclude:
                continue
            try:
                await ws.send_json(message)
            except Exception:
                dead.append(uid)
        for uid in dead:
            self.room_connections[room_id].pop(uid, None)

    async def send_to_user(self, user_id: str, message: dict):
        """Send directly to a specific user."""
        ws = self.user_connections.get(user_id)
        if ws:
            try:
                await ws.send_json(message)
            except Exception:
                self.user_connections.pop(user_id, None)

    async def broadcast_online_status(self, user_id: str, is_online: bool):
        """Notify everyone of presence change."""
        msg = {"type": "presence", "user_id": user_id, "online": is_online, "timestamp": datetime.utcnow().isoformat()}
        for ws in list(self.user_connections.values()):
            try:
                await ws.send_json(msg)
            except Exception:
                pass

    def get_room_count(self, room_id: str) -> int:
        return len(self.room_connections.get(room_id, {}))

    def total_connections(self) -> int:
        return sum(len(v) for v in self.room_connections.values())

manager = ConnectionManager()

# ─────────────────────────────────────────
#  Helper Functions
# ─────────────────────────────────────────
def hash_password(password: str) -> str:
    return hashlib.sha256(password.encode()).hexdigest()

def make_token(user_id: str) -> str:
    return hashlib.sha256(f"{user_id}{datetime.utcnow()}".encode()).hexdigest()[:32]

def get_user_by_token(token: str) -> Optional[dict]:
    for u in users_db.values():
        if u.get("token") == token:
            return u
    return None

def format_message(msg: dict, users: dict) -> dict:
    sender = users.get(msg["sender_id"], {})
    return {
        **msg,
        "sender_name": sender.get("display_name", "Unknown"),
        "sender_color": sender.get("avatar_color", "#6366f1"),
        "sender_avatar": sender.get("avatar_color", "#6366f1"),
    }

# ─────────────────────────────────────────
#  Auth Routes
# ─────────────────────────────────────────
@app.post("/api/auth/register")
async def register(data: UserRegister):
    if any(u["username"] == data.username for u in users_db.values()):
        raise HTTPException(400, "Username already taken")
    user_id = str(uuid.uuid4())
    token = make_token(user_id)
    user = {
        "id": user_id,
        "username": data.username,
        "display_name": data.display_name or data.username,
        "password_hash": hash_password(data.password),
        "avatar_color": data.avatar_color,
        "token": token,
        "created_at": datetime.utcnow().isoformat(),
        "status": "Hey there! I am using ChatSphere.",
        "rooms": ["general"],
    }
    users_db[user_id] = user
    # Auto-join general room
    rooms_db["general"]["members"].append(user_id)
    return {"token": token, "user": {k: v for k, v in user.items() if k != "password_hash"}}

@app.post("/api/auth/login")
async def login(data: UserLogin):
    user = next((u for u in users_db.values() if u["username"] == data.username), None)
    if not user or user["password_hash"] != hash_password(data.password):
        raise HTTPException(401, "Invalid credentials")
    token = make_token(user["id"])
    users_db[user["id"]]["token"] = token
    return {"token": token, "user": {k: v for k, v in user.items() if k != "password_hash"}}

# ─────────────────────────────────────────
#  User Routes
# ─────────────────────────────────────────
@app.get("/api/users")
async def get_users():
    return [
        {k: v for k, v in u.items() if k not in ("password_hash", "token")}
        for u in users_db.values()
    ]

@app.get("/api/users/online")
async def get_online_users():
    return {"online": list(online_users), "count": len(online_users)}

# ─────────────────────────────────────────
#  Room Routes
# ─────────────────────────────────────────
@app.get("/api/rooms")
async def get_rooms():
    return list(rooms_db.values())

@app.post("/api/rooms")
async def create_room(data: RoomCreate):
    room_id = str(uuid.uuid4())[:8]
    room = {
        "id": room_id,
        "name": f"# {data.name.lower().replace(' ', '-')}",
        "type": "group",
        "description": data.description,
        "members": data.members,
        "created_at": datetime.utcnow().isoformat(),
    }
    rooms_db[room_id] = room
    messages_db[room_id] = []
    return room

@app.post("/api/rooms/{room_id}/join")
async def join_room(room_id: str, user_id: str = Form(...)):
    if room_id not in rooms_db:
        raise HTTPException(404, "Room not found")
    if user_id not in rooms_db[room_id]["members"]:
        rooms_db[room_id]["members"].append(user_id)
    if user_id in users_db and room_id not in users_db[user_id].get("rooms", []):
        users_db[user_id].setdefault("rooms", []).append(room_id)
    return {"joined": True}

# ─────────────────────────────────────────
#  Message Routes
# ─────────────────────────────────────────
@app.get("/api/messages/{room_id}")
async def get_messages(room_id: str, limit: int = 50, offset: int = 0):
    msgs = messages_db.get(room_id, [])
    sliced = msgs[-(limit + offset):][:limit] if offset == 0 else msgs[offset:offset + limit]
    return [format_message(m, users_db) for m in sliced]

@app.post("/api/messages")
async def post_message(data: MessageCreate):
    msg_id = str(uuid.uuid4())
    msg = {
        "id": msg_id,
        "room_id": data.room_id,
        "sender_id": "api_user",
        "content": data.content,
        "message_type": data.message_type,
        "reply_to": data.reply_to,
        "reactions": {},
        "timestamp": datetime.utcnow().isoformat(),
        "edited": False,
    }
    messages_db.setdefault(data.room_id, []).append(msg)
    # Broadcast via WebSocket
    await manager.broadcast_to_room(data.room_id, {"type": "new_message", "message": format_message(msg, users_db)})
    return msg

# ─────────────────────────────────────────
#  File Upload Route
# ─────────────────────────────────────────
@app.post("/api/upload")
async def upload_file(
    file: UploadFile = File(...),
    room_id: str = Form(...),
    sender_id: str = Form(...),
):
    MAX_SIZE = 25 * 1024 * 1024  # 25 MB
    content = await file.read()
    if len(content) > MAX_SIZE:
        raise HTTPException(413, "File too large (max 25MB)")

    ext = Path(file.filename).suffix.lower()
    file_id = f"{uuid.uuid4().hex}{ext}"
    save_path = UPLOAD_DIR / file_id
    save_path.write_bytes(content)

    mime = mimetypes.guess_type(file.filename)[0] or "application/octet-stream"
    is_image = mime.startswith("image/")
    msg_type = "image" if is_image else "file"

    msg = {
        "id": str(uuid.uuid4()),
        "room_id": room_id,
        "sender_id": sender_id,
        "content": file.filename,
        "message_type": msg_type,
        "file_url": f"/uploads/{file_id}",
        "file_name": file.filename,
        "file_size": len(content),
        "mime_type": mime,
        "reactions": {},
        "timestamp": datetime.utcnow().isoformat(),
        "edited": False,
    }
    messages_db.setdefault(room_id, []).append(msg)
    await manager.broadcast_to_room(room_id, {"type": "new_message", "message": format_message(msg, users_db)})
    return msg

# ─────────────────────────────────────────
#  Reaction Route
# ─────────────────────────────────────────
@app.post("/api/messages/{msg_id}/react")
async def react_to_message(msg_id: str, emoji: str = Form(...), user_id: str = Form(...), room_id: str = Form(...)):
    msgs = messages_db.get(room_id, [])
    for msg in msgs:
        if msg["id"] == msg_id:
            if emoji not in msg["reactions"]:
                msg["reactions"][emoji] = []
            if user_id in msg["reactions"][emoji]:
                msg["reactions"][emoji].remove(user_id)
            else:
                msg["reactions"][emoji].append(user_id)
            await manager.broadcast_to_room(room_id, {
                "type": "reaction_update",
                "msg_id": msg_id,
                "reactions": msg["reactions"]
            })
            return msg["reactions"]
    raise HTTPException(404, "Message not found")

# ─────────────────────────────────────────
#  WebSocket Endpoint
# ─────────────────────────────────────────
@app.websocket("/ws/{room_id}/{user_id}")
async def websocket_endpoint(websocket: WebSocket, room_id: str, user_id: str):
    await manager.connect(websocket, user_id, room_id)
    user = users_db.get(user_id, {})

    # Announce join
    await manager.broadcast_to_room(room_id, {
        "type": "user_joined",
        "user_id": user_id,
        "display_name": user.get("display_name", "Unknown"),
        "timestamp": datetime.utcnow().isoformat(),
    }, exclude=user_id)
    await manager.broadcast_online_status(user_id, True)

    try:
        while True:
            raw = await websocket.receive_text()
            data = json.loads(raw)
            event = data.get("type")

            # ── CHAT MESSAGE ──────────────────────────
            if event == "message":
                msg = {
                    "id": str(uuid.uuid4()),
                    "room_id": room_id,
                    "sender_id": user_id,
                    "content": data.get("content", ""),
                    "message_type": data.get("message_type", "text"),
                    "reply_to": data.get("reply_to"),
                    "reactions": {},
                    "timestamp": datetime.utcnow().isoformat(),
                    "edited": False,
                }
                messages_db.setdefault(room_id, []).append(msg)
                broadcast_msg = {
                    "type": "new_message",
                    "message": format_message(msg, users_db)
                }
                # Send to all INCLUDING sender (for multi-tab support)
                await manager.broadcast_to_room(room_id, broadcast_msg)
                # Push notification to offline/other-room users who are members
                room = rooms_db.get(room_id, {})
                for member_id in room.get("members", []):
                    if member_id != user_id and member_id not in manager.room_connections.get(room_id, {}):
                        await manager.send_to_user(member_id, {
                            "type": "notification",
                            "from": user.get("display_name", "Someone"),
                            "room_id": room_id,
                            "room_name": room.get("name", ""),
                            "preview": data.get("content", "")[:60],
                            "timestamp": datetime.utcnow().isoformat(),
                        })

            # ── TYPING INDICATOR ─────────────────────
            elif event == "typing_start":
                typing_status.setdefault(room_id, set()).add(user_id)
                await manager.broadcast_to_room(room_id, {
                    "type": "typing",
                    "user_id": user_id,
                    "display_name": user.get("display_name", ""),
                    "is_typing": True,
                }, exclude=user_id)

            elif event == "typing_stop":
                typing_status.get(room_id, set()).discard(user_id)
                await manager.broadcast_to_room(room_id, {
                    "type": "typing",
                    "user_id": user_id,
                    "is_typing": False,
                }, exclude=user_id)

            # ── READ RECEIPT ─────────────────────────
            elif event == "read":
                await manager.broadcast_to_room(room_id, {
                    "type": "read_receipt",
                    "user_id": user_id,
                    "msg_id": data.get("msg_id"),
                }, exclude=user_id)

            # ── PING (keep-alive) ─────────────────────
            elif event == "ping":
                await websocket.send_json({"type": "pong"})

    except WebSocketDisconnect:
        manager.disconnect(user_id, room_id)
        await manager.broadcast_to_room(room_id, {
            "type": "user_left",
            "user_id": user_id,
            "display_name": user.get("display_name", "Unknown"),
            "timestamp": datetime.utcnow().isoformat(),
        })
        await manager.broadcast_online_status(user_id, False)

# ─────────────────────────────────────────
#  Health / Stats
# ─────────────────────────────────────────
@app.get("/api/health")
async def health():
    return {
        "status": "ok",
        "connections": manager.total_connections(),
        "online_users": len(online_users),
        "rooms": len(rooms_db),
        "messages": sum(len(v) for v in messages_db.values()),
    }

@app.get("/")
async def root():
    return {"message": "ChatSphere API running!", "docs": "/docs"}

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True, log_level="info")
