"""Fixed official Qt launcher. Public render calls run on its main event loop."""
import os
from pathlib import Path
import sys
import time
import traceback

if __package__ in (None,''):
    sys.path.insert(0,str(Path(__file__).resolve().parent.parent))

from molscene.native_render import read_json, write_json, validate_request, validate_png
from molscene.artifacts import sha256

_request = None


def _pixels(path, width, height):
    from pymol.Qt import QtGui
    reader=QtGui.QImageReader(str(path))
    image=reader.read()
    if image.isNull() or image.width()!=width or image.height()!=height:
        raise ValueError('Native PNG full decode failed: '+reader.errorString())
    image=image.convertToFormat(QtGui.QImage.Format_RGBA8888)
    pointer=image.constBits(); length=image.bytesPerLine()*height
    if hasattr(pointer,'setsize'): pointer.setsize(length)  # PyQt5 sip buffer
    raw=bytes(pointer)[:length]
    if len(raw)!=length: raise ValueError('Native image buffer is incomplete')
    stride=image.bytesPerLine()
    rgba=b''.join(raw[row*stride:row*stride+width*4] for row in range(height))
    channels=[rgba[index::4] for index in range(4)]
    return dict(count=width*height,rgba_min=[min(c) for c in channels],rgba_max=[max(c) for c in channels],
                nonblack_rgb_gt3=sum(max(r,g,b)>3 for r,g,b in zip(*channels[:3])),opaque_pixels=channels[3].count(255))


def install():
    """Called only by the fixed bootstrap after the official GUI exists."""
    from pymol import cmd
    from pymol.Qt import QtCore, PYQT_NAME
    from pymol.gui import get_qtwindow
    from molscene.timeline import validate_timeline,seek_timeline
    request=_request; work=Path(request['work_dir']); window=get_qtwindow()
    result=dict(schema_version=1,request_id=request['request_id'],source_sha256=request['source_sha256'],
                passed=False,native_pid=os.getpid(),parent_pid=os.getppid(),renderer=None,
                pymol_version=cmd.get_version()[0],qt_binding=PYQT_NAME,gl_valid=False,
                close_requested=False,close_event_seen=False,initial_view=None,frames=[])
    def save(): write_json(work/'result.json',result)
    def close():
        result['close_requested']=True
        try: save()
        finally: window.close()
    def failure():
        result['passed']=False; result['error']=traceback.format_exc()[-8192:]
        close()
    class Observer(QtCore.QObject):
        def eventFilter(self,watched,event):
            if watched is window and event.type()==QtCore.QEvent.Close:
                result['close_event_seen']=True
                try: save()
                except Exception: traceback.print_exc()  # Never obstruct owned window closure.
            return False
    observer=Observer(window); window._molscene_render_observer=observer; window.installEventFilter(observer)
    save(); started=time.monotonic()
    def frame():
        try:
            number=len(result['frames'])+1; spec=request['render']
            if request['timeline'] is not None: seek_timeline(cmd,request['timeline'],number)
            before=list(cmd.get_view()); path=Path(request['outputs'][number-1])
            if path.exists(): raise ValueError('Native output already exists')
            cmd.draw(spec['width'],spec['height'],antialias=0,quiet=1)
            cmd.png(str(path),ray=0,dpi=spec['dpi'],quiet=1)
            diagnostics=validate_png(path,spec['width'],spec['height'])
            result['frames'].append(dict(frame=number,path=str(path),**diagnostics,
                pixels=_pixels(path,spec['width'],spec['height']),view_before=before,view_after=list(cmd.get_view())))
            save()
            write_json(work/'progress.json',dict(request_id=request['request_id'],completed=number,total=len(request['outputs'])))
            if number==len(request['outputs']):
                if sha256(request['source'])!=request['source_sha256']: raise ValueError('Native source changed')
                result['passed']=True; close()
            else:
                QtCore.QTimer.singleShot(0,frame)
        except BaseException: failure()
    def start():
        try:
            if not window.pymolwidget.isValid():
                if time.monotonic()-started>10: raise RuntimeError('Native OpenGL context did not become valid')
                QtCore.QTimer.singleShot(50,start); return
            result['gl_valid']=True; result['renderer']=list(cmd.get_renderer())
            if sha256(request['source'])!=request['source_sha256']: raise ValueError('Native source identity mismatch')
            cmd.load(request['source'],format='pse')
            result['initial_view']=list(cmd.get_view())
            if request['timeline'] is not None: validate_timeline(request['timeline'],cmd)
            save(); QtCore.QTimer.singleShot(50,frame)
        except BaseException: failure()
    QtCore.QTimer.singleShot(50,start)


def main():
    global _request
    _request=validate_request(read_json(sys.argv[1]))
    # Bind the running module so the fixed deferred import shares its data.
    sys.modules['molscene.native_render_child']=sys.modules[__name__]
    import pymol
    pymol.launch(['pymol','-q','-k','-i','-F','-W','256','-H','256',
                  '-d',"/__import__('molscene.native_render_child', fromlist=['install']).install()"])
    # Native close may terminate the interpreter; a return is never the success
    # contract. The parent requires both exit status and validated close result.


if __name__=='__main__': main()
