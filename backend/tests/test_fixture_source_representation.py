from __future__ import annotations
from pathlib import Path
from pypdf import PdfWriter
from ragkb.evaluation.fixture_source_representation import representation
def test_generated_pdf_and_unsupported_image_representation(tmp_path:Path):
 pdf=tmp_path/'a.pdf';w=PdfWriter();w.add_blank_page(100,100);w.write(pdf.open('wb'))
 image=tmp_path/'a.png';image.write_bytes(b'png')
 assert representation('pdf_scanned_or_image',image,{'page':1})['status']=='UNAVAILABLE_FAIL_CLOSED'
 assert representation('pdf_scanned_or_image',pdf,{'page':1})['status']=='UNAVAILABLE_FAIL_CLOSED'
