"""FastAPI web UI application for the Serendipity AI agent platform."""

from pathlib import Path

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

app = FastAPI(title="Serendipity Web UI")

BASE_DIR = Path(__file__).resolve().parent.parent
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "app" / "templates")


@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    """Render the home page listing all agents.

    :param request: The incoming HTTP request.
    :type request: Request
    :returns: The rendered ``agents.html`` template.
    :rtype: TemplateResponse
    """
    return templates.TemplateResponse("agents.html", {"request": request})


@app.get("/agent/{agent_name}", response_class=HTMLResponse)
async def agent_home(request: Request, agent_name: str):
    """Render the home page for a specific agent.

    :param request: The incoming HTTP request.
    :type request: Request
    :param agent_name: The name of the agent.
    :type agent_name: str
    :returns: The rendered ``agent_home.html`` template.
    :rtype: TemplateResponse
    """
    return templates.TemplateResponse("agent_home.html", {"request": request, "agent_name": agent_name})


@app.get("/agent/{agent_name}/conversations", response_class=HTMLResponse)
async def conversations(request: Request, agent_name: str):
    """Render the conversations list page for an agent.

    :param request: The incoming HTTP request.
    :type request: Request
    :param agent_name: The name of the agent.
    :type agent_name: str
    :returns: The rendered ``conversations.html`` template.
    :rtype: TemplateResponse
    """
    return templates.TemplateResponse("conversations.html", {"request": request, "agent_name": agent_name})


@app.get("/agent/{agent_name}/conversation", response_class=HTMLResponse)
async def conversation(request: Request, agent_name: str, conversation_id: str = ""):
    """Render the conversation page for an agent.

    :param request: The incoming HTTP request.
    :type request: Request
    :param agent_name: The name of the agent.
    :type agent_name: str
    :param conversation_id: The optional ID of the conversation to load.
    :type conversation_id: str
    :returns: The rendered ``conversation.html`` template.
    :rtype: TemplateResponse
    """
    return templates.TemplateResponse("conversation.html", {
        "request": request,
        "agent_name": agent_name,
        "conversation_id": conversation_id
    })


@app.get("/settings", response_class=HTMLResponse)
async def settings(request: Request):
    """Render the application settings page.

    :param request: The incoming HTTP request.
    :type request: Request
    :returns: The rendered ``settings.html`` template.
    :rtype: TemplateResponse
    """
    return templates.TemplateResponse("settings.html", {"request": request})


@app.get("/agent/{agent_name}/edit", response_class=HTMLResponse)
async def edit_agent(request: Request, agent_name: str):
    """Render the edit page for an agent.

    :param request: The incoming HTTP request.
    :type request: Request
    :param agent_name: The name of the agent to edit.
    :type agent_name: str
    :returns: The rendered ``edit_agent.html`` template.
    :rtype: TemplateResponse
    """
    return templates.TemplateResponse("edit_agent.html", {"request": request, "agent_name": agent_name})


@app.get("/mcps", response_class=HTMLResponse)
async def mcps(request: Request):
    """Render the MCP servers management page.

    :param request: The incoming HTTP request.
    :type request: Request
    :returns: The rendered ``mcps.html`` template.
    :rtype: TemplateResponse
    """
    return templates.TemplateResponse("mcps.html", {"request": request})


@app.get("/toolsets", response_class=HTMLResponse)
async def toolsets(request: Request):
    """Render the toolsets management page.

    :param request: The incoming HTTP request.
    :type request: Request
    :returns: The rendered ``toolsets.html`` template.
    :rtype: TemplateResponse
    """
    return templates.TemplateResponse("toolsets.html", {"request": request})


@app.get("/skills", response_class=HTMLResponse)
async def skills(request: Request):
    """Render the skills management page.

    :param request: The incoming HTTP request.
    :type request: Request
    :returns: The rendered ``skills.html`` template.
    :rtype: TemplateResponse
    """
    return templates.TemplateResponse("skills.html", {"request": request})


@app.get("/llms", response_class=HTMLResponse)
async def llms(request: Request):
    """Render the LLM configurations page.

    :param request: The incoming HTTP request.
    :type request: Request
    :returns: The rendered ``llms.html`` template.
    :rtype: TemplateResponse
    """
    return templates.TemplateResponse("llms.html", {"request": request})


@app.get("/scheduled-tasks", response_class=HTMLResponse)
async def scheduled_tasks(request: Request):
    """Render the scheduled tasks page.

    :param request: The incoming HTTP request.
    :type request: Request
    :returns: The rendered ``scheduled_tasks.html`` template.
    :rtype: TemplateResponse
    """
    return templates.TemplateResponse("scheduled_tasks.html", {"request": request})


@app.get("/agent/{agent_name}/workflows", response_class=HTMLResponse)
async def workflows(request: Request, agent_name: str, workflow_id: str = "", node_id: str = ""):
    """Render the workflows page for an agent.

    :param request: The incoming HTTP request.
    :type request: Request
    :param agent_name: The name of the agent.
    :type agent_name: str
    :param workflow_id: The optional ID of the workflow to load.
    :type workflow_id: str
    :param node_id: The optional ID of the workflow node to load.
    :type node_id: str
    :returns: The rendered ``workflows.html`` template.
    :rtype: TemplateResponse
    """
    return templates.TemplateResponse("workflows.html", {
        "request": request,
        "agent_name": agent_name,
        "workflow_id": workflow_id,
        "node_id": node_id
    })


@app.get("/api/tts-proxy")
async def tts_proxy(request: Request, server: str, voice: str, text: str, style: str = "default", language: str = "English", speed: float = 1.0):
    """Proxy TTS requests to avoid browser timeouts on slow generation"""
    from fastapi.responses import JSONResponse
    
    # Build the TTS URL
    server_url = server.rstrip('/')
    params = {
        "voice": voice,
        "text": text,
        "style": style,
        "language": language,
    }
    if speed != 1.0:
        params["speed"] = speed
    
    # Use a long timeout for slow TTS generation (5 minutes)
    async with httpx.AsyncClient(timeout=httpx.Timeout(300.0)) as client:
        try:
            response = await client.get(f"{server_url}/synthesize_speech/", params=params)
            response.raise_for_status()
            
            # Ensure we return audio content type
            content_type = response.headers.get("content-type", "audio/wav")
            if "json" in content_type:
                # TTS server returned an error as JSON
                return JSONResponse({"error": "TTS server returned error"}, status_code=500)
            
            return StreamingResponse(
                iter([response.content]),
                media_type=content_type,
                headers={"Cache-Control": "no-cache"}
            )
        except httpx.TimeoutException:
            return JSONResponse({"error": "TTS generation timed out"}, status_code=504)
        except httpx.HTTPStatusError as e:
            return JSONResponse({"error": f"TTS server error: {e.response.status_code}"}, status_code=e.response.status_code)
        except Exception as e:  # noqa: BLE001
            return JSONResponse({"error": str(e)}, status_code=500)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)
