# Copyright 2026 Ahmed Saher Nouh
# SPDX-License-Identifier: Apache-2.0
"""Bounded coordinate generation and delivery for packed regular native grids.

Only output coordinates are generated. Native samples, ordering and masks are
unchanged. The decoder owns profile, geometry and source-identity validation.
"""
import math
import numpy as np

CHUNK = 100_000
MAX_NODES = 32_000_000
MAX_AXIS = 65_536


class RegularColumn:
    """A coordinate/index column that can only materialize a bounded selection."""
    def __init__(self, count, nx, field, origin=(0., 0.), inc=(1., 1.), angle=0.):
        self.count, self.nx, self.field = count, nx, field
        self.origin, self.inc = origin, inc
        self.cos, self.sin = math.cos(angle), math.sin(angle)

    def __len__(self):
        return self.count

    def __getitem__(self, take):
        if isinstance(take, slice):
            start, stop, step = take.indices(self.count)
            if len(range(start, stop, step)) > CHUNK:
                raise ValueError('Regular-grid coordinate selection exceeds chunk bound')
            index = np.arange(start, stop, step, dtype=np.int64)
        else:
            index = np.asarray(take)
            if index.dtype.kind not in 'iu' or index.size > CHUNK:
                raise ValueError('Regular-grid coordinates require bounded integer indices')
            if np.any(index < 0) or np.any(index >= self.count):
                raise IndexError('Regular-grid coordinate index outside native lattice')
        if self.field == 'index': return index
        i, j = index % self.nx, index // self.nx
        if self.field == 'i': return i
        if self.field == 'j': return j
        u, v = i*self.inc[0], j*self.inc[1]
        if self.field == 'x': return self.origin[0]+u*self.cos-v*self.sin
        if self.field == 'y': return self.origin[1]+u*self.sin+v*self.cos
        raise ValueError('Unknown regular-grid coordinate column')


def selections(valid):
    for start in range(0, len(valid), CHUNK):
        take = np.flatnonzero(valid[start:start+CHUNK])+start
        if len(take): yield take


def bounds(x, y, z, valid):
    low, high = np.full(3, np.inf), np.full(3, -np.inf)
    for take in selections(valid):
        for axis, column in enumerate((x, y, z)):
            values = column[take]
            low[axis] = min(low[axis], values.min())
            high[axis] = max(high[axis], values.max())
    return low, high


def verified_blocks(path, header, blocks, formats, delimiter=','):
    """Regenerate each source-derived block to verify every emitted scalar."""
    with path.open('x', encoding='utf-8', newline='\n', buffering=1024*1024) as stream:
        stream.write(header+'\n')
        for block in blocks(): np.savetxt(stream, block, fmt=formats, delimiter=delimiter)
    with path.open(encoding='utf-8') as stream:
        if stream.readline().rstrip('\n') != header: raise ValueError('Grid header changed')
        for expected in blocks():
            actual = np.loadtxt(stream, delimiter=delimiter, max_rows=len(expected), ndmin=2)
            if not np.array_equal(actual, expected, equal_nan=True):
                raise ValueError('Regular-grid numerical read-back mismatch')
        if stream.read().strip(): raise ValueError('Extra regular-grid rows')


def mask_run_blocks(mask):
    """Complete RLE with one pending run across chunk boundaries."""
    pending_start, pending_value = 0, bool(mask[0])
    rows = []
    for start in range(0, len(mask), CHUNK):
        block = mask[start:start+CHUNK]
        changes = np.flatnonzero(block[1:] != block[:-1])+start+1
        if start and bool(block[0]) != pending_value:
            changes = np.r_[start, changes]
        for end in changes:
            rows.append((pending_start, int(end)-pending_start, int(pending_value)))
            pending_start, pending_value = int(end), not pending_value
            if len(rows) == CHUNK:
                yield np.asarray(rows, dtype=np.int64); rows = []
    rows.append((pending_start, len(mask)-pending_start, int(pending_value)))
    if rows: yield np.asarray(rows, dtype=np.int64)


