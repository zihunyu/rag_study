"""Bounded local Tesseract representation adapter; no network capability."""
from __future__ import annotations
import hashlib, subprocess
from pathlib import Path
def ocr(path:Path, executable:Path, tessdata:Path, timeout:float=30)->dict:
 if not executable.is_file():return {'status':'UNAVAILABLE_FAIL_CLOSED','representation_sha256':None,'content':None}
 try:
  done=subprocess.run([str(executable),str(path),'stdout','-l','chi_sim','--tessdata-dir',str(tessdata)],check=True,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,text=True,encoding='utf-8',timeout=timeout)
  text=done.stdout.strip()
  return {'status':'AVAILABLE' if text else 'UNAVAILABLE_FAIL_CLOSED','representation_sha256':hashlib.sha256(text.encode()).hexdigest() if text else None,'content':text or None}
 except (OSError,subprocess.SubprocessError):return {'status':'UNAVAILABLE_FAIL_CLOSED','representation_sha256':None,'content':None}
