# Copyright 2026 Ahmed Saher Nouh
# SPDX-License-Identifier: Apache-2.0
"""Explicit reservoir geometry hypotheses, not an admitted native grid decoder.

All mask/index/corner conventions are arguments. Outputs must stay diagnostic
until compared against independent references. No ACTNUM or unit inference.
"""
from dataclasses import dataclass
import math
import numpy as np

from petrel_native_binary import NativeError
from petrel_native_recovery import scalar_array
from petrel_reservoir_structure import inspect_grid, inspect_property


def packed_mask(node, bit_order):
    if bit_order not in ('little','big'): raise NativeError('Explicit mask bit order required')
    count=node.get('bool_count');raw=node.get('bitmask')
    if type(count) is not int or count<0 or not isinstance(raw,bytes) or len(raw)!=(count+7)//8:
        raise NativeError('Invalid packed definition mask')
    return np.unpackbits(np.frombuffer(raw,dtype='u1'),bitorder=bit_order)[:count].astype(bool)


def property_storage(node):
    """Return serialized values, without assigning null/category or IJK semantics."""
    summary=inspect_property(node);values=node.child('values');vector=summary['storage_representation']
    if vector!='float32_pages':values=values.child('getData')
    parts=[]
    for page in values.child('pages').children:
        if vector=='DiscreteByteVector':a=np.frombuffer(page.scalar(),dtype='u1')
        else:a=page.children[0].values
        parts.append(a)
    result=np.concatenate(parts) if parts else np.array([],dtype='f4')
    return tuple(summary['cell_size']),result,node.child('def_grid').child('cell_defs')


