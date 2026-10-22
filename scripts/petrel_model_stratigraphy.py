# Copyright 2026 Ahmed Saher Nouh
# SPDX-License-Identifier: Apache-2.0
"""Bounded native model stratigraphy links and explicitly derived grid slices.

No source I/O, unit/null/default rules, native mesh reconstruction or RESCUE
writer. Numeric kid tags resolve within the selected grid, never by name.
"""
import numpy as np
from petrel_native_binary import NativeError

PROFILES={'HorizonSubject':[5,0,1,2,21,0,0,1],
          'ZoneSubject':[5,0,1,2,21,0,0,1],
          'HorizonSegSubject':[0,1,2,21,0,0,1],
          'HorizonLinesSubject':[0,0,1,2,21,0,0,1]}

def require(condition,message):
    if not condition:raise NativeError(message)

def integer(value,label):
    require(type(value) is int and 0<=value<=2**31-1,'Invalid '+label)
    return value

def under(stubs,pid,ancestor):
    seen=set()
    for _ in range(128):
        if pid==ancestor:return True
        if pid not in stubs:return False
        require(pid not in seen,'Cycle in native subject ancestry');seen.add(pid)
        pid=stubs[pid]['parent']
    raise NativeError('Subject ancestry exceeds bound')

def resolve(nodes,stubs,grid_id,nk):
    """Resolve typed declarations, preserving raw tags and hierarchy identities."""
    require(type(nk) is int and 0<nk<=10000,'Invalid vertical cell count')
    selected={}
    for n in nodes:
        if n.name not in PROFILES:continue
        pid=n.get('unique_tag')
        if not under(stubs,pid,grid_id):continue
        require(n.attrs.get('Version')==PROFILES[n.name],'Unsupported '+n.name+' profile')
        require(pid not in selected,'Duplicate native subject identity')
        require(stubs[pid]['kind']==n.name,'Stub/model kind differs')
        selected[pid]=n
        require(len(selected)<=100000,'Stratigraphy subject count exceeds bound')
    horizons=[];bytag={};indices=set()
    for pid,n in selected.items():
        if n.name!='HorizonSubject':continue
        tag=integer(n.get('kid_tag'),'horizon kid tag');index=integer(n.get('horizon_index'),'horizon index')
        require(tag not in bytag and index not in indices,'Ambiguous horizon tag/index')
        require(index<=nk,'Horizon index outside vertical lattice')
        h=dict(object_id=pid,name=n.get('name'),kid_tag=tag,horizon_index=index,
               locator=f'Model.ptd/HorizonSubject[{pid}]',segments=[],lines=[])
        horizons.append(h);bytag[tag]=h;indices.add(index)
    require(len(horizons)>=2,'Insufficient horizon declarations')
    byid={h['object_id']:h for h in horizons}
    line_records=[]
    for pid,n in selected.items():
        if n.name not in ('HorizonSegSubject','HorizonLinesSubject'):continue
        parent=stubs[pid]['parent']
        row=dict(object_id=pid,kid_tag=integer(n.get('kid_tag'),'horizon child tag'),name=stubs[pid]['name'])
        if n.name=='HorizonLinesSubject':
            # This observed layout keeps lines under a sibling filter folder.
            # Equal names do not establish a native horizon relationship.
            require(stubs[parent]['kind']=='HorizonLinesFolder','Unexpected horizon-lines parent')
            line_records.append(dict(row,parent_id=parent,horizon_identity_link='not_established'))
            continue
        require(parent in byid,'Horizon child is not bound to a selected horizon')
        byid[parent]['segments'].append(row)
    for h in horizons:
        tags=[s['kid_tag'] for s in h['segments']]
        require(len(tags)==len(set(tags)),'Duplicate horizon segment kid tag')
    zones=[]
    for pid,n in selected.items():
        if n.name!='ZoneSubject':continue
        top=integer(n.get('top_horizon_tag'),'zone top tag');bottom=integer(n.get('base_horizon_tag'),'zone base tag')
        require(top in bytag and bottom in bytag,'Unresolved zone horizon kid tag')
        a,b=bytag[top],bytag[bottom]
        require(a['horizon_index']<b['horizon_index'],'Nonpositive zone interval')
        style=n.child('layering_style');require(style.attrs.get('Version')==[4,0,1],'Unsupported layering style')
        used=style.get('is_used');require(type(used) is bool,'Invalid layering selection flag')
        layers=style.child('noLayers');require(layers.attrs.get('Version')==1,'Unsupported layer count expression')
        declared=integer(layers.get('value'),'declared layer count')
        require(layers.get('expr')=='','Unresolved layer count expression')
        flags={k:n.get(k) for k in ('is_main','is_upper','is_extra_zone')}
        require(all(type(v) is bool for v in flags.values()),'Invalid zone role flag')
        zones.append(dict(object_id=pid,name=n.get('name'),parent_id=stubs[pid]['parent'],
            kid_tag=integer(n.get('kid_tag'),'zone kid tag'),hierarchy_tag=integer(n.get('hierarchy_tag'),'hierarchy tag'),
            raw_top_horizon_tag=top,raw_base_horizon_tag=bottom,top_horizon_id=a['object_id'],base_horizon_id=b['object_id'],
            first_k=a['horizon_index'],stop_k=b['horizon_index'],layering_used=used,declared_layers=declared,
            role_flags=flags,locator=f'Model.ptd/ZoneSubject[{pid}]'))
    require(zones,'No zone declarations');zoneids={z['object_id'] for z in zones}
    childids={z['parent_id'] for z in zones if z['parent_id'] in zoneids}
    zbyid={z['object_id']:z for z in zones}
    for z in zones:
        z['leaf']=z['object_id'] not in childids
        if z['parent_id'] in zoneids:
            p=zbyid[z['parent_id']]
            require(p['first_k']<=z['first_k']<z['stop_k']<=p['stop_k'],'Child zone outside parent interval')
        else:require(stubs[z['parent_id']]['kind']=='ZoneFolder','Unexpected zone parent kind')
        if z['layering_used']:
            require(z['declared_layers']==z['stop_k']-z['first_k'],'Declared layers disagree with horizon indices')
        require(z['layering_used']==z['leaf'],'Layering selection/leaf relation differs from observed profile')
    leaves=sorted((z for z in zones if z['leaf']),key=lambda z:z['first_k'])
    require(leaves[0]['first_k']==0 and leaves[-1]['stop_k']==nk,'Leaf zones do not span lattice')
    require(all(a['stop_k']==b['first_k'] for a,b in zip(leaves,leaves[1:])),'Leaf zone gap or overlap')
    # Each parent must also be exactly covered by its direct children.
    for pid in childids:
        p=zbyid[pid];children=sorted((z for z in zones if z['parent_id']==pid),key=lambda z:z['first_k'])
        require(children[0]['first_k']==p['first_k'] and children[-1]['stop_k']==p['stop_k'] and
                all(a['stop_k']==b['first_k'] for a,b in zip(children,children[1:])),'Child zones do not partition parent')
    return dict(schema='petrel.model-stratigraphy/1',grid_id=grid_id,nk=nk,
        horizons=sorted(horizons,key=lambda h:h['horizon_index']),zones=zones,horizon_line_records=line_records,
        leaf_zone_ids=[z['object_id'] for z in leaves],numeric_tag_links_verified=True,
        native_horizon_mesh_qualified=False,complete_model_qualified=False)

