from __future__ import annotations

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import api_chat, api_data, api_feedback, api_models, api_pdf, db
from .config import DATA_DIR, UPLOAD_DIR, WEB_DIR, ensure_dirs

ensure_dirs()
WEB_DIR.mkdir(parents=True, exist_ok=True)
db.init_db()

app = FastAPI(title="XIN", version="1.0.0", docs_url=None, redoc_url=None)

app.include_router(api_chat.router)
app.include_router(api_data.router)
app.include_router(api_models.router)
app.include_router(api_pdf.router)
app.include_router(api_feedback.router)

# PDF 转换产物静态目录（供下载和预览图使用）
pdf_out = DATA_DIR / "pdf_outputs"
pdf_pre = DATA_DIR / "pdf_previews"
pdf_out.mkdir(parents=True, exist_ok=True)
pdf_pre.mkdir(parents=True, exist_ok=True)

app.mount("/uploads", StaticFiles(directory=str(UPLOAD_DIR)), name="uploads")


@app.get("/")
async def index():
    return FileResponse(
        WEB_DIR / "index.html", headers={"Cache-Control": "no-cache, must-revalidate"}
    )


app.mount("/", StaticFiles(directory=str(WEB_DIR), html=True), name="web")
