"""PDF 转换核心逻辑 — 从 PDF-main/PdfTool-master 迁移而来。

支持：PDF 预览 / PDF→DOCX（可编辑版）/ PDF→DOCX（图片高还原版）/
PDF→Excel / PDF→TXT / PDF→图片包（PNG/JPEG zip）。
纯本地，零 API 依赖。
"""

from __future__ import annotations

import unicodedata
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

import pymupdf  # PyMuPDF 新版（替换了旧的 fitz）
from docx import Document
from docx.shared import Inches
from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

try:
    from pdf2docx import Converter as Pdf2DocxConverter
    _HAS_PDF2DOCX = True
except Exception:
    _HAS_PDF2DOCX = False


class ConversionError(Exception):
    """转换失败时抛出。"""


# ---------- 元信息 ----------

def get_pdf_page_count(pdf_path: Path) -> int:
    try:
        with pymupdf.open(str(pdf_path)) as document:
            return document.page_count
    except Exception as exc:
        raise ConversionError(f"无法读取 PDF 页数：{exc}") from exc


# ---------- 预览 ----------

def render_pdf_preview_pages(pdf_path: Path, preview_dir: Path, dpi: int = 120) -> list[str]:
    if not pdf_path.exists():
        raise ConversionError("上传的 PDF 文件不存在。")

    preview_dir.mkdir(parents=True, exist_ok=True)
    scale = dpi / 72

    try:
        with pymupdf.open(str(pdf_path)) as document:
            if document.page_count == 0:
                raise ConversionError("PDF 没有可预览的页面。")

            image_names = []
            for page_index, page in enumerate(document, start=1):
                pixmap = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=False)
                image_name = f"page_{page_index:03d}.png"
                pixmap.save(preview_dir / image_name)
                image_names.append(image_name)
    except ConversionError:
        raise
    except Exception as exc:
        raise ConversionError(f"PDF 预览生成失败：{exc}") from exc

    return image_names


# ---------- PDF → DOCX（可编辑，走 pdf2docx） ----------

def convert_pdf_to_docx(pdf_path: Path, docx_path: Path) -> None:
    if not _HAS_PDF2DOCX:
        raise ConversionError("pdf2docx 没装，跑一下：pip install pdf2docx")
    if not pdf_path.exists():
        raise ConversionError("上传的 PDF 文件不存在。")

    docx_path.parent.mkdir(parents=True, exist_ok=True)
    converter = Pdf2DocxConverter(str(pdf_path))
    try:
        converter.convert(str(docx_path))
    finally:
        converter.close()

    if not docx_path.exists() or docx_path.stat().st_size == 0:
        raise ConversionError("转换未生成有效的 DOCX 文件。")


# ---------- PDF → DOCX（图片高还原版） ----------

def convert_pdf_to_image_docx(pdf_path: Path, docx_path: Path, dpi: int = 220) -> None:
    if not pdf_path.exists():
        raise ConversionError("上传的 PDF 文件不存在。")

    docx_path.parent.mkdir(parents=True, exist_ok=True)
    image_dir = docx_path.parent / f"{docx_path.stem}_pages"
    image_dir.mkdir(parents=True, exist_ok=True)

    try:
        pdf = pymupdf.open(str(pdf_path))
    except Exception as exc:
        raise ConversionError(f"无法打开 PDF：{exc}") from exc

    if pdf.page_count == 0:
        pdf.close()
        raise ConversionError("PDF 没有可转换页面。")

    document = Document()
    scale = dpi / 72

    try:
        for page_index, page in enumerate(pdf):
            pixmap = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=False)
            image_path = image_dir / f"page_{page_index + 1}.png"
            pixmap.save(image_path)

            section = document.sections[-1]
            section.page_width = Inches(page.rect.width / 72)
            section.page_height = Inches(page.rect.height / 72)
            section.top_margin = Inches(0)
            section.bottom_margin = Inches(0)
            section.left_margin = Inches(0)
            section.right_margin = Inches(0)

            paragraph = document.add_paragraph()
            paragraph.paragraph_format.space_before = 0
            paragraph.paragraph_format.space_after = 0
            paragraph.add_run().add_picture(str(image_path), width=section.page_width)

            if page_index < pdf.page_count - 1:
                document.add_page_break()

        document.save(docx_path)
    finally:
        pdf.close()
        for image_path in image_dir.glob("*.png"):
            image_path.unlink(missing_ok=True)
        try:
            image_dir.rmdir()
        except Exception:
            pass

    if not docx_path.exists() or docx_path.stat().st_size == 0:
        raise ConversionError("高还原转换未生成有效的 DOCX 文件。")


