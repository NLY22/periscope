"""Extract document text and navigable links without performing network I/O."""
from __future__ import annotations

from dataclasses import dataclass, field
from io import BytesIO
import asyncio
import json
import re
import sys
from urllib.parse import unquote, urljoin, urlsplit
import xml.etree.ElementTree as ET
from zipfile import BadZipFile, ZipFile

from bs4 import BeautifulSoup


@dataclass(frozen=True)
class Link:
    url: str
    label: str = ""


@dataclass(frozen=True)
class Form:
    action: str
    method: str
    field_names: list[str] = field(default_factory=list)
    select_options: dict[str, list[str]] = field(default_factory=dict)
    required_fields: list[str] = field(default_factory=list)
    has_hidden_fields: bool = False
    has_sensitive_controls: bool = False
    has_named_submit: bool = False


@dataclass
class Document:
    title: str
    text: str
    format: str
    links: list[Link] = field(default_factory=list)
    page_spans: list[dict] = field(default_factory=list)
    truncated: bool = False
    needs_ocr: bool = False
    pages_total: int = 0
    pages_read: int = 0
    sheet_rows: list[dict] = field(default_factory=list)
    document_blocks: list[dict] = field(default_factory=list)
    incomplete_reasons: list[str] = field(default_factory=list)
    forms: list[Form] = field(default_factory=list)

    def metadata(self) -> dict:
        result = {"format": self.format, "page_spans": self.page_spans,
                  "text_truncated": self.truncated, "needs_ocr": self.needs_ocr,
                  "pages_total": self.pages_total, "pages_read": self.pages_read}
        if self.format == "xlsx":
            result["sheet_rows"] = self.sheet_rows
        elif self.format == "docx":
            result["document_blocks"] = self.document_blocks
            result["incomplete_reasons"] = self.incomplete_reasons
        return result


def _validate_office_archive(body: bytes, format_name: str) -> None:
    try:
        with ZipFile(BytesIO(body)) as archive:
            entries = archive.infolist()
            if len(entries) > 4096 or sum(item.file_size for item in entries) > 40 * 1024 * 1024:
                raise ValueError(format_name + "_expanded_size_limit")
            if any(item.flag_bits & 1 for item in entries):
                raise ValueError("encrypted_" + format_name)
    except BadZipFile as exc:
        raise ValueError("invalid_" + format_name) from exc


def _extract_xlsx(body: bytes, title: str, max_chars: int) -> Document:
    """Extract bounded cell values without inferring headers or calculating formulas."""
    _validate_office_archive(body, "xlsx")

    from openpyxl import load_workbook
    from openpyxl.utils import get_column_letter

    workbook = load_workbook(BytesIO(body), read_only=True, data_only=False, keep_links=False)
    doc = Document(title, "", "xlsx")
    pieces, length, rows_seen = [], 0, 0
    max_rows, max_columns, max_sheets = 5000, 128, 20
    try:
        if len(workbook.worksheets) > max_sheets:
            doc.truncated = True
        for sheet in workbook.worksheets[:max_sheets]:
            if sheet.max_row and sheet.max_row > max_rows - rows_seen:
                doc.truncated = True
            if sheet.max_column and sheet.max_column > max_columns:
                doc.truncated = True
            row_bound = min(sheet.max_row or max_rows + 1, max_rows - rows_seen + 1)
            column_bound = min(sheet.max_column or max_columns + 1, max_columns + 1)
            for row_number, cells in enumerate(sheet.iter_rows(max_row=row_bound, max_col=column_bound), 1):
                if rows_seen >= max_rows:
                    doc.truncated = True
                    break
                rows_seen += 1
                if any(cell.value is not None for cell in cells[max_columns:]):
                    doc.truncated = True
                values = []
                for column, cell in enumerate(cells[:max_columns], 1):
                    value = cell.value
                    if value is None:
                        continue
                    value = value.isoformat() if hasattr(value, "isoformat") else str(value)
                    value = " ".join(value.split())
                    if value:
                        values.append("%s%d=%s" % (get_column_letter(column), row_number, value))
                if not values:
                    continue
                line = "[sheet: %s | row: %d] %s" % (sheet.title, row_number, " | ".join(values))
                separator = "\n" if pieces else ""
                remaining = max_chars - length - len(separator)
                if remaining <= 0:
                    doc.truncated = True
                    break
                kept = line[:remaining]
                start = length + len(separator)
                pieces.append(separator + kept)
                length = start + len(kept)
                doc.sheet_rows.append({"sheet": sheet.title, "row": row_number,
                                       "start": start, "end": length})
                if len(kept) < len(line):
                    doc.truncated = True
                    break
            if rows_seen >= max_rows or length >= max_chars:
                doc.truncated = True
                break
    finally:
        workbook.close()
    doc.text = "".join(pieces)
    return doc


