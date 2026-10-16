"""Bounded PQR parsing and auditable protein preparation validation."""
import math
import re

from .artifacts import checked_path, sha256

PROTEIN_RESIDUES = frozenset('ALA ARG ASN ASP CYS GLN GLU GLY HIS ILE LEU LYS MET PHE PRO SER THR TRP TYR VAL HID HIE HIP ASH GLH CYX CYM LYN HSD HSE HSP'.split())
WATER_RESIDUES = frozenset(('HOH', 'WAT', 'H2O'))


def atom_identity(atom):
    return (atom['chain'], str(atom['resi']), atom['resn'], atom['name'])


def _element(name, resn):
    # PQR has no element column. Protein CA means alpha carbon, never calcium.
    if resn in PROTEIN_RESIDUES | WATER_RESIDUES:
        letters = name.lstrip('0123456789')
        if letters and letters[0] in 'HCNOS':
            return letters[0]
    return None


def read_pqr(path):
    path = checked_path(path)
    if not path.is_file() or not 0 < path.stat().st_size <= 64*1024*1024:
        raise ValueError('PQR must be nonempty and at most 64 MiB')
    atoms, identities = [], set()
    with path.open(encoding='ascii') as handle:
        for line in handle:
            fields = line.split()
            if not fields or fields[0] in ('REMARK', 'TER', 'END', 'ENDMDL', 'HEADER', 'TITLE', 'CRYST1', 'CONECT', 'MASTER'):
                continue
            if fields[0] not in ('ATOM', 'HETATM') or len(fields) not in (10, 11):
                raise ValueError('PQR requires whitespace-separated ATOM/HETATM rows with optional chain')
            chain = fields[4] if len(fields) == 11 else ''
            resi = fields[-6]
            if not re.fullmatch(r'-?\d+[A-Za-z]?', resi):
                raise ValueError('Ambiguous PQR residue identity')
            try:
                serial = int(fields[1])
                numbers = [float(value) for value in fields[-5:]]
            except (ValueError, OverflowError) as exc:
                raise ValueError('Invalid PQR numbers') from exc
            if not all(math.isfinite(value) for value in numbers) or numbers[-1] < 0:
                raise ValueError('PQR coordinates/charge must be finite and radius nonnegative')
            atom = dict(record=fields[0], serial=serial, name=fields[2], resn=fields[3], chain=chain, resi=resi,
                        **dict(zip(('x','y','z','charge','radius'), numbers)), element=_element(fields[2], fields[3]))
            if atom['element'] not in (None, 'H') and atom['radius'] <= 0:
                raise ValueError('Heavy atoms require assigned positive radii')
            identity = atom_identity(atom)
            if identity in identities:
                raise ValueError('Duplicate PQR chemical identity')
            identities.add(identity)
            atoms.append(atom)
            if len(atoms) > 1_000_000:
                raise ValueError('PQR exceeds one million atoms')
    if not atoms or not any(atom['radius'] > 0 for atom in atoms):
        raise ValueError('PQR needs atoms and at least one positive surface radius')
    return dict(atoms=atoms, atom_count=len(atoms), net_charge=math.fsum(a['charge'] for a in atoms),
                charge_range=[min(a['charge'] for a in atoms), max(a['charge'] for a in atoms)],
                radius_range=[min(a['radius'] for a in atoms), max(a['radius'] for a in atoms)],
                zero_radius_sites=sum(a['radius'] == 0 for a in atoms), source_sha256=sha256(path))


def validate_preparation(original_atoms, prepared, diagnostics, options):
    """Fail closed on dropped heavy atoms and unsupported selected chemistry.

    Original display vdw and assigned force-field radii have different meanings;
    both are recorded, never required to be equal. Original coordinates are held
    to PDB's 0.001 A serialization precision.
    """
    if not original_atoms:
        raise ValueError('Preparation selection is empty')
    if not isinstance(diagnostics, str):
        raise ValueError('Preparation diagnostics must be text')
    if re.search(r'(?i)(unassigned|unable to assign|could not assign|failed to assign|missing (?:charge|radius)|error:|critical:)', diagnostics):
        raise ValueError('PDB2PQR reported unassigned atoms or failed preparation')
    originals, excluded = {}, []
    for atom in original_atoms:
        key = atom_identity(atom)
        if key in originals or atom.get('alt', ''):
            raise ValueError('Ambiguous selected atom identity or alternate conformer')
        if atom['resn'] not in PROTEIN_RESIDUES | WATER_RESIDUES or atom.get('element') not in ('H','C','N','O','S'):
            raise ValueError('Unsupported selected ligand, metal, residue or ambiguous element')
        if atom['resn'] in WATER_RESIDUES and options.get('drop_water', False):
            excluded.append(list(key))
        else:
            originals[key] = atom
    actual = {atom_identity(a): a for a in prepared['atoms']}
    retained, repaired, radii = [], [], []
    for key, atom in originals.items():
        if atom['element'] == 'H':
            if options.get('protonation') == 'preserve' and (key not in actual or any(abs(atom[c]-actual[key][c])>.0011 for c in ('x','y','z'))):
                raise ValueError('Assignment-only preparation changed original hydrogen identity or coordinates')
            if key not in actual:
                excluded.append(list(key))
            continue
        if key not in actual:
            raise ValueError('Preparation dropped selected heavy atom: '+repr(key))
        assigned = actual[key]
        if assigned.get('element') != atom['element'] or assigned['radius'] <= 0:
            raise ValueError('Unassigned or ambiguous heavy atom after preparation')
        if any(abs(atom[c]-assigned[c]) > .0011 for c in ('x','y','z')):
            raise ValueError('Preparation moved an original heavy atom')
        retained.append(list(key))
        radii.append(dict(identity=list(key), original_vdw=atom.get('vdw', atom.get('radius')), assigned_radius=assigned['radius']))
    added = [list(key) for key in actual if key not in originals]
    original_residues = {key[:3] for key in originals}
    for key, atom in actual.items():
        if key[:3] not in original_residues:
            raise ValueError('Preparation introduced an unexpected residue')
        if atom.get('element') is None or (atom['element'] != 'H' and atom['radius'] <= 0):
            raise ValueError('Preparation left an unassigned atom')
        if key not in originals and atom['element'] != 'H':
            repaired.append(list(key))
    return dict(retained_heavy_atoms=len(retained), retained_atoms=[list(key) for key in originals if key in actual], added_atoms=added,
                repaired_atoms=repaired, excluded_atoms=excluded, heavy_atom_radii=radii,
                diagnostics=diagnostics, protonation_mode='assignment-only' if options.get('protonation') == 'preserve' else 'propka',
                radius_convention='PDB2PQR assigned electrostatic radii',
                atom_count=prepared['atom_count'], net_charge=prepared['net_charge'], zero_radius_sites=prepared['zero_radius_sites'])
