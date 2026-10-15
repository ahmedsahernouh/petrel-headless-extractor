"""Harmless child timing checks; never opens native project inputs."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import geoviewer_lanes as lanes

class StageTimingTests(unittest.TestCase):
    def test_lane_success_failure_and_correlation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for code in (0, 3):
                self.assertEqual(lanes.run_stage([sys.executable, '-c', 'raise SystemExit('+str(code)+')'],
                    lane='native', category='logs_surfaces', limit=None,
                    state_path=root/'state.json', log_path=root/'child.log'), code)
            rows = json.loads((root/'state.json').read_text())['attempts']
            self.assertEqual([r['status'] for r in rows], ['completed', 'failed'])
            self.assertNotEqual(rows[0]['stage_id'], rows[1]['stage_id'])
            for row in rows:
                self.assertTrue(row['ended_at_utc'])
                self.assertGreaterEqual(row['elapsed_seconds'], 0)
                self.assertEqual(row['runner_pid'], os.getpid())
                self.assertEqual(row['category'], 'logs_surfaces')

    @unittest.skipUnless(os.name == 'nt', 'PowerShell wrapper on Windows')
    def test_powershell_helper_preserves_output_and_exit(self):
        source = (Path(__file__).parent/'invoke_portable_petrel_extract.ps1').read_text()
        helper = source[source.index('$pipelineRunId ='):source.index('$startedUtc =')]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            script = root/'test.ps1'
            script.write_text("$ErrorActionPreference='Stop'\n$outputRootResolved='"+str(root).replace("'", "''")+"'\n"+
                "function Get-PetrelMcpLastExitCode { return $global:LASTEXITCODE }\n"+helper+
                "\n$result = Invoke-TimedPipelineStage 'success' { & '"+sys.executable+"' -c \"print('hello')\" }\n"+
                "if ($result -ne 'hello' -or $LASTEXITCODE -ne 0) { throw 'Output or success exit changed' }\n"+
                "Invoke-TimedPipelineStage 'failure' { & '"+sys.executable+"' -c 'raise SystemExit(3)' }\n"+
                "if ($LASTEXITCODE -ne 3) { throw 'Failure exit changed' }\n", encoding='utf-8')
            result = subprocess.run(['powershell.exe','-NoProfile','-File',str(script)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout+result.stderr)
            rows = json.loads(next(root.glob('pipeline_timings_*.json')).read_text(encoding='utf-8-sig'))
            self.assertIsInstance(rows, list)
            self.assertEqual([r['status'] for r in rows], ['completed', 'failed'])
            self.assertEqual([r['exit_code'] for r in rows], [0, 3])
            self.assertTrue(all(r['elapsed_seconds'] >= 0 and r['ended_at_utc'] for r in rows))

if __name__ == '__main__':
    unittest.main()
