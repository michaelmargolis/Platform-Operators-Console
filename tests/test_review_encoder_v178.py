# test_review_encoder_v178.py ver 1
"""Exercise real review/encoder handler bodies without requiring a Qt display."""
import ast
from pathlib import Path
from types import SimpleNamespace
import time
import pytest

ROOT=Path(__file__).resolve().parents[1]


def extract(file,cls_name,method,namespace=None):
    source=ROOT/file
    tree=ast.parse(source.read_text())
    cls=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name==cls_name)
    fn=next(n for n in cls.body if isinstance(n,ast.FunctionDef) and n.name==method)
    ns=namespace or {}
    exec(compile(ast.fix_missing_locations(ast.Module(body=[fn],type_ignores=[])),str(source),'exec'),ns)
    return ns[method]


def test_encoder_cycles_three_choices_in_both_directions_and_click_uses_current():
    rotate=extract('ui/variant_review.py','VariantReviewDialog','rotate')
    press=extract('ui/variant_review.py','VariantReviewDialog','press')
    selected=[];d=SimpleNamespace(choice=2,_highlight=lambda:None)
    d.buttons=[SimpleNamespace(click=lambda i=i:selected.append(i)) for i in range(3)]
    for direction,expected in [(1,0),(1,1),(1,2),(-1,1),(-1,0),(-1,2)]:
        rotate(d,direction);assert d.choice==expected
    press(d)
    assert selected==[2]


def test_window_routes_gpio_to_review_instead_of_changing_ride_or_intensity():
    events=[]
    dialog=SimpleNamespace(rotate=lambda step:events.append(('rotate',step)),press=lambda:events.append(('click',)))
    win=SimpleNamespace(_review_dialog=dialog,_ignore_encoder_press_until=0)
    rotate=extract('ui/main_window.py','MainWindow','_on_encoder_rotated')
    press=extract('ui/main_window.py','MainWindow','_on_encoder_pressed',{'time':time})
    rotate(win,1);rotate(win,-1);press(win)
    assert events==[('rotate',1),('rotate',-1),('click',)]


@pytest.mark.parametrize('answer,expected',[(0,[]),(1,[('Blackbird','preferred')]),(2,[('Blackbird','rejected')])])
def test_cancel_does_not_write_and_other_buttons_call_core_once(answer,expected):
    writes=[];deleted=[]
    dialog=SimpleNamespace(deleteLater=lambda:deleted.append(True))
    win=SimpleNamespace(_review_dialog=dialog,chk_review_mode=SimpleNamespace(isChecked=lambda:True),
        core=SimpleNamespace(review_variant=lambda *args:writes.append(args)),_sync_review_controls=lambda:None)
    fn=extract('ui/main_window.py','MainWindow','_variant_review_answered',
               {'time':time,'VariantReviewDialog':SimpleNamespace(PREFERRED=1,REJECTED=2)})
    fn(win,answer,dialog,'Blackbird')
    assert writes==expected and deleted==[True]
    assert win._review_dialog is None
    assert win._ignore_encoder_press_until>time.monotonic()


def test_runtime_review_checkbox_and_dialog_are_session_only_and_nonblocking():
    source=(ROOT/'ui/main_window.py').read_text()
    setup=source.split('def _setup_debug_controls',1)[1].split('def _confirm_exit_to_desktop',1)[0]
    assert 'QCheckBox("Review mode"' in setup
    assert 'setChecked(False)' in setup
    assert 'dialog.open()' in setup
    assert 'exec_()' not in setup
    dialog=(ROOT/'ui/variant_review.py').read_text()
    assert 'self.choice = 2' in dialog
    assert 'Select as preferred' in dialog and 'Reject this variant' in dialog and 'Cancel' in dialog
    assert "info['last_for_park']" in dialog