def logical_coordinates(flat, cell_size, slow_to_fast):
    """Explicit flattening hypothesis; no default order or coordinate flip."""
    if tuple(sorted(slow_to_fast))!=('i','j','k'):raise NativeError('Expected a permutation of i,j,k')
    dims=dict(zip('ijk',cell_size));flat=np.asarray(flat)
    if flat.ndim!=1 or flat.dtype.kind not in 'iu' or np.any(flat<0) or np.any(flat>=math.prod(cell_size)):
        raise NativeError('Invalid flat logical index')
    coords={};stride=1
    for axis in reversed(slow_to_fast):
        coords[axis]=(flat//stride)%dims[axis];stride*=dims[axis]
    return np.column_stack([coords[a] for a in 'ijk'])


@dataclass
class PillarTable:
    horizontal_size: tuple
    mask: object
    geometry_start: np.ndarray
    geometry_end: np.ndarray
    degree: np.ndarray
    sequence_offset: np.ndarray
    selectors: np.ndarray
    z: np.ndarray

    @classmethod
    def from_grid(cls,node):
        summary=inspect_grid(node);pillars=node.child('pillars').children
        starts=[];ends=[];degree=[];offsets=[];selectors=[];z=[]
        for pillar in pillars:
            g=pillar.child('geometry');d=pillar.get('degree');degree.append(d)
            if d==1:
                starts.append(scalar_array(g.child('min'),'double',3));ends.append(scalar_array(g.child('max'),'double',3))
            else:
                xy=scalar_array(g.child('position'),'double',2)
                starts.append([*xy,g.get('zmin')]);ends.append([*xy,g.get('zmax')])
            offsets.append(len(z));single=pillar.child('sequence',required=False)
            if single is not None:seqs=[single];selectors.append([0]*4)
            else:
                seqs=pillar.child('sequences').children
                selectors.append(scalar_array(pillar.child('unique_indices'),'index',4))
            z.extend(scalar_array(s.child('zvalues'),'float') for s in seqs)
        if len({len(a) for a in z})!=1:raise NativeError('Nonuniform vertical sequence length needs another hypothesis')
        return cls(tuple(summary['horizontal_node_size']),node.child('node_defs'),np.asarray(starts),
            np.asarray(ends),np.asarray(degree),np.asarray(offsets,dtype='i8'),
            np.asarray(selectors,dtype='i4'),np.asarray(z))

    def node_lookup(self, bit_order, fast_axis):
        if fast_axis not in ('i','j'):raise NativeError('Explicit horizontal fast axis required')
        defined=packed_mask(self.mask,bit_order)
        if int(defined.sum())!=len(self.degree):raise NativeError('Pillar/mask population mismatch')
        flat=np.full(len(defined),-1,dtype='i4');flat[defined]=np.arange(len(self.degree),dtype='i4')
        ni,nj=self.horizontal_size
        return flat.reshape(nj,ni).T if fast_axis=='i' else flat.reshape(ni,nj)

    def corners(self, cells, *, bit_order, fast_axis, flip_i, flip_j, flip_k, corner_slots):
        """Return candidate corners u+2*v+4*w plus explicit withheld reasons.

        Degree-one XY is hypothesized linear in stored Z. A -1 selector is
        withheld, never replaced with sequence zero or an inactive-cell flag.
        """
        if any(type(flag) is not bool for flag in (flip_i,flip_j,flip_k)):
            raise NativeError('Explicit boolean axis flips required')
        if any(type(slot) is not int for slot in corner_slots) or tuple(sorted(corner_slots))!=(0,1,2,3):
            raise NativeError('Explicit integer corner-slot permutation required')
        cells=np.asarray(cells)
        if cells.ndim!=2 or cells.shape[1]!=3 or cells.dtype.kind not in 'iu':raise NativeError('Expected integer cell IJK rows')
        ni,nj=self.horizontal_size;nk=self.z.shape[1]-1
        if len(cells)>100_000:raise NativeError('Diagnostic cell batch exceeds bound')
        if np.any(cells<0) or np.any(cells>=np.array([ni-1,nj-1,nk])):raise NativeError('Cell outside lattice')
        lookup=self.node_lookup(bit_order,fast_axis)
        pidx=[]
        for v in (0,1):
            for u in (0,1):
                i=cells[:,0]+u;j=cells[:,1]+v
                if flip_i:i=ni-1-i
                if flip_j:j=nj-1-j
                pidx.append(lookup[i,j])
        pidx=np.stack(pidx,axis=1);missing=np.any(pidx<0,axis=1)
        safe=np.maximum(pidx,0);selector=self.selectors[safe,np.asarray(corner_slots)[None,:]]
        sequence_counts=np.diff(np.append(self.sequence_offset,len(self.z)))
        multi_sequence=np.any(sequence_counts[safe]>1,axis=1)
        opaque=np.any(selector<0,axis=1);safe_selector=np.maximum(selector,0)
        seq=self.sequence_offset[safe]+safe_selector
        points=[];bad_line=np.zeros(len(cells),dtype=bool)
        for w in (0,1):
            k=cells[:,2]+w
            if flip_k:k=nk-k
            for c in range(4):
                z=self.z[seq[:,c],k].astype('f8');a=self.geometry_start[safe[:,c]];b=self.geometry_end[safe[:,c]]
                line=self.degree[safe[:,c]]==1;dz=b[:,2]-a[:,2]
                bad_line |= line & (dz==0)
                f=np.divide(z-a[:,2],dz,out=np.zeros_like(z),where=line & (dz!=0))
                xy=a[:,:2]+f[:,None]*(b[:,:2]-a[:,:2])
                points.append(np.column_stack([xy,z]))
        xyz=np.stack(points,axis=1);nonfinite=~np.isfinite(xyz).all(axis=(1,2))
        valid=~(missing|opaque|bad_line|nonfinite);xyz[~valid]=np.nan
        return xyz,dict(valid=valid,missing_pillar=missing,opaque_selector=opaque,
                        zero_z_span_line=bad_line,nonfinite=nonfinite,multi_sequence=multi_sequence)


def flat_coordinates(cells, cell_size, slow_to_fast):
    if tuple(sorted(slow_to_fast))!=('i','j','k'):raise NativeError('Expected a permutation of i,j,k')
    cells=np.asarray(cells)
    if cells.ndim!=2 or cells.shape[1]!=3 or cells.dtype.kind not in 'iu':raise NativeError('Expected integer IJK rows')
    if np.any(cells<0) or np.any(cells>=np.array(cell_size)):raise NativeError('Cell outside lattice')
    dims=dict(zip('ijk',cell_size));result=np.zeros(len(cells),dtype='i8')
    for axis in slow_to_fast:result=result*dims[axis]+cells[:,'ijk'.index(axis)]
    return result
