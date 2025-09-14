import os
import asyncio
import json
import logging
import websockets
import psycopg2
from datetime import datetime, UTC
from dotenv import load_dotenv 
from psycopg2 import pool 
from google.oauth2 import id_token
from google.auth.transport import requests
import signal

load_dotenv() 

DATABASE_URL = os.getenv("DATABASE_URL")
SERVER_HOST = os.getenv("SERVER_HOST")
SERVER_PORT = int(os.getenv("SERVER_PORT"))
FIREBASE_PROJECT_ID = os.getenv("FIREBASE_PROJECT_ID")


logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')


ROOMS = {}
db_pool = None



async def chat_handler(websocket):
    """
    Main handler for incoming WebSocket connections.
    Now hardened against invalid JSON messages.
    """
    try:
        
        join_message = await websocket.recv()
        data = json.loads(join_message)

        if data.get("type") == "join":
            token = data.get("token")
            roomId = data.get("roomId")

            if not token or not roomId:
                await websocket.close(1008, "Token and roomId are required for joining.")
                return

        
            decoded_token = await verify_firebase_token(token)
            
            if decoded_token:
                logging.info(f"Decoded token payload: {decoded_token}")
                trusted_userId = decoded_token['user_id']
                
                logging.info(f"Token verified for user: {trusted_userId}")
                await register(websocket, roomId, trusted_userId)
            else:
                logging.warning(f"Failed to verify token for user: {token}")
                await websocket.close(4001, "Invalid authentication token.")
                return
        else:
            await websocket.close(1008, "First message must be of type 'join'.")
            return

        
        async for message in websocket:
            
            
            try:
                data = json.loads(message)
            except json.JSONDecodeError:
                logging.warning(f"Received invalid JSON from user '{getattr(websocket, 'user_id', 'Unknown')}': {message}")
                continue 
            if data.get("type") == "message":
                text = data.get("text")
                current_roomId = websocket.room_id
                current_userId = websocket.user_id

                broadcast_payload = {
                    "type": "message",
                    "text": text,
                    "userId": current_userId,
                    "roomId": current_roomId,
                   
                    "timestamp": datetime.now(UTC).isoformat()
                }
                
                await broadcast(current_roomId, json.dumps(broadcast_payload), exclude_sender=True, sender_websocket=websocket)
                await save_message_to_db(current_roomId, current_userId, text)

            
            elif data.get("type") == "join":
                new_roomId = data.get("roomId")
                current_userId = websocket.user_id
                
                if new_roomId and new_roomId != websocket.room_id:
                    logging.info(f"User '{current_userId}' is switching to room '{new_roomId}'.")
                    await unregister(websocket)
                    await register(websocket, new_roomId, current_userId)
                else:
                    logging.warning(f"User '{current_userId}' sent an invalid room-switch request.")
            
            else:
                logging.warning(f"Received unknown message type: {data.get('type')}")

    except websockets.exceptions.ConnectionClosed as e:
        logging.info(f"Connection closed: {e.code} {e.reason}")
    except Exception as e:
       
        logging.error(f"An unexpected error occurred: {e}", exc_info=True)
    finally:
        await unregister(websocket)
        
        
async def shutdown(server):
    """Gracefully shuts down the server and its connections."""
    logging.info("Shutdown sequence started.")
    
    
    server.close()
    await server.wait_closed()
    
   
    all_clients = [client for room in ROOMS.values() for client in room]
    if all_clients:
        logging.info(f"Closing {len(all_clients)} active connections...")
        
        close_tasks = [
            client.close(code=1001, reason="Server is shutting down") 
            for client in all_clients
        ]
        
        await asyncio.gather(*close_tasks, return_exceptions=True)

def _verify_firebase_token_blocking(token):
    
    try:
        
        decoded_token = id_token.verify_firebase_token(
            token, requests.Request(), audience=FIREBASE_PROJECT_ID
        )
        return decoded_token
    except ValueError as e:
        
        logging.warning(f"Token verification failed: {e}")
        return None

async def verify_firebase_token(token):
    
    return await asyncio.to_thread(_verify_firebase_token_blocking, token)

