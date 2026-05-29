import logging
from contextlib import asynccontextmanager
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(message)s",
    datefmt="%H:%M:%S",
)
logging.getLogger("agent").setLevel(logging.INFO)

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from langchain_core.messages import HumanMessage
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from pydantic import BaseModel

from agent.db import CHECKPOINTER_DB_PATH, init_compliance_db
from agent.graph import build_graph


class ChatRequest(BaseModel):
    conversation_id: str
    query: str


class Citation(BaseModel):
    source: str
    page: int


class ChatResponse(BaseModel):
    conversation_id: str
    answer: str
    citations: list[Citation]
    confidence_score: float
    blocked: bool


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_compliance_db()
    Path(CHECKPOINTER_DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    async with AsyncSqliteSaver.from_conn_string(CHECKPOINTER_DB_PATH) as checkpointer:
        await checkpointer.setup()
        app.state.graph = build_graph(checkpointer)
        yield


app = FastAPI(lifespan=lifespan)

app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/", response_class=HTMLResponse)
async def index():
    return FileResponse("static/index.html")


@app.exception_handler(Exception)
async def _unhandled_exception_handler(request: Request, exc: Exception):
    return JSONResponse(status_code=500, content={"detail": "Internal error"})


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest, request: Request) -> ChatResponse:
    if not req.query or not req.query.strip():
        raise HTTPException(status_code=400, detail="Query cannot be empty.")
    config = {
        "configurable": {"thread_id": req.conversation_id},
        "recursion_limit": 12,
    }
    state = await request.app.state.graph.ainvoke(
        {
            "messages": [HumanMessage(content=req.query)],
            "conversation_id": req.conversation_id,
        },
        config=config,
    )
    return ChatResponse(
        conversation_id=req.conversation_id,
        answer=state.get("answer", ""),
        citations=state.get("citations", []),
        confidence_score=state.get("confidence_score", 0.0),
        blocked=state.get("blocked", False),
    )
