"""Bounded fixed-topology trajectory metrics. Scientific imports are lazy."""
from copy import deepcopy
import math
import re
from pathlib import Path

MAX_FRAMES = 2000
MAX_ATOMS = 100000
MAX_SAMPLES = 2000000
IDENTITY_KEYS = ('object','index','segi','chain','resi','resn','name','element','alt')
LIMITATIONS = [
    'Uniform atom weights; RMSF is population fluctuation and residue RMSF is sqrt(mean(atom RMSF squared)).',
    'Cartesian coordinates only: no PBC correction. Supply whole, pre-imaged or unwrapped molecules.',
    'Coordinate ensembles have no inferred physical time; supplied time spacing is user provenance.',
    'Selections resolve once at the reference state; fitted coordinates are never exported or written to the source.',
]


def validate_trajectory_request(request):
    """Pure normalization, with no PyMOL, Qt or NumPy import."""
    defaults=dict(selection='name CA', fit_selection='name CA', reference_state=1,
                  start=1, stop=None, stride=1, align=True, time_step_ps=None,
                  time_start_ps=0., trajectory_path=None)
    if not isinstance(request, dict) or set(request)-set(defaults)-{'object_name'}:
        raise ValueError('Unknown trajectory request keys')
    value=deepcopy(defaults); value.update(deepcopy(request))
    name=value.get('object_name')
    if not isinstance(name,str) or not re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.-]{0,254}', name):
        raise ValueError('object_name must be one explicit safe object name')
    for key in ('selection','fit_selection'):
        ignored = key == 'fit_selection' and value['align'] is False
        if not isinstance(value[key],str) or (not ignored and not value[key].strip()) or any(c in value[key] for c in '\n\r\x00'):
            raise ValueError(key+' must be a nonempty single-line selection')
    for key in ('reference_state','start','stop','stride'):
        if key=='stop' and value[key] is None: continue
        if type(value[key]) is not int or not 1<=value[key]<=100000:
            raise ValueError(key+' must be a positive integer <=100000')
    if value['stop'] is not None and value['stop']<value['start']: raise ValueError('stop precedes start')
    if type(value['align']) is not bool: raise ValueError('align must be boolean')
    for key in ('time_step_ps','time_start_ps'):
        number=value[key]
        if key=='time_step_ps' and number is None: continue
        try:
            finite = math.isfinite(number)
        except (OverflowError,TypeError,ValueError):
            finite = False
        if isinstance(number,bool) or not isinstance(number,(int,float)) or not finite or (key=='time_step_ps' and number<=0):
            raise ValueError(key+' must be finite'+(' and positive' if key=='time_step_ps' else ''))
        value[key]=float(number)
    path=value['trajectory_path']
    if path is not None and (not isinstance(path,str) or not path or '\x00' in path or Path(path).suffix.lower()!='.dcd'):
        raise ValueError('trajectory_path must be a local .dcd path')
    return value


def _cancel(cancelled):
    if cancelled is not None and cancelled():
        from .bundles import CancelledError
        raise CancelledError('Operation cancelled')


def _key(row): return tuple(row[k] for k in IDENTITY_KEYS)


def _object(cmd,name):
    if name not in cmd.get_names('objects') or cmd.get_type(name)!='object:molecule':
        raise ValueError('object_name must name one existing molecular object')
    count=cmd.count_atoms('%'+name)
    if not 1<=count<=MAX_ATOMS: raise ValueError('Full topology atom count exceeds limit or is empty')
    return count


def _rows(cmd,name,state,expected_count=None):
    from .analysis import collect_atoms
    coords=cmd.get_coordset(name,state=state,copy=1)
    if coords is None or not len(coords): raise ValueError('Missing coordinate state '+str(state))
    rows=collect_atoms(cmd,'%'+name,state=state)
    if expected_count is not None and (len(rows)!=expected_count or len(coords)!=expected_count):
        raise ValueError('Discrete, missing or ambiguous fixed topology coordinates')
    if len({_key(a) for a in rows})!=len(rows): raise ValueError('Duplicate atom identity')
    return [{k:a[k] for k in (*IDENTITY_KEYS,'coord')} for a in rows]


def trajectory_topology(cmd,object_name):
    """State-1 complete identity/coordinates for pre-import DCD snapshot binding."""
    count=_object(cmd,object_name)
    if cmd.count_states('%'+object_name)!=1: raise ValueError('DCD requires a single-state topology')
    return _rows(cmd,object_name,1,count)


def _members(cmd,name,expression,state,full):
    from .analysis import collect_atoms
    rows=collect_atoms(cmd,'(%'+name+') and ('+expression+')',state=state)
    keys={_key(a) for a in rows}
    if not keys or not keys<=set(map(_key,full)): raise ValueError('Invalid reference selection membership')
    return [r for r in full if _key(r) in keys]


def trajectory_plan(cmd,request):
    """Resolve reference members and cap unique sampled+reference work before iteration.

    Returns JSON-ready request, sampled_states, required_states, measurement_atoms,
    fit_atoms, topology_atoms (reference rows), union_atom_count and work_count.
    External DCD must first be imported privately; this helper never mutates coordinates.
    """
    r=validate_trajectory_request(request)
    if r['trajectory_path'] is not None: raise ValueError('Import external DCD privately before loaded-state planning')
    name=r['object_name']; count=_object(cmd,name); states=cmd.count_states('%'+name)
    stop=states if r['stop'] is None else r['stop']
    if stop>states or r['reference_state']>states: raise ValueError('Requested state is out of range')
    sampled=list(range(r['start'],stop+1,r['stride']))
    if not 2<=len(sampled)<=MAX_FRAMES: raise ValueError('Trajectory requires 2..2000 sampled states')
    full=_rows(cmd,name,r['reference_state'],count)
    measured=_members(cmd,name,r['selection'],r['reference_state'],full)
    fit=_members(cmd,name,r['fit_selection'],r['reference_state'],full) if r['align'] else []
    required=sorted(set(sampled+[r['reference_state']]))
    union=len(set(map(_key,measured+fit))); work=union*len(required)
    if work>MAX_SAMPLES: raise ValueError('Trajectory coordinate work budget exceeds limit')
    return dict(request=r,sampled_states=sampled,required_states=required,measurement_atoms=measured,
                fit_atoms=fit,topology_atoms=full,union_atom_count=union,work_count=work)


