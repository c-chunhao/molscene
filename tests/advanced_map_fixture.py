"""Small real two-state map fixture shared by controller and QWidget tests."""
from array import array


def two_state_map(controller, root):
    from molscene.grids import Grid, write_dx
    from molscene.native_maps import native_dx_text

    records, paths = [], []
    for state in (1, 2):
        path = root / f'map-state-{state}.dx'
        grid = Grid((3, 3, 3), (state * 3, 2, 1),
                    ((1, 0, 0), (0, 1, 0), (0, 0, 1)),
                    array('d', (value * state for value in range(27))), 'unknown', {})
        write_dx(path, grid)
        record = controller.density_load(path, format='dx', name=f'map_{state}', state=state)
        records.append(record)
        paths.append(path)
        if state == 1:
            first_grid = grid
    # Associate the exact same first-state geometry/bytes with the first state
    # of the native two-state object; the second record is already associated.
    controller.cmd.load_raw(native_dx_text(first_grid), 'dx', records[1]['name'], state=1)
    controller.cmd.delete(records[0]['name'])
    for stored in controller._advanced_maps:
        if stored['state'] == 1:
            stored['name'] = records[1]['name']
    return records[1]['name'], paths
