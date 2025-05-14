import asyncio
import json
from dotenv import load_dotenv
import os
import websockets
from google import genai
import base64

load_dotenv()
API_KEY = os.getenv("GEMINI_API_KEY")

MODEL = "gemini-2.0-flash-live-001"

client = genai.Client(
    api_key=API_KEY,
    http_options={
        'api_version': 'v1alpha',
    }
)

def save_order(items: list[str]):
    print("\n📝 Pesanan Disimpan:", items)
    return {"status": "ok", "pesanan": items}

tool_save_order = {
    "function_declarations": [
        {
            "name": "save_order",
            "description": "menyimpan pesanan makanan atau minuman pelanggan ke sistem.",
            "parameters": {
                "type": "object",
                "properties": {
                    "items": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "menu": {"type": "string", "description": "Menu makanan atau minuman yang dipesan seperti ayam goreng, fanta, cola, kentang goreng, dll. Jangan mencatat hal-hal aneh seperti hewan, benda asing, atau kata tidak relevan"},
                                "qty": {"type": "integer", "description": "Jumlah item yang dipesan, misalnya 1, 2, 5"}
                            },
                            "required": ["menu", "qty"]
                        }
                    },
                    "note": {
                        "type": "string",
                        "description": "Catatan tambahan dari pelanggan"
                    }
                },
                "required": ["items"]
            },
        }
    ] 
}

#websocket handler
async def gemini_session_handler(client_websocket):
    """Handles the interaction with Gemini API within a websocket session.

    Args:
        client_websocket: The websocket connection to the client.
    """
    try:
        config_message = await client_websocket.recv()
        config_data = json.loads(config_message)
        config = config_data.get("setup", {})
        
        if "generation_config" in config and "response_modalities" in config["generation_config"]:
            del config["generation_config"]["response_modalities"]

        config["tools"] = [tool_save_order]
        
        async with client.aio.live.connect(model=MODEL, config=config) as session:
            print("Connected to Gemini API")

            async def send_to_gemini():
                """Sends audio messages from the client websocket to the Gemini API."""
                try:
                    async for message in client_websocket:
                        try:
                            data = json.loads(message)
                            if "realtime_input" in data:
                                for chunk in data["realtime_input"]["media_chunks"]:
                                    if chunk["mime_type"] == "audio/pcm":
                                        await session.send({"mime_type": "audio/pcm", "data": chunk["data"]})
                        except Exception as e:
                            print(f"Error sending to Gemini: {e}")
                    print("Client connection closed (send)")
                except Exception as e:
                    print(f"Error sending to Gemini: {e}")
                finally:
                    print("send_to_gemini closed")

            async def receive_from_gemini():
                """Receives responses from the Gemini API and forwards them to the client."""
                try:
                    while True:
                        try:
                            print("receiving from gemini")
                            async for response in session.receive():
                                if response.server_content is None:
                                    if response.tool_call is not None:
                                        print(f"Tool call received: {response.tool_call}")
                                        function_calls = response.tool_call.function_calls
                                        function_responses = []

                                        for function_call in function_calls:
                                            name = function_call.name
                                            args = function_call.args
                                            call_id = function_call.id

                                            if name == "save_order":
                                                try:
                                                    result = save_order(args["items"])
                                                    function_responses.append({
                                                        "name": name,
                                                        "response": {"result": result},
                                                        "id": call_id
                                                    })
                                                    await client_websocket.send(json.dumps({"text": json.dumps(function_responses)}))
                                                    print("Function executed")
                                                except Exception as e:
                                                    print(f"Error executing function: {e}")
                                                    continue

                                        await session.send(function_responses)
                                        continue

                                model_turn = response.server_content.model_turn
                                if model_turn:
                                    for part in model_turn.parts:
                                        if hasattr(part, 'text') and part.text is not None:
                                            await client_websocket.send(json.dumps({"text": part.text}))
                                        elif hasattr(part, 'inline_data') and part.inline_data is not None:
                                            base64_audio = base64.b64encode(part.inline_data.data).decode('utf-8')
                                            await client_websocket.send(json.dumps({
                                                "audio": base64_audio,
                                            }))
                                            print("audio received")

                                if response.server_content.turn_complete:
                                    print('\n<Turn complete>')
                        except websockets.exceptions.ConnectionClosedOK:
                            print("Client connection closed normally (receive)")
                            break
                        except Exception as e:
                            print(f"Error receiving from Gemini: {e}")
                            break
                except Exception as e:
                    print(f"Error receiving from Gemini: {e}")
                finally:
                    print("Gemini connection closed (receive)")

            send_task = asyncio.create_task(send_to_gemini())
            receive_task = asyncio.create_task(receive_from_gemini())
            await asyncio.gather(send_task, receive_task)

    except Exception as e:
        print(f"Error in Gemini session: {e}")
    finally:
        print("Gemini session closed.")


async def main() -> None:
    async with websockets.serve(gemini_session_handler, "localhost", 9082):
        print("Running websocket server localhost:9082...")
        await asyncio.Future()  # Keep the server running indefinitely


if __name__ == "__main__":
    asyncio.run(main())