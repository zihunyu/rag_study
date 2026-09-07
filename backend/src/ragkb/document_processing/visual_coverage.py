"""Inventory visible objects and render unsupported Office/PDF graphics as real pages."""

from __future__ import annotations

import io
import os
import shutil
import subprocess
import tempfile
import time
import xml.etree.ElementTree as ET
import zipfile
from contextlib import closing
from pathlib import Path
from typing import Any

from ragkb.application.cancellation import check_cancelled
from ragkb.config import EnvSettings
from ragkb.document_processing.visual_sources import SourceImage
from ragkb.domain.documents import SourceLocator


def inventory(source: Path, kind: str) -> dict[str, Any]:
    objects: list[dict[str, Any]] = []
    rendered_pages: list[int] = []
    inspected = kind in {"image", "docx", "pptx", "xlsx", "pdf", "pdf_scanned"}
    if kind in {"docx", "pptx", "xlsx"}:
        with zipfile.ZipFile(source) as archive:
            for name in archive.namelist():
                if name.lower().endswith((".emf", ".wmf", ".svg")):
                    objects.append({"kind": "vector_image", "part": name})
                if not name.endswith((".xml", ".rels")):
                    continue
                if not name.startswith(("word/", "ppt/", "xl/")):
                    continue
                if archive.getinfo(name).file_size > 20_000_000:
                    objects.append({"kind": "uninspected_xml", "part": name})
                    continue
                root = ET.fromstring(archive.read(name))  # noqa: S314 -- validated OOXML archive
                for node in root.iter():
                    local = node.tag.rsplit("}", 1)[-1]
                    if local == "Relationship" and node.get("TargetMode") == "External":
                        if node.get("Type", "").endswith("/image"):
                            # Record only its part/ID; never fetch external URLs during conversion.
                            objects.append(
                                {
                                    "kind": "external_image",
                                    "part": name,
                                    "object_id": node.get("Id", ""),
                                }
                            )
                    elif local in {
                        "relIds",
                        "cxnSp",
                        "wsp",
                        "shape",
                        "spTree",
                        "oleObject",
                        "OLEObject",
                    }:
                        if local != "spTree":
                            objects.append(
                                {"kind": "smartart" if local == "relIds" else "shape", "part": name}
                            )
                    elif local == "sp" and name.startswith(("ppt/slides/", "xl/drawings/")):
                        # A picture's rectangular crop is not an editable diagram.
                        if any(
                            child.tag.rsplit("}", 1)[-1] in {"prstGeom", "custGeom"}
                            for child in node.iter()
                        ):
                            objects.append({"kind": "shape", "part": name})
                    elif local == "chart" and "charts/" not in name:
                        objects.append({"kind": "chart", "part": name})
    elif kind in {"pdf", "pdf_scanned"}:
        import pypdfium2 as pdfium

        with pdfium.PdfDocument(source) as document:
            for index in range(len(document)):
                with closing(document[index]) as page:
                    if any(obj.type in {2, 4} for obj in page.get_objects()):
                        objects.append({"kind": "vector_graphics", "page": index + 1})
                        rendered_pages.append(index + 1)
    elif kind in {"doc", "ppt", "xls"}:
        objects.append({"kind": "legacy_office", "part": source.name})
    for index, obj in enumerate(objects):
        obj.update(id=f"object-{index + 1}", state="unprocessed")
    return {
        "inspection": "inspected" if inspected else "format_fallback_required",
        "objects": objects,
        "render_pages": rendered_pages,
        "needs_render": any(o["kind"] != "external_image" for o in objects),
    }


