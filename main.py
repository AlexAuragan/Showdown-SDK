import asyncio

from python_showdown.classes.client.client import Client
from python_showdown.classes.combat.random import RandomMoveCombatHandler

WEBSOCKET_URL = "ws://192.168.1.154:8000/showdown/websocket"


async def main() -> None:
    combat_ai = RandomMoveCombatHandler()
    client = Client(WEBSOCKET_URL, combat_handler=combat_ai)

    try:
        await client.connect()
        await client.login("BOT1")
        await client.send("/join lobby")
        client.room_id = "lobby"
        await client.send("Hello !", room_id="lobby")
        await client.challenge("AlexAuragan", "[Gen 1] Random Battle")
        print(f"Connected as {client.username}")

        # Keep the client alive for now.
        await asyncio.Event().wait()

    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(main())
