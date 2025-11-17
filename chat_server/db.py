import logging
from psycopg2 import pool
import asyncio
import os
import logging

db_pool = None

def init_db_pool(dsn):
    global db_pool
    db_pool = pool.SimpleConnectionPool(minconn=1, maxconn=10, dsn=dsn)
    return db_pool

def test_db_connection():
    if not db_pool:
        logging.error("FATAL: Database pool is not initialized.")
        return False
    conn = None
    try:
        conn = db_pool.getconn()
        cur = conn.cursor()
        cur.execute("SELECT VERSION()")
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

def _save_message_to_db_blocking(roomId, userId, email, text):
    conn = None
    try:
        conn = db_pool.getconn()
        cur = conn.cursor()
        cur.execute("INSERT INTO messages (roomId, userId,email, text) VALUES (%s, %s, %s, %s)", (roomId, userId, email, text))
        conn.commit()
        cur.close()
        logging.info(f"Saved message from '{userId}' email '{email}' in room '{roomId}' to DB.")
    except Exception as e:
        logging.error(f"Database error: {e}")
        if conn: conn.rollback()
    finally:
        if conn:
            db_pool.putconn(conn)

async def save_message_to_db(roomId, userId, email, text):
    await asyncio.to_thread(_save_message_to_db_blocking, roomId, userId, email, text)


def fetch_messages_keyset(roomId, before=None, limit=50):
    """Fetch messages with keyset pagination (older messages before a cursor)."""
    conn = None
    messages = []
    nextCursor = None  # bookmark for fetching more

    try:
        conn = db_pool.getconn()
        cur = conn.cursor()

        if before:
            query = """
                SELECT userId,email text, timestamp,like_count 
                FROM messages
                WHERE roomId = %s AND timestamp < %s
                ORDER BY timestamp DESC
                LIMIT %s
            """
            cur.execute(query, (roomId, before, limit))
        else:
            query = """
                SELECT userId,email, text, timestamp,like_count 
                FROM messages
                WHERE roomId = %s
                ORDER BY timestamp DESC
                LIMIT %s
            """
            cur.execute(query, (roomId, limit))

        rows = cur.fetchall()
        for row in rows:
            messages.append({
                "userId": row[0],
                "text": row[2],
                "email": row[1],
                "timestamp": row[3].isoformat(),
                "like_count": row[4]
            })

        # if we got messages, set nextCursor to the last one's timestamp
        if rows:
            nextCursor = rows[-1][3].isoformat()

        cur.close()
    except Exception as e:
        logging.error(f"Database error while fetching messages: {e}")
    finally:
        if conn:
            db_pool.putconn(conn)

    return {
        "messages": messages,
        "nextCursor": nextCursor
    }
    


def _log_moderation_event_blocking(userId, userEmail, roomId, text, category, severity, action):
    conn = None
    try:
        conn = db_pool.getconn()
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO moderation_logs 
            (user_id, user_email, room_id, original_message_text, violation_category, severity_score, action_taken) 
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (userId, userEmail, roomId, text, category, severity, action)
        )
        conn.commit()
        cur.close()
        logging.info(f"Logged moderation event for user '{userId}' in room '{roomId}'. Action: {action}")
    except Exception as e:
        logging.error(f"Database error while logging moderation event: {e}")
        if conn: conn.rollback()
    finally:
        if conn:
            db_pool.putconn(conn)

async def log_moderation_event(userId, userEmail, roomId, text, category, severity, action):
    """Asynchronously logs a moderation event to the database."""
    await asyncio.to_thread(
        _log_moderation_event_blocking, 
        userId, userEmail, roomId, text, category, severity, action
    )
    
def _toggle_like_on_message_blocking(chatid, user_email):
    conn = None
    new_like_count = 0
    try:
        conn = db_pool.getconn()
        cur = conn.cursor()

        
        cur.execute(
            "SELECT 1 FROM message_likes WHERE chatid = %s AND liked_by_email = %s",
            (chatid, user_email)
        )
        already_liked = cur.fetchone()

        if already_liked:
            
            cur.execute(
                "DELETE FROM message_likes WHERE chatid = %s AND liked_by_email = %s",
                (chatid, user_email)
            )
            
            cur.execute(
                "UPDATE messages SET like_count = like_count - 1 WHERE chatid = %s RETURNING like_count",
                (chatid,)
            )
        else:
            
            cur.execute(
                "INSERT INTO message_likes (chatid, liked_by_email) VALUES (%s, %s)",
                (chatid, user_email)
            )
            
            cur.execute(
                "UPDATE messages SET like_count = like_count + 1 WHERE chatid = %s RETURNING like_count",
                (chatid,)
            )

        
        result = cur.fetchone()
        if result:
            new_like_count = result[0]
        
       
        conn.commit()
        cur.close()
        logging.info(f"User '{user_email}' toggled like for message '{chatid}'. New count: {new_like_count}")

    except Exception as e:
        logging.error(f"Database error during like toggle: {e}")
        
        if conn: conn.rollback()
    finally:
        if conn:
            db_pool.putconn(conn)
    
    return new_like_count

async def toggle_like_on_message(chatid, user_email):
    """Asynchronously toggles a like on a message and returns the new like count."""
    return await asyncio.to_thread(_toggle_like_on_message_blocking, chatid, user_email)

def _fetch_recent_messages_blocking(roomId, count=5):
    """Fetches the last 'count' messages from a room for context."""
    conn = None
    messages = []
    try:
        conn = db_pool.getconn()
        cur = conn.cursor()
        cur.execute(
            """
            SELECT email, text FROM messages
            WHERE roomId = %s
            ORDER BY timestamp DESC
            LIMIT %s
            """,
            (roomId, count)
        )
        rows = cur.fetchall()
        # Reverse the list to get the correct chronological order for the LLM.
        messages = [{"email": row[0], "text": row[1]} for row in reversed(rows)]
        cur.close()
    except Exception as e:
        logging.error(f"Database error while fetching recent messages: {e}")
    finally:
        if conn:
            db_pool.putconn(conn)
    return messages

async def fetch_recent_messages(roomId, count=5):
    return await asyncio.to_thread(_fetch_recent_messages_blocking, roomId, count)