def calibrate_cells(model,legend,mask,layer_values,zone_values,cell_size):
    """Full observed comparison, not a generic palette/null serialization rule."""
    ni,nj,nk=cell_size;require(nk==model['nk'] and ni*nj*nk<=32000000,'Cell lattice differs/exceeds bound')
    require(mask.dtype==np.dtype('bool') and mask.shape==(ni*nj*nk,),'Expected flat explicit definition mask')
    require(layer_values.ndim==zone_values.ndim==1 and layer_values.dtype.kind in 'iu' and zone_values.dtype.kind in 'iu','Expected raw integer values')
    require(len(layer_values)==len(zone_values)==int(mask.sum()),'Packed property population differs')
    require(legend['raw_min_discrete_code']==0 and legend['item_count']==len(model['zones']) and len(legend['items'])==len(model['zones']),'Unreviewed zone legend range')
    ordinal={z['object_id']:i for i,z in enumerate(model['zones'])}
    for i,(z,item) in enumerate(zip(model['zones'],legend['items'])):
        # Label equality is corroboration; all cell codes and the independent
        # numeric horizon tag/layer interval links must agree as well.
        require(item['native_ordinal']==i and item['label']==z['name'],'Serialized zone/legend order differs')
        if z['parent_id'] in ordinal:require(item['raw_parent_item']==ordinal[z['parent_id']],'Legend/native zone parent differs')
    expected=np.full(nk,-1,dtype='i4')
    for z in model['zones']:
        if z['leaf']:expected[z['first_k']:z['stop_k']]=ordinal[z['object_id']]
    require(np.all(expected>=0),'Uncovered K interval')
    k=np.flatnonzero(mask)//(ni*nj)
    lm=int(np.count_nonzero(layer_values!=k));zm=int(np.count_nonzero(zone_values!=expected[k]))
    require(lm==zm==0,'Raw layer/zone values disagree with declared intervals')
    return dict(status='all_defined_cell_layer_and_zone_relations_match',defined_cells=len(k),
        layer_values_compared=len(k),zone_values_compared=len(k),layer_mismatches=lm,zone_mismatches=zm,
        per_k_zone_ordinal=expected.tolist(),qualification='observed_exact_fixture_relation_not_generic_code_null_or_display_rule',
        generic_category_rule_qualified=False,native_null_semantics_qualified=False)

def grid_faces(table,index,hypothesis,z_multiplier):
    """Native-coordinate quads at a declared index; no welding or fault contacts.

    Does not assert that grid slices reproduce a separately serialized native
    horizon mesh. Missing pillars/opaque selectors stay withheld.
    """
    nk=table.z.shape[1]-1
    require(type(index) is int and 0<=index<=nk,'Slice index outside lattice')
    require(z_multiplier in (-1,1),'Explicit Z multiplier required')
    require(all(hypothesis.get('flip_'+a) is False for a in 'ijk'),'Slice profile requires explicit unflipped axes')
    ni,nj=(n-1 for n in table.horizontal_size)
    ij=np.array([[i,j] for j in range(nj) for i in range(ni)],dtype='i8')
    k=min(index,nk-1);w=0 if index<nk else 1
    cells=np.column_stack([ij,np.full(len(ij),k,dtype='i8')])
    xyz,flags=table.corners(cells,**hypothesis);ids=np.flatnonzero(flags['valid'])
    faces=np.ascontiguousarray(xyz[ids][:,np.array([0,1,3,2])+4*w]);faces[...,2]*=z_multiplier
    require(np.isfinite(faces).all(),'Nonfinite recovered face')
    return ids,faces,flags
