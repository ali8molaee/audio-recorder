import asyncio
import logging
from hashlib import md5
from io import BytesIO

import fastapi
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from pydub import AudioSegment
from singleton import Singleton


class VoiceReceiverService(metaclass=Singleton):
    def __init__(self):
        self.clients_audios: dict[str, list[bytes]] = {}

    async def process_audio_frame(self, data: bytes, client_id: str):
        if client_id not in self.clients_audios:
            self.clients_audios[client_id] = []
        self.clients_audios[client_id].append(data)
        md5_hash = md5(data).hexdigest()

        logging.debug(
            f"Received audio frame of size {len(data)=} from {client_id} with hash {md5_hash}"
        )

    async def no_voice_activity(self, client_id: str, _):
        if client_id in self.clients_audios and self.clients_audios[client_id]:
            await self.save_audio(client_id)

    def save_wav(self, bio: BytesIO, filename: str) -> None:
        # save audio to disk
        import wave

        import numpy as np

        data = bio.getvalue()
        data_np = np.frombuffer(data[: len(data) // 4 * 4], dtype=np.float32)

        try:
            int_frames: np.ndarray = (data_np * 32767).astype(np.int32)
            with wave.open(filename, "wb") as wav_file:
                num_channels = 1
                sample_width = 4  # 16 bits = 2 bytes
                frame_rate = 16000

                wav_file.setnchannels(num_channels)
                wav_file.setsampwidth(sample_width)
                wav_file.setframerate(frame_rate)
                wav_file.writeframes(int_frames.tobytes())
        except Exception as e:
            logging.error(f"Error saving audio: {e}")

    async def save_audio(self, client_id: str):
        audio_data = self.clients_audios.pop(client_id, None)
        if not audio_data:
            return

        webm = BytesIO(b"".join(audio_data))
        webm.seek(0)

        wav = BytesIO(webm.getvalue())
        wav.seek(0)

        webm_filename = f"{client_id}.webm"
        wav_filename = f"{client_id}.wav"

        with open(webm_filename, "wb") as f:
            f.write(webm.read())

        self.save_wav(wav, wav_filename)
        logging.debug(f"Saved audio for {client_id} to {webm_filename}")

    def convert_webm_to_wav(self, webm: BytesIO) -> BytesIO:
        audio: AudioSegment = AudioSegment.from_file(webm, format="webm")
        wav_io = BytesIO()
        audio.export(wav_io, format="wav")
        wav_io.seek(0)
        return wav_io

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


class ClientAudioSchema(BaseModel):
    texts: list[str] = []
    note: str | None = None


@app.get("/api/{client_id}", response_model=ClientAudioSchema)
async def get_audio(client_id: str):
    client_id = str(client_id)
    return connected_clients.get(client_id, dict(texts=[], note=None))


origins = [
    "http://localhost:3000",
    "http://localhost:3001",
    "http://localhost:8000",
    "https://wln.inbeet.tech",
    "https://app.wln.inbeet.tech",
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

if __name__ == "__main__":
    import uvicorn

    logging.basicConfig(level=logging.DEBUG)

    uvicorn.run(app, host="0.0.0.0", port=8000)
