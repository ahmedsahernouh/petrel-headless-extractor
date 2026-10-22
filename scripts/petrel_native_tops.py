# Copyright 2026 Ahmed Saher Nouh
# SPDX-License-Identifier: Apache-2.0
"""Bounded, read-only binding of observed Points3 well-top picks.

Only the observed Points3 1.2.0.1.1 vector and Model subject profiles are
admitted. A matched native declaration establishes labels and MD ordinals,
not units, vertical datum, or external scientific validation.
"""
from __future__ import annotations

import hashlib
import math
from pathlib import Path
import uuid

import petrel_native_binary as binary

STATUS = 'native_schema_bound'
VERSIONS = {
    'WellTopsSubject': [5,2,6,3,13,1,2,20,0,0,1],
    'WellTopsWellSubject': [4,2,1,2,20,0,0,1],
    'WellTopsSurfaceSubject': [6,2,1,2,20,0,0,1],
    'PointsAttrSubject': [9,1,2,20,0,0,1],
    'PointsAttrFolder': [0,2,20,0,0,1],
    'WellTopsWellFolder': [0,0,2,20,0,0,1],
    'WellTopsSurfaceFolder': [2,0,2,20,0,0,1],
    'WellTopsZoneSubject': [3,2,1,2,20,0,0,1],
    'WellTraceSubject': [33,3,13,1,2,20,0,0,1],
}
NS = 'http://www.slb.com/Petrel/2011/03/Serialization'
FIELDS = ('object_id','data_pk','point_index','well_name','surface','measured_depth',
          'x','y','z','well_link_uuid','well_subject_uuid','surface_subject_uuid',
          'well_kid_tag','surface_kid_tag','md_attribute_uuid','well_attribute_uuid',
          'surface_attribute_uuid','binding_status','depth_units_status',
          'z_semantic_status','crs_status')


class TopBindingError(ValueError):
    pass


def _uid(value):
    try:
        if type(value) is not str or str(uuid.UUID(value)) != value or uuid.UUID(value).int == 0:
            raise ValueError()
    except (ValueError, AttributeError, TypeError):
        raise TopBindingError('Expected canonical nonzero native UUID') from None
    return value


def _scalar(node, field):
    item = node.child(field)
    if not isinstance(item, binary.Node) or item.attrs or item.children:
        raise TopBindingError('Unsupported Model scalar: '+field)
    return item.scalar()


def _model_doc(doc, category):
    if (doc.name != category or doc.attrs.get('xmlns') != NS
            or doc.attrs.get('Version') != VERSIONS[category]):
        raise TopBindingError('Model subject profile outside observed version: '+category)


def model_index(model_payload):
    """Index exact UUIDs, retaining duplicate evidence across every Model class."""
    docs = {}; duplicates = set(); ordinals = {}
    for ordinal, doc in enumerate(binary.read_documents(model_payload)):
        tag_nodes = [c for c in doc.children if c.name == 'unique_tag']
        if len(tag_nodes) != 1 or tag_nodes[0].children:
            continue
        raw = tag_nodes[0].scalar()
        if type(raw) is not str:
            continue
        try:
            tag = _uid(raw)
        except TopBindingError:
            continue
        if tag in docs:
            duplicates.add(tag)
        docs[tag] = doc
        ordinals[tag] = ordinal
    return docs, duplicates, ordinals


def _context_index(context):
    # The global Model inventory can be partial for unrelated records; only
    # identities traversed by this object must be exact and unambiguous.
    objects = context.get('objects')
    if type(objects) is not list:
        raise TopBindingError('project_context objects missing')
    indexed = {}
    for row in objects:
        if not isinstance(row, dict) or not row.get('object_id'):
            continue
        tag = _uid(row['object_id'])
        if tag in indexed:
            raise TopBindingError('Duplicate project_context object UUID: '+tag)
        indexed[tag] = row
    return indexed


