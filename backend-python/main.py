"""
ChatSphere - Premium Python FastAPI Backend 
Features: Persistent SQLite, Global WebSockets, DMs, Groups, Media Uploads, Message Deletion, View-Once Popups
"""

import asyncio
import json
import os
import uuid
import hashlib
import mimetypes
import aiofiles
import aiosqlite
from datetime import datetime
from typing import Dict, List, Optional, Set
from pathlib import Path

from fastapi import (
    FastAPI, WebSocket, WebSocketDisconnect,
    HTTPException, UploadFile, File, Form, Depends
)
from fastapi.responses import FileResponse  # <-- Added for serving frontend files
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import uvicorn

app = FastAPI(title="ChatSphere Nexus")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

UPLOAD_DIR = Path("uploads")
UPLOAD_DIR.mkdir(exist_ok=True)
app.mount("/uploads", StaticFiles(directory="uploads"), name="uploads")

DB_PATH = "chatsphere.db"

# ─────────────────────────────────────────
#  Root Router (Serves Frontend)
# ─────────────────────────────────────────
@app.get("/")
async def serve_frontend():
    """
    Resilient path lookup to locate index.html on both local machines 
    and remote production Linux containers (Render).
    """
    potential_paths = [
        Path(__file__).resolve().parent.parent / "frontend" / "public" / "index.html",  # Repo root structure
        Path("frontend/public/index.html"),                                             # Relative execution path
        Path("index.html")                                                              # Root fallback
    ]
    
    for path in potential_paths:
        if path.exists():
            return FileResponse(path)
            
    raise HTTPException(status_code=404, detail="Frontend index.html file not found.")