def write_tables(output, info, i, j, x, y, z, valid, cells, native_node_defs):
    def nodes():
        for take in selections(valid):
            yield np.column_stack((take, i[take], j[take], x[take], y[take], z[take], valid[take]))
    def xyz():
        for take in selections(valid):
            yield np.column_stack((x[take], y[take], z[take]))
    verified_blocks(output/'nodes.csv', 'node_index,i,j,x,y,raw_value,defined', nodes,
                    ['%d']*3+['%.17g']*3+['%d'])
    verified_blocks(output/'surface.xyz', '# X Y VALUE; native numbers; units/domain/CRS in metadata.json',
                    xyz, '%.17g', delimiter=' ')
    for name, mask in [('node_definitions', native_node_defs), ('cell_definitions', cells)]:
        verified_blocks(output/(name+'.csv'), 'start_index,length,defined',
                        lambda mask=mask: mask_run_blocks(mask), ['%d']*3)
    if not np.array_equal(native_node_defs, valid):
        verified_blocks(output/'usable_node_definitions.csv', 'start_index,length,defined',
                        lambda: mask_run_blocks(valid), ['%d']*3)
    info.update(node_table_scope='usable defined nodes only; original indices retained; native mask in node_definitions.csv; numeric nulls excluded',
                definition_table_scope='Complete run-length tables: start_index,length,defined; never inferred from row position')


def preview(path, x, y, z, valid, cells, info):
    nx, ny = info['node_size']
    ii = np.unique(np.linspace(0, nx-1, min(nx, 256), dtype=int))
    jj = np.unique(np.linspace(0, ny-1, min(ny, 256), dtype=int))
    take = jj[:, None]*nx+ii[None, :]
    node_mask, cell_mask = valid.reshape(ny, nx), cells.reshape(ny-1, nx-1)
    cell_valid = np.empty((len(jj)-1, len(ii)-1), dtype=bool)
    # Each rectangle reads its actual underlying mask, without a full-grid
    # integer prefix sum. Adjacent rectangles intentionally share boundary nodes.
    for row, (j0, j1) in enumerate(zip(jj[:-1], jj[1:])):
        for col, (i0, i1) in enumerate(zip(ii[:-1], ii[1:])):
            cell_valid[row, col] = (node_mask[j0:j1+1, i0:i1+1].all()
                                    and cell_mask[j0:j1, i0:i1].all())
    arrays = dict(x=x[take], y=y[take], value=z[take].astype(np.float64), valid=valid[take],
                  cell_valid=cell_valid, i=ii, j=jj)
    np.savez_compressed(path, **arrays)
    with np.load(path, allow_pickle=False) as checked:
        if set(checked.files) != set(arrays) or any(
                not np.array_equal(checked[k], v, equal_nan=True) for k, v in arrays.items()):
            raise ValueError('Grid preview round-trip failed')
    count, total, low, high = 0, 0., np.inf, -np.inf
    for select in selections(valid):
        values = z[select].astype(np.float64)
        count += len(values); total += values.sum(dtype=np.float64)
        low, high = min(low, values.min()), max(high, values.max())
    mean = total/count
    edges = np.histogram_bin_edges([low, high], bins=40)
    histogram, squares = np.zeros(40, dtype=np.int64), 0.
    for select in selections(valid):
        values = z[select].astype(np.float64)
        squares += ((values-mean)**2).sum(dtype=np.float64)
        histogram += np.histogram(values, bins=edges)[0]
    return dict(preview_grid_shape=[len(jj), len(ii)],
                preview_grid_scope='Original selected nodes; preview cells containing any undefined native node or cell are masked',
                full_grid_stats=dict(valid_count=count, rows=len(z), null_count=len(z)-count,
                    minimum=float(low), maximum=float(high), mean=float(mean), std=float(math.sqrt(squares/count)),
                    histogram_counts=histogram.tolist(), histogram_edges=edges.tolist()))
