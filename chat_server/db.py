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
                SELECT userId,email text, timestamp
                FROM messages
                WHERE roomId = %s AND timestamp < %s
                ORDER BY timestamp DESC
                LIMIT %s
            """
            cur.execute(query, (roomId, before, limit))
        else:
            query = """
                SELECT userId,email, text, timestamp
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
                "timestamp": row[3].isoformat()
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