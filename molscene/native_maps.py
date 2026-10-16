"""Verified float64-to-native map loading, also usable in standalone replay.

PyMOL's DX ASCII reader has bounded float tokens. Canonical scientific tokens
after explicit float32 quantization avoid silently misread finite source data.
Scientific files are never rewritten. Backend imports remain the caller's job.
"""
from array import array
import hashlib
import inspect
import math
from pathlib import Path
import shlex
import struct


def _native_geometry(shape, origin, basis):
    if not isinstance(shape,(tuple,list)) or len(shape)!=3 or any(type(n) is not int or not 2<=n<=4096 for n in shape) or math.prod(shape)>16777216:
        raise ValueError('Invalid or excessive native grid dimensions')
    if not isinstance(origin,(tuple,list)) or not isinstance(basis,(tuple,list)) or len(origin)!=3 or len(basis)!=3 or any(not isinstance(row,(tuple,list)) or len(row)!=3 for row in basis):
        raise ValueError('Native map needs three-dimensional geometry')
    if any(isinstance(v,bool) or not isinstance(v,(float,int)) or not math.isfinite(v) for v in [*origin,*(v for row in basis for v in row)]):
        raise ValueError('Native map geometry must be finite')
    if any(row[a]<=0 or any(row[b]!=0 for b in range(3) if b!=a) for a,row in enumerate(basis)):
        raise ValueError('Native DX requires positive diagonal basis vectors')
    upper=[origin[a]+(shape[a]-1)*basis[a][a] for a in range(3)]
    if any(not math.isfinite(v) or abs(v)>3.4028234663852886e38 for v in [*origin,*upper,*(v for row in basis for v in row)]):
        raise ValueError('Map geometry overflows native float32 storage')
    return [list(origin),upper]


def _native_text(shape, origin, basis, values):
    _native_geometry(shape,origin,basis)
    if len(values)!=math.prod(shape) or any(not math.isfinite(v) or abs(v)>3.4028234663852886e38 for v in values):
        raise ValueError('Native grid values are nonfinite, overflow float32, or mismatch shape')
    counts=' '.join(map(str,shape))
    lines=['object 1 class gridpositions counts '+counts,
           'origin '+' '.join(format(v,'.9e') for v in origin)]
    lines+=['delta '+' '.join(format(v,'.9e') for v in row) for row in basis]
    lines+=['object 2 class gridconnections counts '+counts,
            'object 3 class array type double rank 0 items '+str(len(values))+' data follows']
    native=array('f',values)
    lines+=[' '.join(format(v,'.9e') for v in native[start:start+3]) for start in range(0,len(native),3)]
    lines+=['attribute "dep" string "positions"','object "grid" class field',
            'component "positions" value 1','component "connections" value 2','component "data" value 3','']
    return '\n'.join(lines)


def _load_native_parts(cmd, shape, origin, basis, values, *, name, state=1):
    if not isinstance(name,str) or not name or type(state) is not int or not 1<=state<=100000:
        raise ValueError('Native map needs a name and explicit positive state')
    expected_extent=_native_geometry(shape,origin,basis)
    text=_native_text(shape,origin,basis,values)
    cmd.load_raw(text,'dx',name,state=state,quiet=1)
    native=cmd.get_volume_field(name,state=state,copy=1)
    if tuple(native.shape)!=tuple(shape):
        raise ValueError('Native map dimensions changed on loading')
    digest=hashlib.sha256()
    quantization=0.
    underflow=0
    for actual,source in zip(native.flat,values):
        actual=float(actual)
        if not math.isfinite(actual) or actual!=struct.unpack('<f',struct.pack('<f',source))[0]:
            raise ValueError('Native map values changed unexpectedly on loading')
        digest.update(struct.pack('<d',actual))
        quantization=max(quantization,abs(actual-source))
        underflow+=int(actual==0 and source!=0)
    extent=cmd.get_extent(name,state=state)
    if any(abs(a-b)>1e-4 for actual,expected in zip(extent,expected_extent) for a,b in zip(actual,expected)):
        raise ValueError('Native map physical extent changed on loading')
    return dict(native_value_sha256=digest.hexdigest(),native_extent=extent,
                native_precision=dict(storage='float32',max_abs_quantization_error=quantization,underflow_sites=underflow,
                                      loader='canonical in-memory DX; 10 significant digits after float32 quantization'))


