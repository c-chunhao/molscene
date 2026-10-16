"""Self-contained UTF-8 SVG plots, using only the standard library."""
from html import escape
import math

MAX_POINTS=100000


def line_svg(x,y,*,title,xlabel,ylabel):
    if not x or len(x)!=len(y) or len(x)>MAX_POINTS or not all(math.isfinite(float(v)) for v in (*x,*y)):
        raise ValueError('Plot data must be finite, matched, nonempty and bounded')
    xmin,xmax=min(x),max(x); ymin,ymax=min(0.,min(y)),max(y)
    def expand(low,high):
        if low!=high: return low,high
        pad=max(abs(low)*.05,.5)
        left,right=low-pad,high+pad
        return (left if math.isfinite(left) else low,
                right if math.isfinite(right) else high)
    xmin,xmax=expand(xmin,xmax)
    ymin,ymax=expand(ymin,ymax)
    xscale=max(abs(xmin),abs(xmax),1.)
    yscale=max(abs(ymin),abs(ymax),1.)
    def px(v): return 90+650*(v/xscale-xmin/xscale)/(xmax/xscale-xmin/xscale)
    def py(v): return 370-300*(v/yscale-ymin/yscale)/(ymax/yscale-ymin/yscale)
    ticks=[(xmin*(1-i/5)+xmax*(i/5), ymin*(1-i/5)+ymax*(i/5)) for i in range(6)]
    def tick_labels(values):
        # Retain enough significant figures to distinguish the actual spacing
        # at this offset. Seventeen digits round-trip finite binary64 values.
        for precision in range(5,18):
            labels=[format(v, f'.{precision}g') for v in values]
            if len(set(labels))==len(set(values)): return labels
    xlabels=tick_labels([xv for xv,yv in ticks])
    ylabels=tick_labels([yv for xv,yv in ticks])
    lines=['<svg xmlns="http://www.w3.org/2000/svg" width="800" height="460" viewBox="0 0 800 460">',
           '<rect width="800" height="460" fill="white"/>',
           '<g font-family="sans-serif" fill="#183044" font-size="13">',
           '<text x="400" y="30" text-anchor="middle" font-size="18">'+escape(str(title))+'</text>',
           '<path d="M90 70 V370 H740" stroke="#183044" fill="none"/>']
    for i,(xv,yv) in enumerate(ticks):
        lines.extend([f'<path d="M{px(xv):.3f} 370 v5 M85 {py(yv):.3f} h5" stroke="#183044"/>',
                      f'<text x="{px(xv):.3f}" y="395" text-anchor="middle">{xlabels[i]}</text>',
                      f'<text x="78" y="{py(yv)+4:.3f}" text-anchor="end">{ylabels[i]}</text>'])
    points=' '.join(f'{px(a):.3f},{py(b):.3f}' for a,b in zip(x,y))
    lines.extend(['<polyline fill="none" stroke="#087e8b" stroke-width="2" points="'+points+'"/>',
        '<text x="415" y="435" text-anchor="middle">'+escape(str(xlabel))+'</text>',
        '<text transform="translate(22 220) rotate(-90)" text-anchor="middle">'+escape(str(ylabel))+'</text>',
        '</g></svg>'])
    return '\n'.join(lines)+'\n'


def write_trajectory_plots(directory,result):
    frames=result['frames']; timed=result['time_axis']=='user_supplied_ps'
    x=[r['time_ps'] if timed else r['state'] for r in frames]
    xlabel='Time (ps, user supplied)' if timed else 'Coordinate state (1-based)'
    for field,name,label in (('rmsd_angstrom','rmsd','RMSD'),('rg_angstrom','rg','Rg')):
        (directory/(name+'.svg')).write_text(line_svg(x,[r[field] for r in frames],title=label+' — '+result['parameters']['object_name'],xlabel=xlabel,ylabel=label+' (angstrom)'),encoding='utf-8')
    (directory/'rmsf.svg').write_text(line_svg(list(range(1,len(result['atoms'])+1)),[r['rmsf_angstrom'] for r in result['atoms']],title='Population atom RMSF',xlabel='Selected atom ordinal (atoms.csv)',ylabel='RMSF (angstrom)'),encoding='utf-8')
