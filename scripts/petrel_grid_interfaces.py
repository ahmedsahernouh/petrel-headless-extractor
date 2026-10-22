# Copyright 2026 Ahmed Saher Nouh
# SPDX-License-Identifier: Apache-2.0
"""Diagnostic paired grid faces under an explicit corner-point hypothesis.

Pure numerical functions. Unequal neighboring corners are geometric candidates,
not native fault faces, transmissibilities, active cells or scientific acceptance.
"""
import numpy as np


def scan_interfaces(zcorn, geometry_defined, *, max_candidates):
    """Return (axis/i/j/k rows, paired Z, census); axes 0/1/2 mean I/J/K.

    zcorn follows explicit [2*k+w,2*j+v,2*i+u] ordering. geometry_defined
    expresses constructed geometry availability, never native simulator activity.
    Corners are paired in perimeter order, with no tolerance or deduplication.
    """
    if not isinstance(zcorn,np.ndarray) or zcorn.dtype!=np.dtype('float64') or zcorn.ndim!=3 or any(n<2 or n%2 for n in zcorn.shape):
        raise ValueError('Expected even 3D float64 ZCORN array')
    nk,nj,ni=(n//2 for n in zcorn.shape)
    if not isinstance(geometry_defined,np.ndarray) or geometry_defined.dtype!=np.dtype('bool') or geometry_defined.shape!=(nk,nj,ni):
        raise ValueError('Expected Boolean constructed-geometry availability in KJI order')
    if type(max_candidates) is not int or max_candidates<0:raise ValueError('Explicit nonnegative candidate bound required')
    for k in range(nk):
        if not np.isfinite(zcorn[2*k:2*k+2]).all():raise ValueError('Nonfinite ZCORN input')
    rows=[];values=[];counts={};total=0
    perimeter=[(0,0),(0,1),(1,1),(1,0)]
    for axis in range(3):
        census=dict(logical_neighbor_pairs=0,both_geometry_defined=0,withheld_geometry_unavailable=0,different_paired_corner_z=0,equal_paired_corner_z=0)
        for k in range(nk-(axis==2)):
            if axis==0:
                a=np.stack([zcorn[2*k+w,v::2,1::2][:,:-1] for w,v in perimeter],axis=-1)
                b=np.stack([zcorn[2*k+w,v::2,0::2][:,1:] for w,v in perimeter],axis=-1)
                good=geometry_defined[k,:,:-1]&geometry_defined[k,:,1:]
            elif axis==1:
                a=np.stack([zcorn[2*k+w,1::2,u::2][:-1,:] for w,u in perimeter],axis=-1)
                b=np.stack([zcorn[2*k+w,0::2,u::2][1:,:] for w,u in perimeter],axis=-1)
                good=geometry_defined[k,:-1,:]&geometry_defined[k,1:,:]
            else:
                a=np.stack([zcorn[2*k+1,v::2,u::2] for v,u in perimeter],axis=-1)
                b=np.stack([zcorn[2*k+2,v::2,u::2] for v,u in perimeter],axis=-1)
                good=geometry_defined[k]&geometry_defined[k+1]
            if not np.isfinite(a).all() or not np.isfinite(b).all():raise ValueError('Nonfinite ZCORN input')
            different=np.any(a!=b,axis=-1)&good
            jj,ii=np.nonzero(different);n=len(ii);total+=n
            if total>max_candidates:raise ValueError('Candidate bound exceeded; no truncation permitted')
            if n:
                rows.append(np.column_stack([np.full(n,axis),ii,jj,np.full(n,k)]).astype(np.int32))
                values.append(np.stack([a[different],b[different]],axis=1))
            census['logical_neighbor_pairs']+=good.size;census['both_geometry_defined']+=int(good.sum())
            census['withheld_geometry_unavailable']+=int((~good).sum());census['different_paired_corner_z']+=n
            census['equal_paired_corner_z']+=int(good.sum())-n
        counts['ijk'[axis]]=census
    return (np.concatenate(rows) if rows else np.empty((0,4),dtype=np.int32),
            np.concatenate(values) if values else np.empty((0,2,4),dtype=np.float64),counts)


def face_nodes(rows):
    """Horizontal COORD node indices for each quad perimeter; no source IDs."""
    rows=np.asarray(rows)
    if rows.ndim!=2 or rows.shape[1]!=4 or rows.dtype.kind not in 'iu' or np.any(rows<0) or np.any(rows[:,0]>2):
        raise ValueError('Expected nonnegative axis/I/J/K integer rows')
    axis,i,j,k=rows.T
    jj=np.empty((len(rows),4),dtype=np.int64);ii=np.empty_like(jj)
    a=axis==0;jj[a]=j[a,None]+[0,1,1,0];ii[a]=i[a,None]+[1,1,1,1]
    a=axis==1;jj[a]=j[a,None]+[1,1,1,1];ii[a]=i[a,None]+[0,1,1,0]
    a=axis==2;jj[a]=j[a,None]+[0,0,1,1];ii[a]=i[a,None]+[0,1,1,0]
    return jj,ii


def paired_corners(coord, rows, paired_z):
    """Interpolate paired XYZ quads using the same global COORD pillars.

    Returned shape (pairs,2,4,3). Face winding/physical units remain unqualified.
    A nonzero Z span is required; no padding or replacement is generated here.
    """
    if not isinstance(coord,np.ndarray) or coord.dtype!=np.dtype('float64') or coord.ndim!=4 or coord.shape[-2:]!=(2,3):
        raise ValueError('Expected float64 COORD [Jnode,Inode,2,3]')
    if not isinstance(paired_z,np.ndarray) or paired_z.dtype!=np.dtype('float64') or paired_z.shape!=(len(rows),2,4):
        raise ValueError('Expected paired float64 Z [pairs,2,4]')
    jj,ii=face_nodes(rows)
    if np.any(jj>=coord.shape[0]) or np.any(ii>=coord.shape[1]):raise ValueError('Face node outside COORD')
    a=coord[jj,ii,0];b=coord[jj,ii,1]
    if not np.isfinite(a).all() or not np.isfinite(b).all() or not np.isfinite(paired_z).all():raise ValueError('Nonfinite geometry')
    dz=b[:,:,2]-a[:,:,2]
    if np.any(dz==0):raise ValueError('Zero-span pillar cannot be interpolated')
    fraction=(paired_z-a[:,None,:,2])/dz[:,None,:]
    xy=a[:,None,:,:2]+fraction[:,:,:,None]*(b-a)[:,None,:,:2]
    result=np.concatenate([xy,paired_z[:,:,:,None]],axis=-1)
    if not np.isfinite(result).all():raise ValueError('Nonfinite reconstructed corners')
    return result
