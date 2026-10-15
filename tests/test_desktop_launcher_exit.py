"""Real cmd.exe exit propagation with a tiny entry stub; no UI or extraction."""
from pathlib import Path
import json
import os
import shutil
import subprocess
import tempfile

def main():
    if os.name != 'nt':
        raise SystemExit('Windows cmd.exe required')
    repo = Path(__file__).resolve().parents[1]
    (repo/'build').mkdir(exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix='desktop launcher exit ', dir=repo/'build'))
    (root/'desktop').mkdir()
    shutil.copyfile(repo/'FieldViewer_Desktop.bat',root/'FieldViewer_Desktop.bat')
    entry = root/'desktop/FieldViewer.Desktop.ps1'
    results = []
    for child_exit in (0, 7):
        entry.write_text('param([string]$PackageRoot,[string]$SmokeScreenshot)\nexit '+str(child_exit)+'\n')
        for route in ('direct', 'call'):
            bat = root/'FieldViewer_Desktop.bat'
            command = f'""{bat}" --smoke "{root / "unused.png"}""' if route == 'direct' else f'call "{bat}" --smoke "{root / "unused.png"}"'
            result = subprocess.run(f'cmd.exe /d /s /c {command}', capture_output=True, timeout=30)
            expected = 0 if child_exit == 0 else 1
            row = dict(route=route, child_exit=child_exit, expected=expected, actual=result.returncode,
                       stdout=result.stdout.decode(errors='replace'),stderr=result.stderr.decode(errors='replace'))
            results.append(row)
            assert result.returncode == expected, row
    entry.write_text('throw "Entry must not start without a screenshot argument"\n')
    result = subprocess.run(f'cmd.exe /d /s /c ""{root / "FieldViewer_Desktop.bat"}" --smoke"', capture_output=True, timeout=30)
    assert result.returncode == 2 and not result.stderr, 'Missing argument was not rejected before entry'
    evidence = dict(status='passed',checks=5,root=str(root),results=results,scope='Stub exit behavior only; not GUI rendering')
    (root/'ACCEPTANCE.json').write_text(json.dumps(evidence,indent=2))
    print(json.dumps(evidence,indent=2))

if __name__ == '__main__':
    main()
