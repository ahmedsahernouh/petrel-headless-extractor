# Copyright 2026 Ahmed Saher Nouh
# SPDX-License-Identifier: Apache-2.0
"""Bind raw vector identities through selected PET registry and Model records.

This recovers native declarations, not external identifiers, units, null rules
or a polygon hole interpretation. Geometry/value ordinals remain unchanged.
"""
from collections import Counter
import hashlib
from pathlib import Path
import uuid
import petrel_native_binary as binary

NS = 'http://www.slb.com/Petrel/2011/03/Serialization'
PROFILES = {'PolygonsSubject': [3,2,13,1,0,1,18,0,0,1],
            'PolygonsAttrFolder': [0,0,1,18,0,0,1],
            'PolygonsAttrSubject': [0,1,0,1,18,0,0,1]}
STATUS = 'native_registry_parent_and_Model_index_type_agree; value_to_part_unresolved'
FIELDS = ('attribute_id','attribute_name','attribute_template_id','attribute_folder_id',
          'attribute_binding_status','attribute_model_locator','attribute_registry_locator','source_model_sha256',
          'source_project_sha256','source_blob_sha256')


def uid(value):
    if type(value) is not str or str(uuid.UUID(value)) != value or uuid.UUID(value).int == 0:
        raise ValueError('Expected canonical nonzero native UUID')
    return value


def scalar(node, field):
    item = node.child(field)
    if not isinstance(item,binary.Node) or item.attrs:
        raise ValueError('Unsupported native scalar field: '+field)
    return item.scalar()


def text(value):
    if type(value) is not str or len(value.encode('utf-8')) > 4096:
        raise ValueError('Native attribute text exceeds profile')
    return value


def profile(node, expected):
    return (node.attrs.get('Version') == expected and
            isinstance(node.attrs.get('Version'),list) and
            all(type(x) is int for x in node.attrs['Version']))


def walk(node):
    yield node
    for child in node.children:
        if isinstance(child,binary.Node): yield from walk(child)


def declarations(project_payload, model_payload):
    """Read identities directly; project_context names are not a binding source."""
    roots=list(binary.read_documents(project_payload))
    if len(roots)!=1: raise ValueError('Expected one selected project registry')
    root=roots[0]
    if root.name!='node' or root.attrs.get('Type')!='ProjectSerializer' or root.attrs.get('xmlns')!=NS or not profile(root,[153,1]):
        raise ValueError('Project registry profile not validated for polygon attributes')
    stubs={}; seen=Counter(); errors=[]
    infos=root.child('class_infos')
    if infos.attrs.get('Type')!='ClassInfos' or not profile(infos,[1,1]):
        raise ValueError('ClassInfos profile not validated')
    for number,entry in enumerate(infos.child('map').children):
        kind=entry.get('key'); value=entry.child('value')
        for n in walk(value):
            if n.attrs.get('Type')!='SubjectStub':continue
            raw=n.get('uniqueTag')
            if raw in ('',0):continue
            tag=uid(raw);seen[tag]+=1
            if kind not in PROFILES:continue
            try:
                if not profile(n,[0,1,1]) or value.attrs.get('Type')!='ClassInfo' or not profile(value,[1,1]):
                    raise ValueError('SubjectStub/ClassInfo profile not validated')
                stubs[tag]=dict(object_id=tag,category=kind,parent_id=uid(scalar(n,'parentTag')),
                    name=text(scalar(n,'visualName')),locator=f'PET#class_infos/map/{number}/{tag}')
            except (ValueError,binary.NativeError) as exc:
                errors.append(dict(object_id=tag,reason=str(exc)))
    models={};model_seen=Counter()
    for ordinal,n in enumerate(binary.read_documents(model_payload)):
        # Count duplicates across all native classes, including unrelated types.
        raw=n.get('unique_tag')
        if raw not in ('',0):model_seen[str(raw)]+=1
        if n.name not in PROFILES:continue
        tag=None
        try:
            tag=uid(scalar(n,'unique_tag'))
            if n.attrs.get('xmlns')!=NS or not profile(n,PROFILES[n.name]):
                raise ValueError('Model polygon metadata profile not validated')
            row=dict(object_id=tag,category=n.name,name=text(scalar(n,'name')),
                locator=f'08_native_project/ptd_store/Model.ptd#document={ordinal}/{n.name}',
                model_profile=PROFILES[n.name])
            if n.name=='PolygonsAttrSubject':
                index=scalar(n,'attr_index');kind=scalar(n,'attr_type');template=scalar(n,'template')
                if type(index) is not int or not 0<=index<4096 or type(kind) is not int or kind not in (1,2,4,5):
                    raise ValueError('Model attribute index/type outside profile')
                row.update(index=index,native_type=kind,template_id=None if type(template) is int and template==0 else uid(template))
            models[tag]=row
        except (ValueError,binary.NativeError) as exc:
            errors.append(dict(object_id=tag,locator=f'Model.ptd#document={ordinal}',reason=str(exc)))
    invalid={x['object_id'] for x in errors if x.get('object_id')}
    invalid.update(tag for tag,n in seen.items() if n!=1)
    invalid.update(tag for tag,n in model_seen.items() if n!=1)
    return dict(stubs=stubs,models=models,invalid=sorted(invalid),errors=errors)


