"""Strict bounded little-endian 32-bit CHARMM DCD, without cells/fixed atoms/4D.

Loading is only for an exporter-owned private PyMOL instance. This function
replaces the private topology's coordinates; never call it on the user's source.
"""
import math
from pathlib import Path
import struct
from .artifacts import checked_path, sha256
from .trajectory import MAX_FRAMES, MAX_ATOMS, MAX_SAMPLES, trajectory_topology, _cancel, _key

MAX_BYTES=256*1024*1024
ORDER_REQUIREMENT='DCD has no atom names. Caller must supply its matching topology in the original DCD atom order; count and decoding checks cannot prove chemical correspondence.'


def _record(handle,expected=None,maximum=1024*1024):
    prefix=handle.read(4)
    if len(prefix)!=4: raise ValueError('Truncated DCD record marker')
    size=struct.unpack('<i',prefix)[0]
    if size<0 or size>maximum or (expected is not None and size!=expected):
        raise ValueError('Unsupported or invalid DCD record length')
    data=handle.read(size); trailer=handle.read(4)
    if len(data)!=size or len(trailer)!=4 or struct.unpack('<i',trailer)[0]!=size:
        raise ValueError('Truncated DCD or mismatched record trailer')
    return data


def _header(handle):
    header=_record(handle,84)
    if header[:4]!=b'CORD': raise ValueError('Only ordinary 3D CORD DCD is supported')
    frames=struct.unpack_from('<i',header,4)[0]
    if struct.unpack_from('<i',header,80)[0]<=0: raise ValueError('Only little-endian CHARMM DCD is supported')
    if any(struct.unpack_from('<i',header,offset)[0] for offset in (36,44,48)):
        raise ValueError('DCD fixed atoms, cell/extra blocks and fourth dimensions are unsupported')
    title=_record(handle,maximum=4+80*1024)
    if len(title)<4 or struct.unpack_from('<i',title)[0]<0 or len(title)!=4+80*struct.unpack_from('<i',title)[0]:
        raise ValueError('Malformed DCD title record')
    atoms=struct.unpack('<i',_record(handle,4))[0]
    if not 1<=frames<=MAX_FRAMES or not 1<=atoms<=MAX_ATOMS or frames*atoms>MAX_SAMPLES:
        raise ValueError('DCD full input frame/atom budget exceeds limit')
    return frames,atoms


def iter_dcd_frames(path,*,cancelled=None):
    """Decode every finite float32 coordinate in file order; no backend needed."""
    path=checked_path(path)
    if not path.is_file() or not 0<path.stat().st_size<=MAX_BYTES: raise ValueError('DCD file exceeds byte limit or is empty')
    with path.open('rb') as handle:
        frames,atoms=_header(handle)
        for _ in range(frames):
            _cancel(cancelled)
            axes=[struct.unpack('<'+'f'*atoms,_record(handle,atoms*4)) for axis in range(3)]
            if not all(math.isfinite(value) for axis in axes for value in axis): raise ValueError('DCD coordinates must be finite')
            yield [list(p) for p in zip(*axes)]
        if handle.read(1): raise ValueError('DCD declared frame count disagrees with trailing records')


def scan_dcd(path,*,cancelled=None):
    """Validate header AND every Fortran record, finite coordinate, EOF and cap."""
    path=checked_path(path)
    if path.suffix.lower()!='.dcd': raise ValueError('Expected .dcd input')
    if not path.is_file() or not 0<path.stat().st_size<=MAX_BYTES: raise ValueError('DCD file exceeds byte limit or is empty')
    before=sha256(path)
    with path.open('rb') as handle: frames,atoms=_header(handle)
    decoded=sum(1 for _ in iter_dcd_frames(path,cancelled=cancelled))
    if decoded!=frames or sha256(path)!=before: raise ValueError('DCD input changed during validation')
    return dict(frame_count=frames,atom_count=atoms,bytes=path.stat().st_size,sha256=before,
                format='little-endian 32-bit CHARMM CORD float32, full atoms, no cell',
                atom_order_requirement=ORDER_REQUIREMENT)


def load_dcd(cmd,object_name,path,*,cancelled=None,progress=None):
    """Import ALL decoded frames into an isolated private single-state topology.

    Explicit state=1 replaces the topology state. Independently decoded native
    coordinates and complete original identities are verified at every state.
    """
    topology=trajectory_topology(cmd,object_name); metadata=scan_dcd(path,cancelled=cancelled)
    if metadata['atom_count']!=len(topology): raise ValueError('DCD atom count does not match complete topology')
    _cancel(cancelled)
    n=metadata['frame_count']
    try:
        cmd.load_traj(str(checked_path(path)),object_name,state=1,format='dcd',interval=1,
                      start=1,stop=n,max=n,selection='all',image=0,average=1)
    except Exception as exc: raise ValueError('Native load_traj cannot load the validated DCD subset: '+str(exc)) from exc
    if cmd.count_states('%'+object_name)!=n or cmd.count_atoms('%'+object_name)!=len(topology):
        raise ValueError('Native DCD count/topology verification failed')
    identities=[_key(a) for a in topology]
    from .trajectory import _rows
    for state,oracle in enumerate(iter_dcd_frames(path,cancelled=cancelled),1):
        rows=_rows(cmd,object_name,state,len(topology))
        if [_key(a) for a in rows]!=identities: raise ValueError('Native DCD atom identity changed')
        actual=cmd.get_coordset(object_name,state=state,copy=1)
        if actual is None or len(actual)!=len(oracle) or any(float(actual[i][j])!=oracle[i][j] for i in range(len(oracle)) for j in range(3)):
            raise ValueError('Native DCD coordinate/frame ordering differs from independent decoder')
        if progress is not None: progress(dict(stage='import_dcd',completed=state,total=n,message='Verified DCD source frame '+str(state)))
    if sha256(path)!=metadata['sha256']: raise ValueError('DCD input changed during native import')
    metadata['native_mapping']=[dict(source_frame=i,state=i) for i in range(1,n+1)]
    return metadata