def native_dx_text(grid):
    """Return canonical loader text without changing the Grid or source bytes."""
    return _native_text(grid.shape,grid.origin,grid.basis,grid.values)


def load_native_dx(cmd, grid, *, name, state=1):
    """Load and verify every value and extent; caller owns name and cleanup."""
    return _load_native_parts(cmd,grid.shape,grid.origin,grid.basis,grid.values,name=name,state=state)


def _dx_file_digest(path):
    digest=hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda:stream.read(1024*1024),b''):
            digest.update(chunk)
    return digest.hexdigest()


def load_native_dx_file(cmd, path, metadata, source_sha256, *, name, state=1):
    """Replay an already validated DX association, verifying bytes and count."""
    path=Path(path)
    if not isinstance(source_sha256,str) or len(source_sha256)!=64 or any(c not in '0123456789abcdef' for c in source_sha256):
        raise ValueError('Replay requires a source SHA256 association')
    if not path.is_file() or not 0<path.stat().st_size<=512*1024*1024 or _dx_file_digest(path)!=source_sha256:
        raise ValueError('Replay map source is missing, oversized or changed')
    if not isinstance(metadata,dict) or not {'shape','origin_angstrom','basis_angstrom'}<=set(metadata):
        raise ValueError('Replay requires validated physical grid metadata')
    shape,origin,basis=metadata['shape'],metadata['origin_angstrom'],metadata['basis_angstrom']
    _native_geometry(shape,origin,basis)
    count=math.prod(shape)
    values=array('d')
    with path.open(encoding='ascii') as stream:
        lexer=shlex.shlex(stream,posix=True)
        lexer.whitespace_split=True
        positions=connections=source_origin=None
        source_basis=[]
        def take(expected=None):
            token=next(lexer)
            if expected is not None and token!=expected:
                raise ValueError('Replay DX header differs from validated scalar format')
            return token
        try:
            for token in lexer:
                if token=='object':
                    take(); take('class'); kind=take()
                    if kind in ('gridpositions','gridconnections'):
                        take('counts')
                        dimensions=[int(take()) for _ in range(3)]
                        if kind=='gridpositions' and positions is None:
                            positions=dimensions
                        elif kind=='gridconnections' and connections is None:
                            connections=dimensions
                        else:
                            raise ValueError('Replay contains duplicate grids')
                    elif kind=='array':
                        take('type')
                        if take() not in ('double','float'):
                            raise ValueError('Replay requires a scalar float array')
                        take('rank'); take('0'); take('items')
                        if int(take())!=count:
                            raise ValueError('Replay map scalar item count does not match metadata')
                        take('data'); take('follows')
                        break
                    else:
                        raise ValueError('Unexpected replay DX object before data')
                elif token=='origin' and source_origin is None:
                    source_origin=[float(take()) for _ in range(3)]
                elif token=='delta' and len(source_basis)<3:
                    source_basis.append([float(take()) for _ in range(3)])
                else:
                    raise ValueError('Unexpected replay DX header token')
            else:
                raise ValueError('Replay map has no scalar data')
            if positions!=list(shape) or connections!=list(shape) or source_origin!=list(origin) or source_basis!=[list(row) for row in basis]:
                raise ValueError('Replay map physical geometry differs from source metadata')
            for _ in range(count):
                values.append(float(next(lexer)))
        except (StopIteration,ValueError,OverflowError) as error:
            raise ValueError('Replay map has truncated or invalid scalar data') from error
        following=next(lexer,None)
        if following not in (None,'attribute','object','component'):
            raise ValueError('Replay map has excess scalar values')
    if _dx_file_digest(path)!=source_sha256:
        raise ValueError('Replay source changed while parsing')
    return _load_native_parts(cmd,shape,origin,basis,values,name=name,state=state)


def replay_dx_loader_source():
    """Return the same implementation as self-contained stdlib/public-cmd code."""
    imports='from array import array\nimport hashlib\nimport math\nfrom pathlib import Path\nimport shlex\nimport struct\n'
    functions=(_native_geometry,_native_text,_load_native_parts,_dx_file_digest,load_native_dx_file)
    return imports+'\n'+'\n'.join(inspect.getsource(function) for function in functions)
