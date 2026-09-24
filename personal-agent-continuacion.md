# INSTRUCCIONES PARA CONTINUAR EL PROYECTO PERSONAL AGENT

## Objetivo

Crear un asistente personal accesible desde móvil, con una interfaz similar a ChatGPT, que pueda:

- Responder preguntas generales mediante OpenAI.
- Delegar preguntas de entrenamiento al Training Coach Agent existente.
- Integrar Home Assistant para controlar la casa.
- Mantener memoria personal mediante Supabase.
- Incorporar voz más adelante.

Arquitectura prevista:

Mobile / Open WebUI
        ↓ HTTPS
Nginx en Raspberry Pi / Home Assistant
        ↓ LAN
Open WebUI en el PC (Podman)
        ↓
OpenAI / Gemini / modelos locales
        ↓
Personal Agent / Router
        ↓
Training Coach / Home Assistant / otros agentes


# 1. Training Coach Agent existente

Repositorio:

https://github.com/eloyrgz/training-coach-agent

No modificar este repositorio para la integración con Open WebUI.

La API existente está en:

api.py

Se ejecuta con:

uvicorn api:app --host 0.0.0.0 --port 8000

Endpoints principales:

- /health
- /chat
- /chat/reset
- /sync

El endpoint /chat recibe:

{
  "message": "...",
  "conversation_id": "..."
}

Y devuelve:

{
  "reply": "...",
  "conversation_id": "..."
}


# 2. Open WebUI

El PC es Fedora Linux.

Se utiliza Podman, no Docker.

Open WebUI está funcionando en un contenedor llamado:

open-webui

Está publicado en:

http://localhost:3000

La IP LAN del PC es:

192.168.0.2

Open WebUI ya está conectado correctamente con OpenAI.

GPT-5.6 Luna funciona correctamente.


# 3. Training Coach Bridge

Se creó un bridge independiente para que Open WebUI pueda hablar con Training Coach mediante una API compatible con OpenAI.

Directorio:

~/personal-agent/training-coach-bridge

Entorno virtual:

~/personal-agent/training-coach-bridge/.venv

Archivo:

~/personal-agent/training-coach-bridge/bridge.py

Dependencias instaladas:

fastapi
uvicorn
httpx


# 4. Puerto del bridge

El bridge utiliza:

8100

Actualmente debe ejecutarse escuchando en:

0.0.0.0:8100

Comando:

cd ~/personal-agent/training-coach-bridge
source .venv/bin/activate
uvicorn bridge:app --host 0.0.0.0 --port 8100


# 5. Comunicación desde Open WebUI

IMPORTANTE:

Dentro del contenedor de Open WebUI, localhost NO es el PC.

Para acceder al bridge desde Open WebUI se utiliza:

http://host.containers.internal:8100/v1

Esto ya ha sido probado y funciona.

Prueba realizada:

podman exec open-webui curl -s --max-time 5 http://host.containers.internal:8100/v1/models

Devuelve:

{
  "object": "list",
  "data": [
    {
      "id": "training-coach",
      "object": "model",
      "created": ...,
      "owned_by": "eloy"
    }
  ]
}


# 6. Conexión creada en Open WebUI

En Open WebUI se añadió una conexión:

Connection Type:
External

URL:

http://host.containers.internal:8100/v1

La conexión aparece como:

Server connection verified

Y aparece un modelo:

training-coach

El modelo puede seleccionarse desde Open WebUI.


# 7. Prueba que ya funciona

Desde Open WebUI se preguntó:

"Qué sabes de mi entrenamiento?"

Training Coach respondió correctamente.

Después se preguntó:

"Cuál es mi última actividad?"

Training Coach respondió correctamente indicando la última actividad disponible.

Por tanto:

Open WebUI
    ↓
bridge
    ↓
Training Coach API
    ↓
respuesta

FUNCIONA.


# 8. PROBLEMA ACTUAL

El problema pendiente es que Training Coach no mantiene correctamente el contexto de conversación cuando se realizan varios mensajes desde Open WebUI.

El bridge actualmente utiliza:

conversation_id = request.get(
    "conversation_id",
    str(uuid.uuid4())
)

El problema es que Open WebUI NO está enviando conversation_id dentro del JSON.

Se comprobó imprimiendo:

print("REQUEST KEYS:", request.keys())

Y Open WebUI envió:

dict_keys(['stream', 'model', 'messages', 'tools'])

y también:

dict_keys(['model', 'messages', 'stream'])

Por tanto:

Open WebUI → bridge

NO está enviando:

conversation_id


# 9. ESTADO ACTUAL DEL DEBUG

Se decidió NO solucionar todavía el problema inventando un sistema de IDs.

Primero hay que comprobar si Open WebUI está enviando algún identificador estable en las cabeceras HTTP.

Para ello hay que modificar temporalmente bridge.py.


# 10. PRÓXIMO PASO EXACTO

Abrir:

~/personal-agent/training-coach-bridge/bridge.py

Cambiar:

from fastapi import FastAPI

por:

from fastapi import FastAPI, Request


Después cambiar:

async def chat_completions(request: dict):

