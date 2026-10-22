# Copyright 2026 Ahmed Saher Nouh
# SPDX-License-Identifier: Apache-2.0
"""Pure explicit-mask consensus across caller-supplied order hypotheses.

No files, native decoding, default masks, direction inference or flow semantics.
Absent or ambiguous slots keep an explicit output-only unknown marker.
"""
import numpy as np

def bind_explicit_masks(flags,masks,*,candidate_orders,unknown_value=255):
    flags=np.asarray(flags);masks=np.asarray(masks);orders=np.asarray(candidate_orders)
    if flags.dtype!=np.dtype(bool) or flags.ndim!=2 or flags.shape[1]!=4:
        raise ValueError('Expected Boolean pillar-by-four presence flags')
    if masks.dtype!=np.dtype(bool) or masks.ndim!=2 or masks.shape[1]==0 or len(masks)!=int(flags.sum()):
        raise ValueError('Expected Boolean explicit masks matching presence population')
    if orders.dtype.kind not in 'iu' or orders.ndim!=2 or orders.shape[1]!=4 or not 1<=len(orders)<=24:
        raise ValueError('Expected one to24 explicit integer order permutations')
    if any(sorted(row.tolist())!=[0,1,2,3] for row in orders) or len({tuple(row) for row in orders})!=len(orders):
        raise ValueError('Orders must be distinct permutations of0,1,2,3')
    if type(unknown_value) is not int or not 2<=unknown_value<=255:
        raise ValueError('Unknown output marker must be an integer from2through255')
    if flags.shape[0]*4*masks.shape[1]>32_000_000 or len(orders)*flags.size>4_000_000:
        raise ValueError('Consensus output exceeds bounded array size')
    counts=flags.sum(axis=1);offset=np.cumsum(counts)-counts
    indices=np.full((len(orders),*flags.shape),-1,dtype=np.int64)
    for model,order in enumerate(orders):
        ranks=np.cumsum(flags[:,order],axis=1)-1
        for position,slot in enumerate(order):indices[model,:,slot]=np.where(flags[:,slot],offset+ranks[:,position],-1)
    consensus=flags.copy();values=np.full((*flags.shape,masks.shape[1]),unknown_value,dtype=np.uint8)
    rows,slots=np.nonzero(flags)
    if len(rows):
        reference=masks[indices[0,rows,slots]]
        for model in range(1,len(orders)):
            consensus[rows,slots]&=np.all(reference==masks[indices[model,rows,slots]],axis=1)
        valid_rows,valid_slots=np.nonzero(consensus)
        values[valid_rows,valid_slots]=masks[indices[0,valid_rows,valid_slots]].astype(np.uint8)
    return dict(values=values,present=flags.copy(),consensus=consensus,candidate_source_mask_rows=indices,unknown_value=unknown_value)
