"""Immutable source snapshots and transactional trajectory artifact bundles."""
from copy import deepcopy
from datetime import datetime, timezone
import re
from . import __version__
from .artifacts import checked_path, sha256, write_json, write_csv
from .bundles import Bundle
from .trajectory import validate_trajectory_request, trajectory_analysis, _cancel
from .trajectory_plots import write_trajectory_plots


def _replay(path,request,dcd_metadata):
    lines=['# Standalone trajectory replay: original coordinates, no fitting.','python',
           'from pathlib import Path','_trajectory_root = Path(__script__).resolve().parent',
           "cmd.load(str(_trajectory_root / 'source.pse'), format='pse')"]
    if dcd_metadata is not None:
        lines.append("cmd.load_traj(str(_trajectory_root / 'inputs/trajectory.dcd'), "+repr(request['object_name'])+
                     ', state=1, format="dcd", interval=1, start=1, stop='+str(dcd_metadata['frame_count'])+
                     ', max='+str(dcd_metadata['frame_count'])+', selection="all", image=0, average=1)')
    lines.extend(['python end',''])
    path.write_text('\n'.join(lines),encoding='utf-8')


def export_trajectory(input_session,output_dir,request,*,cancelled=None,progress=None,protected=(),source_input=None):
    request=validate_trajectory_request(request); _cancel(cancelled)
    if input_session is None: raise ValueError('Trajectory requires a captured source PSE session')
    session=checked_path(input_session)
    if not session.is_file() or session.suffix.lower()!='.pse' or not session.stat().st_size:
        raise ValueError('Trajectory source session must be a nonempty PSE')
    external=None if request['trajectory_path'] is None else checked_path(request['trajectory_path'])
    if external is not None:
        from .dcd import scan_dcd
        scan_dcd(external,cancelled=cancelled)
    original=None; provenance=None
    if source_input is not None:
        if not isinstance(source_input,dict) or set(source_input)!={'path','sha256'} or not isinstance(source_input['path'],str) or not isinstance(source_input['sha256'],str) or not re.fullmatch('[0-9a-f]{64}',source_input['sha256']):
            raise ValueError('source_input must contain path and captured sha256')
        original=checked_path(source_input['path'])
        if original.suffix.lower() not in ('.pdb','.cif','.mmcif','.pse') or not original.is_file(): raise ValueError('Unsupported original trajectory source')
        if sha256(original)!=source_input['sha256']: raise ValueError('Original trajectory source hash changed')
        provenance='inputs/original'+original.suffix.lower()
    inventory=['source.pse','frames.csv','atoms.csv','residues.csv','rmsd.svg','rmsf.svg','rg.svg','report.json','manifest.json','replay.pml']
    if external is not None: inventory.append('inputs/trajectory.dcd')
    if provenance is not None: inventory.append(provenance)
    protections=tuple(protected)+(session,)+((external,) if external is not None else ())+((original,) if original is not None else ())
    with Bundle(output_dir,protected=protections,inventory=inventory,cancelled=cancelled,new_directory_only=True) as bundle:
        inputs=dict(session=bundle.copy_input(session,'source.pse'))
        if original is not None:
            inputs['original']=bundle.copy_input(original,provenance)
            if inputs['original']['sha256']!=source_input['sha256']: raise ValueError('Original trajectory source hash changed during capture')
        if external is not None: inputs['trajectory']=bundle.copy_input(external,'inputs/trajectory.dcd')
        _cancel(cancelled)
        from .engine import _runtime
        with _runtime().PyMOL() as pm:
            cmd=pm.cmd; cmd.load(str(bundle.stage/'source.pse'),format='pse')
            native=None; loaded_request=deepcopy(request); loaded_request['trajectory_path']=None
            if external is not None:
                from .dcd import load_dcd
                native=load_dcd(cmd,request['object_name'],bundle.stage/'inputs/trajectory.dcd',cancelled=cancelled,progress=progress)
            result=trajectory_analysis(cmd,loaded_request,cancelled=cancelled,progress=progress)
            version=cmd.get_version()[0]
        result['parameters']=deepcopy(request)
        result['parameters']['trajectory_path']='inputs/trajectory.dcd' if external is not None else None
        if native is not None:
            result['dcd']=native; result['limitations'].append(native['atom_order_requirement'])
        for table in ('frames','atoms','residues'):
            write_csv(bundle.stage/(table+'.csv'),result[table],tuple(result[table][0]))
        write_trajectory_plots(bundle.stage,result)
        _replay(bundle.stage/'replay.pml',request,native)
        _cancel(cancelled)
        artifacts={name:name for name in inventory if name!='manifest.json'}
        artifacts.update(source_session='source.pse',replay='replay.pml',report='report.json',manifest='manifest.json',rmsd='rmsd.svg',rmsf='rmsf.svg',rg='rg.svg')
        report=dict(result,schema_version=2,tool='molscene',tool_version=__version__,pymol_version=version,
                    created_at=datetime.now(timezone.utc).isoformat(),inputs=inputs,artifacts=artifacts,
                    passed=True,snapshot_id=inputs['session']['sha256'],analysis=dict(trajectory=deepcopy(result)),
                    replay_contract='Relative original source coordinates and public PyMOL calls; no MolScene dependency',
                    artifact_hashes={name:sha256(bundle.stage/name) for name in inventory if name not in ('report.json','manifest.json')})
        write_json(bundle.stage/'report.json',report)
        if progress is not None: progress(dict(stage='publish',completed=1,total=1,message='Publishing validated trajectory artifacts'))
        bundle.publish()
        return report
