"""Execute unchanged ResponsiveBars painting with a recording painter double.

Checks drawing commands/colours, not native Qt rasterisation. The real-Qt test
file separately checks matching Inspector/Advanced images when Qt is present.
"""
from types import SimpleNamespace as NS
from pathlib import Path
import ast
import pytest
from tools.sim_player.muscle_extrema import RelativeMuscleRuler
from tools.sim_player import theme

PLAYER=Path(__file__).resolve().parents[1]

class Point:
    def __init__(self,x,y): self.xx=x;self.yy=y
    def x(self):return self.xx
    def y(self):return self.yy
class Rect:
    def __init__(self,x,y,w,h):self.x=x;self.y=y;self.w=w;self.h=h
    def center(self):return Point(self.x+self.w/2,self.y+self.h/2)
class Widget:
    def __init__(self,parent=None):self.tooltip='';self.w=520;self.h=300
    def setMinimumSize(self,*a):pass
    def update(self):pass
    def setToolTip(self,text):self.tooltip=text
    def width(self):return self.w
    def height(self):return self.h
    def rect(self):return Rect(0,0,self.w,self.h)
class Pen:
    def __init__(self,color,width=1,*args):self.color=color;self.width=width
class Painter:
    RenderHint=NS(Antialiasing=1)
    def __init__(self,widget):self.commands=[];widget.painter=self;self.pen=None;self.brush=None
    def setRenderHint(self,*a):pass
    def fillRect(self,*a):pass
    def setFont(self,*a):pass
    def setPen(self,pen):self.pen=pen if isinstance(pen,Pen) else Pen(pen)
    def setBrush(self,brush):self.brush=brush
    def drawLine(self,*args):self.commands.append(('line',self.pen.color,self.pen.width,args))
    def drawRoundedRect(self,rect,*args):self.commands.append(('rect',self.brush,self.pen.color,rect))
    def drawText(self,*args):self.commands.append(('text',self.pen.color,args))
    def end(self):pass


def bars():
    tree=ast.parse((PLAYER/'panels.py').read_text())
    cls=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='ResponsiveBars')
    ns=dict(QWidget=Widget,Qt=NS(PenStyle=NS(NoPen=0),BrushStyle=NS(NoBrush='none'),
             AlignmentFlag=NS(AlignVCenter=1,AlignLeft=2)),
        QPainter=Painter,QColor=lambda v:v,QFont=lambda *a:a,QPen=Pen,QRectF=Rect,QPointF=Point,
        RelativeMuscleRuler=RelativeMuscleRuler,AXES=('Surge','Sway','Heave','Roll','Pitch','Yaw'),
        **{k:getattr(theme,k) for k in ('PANEL','RAIL','TEXT','MUTED','ACCENT','GREEN','ORANGE')})
    exec(compile(ast.fix_missing_locations(ast.Module(body=[cls],type_ignores=[])),str(PLAYER/'panels.py'),'exec'),ns)
    return ns['ResponsiveBars'](False)


@pytest.mark.parametrize('lo,hi',[(600.,800.),(750.,1000.)])
def test_red_extrema_outside_limits_and_grey_valid_limits_are_drawn(lo,hi):
    b=bars();mid=(lo+hi)/2
    b.update_values((mid,)*6,lo,hi,requested=(mid,)*6,
        extrema=((lo-10,mid),(mid,hi+15),*((lo+10,hi-10),)*4),plot_bounds=(lo-25,hi+30))
    b.paintEvent(None)
    lines=[c for c in b.painter.commands if c[0]=='line']
    red=[c for c in lines if c[1]=='#ff6666']
    assert len(red)==2
    legal=[c for c in lines if c[1]=='#7891a5']
    assert len(legal)==2
    left,right=[c[3][0].x() for c in legal]
    assert red[0][3][0].x()<left and red[1][3][0].x()>right
    guides=[c for c in lines if c[1]=='#3c5061']
    assert len(guides)==int((hi-lo)/50)+1
    blue=[c for c in lines if c[1]=='#78c9ed'];gold=[c for c in lines if c[1]=='#ffd17b']
    assert len(blue)==5 and len(gold)==5
    assert 'minimum request' in b.tooltip and 'outside permitted range' in b.tooltip


def test_current_request_warning_still_colors_permitted_bar_red():
    b=bars();b.update_values((750.,)*6,600,800,requested=(590,750,750,750,750,750),
        request_oob=(True,False,False,False,False,False),final_clipped=(False,)*6)
    b.paintEvent(None)
    assert any(c[0]=='rect' and c[1]=='#f36b6b' for c in b.painter.commands)
    assert 'request 590.0 mm (permitted 750.0 mm)' in b.tooltip