por:

async def chat_completions(request: dict, http_request: Request):


Después de:

messages = request.get("messages", [])

añadir:

print("HEADERS:", dict(http_request.headers))


El resultado debe quedar aproximadamente así:

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
import httpx
import time
import uuid

app = FastAPI()

TRAINING_COACH_URL = "http://127.0.0.1:8000/chat"


@app.get("/v1/models")
async def models():
    return {
        "object": "list",
        "data": [
            {
                "id": "training-coach",
                "object": "model",
                "created": int(time.time()),
                "owned_by": "eloy"
            }
        ]
    }


@app.post("/v1/chat/completions")
async def chat_completions(request: dict, http_request: Request):

    messages = request.get("messages", [])

    print("HEADERS:", dict(http_request.headers))

    if not messages:
        return JSONResponse(
            status_code=400,
            content={"error": "No messages provided"}
        )

    user_message = None

    for message in reversed(messages):
        if message.get("role") == "user":
            user_message = message.get("content")
            break

    if not user_message:
        return JSONResponse(
            status_code=400,
            content={"error": "No user message found"}
        )

    conversation_id = request.get(
        "conversation_id",
        str(uuid.uuid4())
    )

    payload = {
        "message": user_message,
        "conversation_id": conversation_id
    }

    async with httpx.AsyncClient(timeout=120.0) as client:
        response = await client.post(
            TRAINING_COACH_URL,
            json=payload
        )

    if response.status_code != 200:
        return JSONResponse(
            status_code=response.status_code,
            content={
                "error": "Training Coach returned an error",
                "details": response.text
            }
        )

    result = response.json()

    return {
        "id": f"chatcmpl-{uuid.uuid4()}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": "training-coach",
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": result["reply"]
                },
                "finish_reason": "stop"
            }
        ]
    }


# 11. Después de modificarlo

Comprobar que no hay errores de sintaxis:

python -m py_compile ~/personal-agent/training-coach-bridge/bridge.py

Si no aparece nada, está correcto.


# 12. Arrancar el bridge

Si había un bridge ejecutándose, detenerlo con:

Ctrl+C

Después:

cd ~/personal-agent/training-coach-bridge
source .venv/bin/activate
uvicorn bridge:app --host 0.0.0.0 --port 8100


# 13. HACER UNA SOLA PRUEBA

Con el bridge ejecutándose:

1. Abrir Open WebUI.
2. Seleccionar training-coach.
3. Enviar solamente un mensaje.
4. Volver inmediatamente a la terminal donde está ejecutándose uvicorn.
5. Copiar la línea que empieza por:

HEADERS:

No hacer todavía más pruebas.


# 14. OBJETIVO DE ESTA PRUEBA

Queremos descubrir si Open WebUI incluye en las cabeceras algún identificador que podamos utilizar para mantener la misma conversación.

NO implementar todavía ninguna solución definitiva.

Primero veremos qué contiene:

HEADERS:


# 15. ERROR 422

Durante las pruebas apareció ocasionalmente:

422 Unprocessable Content

También apareció una petición normal:

INFO: ... "POST /v1/chat/completions HTTP/1.1" 200 OK

El error 422 queda APARCADO por ahora.

Primero solucionar:

Open WebUI
    ↓
identificador de conversación
    ↓
Training Coach conversation_id

Después investigaremos el 422.


# 16. IMPORTANTE

NO modificar:

telegram_bot.py

NO modificar el funcionamiento del Training Coach existente.

El bridge es una capa independiente.


# 17. SITUACIÓN ACTUAL RESUMIDA

Ya funciona:

Open WebUI
    ↓
External Connection
    ↓
host.containers.internal:8100
    ↓
Training Coach Bridge
    ↓
127.0.0.1:8000
    ↓
Training Coach
    ↓
respuesta


Falta:

Open WebUI
    ↓
identificador estable de conversación
    ↓
bridge
    ↓
conversation_id estable
    ↓
Training Coach


# 18. FUTURO

Una vez resuelto el contexto del Training Coach:

1. Crear Personal Agent / Router.
2. El usuario hablará con un único modelo.
3. El router decidirá si la petición es:
   - conversación general → OpenAI
   - entrenamiento → Training Coach
   - casa → Home Assistant
   - etc.
4. Añadir memoria persistente con Supabase.
5. Integrar Home Assistant.
6. Configurar acceso HTTPS mediante el Nginx existente en Raspberry Pi.
7. Utilizar Open WebUI desde móvil/PWA.
8. Añadir voz.


# PUNTO EXACTO DONDE CONTINUAR

El bridge está preparado para el siguiente debug.

NO empezar el proyecto desde cero.

El próximo paso es:

1. Añadir Request.
2. Imprimir HEADERS.
3. Reiniciar bridge.
4. Mandar UN mensaje desde Open WebUI.
5. Mirar la línea HEADERS.
6. Analizar qué identificador, si alguno, está enviando Open WebUI.

Después de eso se decidirá cómo mantener conversation_id.

NO solucionar todavía el 422.
NO modificar Training Coach.
NO modificar Telegram.

Continuar exactamente desde este punto.