def _extract_docx(body: bytes, title: str, max_chars: int) -> Document:
    """Extract document paragraphs and table rows in their original order."""
    _validate_office_archive(body, "docx")
    from docx import Document as WordDocument
    from docx.oxml.ns import qn
    from docx.table import Table, _Cell
    from docx.text.paragraph import Paragraph

    word = WordDocument(BytesIO(body))
    doc = Document(title, "", "docx")

    def mark_incomplete(reason: str) -> None:
        doc.truncated = True
        if reason not in doc.incomplete_reasons:
            doc.incomplete_reasons.append(reason)

    with ZipFile(BytesIO(body)) as archive:
        for name in archive.namelist():
            if not re.fullmatch(r"word/(?:header\d+|footer\d+|footnotes|endnotes|comments)\.xml", name):
                continue
            root = ET.fromstring(archive.read(name))
            if any(node.tag.rsplit("}", 1)[-1] == "t" and (node.text or "").strip()
                   for node in root.iter()):
                mark_incomplete("additional_parts_not_extracted")
                break
    if any(any((text.text or "").strip() for text in box.iter(qn("w:t")))
           for box in word._element.body.iter(qn("w:txbxContent"))):
        mark_incomplete("text_box_not_extracted")
    if any(any((text.text or "").strip() for text in section.iter(qn("w:t")))
           for tag in ("w:sdt", "w:ins", "w:customXml")
           for section in word._element.body.iter(qn(tag))):
        mark_incomplete("structured_content_not_extracted")
    for child in word._element.body.iterchildren():
        if child.tag not in {qn("w:p"), qn("w:tbl"), qn("w:sectPr")} and any(
                (text.text or "").strip() for text in child.iter(qn("w:t"))):
            mark_incomplete("structured_content_not_extracted")
            break
    pieces, length, seen = [], 0, 0
    paragraph_number, table_number = 0, 0
    max_blocks, max_cells = 5000, 128

    def append(text: str, location: dict) -> bool:
        nonlocal length
        if not text.strip():
            return True
        separator = "\n" if pieces else ""
        remaining = max_chars - length - len(separator)
        if remaining <= 0:
            doc.truncated = True
            if "text_limit" not in doc.incomplete_reasons:
                doc.incomplete_reasons.append("text_limit")
            return False
        kept = text[:remaining]
        start = length + len(separator)
        pieces.append(separator + kept)
        length = start + len(kept)
        doc.document_blocks.append({**location, "start": start, "end": length})
        if len(kept) < len(text):
            doc.truncated = True
            if "text_limit" not in doc.incomplete_reasons:
                doc.incomplete_reasons.append("text_limit")
            return False
        return True

    for block in word.iter_inner_content():
        if isinstance(block, Paragraph):
            seen += 1
            paragraph_number += 1
            if seen > max_blocks:
                doc.truncated = True
                doc.incomplete_reasons.append("block_limit")
                break
            if not append(block.text.strip(), {"type": "paragraph", "paragraph": paragraph_number}):
                break
        elif isinstance(block, Table):
            table_number += 1
            stopped = False
            for row_number, row in enumerate(block.rows, 1):
                seen += 1
                if seen > max_blocks:
                    doc.truncated = True
                    doc.incomplete_reasons.append("block_limit")
                    break
                cells = row._tr.tc_lst
                if len(cells) > max_cells:
                    doc.truncated = True
                    if "cell_limit" not in doc.incomplete_reasons:
                        doc.incomplete_reasons.append("cell_limit")
                values = []
                for index, cell_xml in enumerate(cells[:max_cells], 1):
                    cell = _Cell(cell_xml, block)
                    if cell.tables:
                        doc.truncated = True
                        if "nested_table_not_extracted" not in doc.incomplete_reasons:
                            doc.incomplete_reasons.append("nested_table_not_extracted")
                    value = " ".join(cell.text.split())
                    if value:
                        values.append("cell %d=%s" % (index, value))
                if values and not append("[table: %d | row: %d] %s" % (
                        table_number, row_number, " | ".join(values)),
                        {"type": "table_row", "table": table_number, "row": row_number}):
                    stopped = True
                    break
            if seen > max_blocks or stopped:
                break
    doc.text = "".join(pieces)
    return doc


