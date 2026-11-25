"""PDF 转换路由 — FastAPI 版本。支持上传 PDF → 6 种格式转换 → 下载。"""

from __future__ import annotations

import shutil
import tempfile
import time
import uuid
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse

from .config import DATA_DIR
from .pdf_converter import (
    ConversionError,
    EXPORT_FORMATS,
    get_pdf_page_count,
    render_pdf_preview_pages,
    run_conversion,
)

router = APIRouter()

# 存转换输出和预览的目录（跟 uploads/ 同级，都在 data/ 下）
OUTPUT_DIR = DATA_DIR / "pdf_outputs"
PREVIEW_DIR = DATA_DIR / "pdf_previews"
UPLOAD_LIMIT = 100 * 1024 * 1024  # 100 MB


def _safe_pdf(pdf: UploadFile) -> bytes:
    """把上传的 PDF 读进内存，做基础校验。"""
    if not pdf.filename or Path(pdf.filename).suffix.lower() != ".pdf":
        raise HTTPException(400, "只支持 .pdf 文件")
    data = pdf.file.read()
    if len(data) == 0:
        raise HTTPException(400, "文件是空的")
    if len(data) > UPLOAD_LIMIT:
        raise HTTPException(413, f"文件超过 {UPLOAD_LIMIT // 1024 // 1024}MB")
    return data


@router.post("/api/pdf/preview")
async def pdf_preview(file: UploadFile = File(...)):
    """上传 PDF → 返回预览图 URL 列表（每页一张 PNG）。"""
    data = _safe_pdf(file)
    preview_id = uuid.uuid4().hex
    target = PREVIEW_DIR / preview_id / "source.pdf"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)

    try:
        image_names = render_pdf_preview_pages(target, target.parent)
        page_count = get_pdf_page_count(target)
    except ConversionError as exc:
        raise HTTPException(422, str(exc))
    except Exception as exc:
        raise HTTPException(500, f"预览生成失败：{exc}")

    return {
        "preview_id": preview_id,
        "pages": page_count,
        "images": [f"/pdf/preview/{preview_id}/{name}" for name in image_names],
    }


@router.get("/pdf/preview/{preview_id}/{filename}")
async def serve_preview(preview_id: str, filename: str):
    """返回预览图文件。"""
    if not preview_id.isalnum() or not filename.startswith("page_") or not filename.endswith(".png"):
        raise HTTPException(404, "找不到")
    path = PREVIEW_DIR / preview_id / filename
    if not path.exists():
        raise HTTPException(404, "找不到")
    return FileResponse(str(path), media_type="image/png")


@router.post("/api/pdf/convert")
async def pdf_convert(file: UploadFile = File(...), fmt: str = Form("word-fidelity")):
    """上传 PDF → 转换 → 返回下载 URL。

    fmt 可选：word-fidelity / word-editable / excel / png / jpeg / txt
    """
    if fmt not in EXPORT_FORMATS:
        raise HTTPException(400, f"不支持的格式：{fmt}。可选：{list(EXPORT_FORMATS.keys())}")

    data = _safe_pdf(file)
    stem = Path(file.filename).stem.strip() or "converted"

    # 写临时文件
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp) / f"{stem}.pdf"
        tmp_path.write_bytes(data)
        pages = get_pdf_page_count(tmp_path)

        started = time.perf_counter()
        try:
            out_path, out_name = run_conversion(tmp_path, OUTPUT_DIR, fmt)
        except ConversionError as exc:
            raise HTTPException(422, str(exc))
        except Exception as exc:
            raise HTTPException(500, f"转换失败：{exc}")

    elapsed = round(time.perf_counter() - started, 2)

    job_id = out_path.stem  # 用输出文件名当 job_id
    return {
        "job_id": job_id,
        "file_name": out_name,
        "format": fmt,
        "format_label": EXPORT_FORMATS[fmt]["label"],
        "pages": pages,
        "seconds": elapsed,
        "download_url": f"/pdf/download/{job_id}/{out_name}",
    }


@router.get("/pdf/download/{job_id}/{filename}")
async def pdf_download(job_id: str, filename: str):
    """下载转换后的文件。"""
    if not job_id:
        raise HTTPException(404, "找不到")
    # 直接在 OUTPUT_DIR 找匹配的文件
    target = OUTPUT_DIR / filename
    if not target.exists():
        # 也可能文件名里带 job_id 前缀
        matches = list(OUTPUT_DIR.glob(f"*{job_id}*"))
        if matches:
            target = matches[0]
        else:
            raise HTTPException(404, "文件可能已经被清理了，请重新转换。")
    import urllib.parse
    safe_name = target.name.replace(",", "_")
    encoded = urllib.parse.quote(safe_name)
    return FileResponse(
        str(target),
        media_type="application/octet-stream",
        filename=safe_name.encode("ascii", errors="replace").decode("ascii"),
        headers={"Content-Disposition": f'attachment; filename="{safe_name.encode("ascii", errors="replace").decode("ascii")}"; filename*=UTF-8\'\'{encoded}'},
    )


@router.get("/api/pdf/formats")
async def pdf_formats():
    """列出支持的转换格式（给前端下拉框用）。"""
    return {
        "formats": [
            {"key": k, **v}
            for k, v in EXPORT_FORMATS.items()
        ]
    }


@router.delete("/api/pdf/cleanup")
async def pdf_cleanup(older_than_hours: int = 24):
    """清理超过 N 小时的转换产物和预览（默认 24h）。"""
    cutoff = time.time() - older_than_hours * 3600
    removed = {"outputs": 0, "previews": 0}

    if OUTPUT_DIR.exists():
        for p in OUTPUT_DIR.iterdir():
            if p.stat().st_mtime < cutoff:
                try:
                    p.unlink()
                    removed["outputs"] += 1
                except Exception:
                    pass
    if PREVIEW_DIR.exists():
        for p in PREVIEW_DIR.iterdir():
            if p.is_dir() and p.stat().st_mtime < cutoff:
                try:
                    shutil.rmtree(p)
                    removed["previews"] += 1
                except Exception:
                    pass

    return {"removed": removed}
