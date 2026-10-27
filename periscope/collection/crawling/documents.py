"""Extract document text and navigable links without performing network I/O."""
from __future__ import annotations

from dataclasses import dataclass, field
from io import BytesIO
import asyncio
import json
import sys
from urllib.parse import unquote, urljoin, urlsplit
import xml.etree.ElementTree as ET
from zipfile import BadZipFile, ZipFile

from bs4 import BeautifulSoup


@dataclass(frozen=True)
class Link:
    url: str
    label: str = ""


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

    def metadata(self) -> dict:
        result = {"format": self.format, "page_spans": self.page_spans,
                  "text_truncated": self.truncated, "needs_ocr": self.needs_ocr,
                  "pages_total": self.pages_total, "pages_read": self.pages_read}
        if self.format == "xlsx":
            result["sheet_rows"] = self.sheet_rows
        return result


def _extract_xlsx(body: bytes, title: str, max_chars: int) -> Document:
    """Extract bounded cell values without inferring headers or calculating formulas."""
    try:
        with ZipFile(BytesIO(body)) as archive:
            entries = archive.infolist()
            if len(entries) > 4096 or sum(item.file_size for item in entries) > 40 * 1024 * 1024:
                raise ValueError("xlsx_expanded_size_limit")
            if any(item.flag_bits & 1 for item in entries):
                raise ValueError("encrypted_xlsx")
    except BadZipFile as exc:
        raise ValueError("invalid_xlsx") from exc

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
    for node in soup.select("script, style, nav, footer, header, noscript"):
        node.decompose()
    import trafilatura
    text = trafilatura.extract(str(soup), include_comments=False, include_tables=True,
                              favor_precision=True, deduplicate=True) or ""
    if not text:
        main = soup.find("main") or soup.find("article")
        if main:
            text = main.get_text("\n", strip=True)
    return Document(title[:300], text[:max_chars], "html", links=links, truncated=len(text) > max_chars)
