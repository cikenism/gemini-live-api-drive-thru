import asyncio
import json
from dotenv import load_dotenv
import os
import websockets
from google import genai
import base64

# load_dotenv()
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
                                "menu": {"type": "string", "description": "Restoran cepat saji ini menawarkan berbagai pilihan menu lezat seperti pada restoran cepat saji pada umumnya. "},
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

async def gemini_session_handler(client_websocket: websockets.WebSocketServerProtocol):
    """Handles the interaction with Gemini API within a websocket session.

    Args:
        client_websocket: The websocket connection to the client.
    """
    try:
        config_message = await client_websocket.recv()
        config_data = json.loads(config_message)
        config = config_data.get("setup", {})
        
        config["tools"] = [tool_save_order]

        config["system_instruction"] = {
            "role": "system",
            "parts": [
                {
                    "text": (
                        "Anda adalah asisten drive-thru restoran cepat saji yang ramah dan efisien. "
                        "Gunakan bahasa Indonesia yang santai dan alami, seperti saat berbicara langsung dengan pelanggan di drive-thru restoran cepat saji. Hindari terjemahan harfiah dari bahasa Inggris."
                        "Bantu mereka memesan makanan atau minuman, jawab pertanyaan dengan ringkas, dan arahkan proses pemesanan secara efisien."
                        "Contoh respons:"
                        "- \"Halo, mau pesan apa hari ini?"
                        "- \"Baik, 1 Burger Keju dan 1 Fanta. Ada tambahan lainnya?"
                        "- \"Sip! Pesanan lagi diproses. Makasih, ya!"
                        "Jika pelanggan mengatakan hal yang tidak relevan atau tidak dimengerti, tanggapi dengan sopan dan minta klarifikasi."
                        "Contoh: \"Maaf, bisa diulangi pesanannya? Saya belum menangkap dengan jelas."
                    )
                }
            ]
        }

        async with client.aio.live.connect(model=MODEL, config=config) as session:
            print("Connected to Gemini API")

            async def send_to_gemini():
                """Sends messages from the client websocket to the Gemini API."""
                try:
                  async for message in client_websocket:
                      try:
                          data = json.loads(message)
                          if "realtime_input" in data:
                              for chunk in data["realtime_input"]["media_chunks"]:
                                  if chunk["mime_type"] == "audio/pcm":
                                      await session.send({"mime_type": "audio/pcm", "data": chunk["data"]})
                                      
                                  elif chunk["mime_type"] == "image/jpeg":
                                      await session.send({"mime_type": "image/jpeg", "data": chunk["data"]})
                                      
                      except Exception as e:
                          print(f"Error sending to Gemini: {e}")
                  print("Client connection closed (send)")
                except Exception as e:
                     print(f"Error sending to Gemini: {e}")
                finally:
                   print("send_to_gemini closed")



            async def receive_from_gemini():
                """Receives responses from the Gemini API and forwards them to the client, looping until turn is complete."""
                try:
                    while True:
                        try:
                            print("receiving from gemini")
                            async for response in session.receive():
                                #first_response = True
                                #print(f"response: {response}")
                                if response.server_content is None:
                                    if response.tool_call is not None:
                                          #handle the tool call
                                           print(f"Tool call received: {response.tool_call}")

                                           function_calls = response.tool_call.function_calls
                                           function_responses = []

                                           for function_call in function_calls:
                                                 name = function_call.name
                                                 args = function_call.args
                                                 # Extract the numeric part from Gemini's function call ID
                                                 call_id = function_call.id

                                                 # Validate function name
                                                 if name == "save_order":
                                                      try:
                                                          result = save_order(args["items"])
                                                          function_responses.append(
                                                             {
                                                                 "name": name,
                                                                 #"response": {"result": "The light is broken."},
                                                                 "response": {"result": result},
                                                                 "id": call_id  
                                                             }
                                                          ) 
                                                          await client_websocket.send(json.dumps({"text": json.dumps(function_responses)}))
                                                          print("Function executed")
                                                      except Exception as e:
                                                          print(f"Error executing function: {e}")
                                                          continue


                                           # Send function response back to Gemini
                                           print(f"function_responses: {function_responses}")
                                           await session.send(function_responses)
                                           continue

                                    #print(f'Unhandled server message! - {response}')
                                    #continue

                                model_turn = response.server_content.model_turn
                                if model_turn:
                                    for part in model_turn.parts:
                                        #print(f"part: {part}")
                                        if hasattr(part, 'text') and part.text is not None:
                                            #print(f"text: {part.text}")
                                            await client_websocket.send(json.dumps({"text": part.text}))
                                        elif hasattr(part, 'inline_data') and part.inline_data is not None:
                                            # if first_response:
                                            #print("audio mime_type:", part.inline_data.mime_type)
                                                #first_response = False
                                            base64_audio = base64.b64encode(part.inline_data.data).decode('utf-8')
                                            await client_websocket.send(json.dumps({
                                                "audio": base64_audio,
                                            }))
                                            print("audio received")

                                if response.server_content.turn_complete:
                                    print('\n<Turn complete>')
                        except websockets.exceptions.ConnectionClosedOK:
                            print("Client connection closed normally (receive)")
                            break  # Exit the loop if the connection is closed
                        except Exception as e:
                            print(f"Error receiving from Gemini: {e}")
                            break # exit the lo

                except Exception as e:
                      print(f"Error receiving from Gemini: {e}")
                finally:
                      print("Gemini connection closed (receive)")


            # Start send loop
            send_task = asyncio.create_task(send_to_gemini())
            # Launch receive loop as a background task
            receive_task = asyncio.create_task(receive_from_gemini())
            await asyncio.gather(send_task, receive_task)


    except Exception as e:
        print(f"Error in Gemini session: {e}")
    finally:
        print("Gemini session closed.")


# async def main() -> None:
#     async with websockets.serve(gemini_session_handler, "localhost", 9082):
#         print("Running websocket server localhost:9082...")
#         await asyncio.Future()  # Keep the server running indefinitely

async def main() -> None:
    port = int(os.environ.get("PORT", 8000))  # Gunakan PORT dari Railway
    async with websockets.serve(gemini_session_handler, "0.0.0.0", port):
        print(f"Running websocket server on 0.0.0.0:{port}...")
        await asyncio.Future()

if __name__ == "__main__":
    asyncio.run(main())