"""Verified ASCII surface exports and bounded native-grid previews.

Website: https://saherlabs.dev/
Project: https://github.com/ahmedsahernouh/petrel-headless-extractor
Coordinates and values remain in native units and signs; no resampling of exports.
"""
import shutil
import numpy as np

CHUNK = 100_000


def table(path, header, columns, formats, indices=None):
    """Write and read back in chunks, keeping large surfaces within memory bounds."""
    count = len(columns[0]) if indices is None else len(indices)
    def blocks():
        for start in range(0, count, CHUNK):
            take = slice(start, start+CHUNK) if indices is None else indices[start:start+CHUNK]
            yield np.column_stack([column[take] for column in columns])
    with path.open('x', encoding='utf-8', newline='\n') as stream:
        stream.write(header+'\n')
        for block in blocks(): np.savetxt(stream, block, fmt=formats, delimiter=',')
    with path.open(encoding='utf-8') as stream:
        if stream.readline().rstrip('\n') != header: raise ValueError('CSV header changed')
        for expected in blocks():
            actual = np.loadtxt(stream, delimiter=',', max_rows=len(expected), ndmin=2)
            if not np.array_equal(actual, expected): raise ValueError('Surface CSV read-back mismatch')
        if stream.read().strip(): raise ValueError('Extra surface CSV rows')


def definition_runs(path, mask):
    starts = np.r_[0, np.flatnonzero(mask[1:] != mask[:-1])+1]
    lengths = np.diff(np.r_[starts, len(mask)])
    table(path, 'start_index,length,defined', [starts, lengths, mask[starts]], ['%d']*3)
    # Runs cover the complete native mask, including implicit all-defined cells.
    if not np.array_equal(np.repeat(mask[starts], lengths), mask):
        raise ValueError('Definition-run reconstruction failed')