def _office_pdf(source: Path, work: Path, settings: EnvSettings) -> Path:
    options = [
        settings.ocr_soffice_path,
        shutil.which("soffice") or "",
        "C:/Program Files/LibreOffice/program/soffice.com",
        "C:/Program Files/LibreOffice/program/soffice.exe",
    ]
    executable = next((path for path in options if path and Path(path).is_file()), None)
    if executable is None:
        raise ValueError("VISUAL_OFFICE_RENDERER_UNAVAILABLE")
    profile = work / "profile"
    user = profile / "user"
    user.mkdir(parents=True)
    # Untrusted documents must not execute macros or update linked resources.
    (user / "registrymodifications.xcu").write_text(
        '<?xml version="1.0"?><oor:items xmlns:oor="http://openoffice.org/2001/registry">'
        '<item oor:path="/org.openoffice.Office.Common/Security/Scripting">'
        '<prop oor:name="MacroSecurityLevel" oor:op="fuse"><value>3</value></prop></item>'
        '<item oor:path="/org.openoffice.Office.Writer/Content/Update">'
        '<prop oor:name="Link" oor:op="fuse"><value>2</value></prop></item>'
        '<item oor:path="/org.openoffice.Office.Calc/Content/Update">'
        '<prop oor:name="Link" oor:op="fuse"><value>0</value></prop></item></oor:items>',
        encoding="utf-8",
    )
    command = [
        executable,
        "-env:UserInstallation=" + profile.as_uri(),
        "--headless",
        "--nologo",
        "--nodefault",
        "--nofirststartwizard",
        "--norestore",
        "--convert-to",
        "pdf",
        "--outdir",
        str(work),
        str(source.resolve()),
    ]
    deadline = time.monotonic() + settings.ocr_render_timeout_seconds
    with subprocess.Popen(  # noqa: S603 -- verified executable, isolated profile, no shell
        command,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    ) as proc:  # noqa: S603
        try:
            while proc.poll() is None:
                check_cancelled()
                if time.monotonic() > deadline:
                    raise ValueError("VISUAL_RENDER_TIMEOUT")
                time.sleep(0.1)
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait(timeout=5)
        if proc.returncode != 0:
            raise ValueError("VISUAL_OFFICE_RENDER_FAILED")
    result = work / (source.stem + ".pdf")
    if not result.is_file():
        raise ValueError("VISUAL_OFFICE_RENDER_FAILED")
    return result


def render_fallback(
    source: Path, kind: str, report: dict[str, Any], settings: EnvSettings, temp_root: Path
) -> tuple[list[SourceImage], dict[str, Any]]:
    import pypdfium2 as pdfium

    images: list[SourceImage] = []
    temp_root.mkdir(parents=True, exist_ok=True)
    if any(obj["kind"] == "external_image" for obj in report["objects"]):
        # Rendering a source with external relationships could update them implicitly.
        # Keep it explicit until the user embeds the missing original in a new version.
        return images, {**report, "render_error": "VISUAL_EXTERNAL_IMAGE_REQUIRES_EMBEDDING"}
    with tempfile.TemporaryDirectory(prefix="visual-render-", dir=temp_root) as folder:
        path = (
            source
            if kind in {"pdf", "pdf_scanned"}
            else _office_pdf(source, Path(folder), settings)
        )
        with pdfium.PdfDocument(path) as document:
            desired = report["render_pages"] or list(range(1, len(document) + 1))
            selected = desired[: settings.ocr_render_max_pages]
            for number in selected:
                check_cancelled()
                with closing(document[number - 1]) as page:
                    if (
                        page.get_width() * page.get_height() * (settings.ocr_render_dpi / 72) ** 2
                        > settings.ocr_max_image_pixels
                    ):
                        raise ValueError("OCR_IMAGE_PIXELS_LIMIT")
                    bitmap = page.render(scale=settings.ocr_render_dpi / 72)
                    image = bitmap.to_pil()
                    output = io.BytesIO()
                    image.save(output, format="PNG")
                    image.close()
                    bitmap.close()
                images.append(
                    SourceImage(
                        output.getvalue(),
                        SourceLocator(page=number, part=f"rendered/page-{number}.png"),
                        section_path=f"第 {number} 页（页面渲染）",
                        caption=f"第 {number} 页完整画面",
                    )
                )
            complete = len(selected) == len(desired)
            for obj in report["objects"]:
                if obj.get("page") in selected or ("page" not in obj and complete):
                    obj["state"] = "rendered"
            report.update(
                inspection="inspected",
                rendered_pages=selected,
                render_page_count=len(desired),
                missing_pages=desired[len(selected) :],
                rendered_whole_document=kind not in {"pdf", "pdf_scanned"},
                renderer="pdfium:150dpi"
                if settings.ocr_render_dpi == 150
                else f"pdfium:{settings.ocr_render_dpi}dpi",
            )
    return images, report
