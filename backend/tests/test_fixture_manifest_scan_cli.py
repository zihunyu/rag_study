from __future__ import annotations
import subprocess, sys
from pathlib import Path
import yaml
def test_cli_writes_content_free_manifest_report(tmp_path: Path):
 data=tmp_path/'data';data.mkdir();(data/'a.bin').write_bytes(b'x')
 (tmp_path/'meta.yaml').write_text(yaml.safe_dump({'samples':[{'file':'a.bin','expected_locators':[]}]}),encoding='utf-8')
 manifest=tmp_path/'manifest.yaml';manifest.write_text(yaml.safe_dump({'collection_plan':[{'format':'x','metadata_path':'meta.yaml','sample_directory':'data'}]}),encoding='utf-8')
 output=tmp_path/'report.json';script=Path(__file__).resolve().parents[2]/'scripts/scan_fixture_render_coverage.py'
 subprocess.run([sys.executable,str(script),'--dry-run','--manifest',str(manifest),'--output',str(output)],check=True)
 assert output.is_file();assert 'source_sha256' in output.read_text(encoding='utf-8')