# ---------- PDF → TXT ----------

def export_pdf_text(pdf_path: Path, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)

    def display_width(value: str) -> int:
        width = 0
        for char in value:
            width += 2 if unicodedata.east_asian_width(char) in {"F", "W"} else 1
        return width

    def layout_text_page(page, columns: int = 118) -> str:
        words = page.get_text("words", sort=True)
        if not words:
            return ""

        line_buckets: list[dict] = []
        for word in sorted(words, key=lambda item: ((item[1] + item[3]) / 2, item[0])):
            y_center = (word[1] + word[3]) / 2
            target = None
            for bucket in line_buckets:
                if abs(bucket["y"] - y_center) <= 3.5:
                    target = bucket
                    break
            if target is None:
                target = {"y": y_center, "words": []}
                line_buckets.append(target)
            target["words"].append(word)

        lines = []
        scale = columns / max(page.rect.width, 1)
        for bucket in sorted(line_buckets, key=lambda item: item["y"]):
            line = ""
            for word in sorted(bucket["words"], key=lambda item: item[0]):
                value = str(word[4]).strip()
                if not value:
                    continue
                column = max(0, int(round(word[0] * scale)))
                current_width = display_width(line)
                if column > current_width:
                    line += " " * (column - current_width)
                elif line:
                    line += " "
                line += value
            lines.append(line.rstrip())

        return "\n".join(line for line in lines if line.strip())

    try:
        with pymupdf.open(str(pdf_path)) as document:
            pages = []
            for page_index, page in enumerate(document, start=1):
                page_text = layout_text_page(page)
                if page_text:
                    pages.append(page_text)
    except Exception as exc:
        raise ConversionError(f"文本提取失败：{exc}") from exc

    output_path.write_text("\n\n\f\n\n".join(pages), encoding="utf-8")
    if output_path.stat().st_size == 0:
        raise ConversionError("TXT 文件为空，可能是扫描版 PDF。")


# ---------- PDF → Excel ----------