def _save_message_to_db_blocking(roomId, userId, text):
    conn = None
    try:
        conn = db_pool.getconn()
        cur = conn.cursor()
        cur.execute("INSERT INTO messages (roomId, userId, text) VALUES (%s, %s, %s)", (roomId, userId, text))
        conn.commit()
        cur.close()
        logging.info(f"Saved message from '{userId}' in room '{roomId}' to DB.")
    except Exception as e:
        logging.error(f"Database error: {e}")
        if conn: conn.rollback()
    finally:
        if conn:
            db_pool.putconn(conn)

async def save_message_to_db(roomId, userId, text):
    await asyncio.to_thread(_save_message_to_db_blocking, roomId, userId, text)

async def register(websocket, roomId, userId):
    if roomId not in ROOMS: ROOMS[roomId] = set()
    ROOMS[roomId].add(websocket)
    websocket.room_id = roomId
    websocket.user_id = userId
    logging.info(f"User '{userId}' registered to room '{roomId}'. Current rooms: {list(ROOMS.keys())}")
    announcement = {"type": "announcement", "message": f"User '{userId}' has joined the room."}
    await broadcast(roomId, json.dumps(announcement), exclude_sender=False)

async def unregister(websocket):
    roomId = getattr(websocket, 'room_id', None)
    userId = getattr(websocket, 'user_id', 'Unknown')
    if roomId and roomId in ROOMS and websocket in ROOMS[roomId]:
        ROOMS[roomId].remove(websocket)
        logging.info(f"User '{userId}' unregistered from room '{roomId}'.")
        announcement = {"type": "announcement", "message": f"User '{userId}' has left the room."}
        await broadcast(roomId, json.dumps(announcement), exclude_sender=False)
        if not ROOMS[roomId]:
            del ROOMS[roomId]
            logging.info(f"Room '{roomId}' is now empty and has been removed.")

async def broadcast(roomId, message, exclude_sender=True, sender_websocket=None):
    if roomId in ROOMS:
        clients = list(ROOMS[roomId])
        message_tasks = [client.send(message) for client in clients if not (exclude_sender and client == sender_websocket)]
        if message_tasks: await asyncio.gather(*message_tasks)

def test_db_connection():
    if not db_pool:
        logging.error("FATAL: Database pool is not initialized.")
        return False
    logging.info("Testing database connection...")
    conn = None
    try:
        conn = db_pool.getconn()
        cur = conn.cursor()
        cur.execute('SELECT VERSION()')
        version = cur.fetchone()[0]
        logging.info(f"Database connection successful! PostgreSQL version: {version}")
        cur.close()
        return True
    except Exception as e:
        logging.error(f"FATAL: Database connection failed: {e}")
        return False
    finally:
        if conn:
            db_pool.putconn(conn)

async def main():
    global db_pool 
    loop = asyncio.get_running_loop()
    stop = loop.create_future()
    if os.name != 'nt':
        loop.add_signal_handler(signal.SIGINT, stop.set_result, None)
        loop.add_signal_handler(signal.SIGTERM, stop.set_result, None)
    
    try:
       
        logging.info("Initializing database connection pool...")
        db_pool = pool.SimpleConnectionPool(
            minconn=1,   
            maxconn=10,  
            dsn=DATABASE_URL
        )
        
        if not test_db_connection():
            logging.error("Exiting due to database connection failure.")
            return

        logging.info(f"Starting WebSocket server on ws://{SERVER_HOST}:{SERVER_PORT}")
        server = await websockets.serve(chat_handler, SERVER_HOST, SERVER_PORT)
        logging.info("Server started. Press Ctrl+C to stop.")
        await stop
    except KeyboardInterrupt:
        
        logging.info("KeyboardInterrupt received.") 

    finally:
        
        if server:
            await shutdown(server)
        
        if db_pool:
            logging.info("Closing database connection pool.")
            db_pool.closeall()
        
        logging.info("Server shut down gracefully.")

if __name__ == "__main__":
    if not DATABASE_URL:
        logging.error("FATAL: DATABASE_URL environment variable not set.")
    else:
        try:
            asyncio.run(main())
        except KeyboardInterrupt:
            logging.info("Server is shutting down.")
