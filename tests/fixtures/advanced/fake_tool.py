"""Process oracle only; never substitutes for a scientific tool."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time


def record(path, role):
    with Path(path).open('a', encoding='utf-8') as handle:
        handle.write(json.dumps({'role': role, 'pid': os.getpid()}) + '\n')


if '--version' in sys.argv:
    print(os.environ.get('MOLSCENE_FAKE_VERSION', 'APBS 3.4.1'))
    sys.exit(int(os.environ.get('MOLSCENE_FAKE_VERSION_EXIT', '0')))
mode = sys.argv[1]
if mode == 'capture':
    details = {'argv': sys.argv[3:], 'environment': dict(os.environ)}
    if os.name == 'nt':
        import ctypes
        details['console_window'] = int(ctypes.windll.kernel32.GetConsoleWindow())
    Path(sys.argv[2]).write_text(json.dumps(details), encoding='utf-8')
elif mode == 'exit':
    print('diagnostic', file=sys.stderr, flush=True)
    sys.exit(int(sys.argv[2]))
elif mode == 'flood':
    for index in range(300):
        os.write(1, b'o' * 65536)
        os.write(2, b'e' * 65536)
elif mode in ('tree', 'orphan'):
    record(sys.argv[2], 'tool')
    subprocess.Popen([sys.executable, '-B', __file__, 'child', sys.argv[2]],
                     stdin=subprocess.DEVNULL)
    if mode == 'orphan':
        deadline = time.monotonic() + 5
        while len(Path(sys.argv[2]).read_text().splitlines()) < 3 and time.monotonic() < deadline:
            time.sleep(.01)
    else:
        time.sleep(60)
elif mode == 'child':
    record(sys.argv[2], 'child')
    subprocess.Popen([sys.executable, '-B', __file__, 'grandchild', sys.argv[2]],
                     stdin=subprocess.DEVNULL)
    time.sleep(60)
elif mode == 'grandchild':
    record(sys.argv[2], 'grandchild')
    time.sleep(60)
elif mode == 'sleep':
    time.sleep(60)
elif mode == 'missing':
    pass
