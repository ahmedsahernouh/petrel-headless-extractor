"""Real Windows bootstrap collection with absent Get-FileHash and failing children."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile


@unittest.skipUnless(os.name == 'nt', 'Windows bootstrap boundary')
class PowerShellCollectorTests(unittest.TestCase):
    def invoke(self, mode):
        with tempfile.TemporaryDirectory(prefix='FV support test ') as folder:
            root=Path(folder); toolkit=root/'toolkit'; session=root/'session'
            (toolkit/'scripts').mkdir(parents=True); (toolkit/'00_manifest').mkdir()
            session.mkdir(); (session/'session.json').write_text('{}')
            (session/'BOOTSTRAP_LOG.txt').write_text('retained startup evidence')
            builder=toolkit/'scripts/geoviewer_support.py'
            builder.write_text("import json,sys\nfrom pathlib import Path\nsession=Path(sys.argv[2])\n"+
                ("raise RuntimeError('collector diagnostic sentinel')\n" if mode=='child_error' else
                 "(session/'called.json').write_text(json.dumps({'ok':True}))\nprint('support fixture completed')\n"))
            expected=hashlib.sha256(builder.read_bytes()).hexdigest()
            rows=[] if mode=='missing_manifest_entry' else [dict(path='scripts/geoviewer_support.py',sha256='0'*64 if mode=='hash_mismatch' else expected)]
            (toolkit/'00_manifest/toolkit_files.json').write_text(json.dumps({'files':rows}))
            if mode!='missing_python':
                runtime=toolkit/'runtime'; runtime.mkdir()
                # Read-only-use links to embedded runtime root dependencies; no site packages needed.
                for source in Path(sys.executable).parent.iterdir():
                    if source.is_file():
                        if source.suffix=='._pth':shutil.copy2(source,runtime/source.name)
                        else:
                            try:os.link(source,runtime/source.name)
                            except OSError as error:
                                if getattr(error,'winerror',None)!=17:raise
                                shutil.copy2(source,runtime/source.name)
            script=root/'invoke.ps1'
            support=Path(__file__).with_name('geoviewer_support.ps1')
            quote=lambda p:"'"+str(p).replace("'","''")+"'"
            script.write_text("Set-StrictMode -Version Latest\n$ErrorActionPreference='Stop'\n"+
                "function Get-FileHash { throw 'Get-FileHash must not be called' }\n"+
                '. '+quote(support)+'\n'+
                '$savedExit=10\nStop-GeoViewerSupport -ToolkitRoot '+quote(toolkit)+
                ' -Session '+quote(session)+' -ExitCode $savedExit -Failure $null\nexit $savedExit\n')
            run=subprocess.run(['powershell.exe','-NoProfile','-ExecutionPolicy','Bypass','-File',str(script)],
                               capture_output=True,text=True,timeout=30)
            self.assertEqual(run.returncode,10,run.stdout+run.stderr)
            receipt=json.loads((session/'support_collector.json').read_text(encoding='utf-8-sig'))
            self.assertEqual(json.loads((session/'launcher_result.json').read_text(encoding='utf-8-sig'))['exit_code'],10)
            self.assertEqual((session/'BOOTSTRAP_LOG.txt').read_text(),'retained startup evidence')
            if mode=='valid':
                self.assertEqual(receipt['status'],'completed')
                self.assertEqual(receipt['exit_code'],0)
                self.assertTrue((session/'called.json').exists())
                self.assertFalse((session/'STARTUP_SUPPORT.zip').exists())
            else:
                expected_status={'child_error':'collector_failed','missing_python':'python_missing'}.get(mode,'verification_failed')
                self.assertEqual(receipt['status'],expected_status,receipt)
                self.assertFalse((session/'called.json').exists())
                with zipfile.ZipFile(session/'STARTUP_SUPPORT.zip') as archive:
                    self.assertIsNone(archive.testzip())
                    saved=json.loads(archive.read('support_collector.json').decode('utf-8-sig'))
                    self.assertEqual(saved['status'],receipt['status'])
                    if mode=='child_error':
                        error=archive.read('support_collector_stderr.txt').decode('utf-8-sig')
                        self.assertIn('Traceback',error)
                        self.assertIn('collector diagnostic sentinel',error)
                    self.assertIn('UNREDACTED',archive.read('README.txt').decode('utf-8-sig'))

    def test_valid_without_get_file_hash(self): self.invoke('valid')
    def test_hash_mismatch_does_not_launch(self): self.invoke('hash_mismatch')
    def test_missing_entry_does_not_launch(self): self.invoke('missing_manifest_entry')
    def test_missing_python_explains_fallback(self): self.invoke('missing_python')
    def test_child_failure_retains_traceback(self): self.invoke('child_error')


if __name__=='__main__': unittest.main()
