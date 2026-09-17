# Copyright 2026 Ahmed Saher Nouh
# SPDX-License-Identifier: Apache-2.0
"""Relocated offline BAT acceptance for native polygon/grid exports and report-only mode.

Website: https://saherlabs.dev/
Project: https://github.com/ahmedsahernouh/petrel-headless-extractor
Uses synthetic data only; all outputs go into a new evidence directory.
"""
from pathlib import Path
import argparse,csv,hashlib,json,os,subprocess,zipfile


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--zip',type=Path,required=True)
    parser.add_argument('--evidence-dir',type=Path,required=True)
    args=parser.parse_args();out=args.evidence_dir.resolve();out.mkdir(parents=True,exist_ok=False)
    relocated=out/'Relocated with spaces';relocated.mkdir()
    with zipfile.ZipFile(args.zip) as archive:
        assert {Path(i.filename).parts[0] for i in archive.infolist()}=={'GeoViewer','GeoViewer_data_extractor.bat'}
        assert [i.filename for i in archive.infolist() if i.filename.endswith('.bat')]==['GeoViewer_data_extractor.bat']
        for entry in archive.infolist():
            assert (relocated/entry.filename).resolve().is_relative_to(relocated)
        archive.extractall(relocated)
    package=relocated/'GeoViewer';bat=relocated/'GeoViewer_data_extractor.bat'
    win=Path(os.environ['SystemRoot']);env=os.environ.copy()
    env.update(PATH=str(win/'System32')+';'+str(win/'System32/WindowsPowerShell/v1.0'),
               PYTHONHOME=str(out/'no-system-python'),PYTHONPATH=str(out/'no-system-modules'),
               PYTHON=str(out/'python_missing.exe'),PETREL_MCP_PYTHON=str(out/'python_missing.exe'),
               HTTP_PROXY='http://127.0.0.1:9',HTTPS_PROXY='http://127.0.0.1:9',PIP_NO_INDEX='1')
    checks=[]
    def execute(name,command):
        proc=subprocess.run(command,cwd=out,env=env,stdin=subprocess.DEVNULL,
                            stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,timeout=900)
        (out/(name+'.txt')).write_text(proc.stdout,encoding='utf-8')
        checks.append(dict(name=name,passed=proc.returncode==0,exit_code=proc.returncode))
        assert proc.returncode==0,proc.stdout[-4000:]
        print(name,'passed',flush=True)
        return proc.stdout
    def launch(name,arguments):
        command='"'+str(bat)+'"'+''.join(' "'+str(a)+'"' for a in arguments)
        return execute(name,'"'+str(win/'System32/cmd.exe')+'" /d /s /c "'+command+'"')
    assert not (package/'runtime').exists()
    launch('offline_bootstrap',['--check','-NoPause'])
    install=json.loads((package/'build/dependencies/last_check.json').read_text(encoding='utf-8'))
    assert install['network_used'] is False and install['system_python_modified'] is False
    assert install['status']=='repaired' and install['installed_file_count']>1000
    capabilities=launch('native_capabilities',['-Capabilities','-NoPause'])
    assert 'RegValGrid2' in capabilities and 'XYZ/CSV/ZMAP' in capabilities and 'matching .ptd folder' in capabilities
    py=package/'runtime/python.exe'
    fixture=out/'make_polygon_fixture.py'
    fixture.write_text('''from pathlib import Path
import sys,shutil,sqlite3,uuid,json
sys.path.insert(0,sys.argv[1])
from test_petrel_native_recovery import RecoveryTests,frame,polygon_doc,surface_doc,envelope
from export_petrel_native_spatial_zero_gui import NATIVE_FLOAT_MAX_SENTINEL
t=RecoveryTests();t.setUp();t.root=Path(sys.argv[2]);t.root.mkdir()
p=t.fixture(kind='RegValGrid2',surface=True,metric=False,doc=surface_doc(packed=True,legacy=True));src=t.root/'source';src.mkdir()
shutil.copyfile(p/'08_native_project/project_file/test.pet',src/'Fixture.pet')
shutil.copytree(p/'08_native_project/ptd_store',src/'Fixture.ptd')
parts=[[(10.,10.,1.),(14.,10.,1.),(14.,14.,1.)],[],[(20.,20.,2.),(21.,20.,2.),(NATIVE_FLOAT_MAX_SENTINEL,0.,0.),(23.,20.,2.),(24.,20.,2.)]]
tag=str(uuid.uuid4())
with sqlite3.connect(src/'Fixture.ptd/Data.ptd') as db:
    db.execute('ALTER TABLE data ADD COLUMN time_stamp TEXT')
    db.execute('INSERT INTO data VALUES(3,?,"Synthetic polygon",1,"Polygons3","")',(tag,))
    db.execute('INSERT INTO blob_parts VALUES(3,0,?)',(envelope(frame(polygon_doc(parts,[True,False,False]),split=True)),))
db.close()
print(json.dumps(dict(project=str(src/'Fixture.pet'),polygon_id=tag)))
''',encoding='utf-8')
    made=execute('synthetic_fixture',[str(py),'-B',str(fixture),str(package/'scripts'),str(out/'Synthetic native')])
    data=json.loads(made.strip().splitlines()[-1]);project=Path(data['project'])
    before={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in project.parent.rglob('*') if p.is_file()}
    for report_only in (False,True):
        result=out/('Report only' if report_only else 'Converted')
        launch('report_only_bat' if report_only else 'conversion_bat',[project,result,'convert',*(['-ReportOnly'] if report_only else []),'-NoPause'])
        html=next(result.glob('*_REPORT.html')).read_text(encoding='utf-8')
        assert 'polygon-object-filter' in html and f'data-polygon-object="{data["polygon_id"]}"' in html
        assert html.count('data-segment-id="0"')==1 and html.count('data-segment-id="2"')==2
        assert 'Complete data inventory tree' in html
        assert 'Native grid 3' in html and 'selected native nodes' in html
        assert 'unresolved unit' in html
        paths=list(result.rglob('native_polygons_vertices.csv'))
        if report_only:
            assert not paths
            assert not list(result.rglob('surface.xyz'))+list(result.rglob('surface.zmap'))+list(result.rglob('nodes.csv'))
        else:
            assert len(paths)==1
            with paths[0].open(encoding='utf-8-sig',newline='') as stream:rows=list(csv.DictReader(stream))
            assert [(r['segment_id'],r['vertex_index']) for r in rows]==[('0','0'),('0','1'),('0','2'),('2','0'),('2','1'),('2','3'),('2','4')]
            receipt=json.loads(next(result.rglob('native_spatial_decode_report.json')).read_text(encoding='utf-8'))
            item=next(r for r in receipt['objects'] if r['blob_type']=='Polygons3')
            assert item['outer_item_count']==3 and item['segments'][1]['declared_vertex_count']==0
            assert item['segments'][2]['missing_vertex_slots']==1 and item['segments'][0]['is_closed_native']
            recovery=json.loads(next(result.rglob('native_recovery_report.json')).read_text(encoding='utf-8'))
            grid=recovery['objects'][0]
            assert grid['dataset_exported'] and grid['numeric_round_trip']=='exact'
            assert grid['unit'] is None and grid['defined_nodes']==5
            assert len(list(result.rglob('surface.zmap')))==1 and len(list(result.rglob('surface.xyz')))==1
            assert len(list(result.rglob('node_definitions.csv')))==1
            zmap=next(result.rglob('surface.zmap'))
            execute('independent_zmap_readback',[str(py),'-B','-c',"import sys,numpy as np;from zmapio import ZMAPGrid;z=ZMAPGrid(sys.argv[1]);np.testing.assert_array_equal(z.z_values,[[4,1],[5,2],[np.nan,3]]);np.testing.assert_array_equal(z.x_values[0],[100,120,140]);np.testing.assert_array_equal(z.y_values[:,0],[230,200])",str(zmap)])
        assert before=={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in project.parent.rglob('*') if p.is_file()}
    command="import sys,unittest;sys.path.insert(0,sys.argv[1]);r=unittest.TextTestRunner().run(unittest.defaultTestLoader.loadTestsFromNames(['test_petrel_native_recovery','test_petrel_native_spatial_zero_gui','test_petrel_visual_report','test_project_seismic','test_petrel_binary_conversion','test_companion_large_files','test_petrel_progress','test_petrel_surface_export','test_geoviewer_release','test_geoviewer_robustness']));sys.exit(not r.wasSuccessful())"
    execute('packaged_regressions',[str(py),'-B','-c',command,str(package/'scripts')])
    receipt=dict(status='passed',zip_sha256=hashlib.sha256(args.zip.read_bytes()).hexdigest(),checks=checks,
                 source_unchanged=True,offline_bootstrap=True,system_python_used=False)
    (out/'validation.json').write_text(json.dumps(receipt,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(receipt,indent=2))


if __name__=='__main__':main()