def export_pdf_excel(pdf_path: Path, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "PDF Tables"
    sheet.sheet_view.showGridLines = False

    header_fill = PatternFill("solid", fgColor="DBEAFE")
    thin_gray = Side(style="thin", color="CBD5E1")
    border = Border(left=thin_gray, right=thin_gray, top=thin_gray, bottom=thin_gray)
    center = Alignment(horizontal="center", vertical="center", wrap_text=True)
    left = Alignment(horizontal="left", vertical="top", wrap_text=True)
    table_count = 0

    def normalize_cell(value) -> str:
        return "" if value is None else str(value).strip()

    def has_data_rows(rows: list[list[str]]) -> bool:
        return len(rows) >= 2 and any(any(cell for cell in row) for row in rows[1:])

    def split_words_into_cells(words) -> list[str]:
        if not words:
            return []
        words = sorted(words, key=lambda item: item[0])
        cells = [[words[0][4]]]
        previous = words[0]
        for word in words[1:]:
            gap = word[0] - previous[2]
            if gap > 14:
                cells.append([word[4]])
            else:
                cells[-1].append(word[4])
            previous = word
        return [" ".join(cell).strip() for cell in cells]

    def extract_tables_by_words(page) -> list[list[list[str]]]:
        lines = {}
        for word in page.get_text("words", sort=True):
            y_center = round((word[1] + word[3]) / 2 / 4) * 4
            lines.setdefault(y_center, []).append(word)
        rows = []
        for _, words in sorted(lines.items()):
            cells = split_words_into_cells(words)
            if len(cells) >= 3:
                rows.append(cells)

        tables = []
        current = []
        expected_cols = 0
        for row in rows:
            if not current:
                current = [row]
                expected_cols = len(row)
                continue
            if abs(len(row) - expected_cols) <= 1:
                current.append(row)
                expected_cols = max(expected_cols, len(row))
            else:
                if has_data_rows(current):
                    tables.append(current)
                current = [row]
                expected_cols = len(row)
        if has_data_rows(current):
            tables.append(current)
        return tables

    def write_table(rows: list[list[str]]) -> None:
        nonlocal table_count
        rows = [[normalize_cell(cell) for cell in row] for row in rows]
        rows = [row for row in rows if any(cell for cell in row)]
        if not has_data_rows(rows):
            return
        table_count += 1
        start_row = sheet.max_row + 1 if table_count > 1 else 1
        max_cols = max(len(row) for row in rows)
        for row_offset, row in enumerate(rows):
            target_row = start_row + row_offset
            padded = row + [""] * (max_cols - len(row))
            for col_index, cell_value in enumerate(padded, start=1):
                cell = sheet.cell(target_row, col_index, cell_value)
                cell.border = border
                cell.alignment = center if row_offset == 0 else left
                if row_offset == 0:
                    cell.fill = header_fill
                    cell.font = Font(bold=True, color="0F172A")

    try:
        with pymupdf.open(str(pdf_path)) as document:
            for page_index, page in enumerate(document, start=1):
                page_table_count = table_count
                tables = page.find_tables()
                for table in tables.tables:
                    write_table(table.extract())
                if table_count == page_table_count:
                    for word_table in extract_tables_by_words(page):
                        write_table(word_table)
    except Exception as exc:
        raise ConversionError(f"Excel 导出失败：{exc}") from exc

    if table_count == 0:
        sheet.title = "PDF Text"
        sheet.append(["page", "line"])
        try:
            with pymupdf.open(str(pdf_path)) as document:
                for page_index, page in enumerate(document, start=1):
                    lines = [line.strip() for line in page.get_text("text").splitlines()]
                    for line in lines:
                        if line:
                            sheet.append([page_index, line])
        except Exception as exc:
            raise ConversionError(f"Excel 文本回退导出失败：{exc}") from exc
        if sheet.max_row == 1:
            raise ConversionError("没有识别到表格或可写入 Excel 的文本，可能是扫描版 PDF。")

    for column_cells in sheet.columns:
        column_letter = get_column_letter(column_cells[0].column)
        max_length = max(len(str(cell.value or "")) for cell in column_cells)
        sheet.column_dimensions[column_letter].width = min(max(max_length + 3, 12), 34)

    for row in sheet.iter_rows():
        for cell in row:
            cell.alignment = cell.alignment.copy(wrap_text=True)

    workbook.save(output_path)


# ---------- PDF → 图片包 ----------

def export_pdf_images(pdf_path: Path, output_path: Path, image_format: str, dpi: int = 220) -> None:
    image_format = image_format.lower()
    if image_format not in {"png", "jpeg"}:
        raise ConversionError("不支持的图片格式。")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    scale = dpi / 72

    try:
        with pymupdf.open(str(pdf_path)) as document, ZipFile(output_path, "w", ZIP_DEFLATED) as archive:
            if document.page_count == 0:
                raise ConversionError("PDF 没有可导出的页面。")
            for page_index, page in enumerate(document, start=1):
                pixmap = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=False)
                suffix = "jpg" if image_format == "jpeg" else "png"
                image_bytes = pixmap.tobytes("jpeg" if image_format == "jpeg" else "png")
                archive.writestr(f"page_{page_index:03d}.{suffix}", image_bytes)
    except ConversionError:
        raise
    except Exception as exc:
        raise ConversionError(f"图片导出失败：{exc}") from exc

    if not output_path.exists() or output_path.stat().st_size == 0:
        raise ConversionError("图片压缩包生成失败。")


# ---------- 格式注册表 ----------

EXPORT_FORMATS: dict[str, dict] = {
    "word-fidelity": {"extension": ".docx", "label": "Word 图片版（高还原）"},
    "word-editable": {"extension": ".docx", "label": "Word 可编辑版"},
    "excel": {"extension": ".xlsx", "label": "Excel"},
    "png": {"extension": ".zip", "label": "PNG 图像包"},
    "jpeg": {"extension": ".zip", "label": "JPEG 图像包"},
    "txt": {"extension": ".txt", "label": "TXT 文本"},
}


def run_conversion(pdf_path: Path, out_dir: Path, fmt: str) -> tuple[Path, str]:
    """统一入口：给定 PDF + 格式，返回 (输出文件路径, 文件名)。"""
    if fmt not in EXPORT_FORMATS:
        raise ConversionError(f"不支持的格式：{fmt}")
    fmt_info = EXPORT_FORMATS[fmt]
    stem = pdf_path.stem.strip() or "converted"
    out_path = out_dir / f"{stem}{fmt_info['extension']}"
    out_dir.mkdir(parents=True, exist_ok=True)

    if fmt == "word-fidelity":
        convert_pdf_to_image_docx(pdf_path, out_path)
    elif fmt == "word-editable":
        convert_pdf_to_docx(pdf_path, out_path)
    elif fmt == "excel":
        export_pdf_excel(pdf_path, out_path)
    elif fmt == "png":
        export_pdf_images(pdf_path, out_path, "png")
    elif fmt == "jpeg":
        export_pdf_images(pdf_path, out_path, "jpeg")
    elif fmt == "txt":
        export_pdf_text(pdf_path, out_path)

    return out_path, f"{out_path.name}"
