# Copyright 2026 Ahmed Saher Nouh
# SPDX-License-Identifier: Apache-2.0
"""Compare explicit horizontal connection bits with raw integer labels.

No bit packing, unavailable code, channel order or simulator semantics inferred.
"""
import numpy as np


def compare_partition(labels, connection_bits, *, unavailable_value, positive_i_channel, positive_j_channel):
    if not isinstance(labels,np.ndarray) or labels.ndim!=2 or labels.dtype.kind not in 'iu' or not labels.size or labels.size>1_000_000:
        raise ValueError('Expected bounded 2D integer labels')
    if type(unavailable_value) is not int:raise ValueError('Explicit integer unavailable marker required')
    channels=(positive_i_channel,positive_j_channel)
    if any(type(v) is not int for v in channels) or sorted(channels)!=[0,1]:raise ValueError('Explicit distinct I/J edge channels required')
    nj,ni=labels.shape
    if not isinstance(connection_bits,np.ndarray) or connection_bits.dtype!=np.dtype('bool') or connection_bits.shape!=(nj+1,ni+1,2):
        raise ValueError('Expected Boolean [Jnode,Inode,2] edge flags')
    available=labels!=unavailable_value
    parent=np.arange(labels.size,dtype=np.int32)
    def find(i):
        while parent[i]!=i:parent[i]=parent[parent[i]];i=int(parent[i])
        return i
    def union(a,b):
        a,b=find(a),find(b)
        if a!=b:parent[b]=a
    census={}
    for axis in 'ij':
        if axis=='i':
            a,b=labels[:,:-1],labels[:,1:];valid=available[:,:-1]&available[:,1:]
            edge=connection_bits[:nj,1:ni,positive_j_channel]
        else:
            a,b=labels[:-1,:],labels[1:,:];valid=available[:-1,:]&available[1:,:]
            edge=connection_bits[1:nj,:ni,positive_i_channel]
        census[axis]=dict(both_labels_available=int(valid.sum()),different_labels_but_edge_one=int(((a!=b)&edge&valid).sum()),same_label_but_edge_zero=int(((a==b)&~edge&valid).sum()))
        for j,i in np.argwhere(valid&edge):
            cell=int(j)*ni+int(i);union(cell,cell+(1 if axis=='i' else ni))
    groups={}
    for flat in np.flatnonzero(available.reshape(-1)):groups.setdefault(find(int(flat)),[]).append(int(flat))
    ids=np.full(labels.shape,-1,dtype=np.int32);flat_labels=labels.reshape(-1);rows=[]
    for ordinal,(root,members) in enumerate(sorted(groups.items())):
        ids.reshape(-1)[members]=ordinal;values,counts=np.unique(flat_labels[members],return_counts=True)
        rows.append(dict(ordinal=ordinal,cells=len(members),native_labels={int(v):int(c) for v,c in zip(values,counts)}))
    unique=np.unique(labels[available]);equal=len(groups)==len(unique) and all(len(r['native_labels'])==1 for r in rows)
    return dict(component_ids=ids,components=rows,component_count=len(groups),native_label_count=len(unique),
                partition_equal=equal,axis_census=census,available_horizontal_cells=int(available.sum()),
                qualification='horizontal_partition_comparison_only_not_flow_or_activity')
