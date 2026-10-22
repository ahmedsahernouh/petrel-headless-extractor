# Copyright 2026 Ahmed Saher Nouh
# SPDX-License-Identifier: Apache-2.0
"""Experimental full-lattice GRDECL geometry from an explicit native hypothesis.

Missing geometry receives labelled collapsed padding and output ACTNUM=0. Source
simulator activity is never inferred. This is not a qualified production profile.
"""
import hashlib
import json
import math
from pathlib import Path
import numpy as np
from petrel_native_binary import NativeError


def file_hash(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda:stream.read(1024*1024),b''):h.update(block)
    return h.hexdigest()


def artifact(path):
    path=Path(path)
    return dict(path=path.name,bytes=path.stat().st_size,sha256=file_hash(path))


def write_array(stream,keyword,array,fmt):
    stream.write(keyword+'\n')
    array=np.asarray(array).reshape(-1)
    for start in range(0,len(array),99_996):
        block=array[start:start+99_996]
        rows=(len(block)//6)*6
        if rows:np.savetxt(stream,block[:rows].reshape(-1,6),fmt=fmt)
        if rows<len(block):np.savetxt(stream,block[rows:].reshape(1,-1),fmt=fmt)
    stream.write('/\n')


def check_ascii_arrays(path,expected):
    """Streaming numeric comparison, separate from independent-reader validation."""
    keyword=None;offset=0;seen=[];buffer=[]
    def check_buffer():
        nonlocal offset
        if not buffer:return
        values=np.fromstring(' '.join(buffer),sep=' ',dtype=expected[keyword].dtype)
        ref=expected[keyword].reshape(-1)[offset:offset+len(values)]
        if not len(values) or not np.array_equal(values,ref):raise NativeError('ASCII array differs')
        offset+=len(values);buffer.clear()
    with Path(path).open(encoding='ascii') as stream:
        for line in stream:
            line=line.strip()
            if not line or line.startswith('--'):continue
            if keyword is None:
                if line=='SPECGRID':
                    next(stream)
                    continue
                if line not in expected or line in seen:raise NativeError('Unexpected/duplicate ASCII keyword')
                keyword=line;offset=0;seen.append(line)
                continue
            if line=='/':
                check_buffer()
                if offset!=expected[keyword].size:raise NativeError('ASCII array length differs')
                keyword=None
                continue
            buffer.append(line)
            if len(buffer)==1024:check_buffer()
    if keyword is not None or set(seen)!=set(expected):raise NativeError('Incomplete ASCII arrays')
    return {name:int(array.size) for name,array in expected.items()}


def export_geometry(table,output,*,hypothesis,z_multiplier,progress=None,write_ascii=True):
    if type(write_ascii) is not bool:raise NativeError('Explicit Boolean ASCII selection required')
    if z_multiplier not in (-1,1):raise NativeError('Explicit Z multiplier required')
    if any(hypothesis.get('flip_'+a) is not False for a in 'ijk'):
        raise NativeError('This experimental full-lattice profile does not admit axis flips')
    if hypothesis.get('fast_axis')!='i':raise NativeError('Only explicit I-fast profile admitted')
    hi,hj=table.horizontal_size;ni,nj,nk=hi-1,hj-1,table.z.shape[1]-1
    count=ni*nj*nk
    if not 0<count<=32_000_000 or ni*nj>100_000:raise NativeError('Grid exceeds diagnostic slab bound')
    lookup=table.node_lookup(hypothesis['bit_order'],hypothesis['fast_axis']).T
    native_pillars=lookup>=0
    if not native_pillars.any():raise NativeError('No recovered native pillars')
    if not np.isfinite(table.geometry_start).all() or not np.isfinite(table.geometry_end).all():
        raise NativeError('Nonfinite source pillar endpoints')
    output=Path(output);output.mkdir(parents=True,exist_ok=False)
    safe=np.maximum(lookup,0)
    coord=np.stack([table.geometry_start[safe],table.geometry_end[safe]],axis=2).copy()
    # Copying is deliberate padding, never a recovered missing-pillar position.
    repaired=np.zeros_like(native_pillars)
    for j,i in zip(*np.nonzero(native_pillars)):
        p=lookup[j,i]
        if coord[j,i,0,2]==coord[j,i,1,2]:
            if table.degree[p]!=0:raise NativeError('Zero-Z-span nonvertical native line')
            coord[j,i,0,2]-=1.;coord[j,i,1,2]+=1.;repaired[j,i]=True
    first_j,first_i=np.argwhere(lookup==0)[0]
    coord[~native_pillars]=coord[first_j,first_i]
    if np.any(coord[:,:,0,2]==coord[:,:,1,2]):
        raise NativeError('Padding pillar does not span Z')
    coord[...,2]*=z_multiplier
    np.save(output/'coord.npy',coord,allow_pickle=False)
    np.save(output/'pillar_native_defined.npy',native_pillars,allow_pickle=False)
    defined=np.lib.format.open_memmap(output/'geometry_defined.npy',mode='w+',dtype='bool',shape=(count,))
    depths=np.lib.format.open_memmap(output/'zcorn.npy',mode='w+',dtype='f8',shape=(2*nk,2*nj,2*ni))
    ij=np.array([[i,j] for j in range(nj) for i in range(ni)],dtype='i8')
    counts={};native_cells=0;multi_cells=0;max_xy_error=0.
    for k in range(nk):
        cells=np.column_stack([ij,np.full(len(ij),k,dtype='i8')])
        xyz,flags=table.corners(cells,**hypothesis)
        good=flags['valid'];native_cells+=int(good.sum());multi_cells+=int((flags['multi_sequence']&good).sum())
        defined[k*ni*nj:(k+1)*ni*nj]=good
        for name,mask in flags.items():
            if name not in ('valid','multi_sequence'):counts[name]=counts.get(name,0)+int(mask.sum())
        points=xyz.reshape(nj,ni,8,3);points[...,2]*=z_multiplier
        valid=good.reshape(nj,ni)
        for w in (0,1):
            for v in (0,1):
                for u in (0,1):
                    c=u+2*v+4*w
                    z=np.where(valid,points[...,c,2],0.)
                    a=coord[v:v+nj,u:u+ni,0,:];b=coord[v:v+nj,u:u+ni,1,:]
                    fraction=(z-a[...,2])/(b[...,2]-a[...,2])
                    expected_xy=a[...,:2]+fraction[...,None]*(b[...,:2]-a[...,:2])
                    if valid.any():
                        error=float(np.abs(expected_xy[valid]-points[...,c,:2][valid]).max())
                        max_xy_error=max(max_xy_error,error)
                        if error>1e-7:raise NativeError('COORD representation loses recovered corner XY')
                    depths[2*k+w,v::2,u::2]=z
        if progress and (k%25==0 or k+1==nk):progress('geometry_slab',processed_layers=k+1,total_layers=nk)
    defined.flush();depths.flush()
    if not np.isfinite(depths).all():raise NativeError('Nonfinite output depths')
    path=output/'grid.grdecl'
    if write_ascii:
      with path.open('x',encoding='ascii',newline='\n') as stream:
        stream.write('-- EXPERIMENTAL FULL LATTICE, NOT A SIMULATOR-READY MODEL\n')
        stream.write('-- ACTNUM IS OUTPUT GEOMETRY SELECTION, NOT RECOVERED SIMULATOR ACTIVITY\n')
        stream.write('-- UNRECOVERED CELLS HAVE GENERATED COLLAPSED Z=0 PADDING\n')
        stream.write('-- MISSING PILLAR ENDPOINTS COPY FIRST SOURCE PILLAR; SEE NPY MASK\n')
        stream.write(f'-- EXPLICIT DIAGNOSTIC Z MULTIPLIER {z_multiplier}; UNITS/CRS/DATUM UNVERIFIED\n')
        stream.write(f'SPECGRID\n{ni} {nj} {nk} 1 F /\n')
        write_array(stream,'COORD',coord,'%.17g')
        write_array(stream,'ZCORN',depths,'%.17g')
        write_array(stream,'ACTNUM',defined.astype('i4'),'%d')
    readback=None
    if write_ascii:
        if progress:progress('geometry_ascii_readback')
        readback=check_ascii_arrays(path,dict(COORD=coord,ZCORN=depths,ACTNUM=defined.astype('i4')))
    receipt=dict(schema='petrel.eclipse.geometry/1',status='experimental_numeric_readback_passed',
        cell_size=[ni,nj,nk],logical_slots=count,geometry_defined_cells=native_cells,
        generated_collapsed_padding_cells=count-native_cells,
        source_pillars=int(native_pillars.sum()),generated_pillars=int((~native_pillars).sum()),
        degree_zero_span_extensions=int(repaired.sum()),valid_multi_sequence_cells=multi_cells,
        withheld_reasons=counts,maximum_pillar_xy_representation_error=max_xy_error,
        actnum_origin='constructed_output_geometry_selection',native_simulator_activity_recovered=False,
        padding=dict(pillar='copy source pillar record index0 for missing logical pillars',
            cells='all8 depth values set to0 for unrecovered cell; zero thickness, output ACTNUM0'),
        z_multiplier=z_multiplier,units='unverified',datum='unverified',crs='unverified',
        mapping_hypothesis=hypothesis,ascii_arrays_verified=readback,
        complete_model_export=False,independent_source_geometry_validation=False,
        artifacts=[artifact(output/n) for n in ['coord.npy','pillar_native_defined.npy','geometry_defined.npy','zcorn.npy']+(['grid.grdecl'] if write_ascii else [])])
    receipt['ascii_written']=write_ascii
    if not write_ascii:receipt['status']='experimental_geometry_arrays_written'
    (output/'GEOMETRY.json').write_text(json.dumps(receipt,indent=2),encoding='utf-8')
    return receipt