# ─────────────────────────────────────────
#  Database Initialization
# ─────────────────────────────────────────
async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY, username TEXT UNIQUE, password_hash TEXT,
                display_name TEXT, avatar_url TEXT, bio TEXT, token TEXT
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS rooms (
                id TEXT PRIMARY KEY, name TEXT, is_dm BOOLEAN, created_at TEXT
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS room_members (
                room_id TEXT, user_id TEXT, PRIMARY KEY(room_id, user_id)
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                id TEXT PRIMARY KEY, room_id TEXT, sender_id TEXT, content TEXT,
                message_type TEXT, is_view_once BOOLEAN, is_viewed BOOLEAN,
                file_url TEXT, file_name TEXT, timestamp TEXT
            )
        """)
        await db.commit()

@app.on_event("startup")
async def startup():
    await init_db()

async def get_db():
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        yield db

# ─────────────────────────────────────────
#  Models & Global Connection Manager
# ─────────────────────────────────────────
class UserAuth(BaseModel):
    username: str
    password: str
    display_name: Optional[str] = None

class ProfileUpdate(BaseModel):
    display_name: str
    bio: str
    avatar_url: str

class GroupCreate(BaseModel):
    name: str
    member_ids: List[str]

class DMCreate(BaseModel):
    target_user_id: str

def hash_pass(password: str) -> str:
    return hashlib.sha256(password.encode()).hexdigest()

class ConnectionManager:
    def __init__(self):
        self.active_connections: Dict[str, WebSocket] = {}

    async def connect(self, websocket: WebSocket, user_id: str):
        await websocket.accept()
        self.active_connections[user_id] = websocket

    def disconnect(self, user_id: str):
        self.active_connections.pop(user_id, None)

    async def send_to_user(self, user_id: str, message: dict):
        ws = self.active_connections.get(user_id)
        if ws:
            try:
                await ws.send_json(message)
            except:
                self.disconnect(user_id)

manager = ConnectionManager()

# ─────────────────────────────────────────
#  REST API Routes
# ─────────────────────────────────────────
@app.post("/api/auth/register")
async def register(data: UserAuth, db: aiosqlite.Connection = Depends(get_db)):
    cursor = await db.execute("SELECT id FROM users WHERE username = ?", (data.username,))
    if await cursor.fetchone(): raise HTTPException(400, "Username taken")
    
    user_id = str(uuid.uuid4())
    token = uuid.uuid4().hex
    await db.execute(
        "INSERT INTO users (id, username, password_hash, display_name, avatar_url, bio, token) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (user_id, data.username, hash_pass(data.password), data.display_name or data.username, "", "Available", token)
    )
    await db.commit()
    return {"token": token, "user": {"id": user_id, "username": data.username, "display_name": data.display_name, "avatar_url": "", "bio": "Available"}}

@app.post("/api/auth/login")
async def login(data: UserAuth, db: aiosqlite.Connection = Depends(get_db)):
    cursor = await db.execute("SELECT id, username, display_name, avatar_url, bio FROM users WHERE username = ? AND password_hash = ?", (data.username, hash_pass(data.password)))
    row = await cursor.fetchone()
    if not row: raise HTTPException(401, "Invalid credentials")
    
    token = uuid.uuid4().hex
    await db.execute("UPDATE users SET token = ? WHERE id = ?", (token, row['id']))
    await db.commit()
    return {"token": token, "user": dict(row)}

@app.get("/api/users")
async def get_users(db: aiosqlite.Connection = Depends(get_db)):
    cursor = await db.execute("SELECT id, username, display_name, avatar_url, bio FROM users")
    return [dict(r) for r in await cursor.fetchall()]

@app.put("/api/users/{user_id}")
async def update_profile(user_id: str, data: ProfileUpdate, db: aiosqlite.Connection = Depends(get_db)):
    await db.execute("UPDATE users SET display_name = ?, bio = ?, avatar_url = ? WHERE id = ?", 
                    (data.display_name, data.bio, data.avatar_url, user_id))
    await db.commit()
    return {"status": "success"}

@app.delete("/api/users/{user_id}")
async def delete_account(user_id: str, db: aiosqlite.Connection = Depends(get_db)):
    await db.execute("DELETE FROM users WHERE id = ?", (user_id,))
    await db.execute("DELETE FROM room_members WHERE user_id = ?", (user_id,))
    await db.commit()
    return {"status": "deleted"}

@app.get("/api/rooms/{user_id}")
async def get_rooms(user_id: str, db: aiosqlite.Connection = Depends(get_db)):
    cursor = await db.execute("""
        SELECT r.id, r.name, r.is_dm 
        FROM rooms r 
        JOIN room_members rm ON r.id = rm.room_id 
        WHERE rm.user_id = ?
        ORDER BY r.created_at DESC
    """, (user_id,))
    rooms = [dict(r) for r in await cursor.fetchall()]
    
    for r in rooms:
        if r['is_dm']:
            c2 = await db.execute("""
                SELECT u.display_name FROM users u 
                JOIN room_members rm ON u.id = rm.user_id 
                WHERE rm.room_id = ? AND u.id != ?
            """, (r['id'], user_id))
            other = await c2.fetchone()
            if other: r['name'] = other['display_name']
    return rooms

@app.post("/api/rooms/dm/{user_id}")
async def create_dm(user_id: str, data: DMCreate, db: aiosqlite.Connection = Depends(get_db)):
    cursor = await db.execute("""
        SELECT rm1.room_id FROM room_members rm1
        JOIN room_members rm2 ON rm1.room_id = rm2.room_id
        JOIN rooms r ON r.id = rm1.room_id
        WHERE r.is_dm = 1 AND rm1.user_id = ? AND rm2.user_id = ?
    """, (user_id, data.target_user_id))
    existing = await cursor.fetchone()
    if existing: return {"id": existing['room_id']}

    room_id = str(uuid.uuid4())
    await db.execute("INSERT INTO rooms (id, name, is_dm, created_at) VALUES (?, '', 1, ?)", (room_id, datetime.utcnow().isoformat()))
    await db.execute("INSERT INTO room_members (room_id, user_id) VALUES (?, ?)", (room_id, user_id))
    await db.execute("INSERT INTO room_members (room_id, user_id) VALUES (?, ?)", (room_id, data.target_user_id))
    await db.commit()
    return {"id": room_id}

@app.post("/api/rooms/group/{user_id}")
async def create_group(user_id: str, data: GroupCreate, db: aiosqlite.Connection = Depends(get_db)):
    room_id = str(uuid.uuid4())
    await db.execute("INSERT INTO rooms (id, name, is_dm, created_at) VALUES (?, ?, 0, ?)", (room_id, data.name, datetime.utcnow().isoformat()))
    
    members = set(data.member_ids)
    members.add(user_id)
    for m_id in members:
        await db.execute("INSERT INTO room_members (room_id, user_id) VALUES (?, ?)", (room_id, m_id))
    await db.commit()
    return {"id": room_id}

@app.get("/api/messages/{room_id}")
async def get_messages(room_id: str, db: aiosqlite.Connection = Depends(get_db)):
    cursor = await db.execute("""
        SELECT m.*, u.display_name as sender_name, u.avatar_url as sender_avatar 
        FROM messages m 
        LEFT JOIN users u ON m.sender_id = u.id 
        WHERE m.room_id = ? AND (m.is_view_once = 0 OR m.is_viewed = 0)
        ORDER BY m.timestamp ASC LIMIT 100
    """, (room_id,))
    
    msgs = [dict(r) for r in await cursor.fetchall()]
    for m in msgs:
        if m['sender_name'] is None and m['sender_id'] != 'api':
            m['sender_name'] = "Deleted User"
            m['sender_avatar'] = ""
    return msgs

# ─────────────────────────────────────────
#  File Uploads (Media & Avatars)
# ─────────────────────────────────────────
@app.post("/api/upload")
async def upload_media(room_id: str = Form(...), user_id: str = Form(...), is_view_once: bool = Form(False), file: UploadFile = File(...), db: aiosqlite.Connection = Depends(get_db)):
    MAX_SIZE = 25 * 1024 * 1024
    ext = Path(file.filename).suffix.lower()
    file_id = f"media_{uuid.uuid4().hex}{ext}"
    save_path = UPLOAD_DIR / file_id
    
    file_size = 0
    async with aiofiles.open(save_path, 'wb') as out_file:
        while content := await file.read(1024 * 1024):
            file_size += len(content)
            if file_size > MAX_SIZE:
                save_path.unlink(missing_ok=True)
                raise HTTPException(413, "File too large")
            await out_file.write(content)

    mime = mimetypes.guess_type(file.filename)[0] or "application/octet-stream"
    msg_type = "image" if mime.startswith("image/") else "file"
    file_url = f"/uploads/{file_id}"
    
    msg_id = str(uuid.uuid4())
    now = datetime.utcnow().isoformat()
    
    await db.execute("""
        INSERT INTO messages (id, room_id, sender_id, content, message_type, is_view_once, is_viewed, file_url, file_name, timestamp)
        VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?, ?)
    """, (msg_id, room_id, user_id, file.filename, msg_type, is_view_once, file_url, file.filename, now))
    await db.commit()
    
    c_user = await db.execute("SELECT display_name, avatar_url FROM users WHERE id = ?", (user_id,))
    user_info = dict(await c_user.fetchone() or {})
    
    c_room = await db.execute("SELECT name, is_dm FROM rooms WHERE id = ?", (room_id,))
    room_info = await c_room.fetchone()
    
    broadcast_msg = {
        "type": "new_message",
        "message": {
            "id": msg_id, "room_id": room_id, "sender_id": user_id,
            "content": file.filename, "message_type": msg_type,
            "file_url": file_url, "file_name": file.filename,
            "is_view_once": is_view_once, "sender_name": user_info.get("display_name"),
            "sender_avatar": user_info.get("avatar_url"),
            "room_name": room_info['name'] if room_info else "Chat", "is_dm": room_info['is_dm'] if room_info else False
        }
    }
    
    c_mem = await db.execute("SELECT user_id FROM room_members WHERE room_id = ?", (room_id,))
    for m in await c_mem.fetchall():
        target_id = m['user_id']
        if room_info and room_info['is_dm'] and target_id != user_id:
            broadcast_msg["message"]["room_name"] = user_info.get("display_name")
        await manager.send_to_user(target_id, broadcast_msg)
        
    return {"status": "success"}

@app.post("/api/profile/avatar")
async def upload_avatar(user_id: str = Form(...), file: UploadFile = File(...), db: aiosqlite.Connection = Depends(get_db)):
    ext = Path(file.filename).suffix.lower()
    file_id = f"avatar_{uuid.uuid4().hex}{ext}"
    save_path = UPLOAD_DIR / file_id
    
    async with aiofiles.open(save_path, 'wb') as out_file:
        while content := await file.read(1024 * 1024):
            await out_file.write(content)
            
    avatar_url = f"/uploads/{file_id}"
    await db.execute("UPDATE users SET avatar_url = ? WHERE id = ?", (avatar_url, user_id))
    await db.commit()
    return {"avatar_url": avatar_url}

# ─────────────────────────────────────────
#  Global WebSocket Endpoint
# ─────────────────────────────────────────
@app.websocket("/ws/{user_id}")
async def websocket_endpoint(websocket: WebSocket, user_id: str):
    await manager.connect(websocket, user_id)
    
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        
        cursor = await db.execute("SELECT display_name, avatar_url FROM users WHERE id = ?", (user_id,))
        user_info = dict(await cursor.fetchone() or {})
        
        try:
            while True:
                data = await websocket.receive_json()
                event = data.get("type")

                if event == "message":
                    room_id = data.get("room_id")
                    content = data.get("content", "")
                    is_vo = data.get("is_view_once", False)
                    msg_id = str(uuid.uuid4())
                    now = datetime.utcnow().isoformat()
                    
                    await db.execute("""
                        INSERT INTO messages (id, room_id, sender_id, content, message_type, is_view_once, is_viewed, timestamp)
                        VALUES (?, ?, ?, ?, 'text', ?, 0, ?)
                    """, (msg_id, room_id, user_id, content, is_vo, now))
                    await db.commit()
                    
                    c2 = await db.execute("SELECT name, is_dm FROM rooms WHERE id = ?", (room_id,))
                    room_info = await c2.fetchone()
                    
                    c3 = await db.execute("SELECT user_id FROM room_members WHERE room_id = ?", (room_id,))
                    members = await c3.fetchall()
                    
                    broadcast_msg = {
                        "type": "new_message",
                        "message": {
                            "id": msg_id, "room_id": room_id, "sender_id": user_id,
                            "content": content, "message_type": "text",
                            "is_view_once": is_vo, "sender_name": user_info.get("display_name"),
                            "sender_avatar": user_info.get("avatar_url"),
                            "room_name": room_info['name'] if room_info else "Chat",
                            "is_dm": room_info['is_dm'] if room_info else False
                        }
                    }
                    
                    for m in members:
                        target_id = m['user_id']
                        if room_info and room_info['is_dm'] and target_id != user_id:
                            broadcast_msg["message"]["room_name"] = user_info.get("display_name")
                        await manager.send_to_user(target_id, broadcast_msg)
                        
                elif event == "viewed_once":
                    msg_id = data.get("msg_id")
                    room_id = data.get("room_id")
                    await db.execute("UPDATE messages SET is_viewed = 1 WHERE id = ?", (msg_id,))
                    await db.commit()
                    
                    c3 = await db.execute("SELECT user_id FROM room_members WHERE room_id = ?", (room_id,))
                    for m in await c3.fetchall():
                        await manager.send_to_user(m['user_id'], {"type": "message_destroyed", "msg_id": msg_id, "room_id": room_id})

                elif event == "delete_message":
                    msg_id = data.get("msg_id")
                    room_id = data.get("room_id")
                    
                    # WhatsApp style: update record row info to show it's deleted instead of dropping completely
                    await db.execute("""
                        UPDATE messages 
                        SET content = '🚫 This message was deleted', message_type = 'text', file_url = NULL, file_name = NULL 
                        WHERE id = ?
                    """, (msg_id,))
                    await db.commit()
                    
                    c3 = await db.execute("SELECT user_id FROM room_members WHERE room_id = ?", (room_id,))
                    for m in await c3.fetchall():
                        await manager.send_to_user(m['user_id'], {"type": "message_deleted", "msg_id": msg_id, "room_id": room_id})

        except WebSocketDisconnect:
            manager.disconnect(user_id)

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True, log_level="info")
