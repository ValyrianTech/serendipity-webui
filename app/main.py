from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pathlib import Path

app = FastAPI(title="Serendipity Web UI")

BASE_DIR = Path(__file__).resolve().parent.parent
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "app" / "templates")


@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return templates.TemplateResponse("agents.html", {"request": request})


@app.get("/agent/{agent_name}", response_class=HTMLResponse)
async def agent_home(request: Request, agent_name: str):
    return templates.TemplateResponse("agent_home.html", {"request": request, "agent_name": agent_name})


@app.get("/agent/{agent_name}/conversations", response_class=HTMLResponse)
async def conversations(request: Request, agent_name: str):
    return templates.TemplateResponse("conversations.html", {"request": request, "agent_name": agent_name})


@app.get("/agent/{agent_name}/conversation", response_class=HTMLResponse)
async def conversation(request: Request, agent_name: str, conversation_id: str = ""):
    return templates.TemplateResponse("conversation.html", {
        "request": request,
        "agent_name": agent_name,
        "conversation_id": conversation_id
    })


@app.get("/settings", response_class=HTMLResponse)
async def settings(request: Request):
    return templates.TemplateResponse("settings.html", {"request": request})


@app.get("/agent/{agent_name}/edit", response_class=HTMLResponse)
async def edit_agent(request: Request, agent_name: str):
    return templates.TemplateResponse("edit_agent.html", {"request": request, "agent_name": agent_name})


@app.get("/mcps", response_class=HTMLResponse)
async def mcps(request: Request):
    return templates.TemplateResponse("mcps.html", {"request": request})


@app.get("/toolsets", response_class=HTMLResponse)
async def toolsets(request: Request):
    return templates.TemplateResponse("toolsets.html", {"request": request})


@app.get("/llms", response_class=HTMLResponse)
async def llms(request: Request):
    return templates.TemplateResponse("llms.html", {"request": request})


@app.get("/agent/{agent_name}/workflows", response_class=HTMLResponse)
async def workflows(request: Request, agent_name: str, workflow_id: str = "", node_id: str = ""):
    return templates.TemplateResponse("workflows.html", {
        "request": request,
        "agent_name": agent_name,
        "workflow_id": workflow_id,
        "node_id": node_id
    })


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)
