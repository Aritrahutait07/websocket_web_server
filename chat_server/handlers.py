import json
import logging
from datetime import datetime, UTC
import websockets
from auth import verify_firebase_token
from rooms import register, unregister, broadcast
from db import save_message_to_db, fetch_messages_keyset,toggle_like_on_message
import asyncio
import aiohttp
from moderation import analyze_text, evaluate_analysis


async def chat_handler(websocket):
    async with aiohttp.ClientSession() as session:
        try:
            join_message = await websocket.recv()
            data = json.loads(join_message)

            if data.get("type") != "join":
                await websocket.close(1008, "First message must be join.")
                return

            token, roomId = data.get("token"), data.get("roomId")
            if not token or not roomId:
                await websocket.close(1008, "Token & roomId required.")
                return

            decoded_token = await verify_firebase_token(token)
            if not decoded_token:
                await websocket.close(4001, "Invalid authentication token.")
                return

            trusted_userId = decoded_token['user_id']
            
            user_email = decoded_token.get('email', trusted_userId)
            
            await register(websocket, roomId, trusted_userId, user_email)

            
            
            async for message in websocket:
                await handle_message(websocket, message, session)

        except websockets.exceptions.ConnectionClosed as e:
            logging.info(f"Connection closed: {e.code} {e.reason}")
        finally:
            await unregister(websocket)


async def handle_message(websocket, raw_message, session):
    try:
        data = json.loads(raw_message)
    except json.JSONDecodeError:
        logging.warning("Invalid JSON")
        return

    if data.get("type") == "message":
        message_text = data.get("text", "").strip()
        if not message_text:
            return 

        
        analysis_response = await analyze_text(session, message_text)
        decision, category = evaluate_analysis(analysis_response)
        
        #await asyncio.sleep(5)
        if decision == 'OK':
            
            payload = {
                "type": "message",
                "text": message_text,
                "userId": websocket.user_id, 
                "email": websocket.user_email,
                "roomId": websocket.room_id,
                "timestamp": datetime.now(UTC).isoformat(),
            }
            #print("\nMessage Content")
            await broadcast(websocket.room_id, json.dumps(payload), exclude_sender=True, sender_websocket=websocket)
            await save_message_to_db(websocket.room_id, websocket.user_id, websocket.user_email, message_text)

        elif decision == 'BLOCK' :
            message_text = f"Your message was blocked due to moderation rules due to category {category}. Please adhere to community guidelines."
            payload = {
                "type": "message",
                "text": message_text,
                "userId": websocket.user_id, 
                "email": websocket.user_email,
                "roomId": websocket.room_id,
                "timestamp": datetime.now(UTC).isoformat(),
            }
           #print("\nModeration Blocked Content")
            await broadcast(websocket.room_id, json.dumps(payload), exclude_sender=True, sender_websocket=websocket)
            await save_message_to_db(websocket.room_id, websocket.user_id, websocket.user_email, message_text)
            
            

        elif decision == 'SELF_HARM_ALERT':
            
            message_text = f"Your message was flagged for self-harm content. Please reach out to a crisis hotline or a trusted person for support."
            payload = {
                "type": "message",
                "text": message_text,
                "userId": websocket.user_id, 
                "email": websocket.user_email,
                "roomId": websocket.room_id,
                "timestamp": datetime.now(UTC).isoformat(),
            }
            #print("\nSelf Harm Alert Content")
            await broadcast(websocket.room_id, json.dumps(payload), exclude_sender=True, sender_websocket=websocket)
            await save_message_to_db(websocket.room_id, websocket.user_id, websocket.user_email, message_text)
            
            

    elif data.get("type") == "join":
        new_roomId = data.get("roomId")
        current_userId = websocket.user_email
        #current_userEmail = websocket.email
                
        if new_roomId and new_roomId != websocket.room_id:
            logging.info(f"User '{current_userId}' is switching to room '{new_roomId}'.")
            await unregister(websocket)
            await register(websocket, new_roomId, current_userId)
            
        else:
            logging.warning(f"User '{current_userId}' sent an invalid room-switch request.")

    elif data.get("type") == "load_chat":
        before = data.get("before")  # ISO timestamp string
        limit = data.get("limit", 50)
        try:
            limit = int(limit)
            if limit <= 0 or limit > 100:
                limit = 50
        except (ValueError, TypeError):
            limit = 50

        messages = fetch_messages_keyset(websocket.room_id, before, limit)
        response = {
            "type": "chat_history",
            "messages": messages
        }
        await websocket.send(json.dumps(response))
        
    
    elif data.get("type") == "like":
        chatid = data.get("chatid")
        user_email = websocket.user_email
        
        if not chatid or not user_email:
            logging.warning("Like message missing chatid or user_email.")
            return
        
        new_like_count = await toggle_like_on_message(chatid, user_email)
        
        response = {
            "type": "like_update",
            "chatid": chatid,
            "user_email": user_email,
            "new_like_count": new_like_count
        }
        
        await websocket.send(json.dumps(response))
    else:
        logging.warning(f"Unknown message type: {data.get('type')}")
        return
        