def trajectory_analysis(cmd,request,*,cancelled=None,progress=None):
    """Analyze copied world arrays, retaining source coordinates and B factors."""
    _cancel(cancelled)
    plan=trajectory_plan(cmd,request); r=plan['request']
    try: import numpy as np
    except ImportError as exc: raise RuntimeError('Trajectory analysis requires NumPy from the compatible PyMOL scientific runtime') from exc
    measured=plan['measurement_atoms']; fit=plan['fit_atoms']; full=plan['topology_atoms']
    mkeys=list(map(_key,measured)); fkeys=list(map(_key,fit)); fullkeys=list(map(_key,full))
    reference=np.array([a['coord'] for a in measured],dtype=float)
    target=np.array([a['coord'] for a in fit],dtype=float)
    def centered(array):
        center=array.mean(axis=0); shifted=array-center
        if not np.isfinite(shifted).all(): raise ValueError('Nonfinite centered coordinates')
        if len(array)<3 or np.linalg.matrix_rank(shifted,tol=max(1.,float(np.linalg.norm(shifted)))*1e-12)<2:
            raise ValueError('Fit requires at least three noncollinear atoms; degenerate geometry')
        return center,shifted
    if r['align']: target_center,target_zero=centered(target)
    mean=np.zeros_like(reference); m2=np.zeros_like(reference); frames=[]
    try:
        with np.errstate(over='raise',invalid='raise',divide='raise'):
            for ordinal,state in enumerate(plan['sampled_states'],1):
                _cancel(cancelled)
                rows=_rows(cmd,r['object_name'],state,len(full))
                if list(map(_key,rows))!=fullkeys: raise ValueError('Atom identity changed across coordinate states')
                mapping={_key(a):a['coord'] for a in rows}
                moving=np.array([mapping[k] for k in mkeys],dtype=float)
                raw=float(np.sqrt(np.mean(np.sum((moving-reference)**2,axis=1))))
                rg=float(np.sqrt(np.mean(np.sum((moving-moving.mean(axis=0))**2,axis=1))))
                fitted=None; determinant=None
                if r['align']:
                    fit_coords=np.array([mapping[k] for k in fkeys],dtype=float)
                    center,zero=centered(fit_coords)
                    u,s,vt=np.linalg.svd(zero.T@target_zero)
                    correction=np.eye(3); correction[2,2]=1. if np.linalg.det(u@vt)>=0 else -1.
                    rotation=u@correction@vt
                    determinant=float(np.linalg.det(rotation))
                    moving=(moving-center)@rotation+target_center
                    fitted=float(np.sqrt(np.mean(np.sum((moving-reference)**2,axis=1))))
                delta=moving-mean; mean+=delta/ordinal; m2+=delta*(moving-mean)
                time=None if r['time_step_ps'] is None else r['time_start_ps']+(state-1)*r['time_step_ps']
                if time is not None and not math.isfinite(time): raise ValueError('Computed time is nonfinite')
                frames.append(dict(frame=ordinal,state=state,source_frame=state,time_ps=time,raw_rmsd_angstrom=raw,
                                   fitted_rmsd_angstrom=fitted,rmsd_angstrom=fitted if r['align'] else raw,
                                   rg_angstrom=rg,fit_determinant=determinant))
                if progress is not None: progress(dict(stage='analyze',completed=ordinal,total=len(plan['sampled_states']),message='Analyzing coordinate state '+str(state)))
            fluct=np.sqrt(np.maximum(0.,np.sum(m2,axis=1)/len(frames)))
    except (FloatingPointError,np.linalg.LinAlgError) as exc: raise ValueError('Trajectory numeric calculation is nonfinite or unstable') from exc
    atoms=[dict({k:a[k] for k in IDENTITY_KEYS},rmsf_angstrom=float(value)) for a,value in zip(measured,fluct)]
    groups={}
    for atom in atoms:
        key=tuple(atom[k] for k in ('object','segi','chain','resi','resn'))
        groups.setdefault(key,[]).append(atom['rmsf_angstrom'])
    residues=[dict(zip(('object','segi','chain','resi','resn'),key),atom_count=len(values),
                   rmsf_angstrom=math.sqrt(math.fsum(v*v for v in values)/len(values))) for key,values in groups.items()]
    _cancel(cancelled)
    return dict(operation='trajectory',parameters=r,frames=frames,atoms=atoms,residues=residues,
                frame_count=len(frames),atom_count=len(atoms),residue_count=len(residues),
                full_topology_atom_count=len(full),fit_atom_count=len(fit),work_count=plan['work_count'],
                selected_identities=[{k:a[k] for k in IDENTITY_KEYS} for a in measured],
                fit_identities=[{k:a[k] for k in IDENTITY_KEYS} for a in fit],
                selection_state_policy='reference_frozen_exact_membership',weighting='uniform',
                rmsf_normalization='population',time_axis='coordinate_state' if r['time_step_ps'] is None else 'user_supplied_ps',
                limits=dict(max_frames=MAX_FRAMES,max_atoms=MAX_ATOMS,max_samples=MAX_SAMPLES),limitations=list(LIMITATIONS))
