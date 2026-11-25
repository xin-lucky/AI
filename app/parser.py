"""统一的文档文本抽取器 — 支持 PDF / DOCX / XLSX / XLS / TXT / CSV。

所有抽取函数都返回纯字符串，失败时返回空串（带 warning 信息）。
没有外部 API 依赖，全在本地跑。
"""

from __future__ import annotations

import csv
import io
import os
from pathlib import Path

# --- 可选依赖（某个装不上就跳过对应格式） ---
try:
    import pymupdf  # PyMuPDF 新版
    _HAS_PDF = True
except Exception:
    try:
        import fitz as pymupdf  # 旧版兼容
        _HAS_PDF = True
    except Exception:
        _HAS_PDF = False

try:
    import docx  # python-docx
    _HAS_DOCX = True
except Exception:
    _HAS_DOCX = False

try:
    import openpyxl  # openpyxl
    _HAS_XLSX = True
except Exception:
    _HAS_XLSX = False


# 支持的后缀
SUPPORTED_EXT = {".pdf", ".docx", ".xlsx", ".xls", ".txt", ".csv", ".md"}

# 单文档最多塞进 prompt 的字符数（截断用）
MAX_CHARS_PER_DOC = 8000


def parse_document(path: str | os.PathLike) -> dict:
    """抽一个文件的文本。返回 {text, format, error, truncated, filename}。

    text 为空时看 error。长文档会截断，truncated=True 时说明中间跳过了。
    """
    p = Path(path)
    suffix = p.suffix.lower()
    filename = p.name

    result: dict = {
        "text": "",
        "format": suffix.lstrip("."),
        "error": None,
        "truncated": False,
        "filename": filename,
    }

    if not p.exists():
        result["error"] = "文件不存在"
        return result
    if p.stat().st_size == 0:
        result["error"] = "文件是空的"
        return result
    # 50 MB 上限（超过直接拒绝，防止爆内存）
    if p.stat().st_size > 50 * 1024 * 1024:
        result["error"] = "文件超过 50MB，暂时处理不了"
        return result

    try:
        raw = _dispatch(p, suffix)
    except Exception as exc:
        result["error"] = f"解析失败：{exc}"
        return result

    text = (raw or "").strip()
    if not text:
        result["error"] = "没抽到内容，可能是扫描件（图片型 PDF 需要 OCR）"
        return result

    # 截断：保留前 N 字 + 后 N 字，中间跳过
    if len(text) > MAX_CHARS_PER_DOC:
        head = text[:MAX_CHARS_PER_DOC * 3 // 4]
        tail = text[-MAX_CHARS_PER_DOC // 4:]
        skipped = len(text) - len(head) - len(tail)
        text = f"{head}\n\n……[中间 {skipped} 字省略]……\n\n{tail}"
        result["truncated"] = True

    result["text"] = text
    return result


def _dispatch(path: Path, suffix: str) -> str:
    if suffix == ".pdf":
        if not _HAS_PDF:
            raise RuntimeError("PDF 支持没装：pip install PyMuPDF")
        return _parse_pdf(path)
    if suffix == ".docx":
        if not _HAS_DOCX:
            raise RuntimeError("DOCX 支持没装：pip install python-docx")
        return _parse_docx(path)
    if suffix in (".xlsx", ".xls"):
        if not _HAS_XLSX:
            raise RuntimeError("Excel 支持没装：pip install openpyxl")
        return _parse_xlsx(path)
    if suffix in (".txt", ".md"):
        return _parse_txt(path)
    if suffix == ".csv":
        return _parse_csv(path)
    raise RuntimeError(f"不支持的格式：{suffix}")


# ---------- 各格式实现 ----------

def _parse_pdf(path: Path) -> str:
    """PyMuPDF 抽 PDF 文本，逐页拼接。"""
    parts: list[str] = []
    doc = pymupdf.open(str(path))
    try:
        for i, page in enumerate(doc):
            text = page.get_text("text") or ""
            text = text.strip()
            if text:
                parts.append(f"[第 {i + 1} 页]\n{text}")
    finally:
        doc.close()
    return "\n\n".join(parts)


def _parse_docx(path: Path) -> str:
    """python-docx 抽段落 + 表格里的文字。"""
    d = docx.Document(str(path))
    parts: list[str] = []
    for para in d.paragraphs:
        t = (para.text or "").strip()
        if t:
            parts.append(t)
    # 表格里的内容也抽出来（表格 = 行列表 = 单元格列表）
    for ti, table in enumerate(d.tables):
        rows_text: list[str] = []
        for row in table.rows:
            cells = [cell.text.strip().replace("\n", " ") for cell in row.cells]
            rows_text.append(" | ".join(c for c in cells if c))
        table_str = "\n".join(r for r in rows_text if r)
        if table_str:
            parts.append(f"[表格 {ti + 1}]\n{table_str}")
    return "\n".join(parts)


def _parse_xlsx(path: Path) -> str:
    """openpyxl 抽所有 sheet 的内容，每个 sheet 带标题。"""
    wb = openpyxl.load_workbook(str(path), data_only=True, read_only=True)
    try:
        sheets: list[str] = []
        for name in wb.sheetnames:
            ws = wb[name]
            rows_text: list[str] = []
            for row in ws.iter_rows(values_only=True):
                cells = ["" if c is None else str(c).strip() for c in row]
                # 整行都是空的跳过
                if any(cells):
                    rows_text.append(" | ".join(cells))
            if rows_text:
                sheets.append(f"[Sheet: {name}]\n" + "\n".join(rows_text))
        return "\n\n".join(sheets)
    finally:
        wb.close()


def _parse_txt(path: Path) -> str:
    """TXT / MD：按 UTF-8 读，带 GBK 降级。"""
    for enc in ("utf-8", "utf-8-sig", "gbk", "gb18030", "latin-1"):
        try:
            return path.read_text(encoding=enc)
        except UnicodeDecodeError:
            continue
    # 最后兜底：忽略错误
    return path.read_text(encoding="utf-8", errors="ignore")


def _parse_csv(path: Path) -> str:
    """CSV：用 csv module 读，按表格格式拼。"""
    for enc in ("utf-8", "utf-8-sig", "gbk", "gb18030"):
        try:
            raw = path.read_text(encoding=enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        raw = path.read_text(encoding="utf-8", errors="ignore")

    # 猜测分隔符
    sample = raw[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
    except Exception:
        dialect = csv.excel

    rows_text: list[str] = []
    reader = csv.reader(io.StringIO(raw), dialect)
    for row in reader:
        cells = [str(c).strip() for c in row]
        if any(cells):
            rows_text.append(" | ".join(cells))
    return "\n".join(rows_text)
