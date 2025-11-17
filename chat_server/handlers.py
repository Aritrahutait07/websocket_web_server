import json
import logging
from datetime import datetime, UTC
import websockets
from auth import verify_firebase_token
from rooms import register, unregister, broadcast
from db import save_message_to_db, fetch_messages_keyset, toggle_like_on_message, fetch_recent_messages
import asyncio
import aiohttp
from moderation import analyze_text, evaluate_analysis
from contextual_moderation import analyze_with_gemini_context


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
            logging.info("Content is safe, no moderation needed.")
            payload = {
                "type": "message",
                "text": message_text,
                "userId": websocket.user_id, 
                "email": websocket.user_email,
                "roomId": websocket.room_id,
                "timestamp": datetime.now(UTC).isoformat(),
            }
            #print("\nMessage Content")
            #print(f"Current Message Text: {message_text}")
            await broadcast(websocket.room_id, json.dumps(payload), exclude_sender=True, sender_websocket=websocket)
            await save_message_to_db(websocket.room_id, websocket.user_id, websocket.user_email, message_text)

        elif decision == 'BLOCK' :
            #start_time = datetime.now(UTC).isoformat()
            history_messages = await fetch_recent_messages(websocket.room_id, count=5)
            gemini_decision, reason = await analyze_with_gemini_context(history_messages, message_text, websocket.user_email)
            if gemini_decision == 'SAFE':
                logging.info("Gemini Contextual Check Passed inside Block Action")
                payload = {
                    "type": "message",
                    "text": message_text,
                    "userId": websocket.user_id, 
                    "email": websocket.user_email,
                    "roomId": websocket.room_id,
                    "timestamp": datetime.now(UTC).isoformat(),
                }
                await broadcast(websocket.room_id, json.dumps(payload), exclude_sender=True, sender_websocket=websocket)
                await save_message_to_db(websocket.room_id, websocket.user_id, websocket.user_email, message_text)
                #print("\nGemini Contextual Check Passed")
                #pass
            elif gemini_decision == 'BLOCK':       
                #print("\nGemini Contextual Check Failed inside Block Action")
                logging.info("Gemini Contextual Check Failed inside Block Action")
                message_text = f"Your message was blocked due to moderation rules due to category {category}. Please adhere to community guidelines [RETRACTED]."
                payload = {
                    "type": "message",
                    "text": message_text,
                    "userId": websocket.user_id, 
                    "email": websocket.user_email,
                    "roomId": websocket.room_id,
                    "timestamp": datetime.now(UTC).isoformat(),
                }
                await broadcast(websocket.room_id, json.dumps(payload), exclude_sender=True, sender_websocket=websocket)
                await save_message_to_db(websocket.room_id, websocket.user_id, websocket.user_email, message_text)
                
            elif gemini_decision == 'SELF_HARM_ALERT':
                logging.info("Gemini Contextual Check Failed inside Block Action - Self Harm Alert")
                message_text = f"Your message was blocked due to self-harm alert. Please reach out to a trusted friend or professional for support. [RETRACTED]"
                payload = {
                    "type": "message",
                    "text": message_text,
                    "userId": websocket.user_id, 
                    "email": websocket.user_email,
                    "roomId": websocket.room_id,
                    "timestamp": datetime.now(UTC).isoformat(),
                }
            #end_time = datetime.now(UTC).isoformat()
            #total_time = (datetime.fromisoformat(end_time) - datetime.fromisoformat(start_time)).total_seconds()
            # print("\nModeration Check Time:", total_time, "seconds")
            # print("\nModeration Blocked Content")
            # print(f"Current Message Text: {message_text}")
                await broadcast(websocket.room_id, json.dumps(payload), exclude_sender=True, sender_websocket=websocket)
                #logging.info(f"Current Message Text: {message_text}")
                await save_message_to_db(websocket.room_id, websocket.user_id, websocket.user_email, message_text)
            
            

        elif decision == 'SELF_HARM_ALERT':
            #start_time = datetime.now(UTC).isoformat()
            history_messages = await fetch_recent_messages(websocket.room_id, count=5)
            gemini_decision, reason = await analyze_with_gemini_context(history_messages, message_text, websocket.user_email)
            
            if gemini_decision == 'SAFE':
                #print("\nGemini Contextual Check Passed for Self Harm Alert inside Self Harm Alert")
                logging.info("Gemini Contextual Check Passed for Self Harm Alert")
                payload = {
                    "type": "message",
                    "text": message_text,
                    "userId": websocket.user_id, 
                    "email": websocket.user_email,
                    "roomId": websocket.room_id,
                    "timestamp": datetime.now(UTC).isoformat(),
                }
                await broadcast(websocket.room_id, json.dumps(payload), exclude_sender=True, sender_websocket=websocket)
                await save_message_to_db(websocket.room_id, websocket.user_id, websocket.user_email, message_text)
            elif gemini_decision == 'SELF_HARM_ALERT':
                #print("\nGemini Contextual Check Failed for Self Harm Alert")
                logging.info("Gemini Contextual Check Failed for Self Harm Alert")
                message_text = f"Your message was flagged for self-harm content. Please reach out to a crisis hotline or a trusted person for support [RETRACTED]."
                payload = {
                    "type": "message",
                    "text": message_text,
                    "userId": websocket.user_id, 
                    "email": websocket.user_email,
                    "roomId": websocket.room_id,
                    "timestamp": datetime.now(UTC).isoformat(),
                }
                await broadcast(websocket.room_id, json.dumps(payload), exclude_sender=True, sender_websocket=websocket)
                await save_message_to_db(websocket.room_id, websocket.user_id, websocket.user_email, message_text)
            elif gemini_decision == 'BLOCK':
                #print("\nGemini Contextual Check Failed for Self Harm Alert")
                logging.info("Gemini Contextual Check Failed for Self Harm Alert")
                message_text = f"Your message was blocked due to moderation rules due to category {category}. Please adhere to community guidelines [RETRACTED]."
                payload = {
                    "type": "message",
                    "text": message_text,
                    "userId": websocket.user_id, 
                    "email": websocket.user_email,
                    "roomId": websocket.room_id,
                    "timestamp": datetime.now(UTC).isoformat(),
                }
            #end_time = datetime.now(UTC).isoformat()
            #total_time = (datetime.fromisoformat(end_time) - datetime.fromisoformat(start_time)).total_seconds()
            #print("\nModeration Check Time for Self Harm Alert:", total_time, "seconds")
            #print("\nSelf Harm Alert Content")
            #print(f"Current Message Text: {message_text}")
                #logging.info(f"Current Message Text: {message_text}")
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
        
        if not chatid:
            logging.warning("Like message missing chatid.")
            return
        
        user_email = websocket.user_email
        new_like_count = await toggle_like_on_message(chatid, user_email)
        
        response = {
            "type": "like_update",
            "chatid": chatid,
            "new_like_count": new_like_count,
            "user_email": user_email
        }
        await websocket.send(json.dumps(response))
        logging.info(f"User '{user_email}' toggled like for message '{chatid}'. New count: {new_like_count}")
        
    else:
        logging.warning(f"Unknown message type: {data.get('type')}")