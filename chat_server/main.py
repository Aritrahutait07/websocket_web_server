import asyncio
import logging
import websockets
import signal
from config import DATABASE_URL, SERVER_HOST, SERVER_PORT
from db import init_db_pool, test_db_connection
from handlers import chat_handler
import os

async def shutdown(server):
    logging.info("Shutdown sequence started...")
    server.close()
    await server.wait_closed()

async def main():
    db_pool = init_db_pool(DATABASE_URL)
    if not test_db_connection():
        return

    loop = asyncio.get_running_loop()
    stop = loop.create_future()
    if os.name != 'nt':
        loop.add_signal_handler(signal.SIGINT, stop.set_result, None)
        loop.add_signal_handler(signal.SIGTERM, stop.set_result, None)

    server = await websockets.serve(chat_handler, SERVER_HOST, SERVER_PORT)
    logging.info(f"Server running on ws://{SERVER_HOST}:{SERVER_PORT}")
    await stop
    await shutdown(server)

if __name__ == "__main__":
    asyncio.run(main())