def _ancestor(objects, subject, ancestor):
    seen = set()
    while subject:
        if subject == ancestor:
            return True
        if subject in seen or subject not in objects:
            raise TopBindingError('Cyclic or missing Model parent chain')
        seen.add(subject)
        subject = objects[subject].get('parent_id')
    return False


def _exact(docs, duplicates, objects, tag, category, *, derived_context_name=False):
    if tag in duplicates or tag not in docs or tag not in objects:
        raise TopBindingError('Missing or duplicate Model/context identity: '+tag)
    doc = docs[tag]; row = objects[tag]
    _model_doc(doc, category)
    if row.get('category') != category or _scalar(doc, 'unique_tag') != tag:
        raise TopBindingError('Model/context class or UUID conflict: '+tag)
    model_name = _scalar(doc, 'name')
    context_name = row.get('name')
    if (type(model_name) is not str or type(context_name) is not str or
            (model_name != context_name and not (derived_context_name and not model_name))):
        raise TopBindingError('Model/context name conflict: '+tag)
    return doc, row


def _integer(value, label, *, maximum=2**31-1):
    if type(value) is not int or not 0 <= value <= maximum:
        raise TopBindingError('Invalid native '+label)
    return value


def _vector_values(vector, expected_type, expected_slots):
    if not isinstance(vector, binary.Node) or vector.name != 'type_vector' or vector.attrs != {'Version':5} or vector.content:
        raise TopBindingError('Unsupported Points3 type_vector profile')
    kind = _scalar(vector, 'type')
    if kind != expected_type or type(kind) is not int:
        raise TopBindingError('Model/Points3 attribute type conflict')
    collection_name, item_name = (('doubles','double') if kind == 4 else ('kidtags','kidtag'))
    if sorted(c.name for c in vector.children) != sorted(('type', collection_name, 'default')):
        raise TopBindingError('Unsupported Points3 vector fields')
    collection = vector.child(collection_name)
    if collection.attrs != {'Size':expected_slots} or collection.content or len(collection.children) != 1:
        raise TopBindingError('Points3 attribute count/layout mismatch')
    values = collection.children[0]
    if not isinstance(values, binary.Array) or values.name != item_name or len(values.values) != expected_slots:
        raise TopBindingError('Points3 typed attribute array mismatch')
    if kind == 4 and (values.values.dtype.kind != 'f' or values.values.dtype.itemsize != 8):
        raise TopBindingError('MD is not binary64')
    if kind == 6 and values.values.dtype.kind not in 'iu':
        raise TopBindingError('Kid tags are not integers')
    return values.values