def bind_vectors(object_id, vectors, source):
    """All-or-nothing mapping for an object; raw vectors survive any rejection."""
    if source.get('status')!='source_recorded':
        return dict(status='unresolved',reason=source.get('reason','source declarations unavailable'),bindings=[])
    try:
        d=source['declarations'];stubs=d['stubs'];models=d['models'];invalid=set(d['invalid'])
        def exact(tag,kind):
            a=stubs.get(tag);b=models.get(tag)
            if (tag in invalid or not a or not b or a['category']!=kind or b['category']!=kind or a['name']!=b['name']):
                raise ValueError('Missing, ambiguous or conflicting registry/Model identity: '+str(tag))
            return a,b
        exact(object_id,'PolygonsSubject')
        folder_ids=[tag for tag,row in stubs.items() if row['parent_id']==object_id and row['category']=='PolygonsAttrFolder']
        if len(folder_ids)!=1:raise ValueError('Expected one exact native attribute folder')
        folder=folder_ids[0];exact(folder,'PolygonsAttrFolder')
        candidates=[]
        for tag,row in stubs.items():
            if row['parent_id']!=folder or row['category']!='PolygonsAttrSubject':continue
            stub,model=exact(tag,'PolygonsAttrSubject');candidates.append((stub,model))
        if len(candidates)!=len(vectors):raise ValueError('Native attribute subjects/vector count mismatch')
        by_index={}
        for stub,model in candidates:
            if model['index'] in by_index:raise ValueError('Duplicate native attribute vector index')
            by_index[model['index']]=(stub,model)
        bound=[]
        for index,vector in enumerate(vectors):
            if vector.index!=index or index not in by_index:raise ValueError('Missing native attribute vector index')
            stub,model=by_index[index]
            if model['native_type']!=vector.native_type:raise ValueError('Native attribute type disagrees with vector')
            bound.append(dict(attribute_id=model['object_id'],attribute_name=model['name'],
                attribute_template_id=model['template_id'],attribute_folder_id=folder,
                attribute_binding_status=STATUS,attribute_model_locator=model['locator'],
                attribute_registry_locator=stub['locator'],source_model_sha256=source['source_model_sha256'],
                source_project_sha256=source['source_project_sha256'],
                semantic_status='native_label_and_template_declaration; part_units_null_defaults_ring_roles_unresolved'))
        return dict(status=STATUS,bindings=bound,scope='Selected copied PET/Model native identity declarations; not physical semantics or original source-generation attestation')
    except (ValueError,binary.NativeError) as exc:
        return dict(status='unresolved',reason=str(exc),bindings=[])


def load_package(package, data_file):
    """Only exact package copies; never follow the original source path."""
    package=Path(package).resolve();model=package/'08_native_project/ptd_store/Model.ptd'
    try:
        if Path(data_file).resolve() != (package/'08_native_project/ptd_store/Data.ptd').resolve():
            raise ValueError('Custom Data.ptd override is not bound to package Model/PET')
        if not Path(data_file).resolve().is_relative_to(package) or Path(data_file).is_symlink():
            raise ValueError('Copied Data.ptd path escaped or is linked')
        projects=list((package/'08_native_project/project_file').glob('*.pet'))
        if len(projects)!=1:raise ValueError('Expected one copied selected PET')
        project=projects[0]
        for path in (project,model):
            if not path.resolve().is_relative_to(package) or path.is_symlink():
                raise ValueError('Copied source path escaped or is linked')
        before=[p.stat() for p in (project,model)]
        pet_raw=binary.read_bounded(project,64*1024*1024);model_raw=binary.read_bounded(model,128*1024*1024)
        d=declarations(binary.project_payload(pet_raw),binary.decompress(model_raw,128*1024*1024))
        for p,prior in zip((project,model),before):
            now=p.stat()
            if (prior.st_size,prior.st_mtime_ns)!=(now.st_size,now.st_mtime_ns):raise ValueError('Copied metadata changed during read')
        return dict(status='source_recorded',declarations=d,source_project_sha256=hashlib.sha256(pet_raw).hexdigest(),
            source_model_sha256=hashlib.sha256(model_raw).hexdigest(),source_project_path=str(project),source_model_path=str(model))
    except (ValueError,OSError,binary.NativeError) as exc:
        return dict(status='unresolved',reason=str(exc))
