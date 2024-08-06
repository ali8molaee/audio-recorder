import asyncio
import logging

import fastapi

import wave
import logging
from singleton import Singleton


class VoiceReceiverService:
    def __init__(self):
        self.clients_audios: dict[str, list[bytes]] = {}

    async def process_audio_frame(self, data: bytes, client_id: str):
        if client_id not in self.clients_audios:
            self.clients_audios[client_id] = []
        self.clients_audios[client_id].append(data)
        logging.debug(f"Received audio frame from {client_id}")

    async def no_voice_activity(self, client_id: str, _):
        if client_id in self.clients_audios and self.clients_audios[client_id]:
            await self.save_audio(client_id)

    async def save_audio(self, client_id: str):
        audio_data = self.clients_audios.pop(client_id, None)
        if not audio_data:
            return

        filename = f"{client_id}.webm"
        with open(filename, "wb") as f:
            f.write(b"".join(audio_data))
        logging.debug(f"Saved audio for {client_id} to {filename}")

    def pop_clients_audios(self, client_id: str):
        if client_id in self.clients_audios:
            self.clients_audios.pop(client_id, None)


connected_clients = {}
app = fastapi.FastAPI()

@app.websocket("/ws/{client_id}")
async def get_audio(websocket: fastapi.WebSocket, client_id: str):
    client_id = str(client_id)
    await websocket.accept()
    connected_clients[client_id] = websocket
    logging.debug(f"WebSocket connection established for {client_id}")
    try:
        while True:
            try:
                message = await asyncio.wait_for(websocket.receive(), timeout=5.0)
            except asyncio.TimeoutError:
                await VoiceReceiverService().no_voice_activity(client_id, -1)
                continue

            if isinstance(message, dict) and "bytes" in message:
                data = message["bytes"]
                await VoiceReceiverService().process_audio_frame(data, client_id)
            elif isinstance(message, dict) and "text" in message:
                data = message["text"]
                if data == "close()":
                    await VoiceReceiverService().no_voice_activity(client_id, -1)
                    # await websocket.close()
                    break
            else:
                break

    except Exception as e:
        import traceback

        traceback_str = "".join(traceback.format_tb(e.__traceback__))
        logging.error(f"WebSocket connection closed for {client_id}: {e}")
        logging.error(f"Exception: {traceback_str} {e}")
    finally:
        VoiceReceiverService().pop_clients_audios(client_id)
        connected_clients.pop(client_id, None)


if __name__ == "__main__":
    import uvicorn
    logging.basicConfig(level=logging.DEBUG)

    uvicorn.run(app, host="0.0.0.0", port=8000)