def bind(object_id, data_pk, points, points_payload, context, model_payload, *, blob_sha256=None,
         model_sha256=None):
    """Return (rows, receipt) or raise without emitting a partial row table."""
    object_id = _uid(object_id)
    objects = _context_index(context)
    docs, duplicates, ordinals = model_index(model_payload)
    root_doc, _ = _exact(docs, duplicates, objects, object_id, 'WellTopsSubject')
    if len(points) == 0 or any(point is None for point in points):
        raise TopBindingError('Well-top point slots empty or missing')
    roots = list(binary.read_points3_documents(points_payload))
    if len(roots) != 1 or roots[0].attrs.get('Type') != 'Points3' or roots[0].attrs.get('Version') != [1,2,0,1,1]:
        raise TopBindingError('Points3 root profile outside observed version')
    root = roots[0]
    if root.get('has_attr') is not True or root.child('vertices').attrs.get('Size') != len(points):
        raise TopBindingError('Points3 attribute/vertex count conflict')
    attributes = root.child('attributes')
    vectors = attributes.children
    if attributes.attrs.get('Size') != len(vectors) or len(vectors) > 4096:
        raise TopBindingError('Points3 vector count conflict')
    folders = [tag for tag,row in objects.items() if row.get('parent_id') == object_id and
               row.get('category') == 'PointsAttrFolder']
    if len(folders) != 1:
        raise TopBindingError('Expected one PointsAttrFolder under WellTopsSubject')
    folder = folders[0]
    _exact(docs,duplicates,objects,folder,'PointsAttrFolder',derived_context_name=True)
    attrs = [tag for tag,row in objects.items() if row.get('parent_id') == folder and
             row.get('category') == 'PointsAttrSubject']
    by_index = {}; named = {}
    for tag in attrs:
        doc,_ = _exact(docs,duplicates,objects,tag,'PointsAttrSubject')
        raw_index = _scalar(doc,'attr_index')
        if type(raw_index) is int and raw_index == -3:
            continue  # Observed unallocated "Depth" subject has no stored vector.
        index = _integer(raw_index,'attr_index',maximum=4095)
        kind = _integer(_scalar(doc,'attr_type'),'attr_type',maximum=255)
        if index in by_index:
            raise TopBindingError('Duplicate Model attribute index')
        by_index[index] = (tag,kind)
        name = _scalar(doc,'name')
        if name in ('MD','Surface','Well'):
            if name in named:
                raise TopBindingError('Duplicate required attribute name: '+name)
            named[name] = (index,tag,kind)
    if set(by_index) != set(range(len(vectors))) or set(named) != {'MD','Surface','Well'}:
        raise TopBindingError('Missing Model attribute indices or required names')
    for index, vector in enumerate(vectors):
        if not isinstance(vector,binary.Node) or vector.attrs != {'Version':5} or vector.name != 'type_vector' or vector.content:
            raise TopBindingError('Unsupported Points3 vector')
        if _scalar(vector,'type') != by_index[index][1]:
            raise TopBindingError('Model/Points3 vector type conflict')
        collections = [c for c in vector.children if c.name not in ('type','default')]
        if len(collections) != 1 or collections[0].attrs.get('Size') != len(points):
            raise TopBindingError('Points3 vector slot count conflict')
    if (named['MD'][2],named['Surface'][2],named['Well'][2]) != (4,6,6):
        raise TopBindingError('Required attribute types outside MD/Surface/Well profile')
    md = _vector_values(vectors[named['MD'][0]],4,len(points))
    surfaces = _vector_values(vectors[named['Surface'][0]],6,len(points))
    wells = _vector_values(vectors[named['Well'][0]],6,len(points))
    well_folder_ids = [tag for tag,row in objects.items() if row.get('parent_id') == object_id and
                       row.get('category') == 'WellTopsWellFolder']
    if len(well_folder_ids) != 1:
        raise TopBindingError('Expected one WellTopsWellFolder')
    well_folder = well_folder_ids[0]
    _exact(docs,duplicates,objects,well_folder,'WellTopsWellFolder',derived_context_name=True)
    well_map = {}; surface_map = {}
    for tag,row in objects.items():
        if row.get('category') == 'WellTopsWellSubject' and row.get('parent_id') == well_folder:
            doc,context_row = _exact(docs,duplicates,objects,tag,'WellTopsWellSubject',derived_context_name=True)
            kid = _integer(_scalar(doc,'kid_tag'),'well kid_tag')
            if kid in well_map: raise TopBindingError('Duplicate Well Tops well kid_tag')
            name = _scalar(doc,'name')
            link = _scalar(doc,'well_link')
            if link:
                link = _uid(link)
                linked_doc,_ = _exact(docs,duplicates,objects,link,'WellTraceSubject')
                linked_name = _scalar(linked_doc,'name')
                if name and name != linked_name:
                    raise TopBindingError('Well Tops / WellTrace name conflict')
                name = name or linked_name
            if context_row['name'] != name:
                raise TopBindingError('Well Tops well context/link name conflict')
            if not name: raise TopBindingError('Unnamed Well Tops well')
            well_map[kid] = (name,tag,link)
        if row.get('category') == 'WellTopsSurfaceSubject' and _ancestor(objects,tag,object_id):
            parent = row.get('parent_id'); chain = set()
            while parent != object_id:
                if parent in chain or parent not in objects:
                    raise TopBindingError('Well Tops surface parent chain cyclic or missing')
                chain.add(parent)
                category=objects[parent].get('category')
                if category not in ('WellTopsSurfaceFolder','WellTopsZoneSubject'):
                    raise TopBindingError('Well Tops surface parent chain outside observed profile')
                _exact(docs,duplicates,objects,parent,category,derived_context_name=True)
                parent=objects[parent].get('parent_id')
            doc,_ = _exact(docs,duplicates,objects,tag,'WellTopsSurfaceSubject')
            kid = _integer(_scalar(doc,'kid_tag'),'surface kid_tag')
            if kid in surface_map: raise TopBindingError('Duplicate Well Tops surface kid_tag')
            name = _scalar(doc,'name')
            if not name: raise TopBindingError('Unnamed Well Tops surface')
            surface_map[kid] = (name,tag)
    if not well_map or not surface_map:
        raise TopBindingError('Well Tops well or surface tag map missing')
    rows = []
    for index,(point,m,s,w) in enumerate(zip(points,md,surfaces,wells)):
        if not math.isfinite(float(m)) or any(not math.isfinite(float(v)) for v in point):
            raise TopBindingError('Nonfinite MD or XYZ in well top pick')
        si = int(s); wi = int(w)
        if si not in surface_map or wi not in well_map:
            raise TopBindingError('Well Tops kid_tag missing in exact ancestor map')
        wn,wt,link = well_map[wi]; sn,st = surface_map[si]
        # A placeholder WellTopsWellSubject without a WellTrace link cannot
        # establish a requested well identity for a used pick.
        if not link:
            raise TopBindingError('Used well kid_tag lacks exact WellTrace link')
        rows.append(dict(object_id=object_id,data_pk=data_pk,point_index=index,
            well_name=wn,surface=sn,measured_depth=float(m),x=float(point[0]),
            y=float(point[1]),z=float(point[2]),well_link_uuid=link,
            well_subject_uuid=wt,surface_subject_uuid=st,well_kid_tag=wi,
            surface_kid_tag=si,md_attribute_uuid=named['MD'][1],
            surface_attribute_uuid=named['Surface'][1],well_attribute_uuid=named['Well'][1],
            binding_status=STATUS,depth_units_status='unresolved_from_native_subject',
            z_semantic_status='native_xyz_z; not_TVD_or_TVDSS',
            crs_status='not_resolved_from_native_geometry_payload'))
    receipt = dict(status=STATUS,object_id=object_id,data_pk=data_pk,row_count=len(rows),
        point_count=len(points),vector_count=len(vectors),model_vector_type_matches=len(vectors),
        model_document_ordinal=ordinals[object_id],well_tops_subject_version=root_doc.attrs['Version'],
        source_blob_sha256=blob_sha256,source_model_sha256=model_sha256,
        attribute_subjects={name:tag for name,(_,tag,_) in named.items()},
        used_well_link_uuids=sorted({r['well_link_uuid'] for r in rows}),
        used_well_count=len({r['well_link_uuid'] for r in rows}),
        used_surface_count=len({r['surface_subject_uuid'] for r in rows}),
        boundary='Native Model and Points3 schema bound; MD and Z units/datum unresolved; Z is not TVD; no generic external validation.')
    return rows,receipt


def load_model(package, data_file):
    package=Path(package).resolve()
    expected=package/'08_native_project/ptd_store/Data.ptd'
    model=package/'08_native_project/ptd_store/Model.ptd'
    if Path(data_file).resolve()!=expected.resolve() or model.is_symlink() or expected.is_symlink():
        raise TopBindingError('Native top binding requires exact copied Data.ptd and Model.ptd')
    before=model.stat()
    raw=binary.read_bounded(model,128*1024*1024)
    if (before.st_size,before.st_mtime_ns)!=(model.stat().st_size,model.stat().st_mtime_ns):
        raise TopBindingError('Copied Model.ptd changed during read')
    return binary.decompress(raw,128*1024*1024),hashlib.sha256(raw).hexdigest()