async def extract_document_async(body: bytes, content_type: str, url: str) -> Document:
    """Keep HTML/PDF CPU work cancellable, including executor shutdown."""
    from periscope.runtime import paths
    worker = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "periscope.collection.crawling.worker",
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        cwd=str(paths.ROOT), env=paths.subprocess_env())
    try:
        header = json.dumps({"content_type": content_type, "url": url}).encode("utf-8")
        output, _ = await worker.communicate(header + b"\n" + body)
        payload = json.loads(output)
        if worker.returncode or "error" in payload:
            raise ValueError(payload.get("error") or "document_worker_failed")
        payload["links"] = [Link(**link) for link in payload["links"]]
        payload["forms"] = [Form(**form) for form in payload.get("forms", [])]
        return Document(**payload)
    finally:
        if worker.returncode is None:
            worker.kill()
            await worker.wait()


def extract_document(body: bytes, content_type: str, url: str, *,
                     max_chars: int = 60000, pdf_pages: int = 40) -> Document:
    if max_chars < 1 or pdf_pages < 1:
        raise ValueError("document limits must be positive")
    mime = content_type.split(";", 1)[0].lower().strip()
    fallback_title = unquote(urlsplit(url).path.rsplit("/", 1)[-1]) or url
    xlsx_mime = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    if mime == xlsx_mime or (urlsplit(url).path.lower().endswith(".xlsx") and body.startswith(b"PK")):
        return _extract_xlsx(body, fallback_title, max_chars)
    docx_mime = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    if mime == docx_mime or (urlsplit(url).path.lower().endswith(".docx") and body.startswith(b"PK")):
        return _extract_docx(body, fallback_title, max_chars)
    if body.startswith(b"%PDF-") or mime == "application/pdf":
        from pypdf import PdfReader
        reader = PdfReader(BytesIO(body))
        if reader.is_encrypted:
            raise ValueError("encrypted_pdf")
        doc = Document(str((reader.metadata or {}).get("/Title") or fallback_title), "", "pdf", pages_total=len(reader.pages))
        pieces, length = [], 0
        for number, page in enumerate(reader.pages[:pdf_pages], 1):
            stream = page.get_contents()
            if stream is not None and len(stream.get_data()) > 2 * 1024 * 1024:
                doc.truncated = True
                break
            text = (page.extract_text() or "").strip()
            doc.pages_read += 1
            if not text:
                doc.needs_ocr = True
                continue
            separator = "\n\n" if pieces else ""
            remaining = max_chars - length - len(separator)
            if remaining <= 0:
                doc.truncated = True
                break
            kept = text[:remaining]
            start = length + len(separator)
            pieces.append(separator + kept)
            length = start + len(kept)
            doc.page_spans.append({"page": number, "start": start, "end": length,
                                   "url": url + "#page=" + str(number)})
            if len(kept) < len(text):
                doc.truncated = True
                break
        doc.text = "".join(pieces)
        doc.truncated |= doc.pages_read < doc.pages_total
        return doc
    if "xml" in mime or body.lstrip().startswith(b"<?xml"):
        root = ET.fromstring(body)
        if root.tag.rsplit("}", 1)[-1] not in ("urlset", "sitemapindex"):
            raise ValueError("unsupported_xml_document")
        links = [Link(urljoin(url, (node.text or "").strip())) for node in root.iter()
                 if node.tag.rsplit("}", 1)[-1] == "loc" and (node.text or "").strip()]
        return Document(fallback_title, "", "sitemap", links=links)
    if mime == "text/plain":
        text = body.decode("utf-8", errors="replace").strip()
        return Document(fallback_title, text[:max_chars], "text", truncated=len(text) > max_chars)
    if mime not in ("", "text/html", "application/xhtml+xml"):
        raise ValueError("unsupported_document_type")
    soup = BeautifulSoup(body, "html.parser", from_encoding="utf-8")
    title_node = soup.find("h1") or soup.title
    title = title_node.get_text(" ", strip=True) if title_node else fallback_title
    links = [Link(urljoin(url, anchor["href"]), anchor.get_text(" ", strip=True))
             for anchor in soup.find_all("a", href=True)
             if anchor["href"].strip() and not anchor["href"].startswith(("#", "javascript:", "mailto:", "tel:"))]
    forms = []
    for form in soup.find_all("form"):
        names = []
        select_options: dict[str, list[str]] = {}
        required_fields = []
        has_hidden_fields = False
        has_sensitive_controls = False
        has_named_submit = False
        for control in form.find_all(("input", "select", "textarea", "button")):
            name = str(control.get("name") or "").strip()
            control_type = str(control.get("type") or ("submit" if control.name == "button" else "text")).lower()
            if control.has_attr("disabled") or control.find_parent("fieldset", disabled=True):
                continue
            if control_type in {"password", "file"}:
                has_sensitive_controls = True
                continue
            if not name:
                continue
            if control_type in {"submit", "image"}:
                has_named_submit = True
                continue
            if control_type == "hidden":
                has_hidden_fields = True
                continue
            if control_type in {"submit", "reset", "button", "image"}:
                continue
            if name not in names:
                names.append(name)
            if control.has_attr("required") and name not in required_fields:
                required_fields.append(name)
            if control.name == "select":
                allowed = select_options.setdefault(name, [])
                for option in control.find_all("option"):
                    if option.has_attr("disabled") or option.find_parent("optgroup", disabled=True):
                        continue
                    value = str(option.get("value")) if option.has_attr("value") else option.get_text(" ", strip=True)
                    if value not in allowed:
                        allowed.append(value)
        forms.append(Form(action=urljoin(url, str(form.get("action") or "")),
                          method=str(form.get("method") or "get").strip().lower(),
                          field_names=names, select_options=select_options,
                          required_fields=required_fields, has_hidden_fields=has_hidden_fields,
                          has_sensitive_controls=has_sensitive_controls,
                          has_named_submit=has_named_submit))
    for node in soup.select("script, style, nav, footer, header, noscript"):
        node.decompose()
    import trafilatura
    text = trafilatura.extract(str(soup), include_comments=False, include_tables=True,
                              favor_precision=True, deduplicate=True) or ""
    if not text:
        main = soup.find("main") or soup.find("article")
        if main:
            text = main.get_text("\n", strip=True)
    return Document(title[:300], text[:max_chars], "html", links=links,
                    truncated=len(text) > max_chars, forms=forms)