def write_zmap(path, z, valid, info):
    """Node-registered ZMAP: X columns left-to-right; Y rows top-to-bottom.

    The receiving reader must interpret header extents as node coordinates
    (GDAL ZMAP_PIXEL_IS_POINT=TRUE). CRS and units stay in the metadata.
    """
    nx, ny = info['node_size']; values = z.reshape(ny, nx); mask = valid.reshape(ny, nx)
    null = -1e30
    while np.any(z[valid] == null): null *= 10
    name = ''.join(c if c.isascii() and (c.isalnum() or c in '_-') else '_' for c in info['name'])[:64] or 'Surface'
    origin = info['origin']; maximum = info['maximum']; width = 28; per_line = 3
    with path.open('x', encoding='ascii', newline='\n') as stream:
        stream.write('! Website: https://saherlabs.dev/\n! Native node coordinates; ZMAP_PIXEL_IS_POINT=TRUE in GDAL.\n')
        stream.write('! Units, CRS, native sign and independent cell definitions: metadata.json\n')
        stream.write(f'@{name}, GRID, {per_line}\n{width}, {null:.17e}, , 17, 1\n')
        stream.write(f'{ny}, {nx}, {origin[0]:.17g}, {maximum[0]:.17g}, {origin[1]:.17g}, {maximum[1]:.17g}\n0.0, 0.0, 0.0\n@\n')
        for column in range(nx):
            vals = np.where(mask[::-1,column], values[::-1,column], null)
            complete = ny//per_line*per_line
            if complete: np.savetxt(stream, vals[:complete].reshape(-1,per_line), fmt='%28.17e', delimiter='')
            if complete < ny: stream.write(''.join(f'{value:28.17e}' for value in vals[complete:])+'\n')
    with path.open(encoding='ascii') as stream:
        markers = 0
        while markers != 2:
            line = stream.readline()
            if not line: raise ValueError('Incomplete ZMAP header')
            markers += int(line.startswith('@'))
        for column in range(nx):
            parts = [stream.readline() for _ in range((ny+per_line-1)//per_line)]
            actual = np.fromstring(' '.join(parts), sep=' ')
            expected = np.where(mask[::-1,column], values[::-1,column], null)
            if not np.array_equal(actual, expected): raise ValueError('ZMAP value, null or ordering mismatch')
        if stream.read().strip(): raise ValueError('Extra ZMAP samples')
    return dict(zmap_status='written_verified', zmap_null=null,
                zmap_registration='node coordinates; GDAL ZMAP_PIXEL_IS_POINT=TRUE',
                zmap_order='columns X increasing, within each column Y decreasing',
                zmap_topology_boundary='Node definitions retained; independent cell exclusions remain in the cell-definition CSV')


def preview(path, x, y, z, valid, cells, info):
    nx, ny = info['node_size']
    ii = np.unique(np.linspace(0,nx-1,min(nx,256),dtype=int))
    jj = np.unique(np.linspace(0,ny-1,min(ny,256),dtype=int))
    selected = np.ix_(jj,ii)
    def box_counts(bad, inclusive):
        prefix=np.pad(bad.astype(np.int32).cumsum(0,dtype=np.int32).cumsum(1,dtype=np.int32),((1,0),(1,0)))
        high_i=ii[1:]+int(inclusive); high_j=jj[1:]+int(inclusive)
        return (prefix[np.ix_(high_j,high_i)]-prefix[np.ix_(jj[:-1],high_i)]
                -prefix[np.ix_(high_j,ii[:-1])]+prefix[np.ix_(jj[:-1],ii[:-1])])
    # Omit a preview cell if any underlying native node/cell is undefined.
    # Decimation therefore never fills a hole or joins separated grid patches.
    cell_valid=(box_counts(~valid.reshape(ny,nx),True)==0)&(box_counts(~cells.reshape(ny-1,nx-1),False)==0)
    arrays=dict(x=x.reshape(ny,nx)[selected], y=y.reshape(ny,nx)[selected],
                value=z.reshape(ny,nx)[selected], valid=valid.reshape(ny,nx)[selected],
                cell_valid=cell_valid, i=ii, j=jj)
    np.savez_compressed(path, **arrays)
    with np.load(path, allow_pickle=False) as checked:
        if set(checked.files)!=set(arrays) or any(not np.array_equal(checked[k],v) for k,v in arrays.items()):
            raise ValueError('Grid preview round-trip failed')
    values=z[valid]; histogram,edges=np.histogram(values,bins=40)
    return dict(preview_grid_shape=[len(jj),len(ii)],
                preview_grid_scope='Original selected nodes; preview cells containing any undefined native node or cell are masked',
                full_grid_stats=dict(valid_count=len(values),rows=len(z),null_count=int((~valid).sum()),
                    minimum=float(values.min()),maximum=float(values.max()),mean=float(values.mean()),std=float(values.std()),
                    histogram_counts=histogram.tolist(),histogram_edges=edges.tolist()))


def write_surface(output, info, i, j, x, y, z, valid, cells, compact=False, native_node_defs=None):
    # Conservative ASCII-space bound leaves room for readback receipts/report.
    # Failure occurs before writing datasets and stays a per-object QC outcome.
    need=len(z)*350+64*1024**2
    if shutil.disk_usage(output).free < need:
        raise ValueError(f'Insufficient output space for this grid: allow at least {need/1024**3:.2f} GiB free; choose a larger output drive')
    print(f'Surface: {info["name"]} | {int(valid.sum()):,}/{len(z):,} usable nodes | writing ASCII',flush=True)
    index=np.arange(len(z)); defined=np.flatnonzero(valid)
    table(output/'nodes.csv', 'node_index,i,j,x,y,raw_value,defined',
          [index,i,j,x,y,z,valid], ['%d']*3+['%.17g']*3+['%d'], defined if compact else None)
    with (output/'surface.xyz').open('x',encoding='utf-8',newline='\n') as stream:
        stream.write('# X Y VALUE; native numbers; units/domain/CRS in metadata.json\n')
        for start in range(0,len(defined),CHUNK):
            take=defined[start:start+CHUNK];np.savetxt(stream,np.column_stack([x[take],y[take],z[take]]),fmt='%.17g')
    with (output/'surface.xyz').open(encoding='utf-8') as stream:
        stream.readline()
        for start in range(0,len(defined),CHUNK):
            take=defined[start:start+CHUNK];expected=np.column_stack([x[take],y[take],z[take]])
            if not np.array_equal(np.loadtxt(stream,max_rows=len(take),ndmin=2),expected):
                raise ValueError('XYZ numeric read-back failed')
        if stream.read().strip():raise ValueError('Extra XYZ nodes')
    if compact:
        definition_runs(output/'node_definitions.csv',native_node_defs if native_node_defs is not None else valid)
        if native_node_defs is not None and not np.array_equal(native_node_defs,valid):
            definition_runs(output/'usable_node_definitions.csv',valid)
        definition_runs(output/'cell_definitions.csv',cells)
        info.update(node_table_scope='usable defined nodes only; original indices retained; native mask in node_definitions.csv; numeric nulls excluded',
                    definition_table_scope='Complete run-length tables: start_index,length,defined; never inferred from row position')
    else:
        index=np.arange(len(cells));nx=info['node_size'][0]
        table(output/'cells.csv','cell_index,i,j,defined',[index,index%(nx-1),index//(nx-1),cells],['%d']*4)
        info['node_table_scope']='all native node positions'
    import geoviewer_io as gio
    from geoviewer_diagnostics import event
    if info['blob_type']=='RegValGrid2':
        zmap_pending=output/'zmap.partial'
        try:
            zmap_pending.mkdir()
            info.update(write_zmap(zmap_pending/'surface.zmap',z,valid,info))
            if (zmap_pending/'surface.zmap').is_file():
                gio.retry_io(lambda:(zmap_pending/'surface.zmap').rename(output/'surface.zmap'),
                             operation='finalize_zmap',source=zmap_pending/'surface.zmap',destination=output/'surface.zmap')
            try:zmap_pending.rmdir()
            except OSError as exc:event('staging_cleanup_deferred',severity='warning',path=str(zmap_pending),reason=str(exc))
        except Exception as exc:
            if gio.systemic(exc):raise
            info.update(zmap_status='failed',zmap_reason=str(exc),export_status='partial')
            event('format_failed',severity='error',object_id=info.get('object_id'),name=info['name'],format='ZMAP',**gio.error_details(exc))
    else:info.update(zmap_status='not_applicable',zmap_reason='Explicit XYZ mesh is not asserted to be an axis-aligned regular grid; use XYZ/CSV')
    preview_pending = output/'preview.partial'
    try:
        preview_pending.mkdir()
        info.update(preview(preview_pending/'grid_preview.npz',x,y,z,valid,cells,info))
        (preview_pending/'grid_preview.npz').rename(output/'grid_preview.npz')
        try:preview_pending.rmdir()
        except OSError as exc:event('staging_cleanup_deferred',severity='warning',path=str(preview_pending),reason=str(exc))
    except Exception as exc:
        if gio.systemic(exc): raise
        info.update(preview_status='failed',preview_reason=str(exc))
        event('preview_failed',severity='warning',object_id=info.get('object_id'),name=info['name'],**gio.error_details(exc))
    info['topology_boundary']='Native node and cell definitions retained; display decimation masks gaps; no fault connections inferred'
