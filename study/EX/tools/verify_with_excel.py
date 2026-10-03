"""문제 은행의 수식 문제를 실제 Excel 로 계산해, 이 앱 계산기의 기대 값과 같은지 확인한다(Excel·pywin32 필요).

    python tools/verify_with_excel.py            # 모든 수식 문제(정답 수식 + 다른 정답)
    python tools/verify_with_excel.py lookup     # 한 분류만

TODAY()/NOW() 를 쓰는 문제는 앱이 날짜를 2026-10-01 로 고정해 계산하므로 Excel 에서도 같은 날짜로 바꿔 넣어 비교한다.
"""
import datetime as dt
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import content  # noqa: E402
from core import formula as fx  # noqa: E402

XL_ERRORS = {-2146826281: '#DIV/0!', -2146826246: '#N/A', -2146826259: '#NAME?', -2146826288: '#NULL!',
             -2146826252: '#NUM!', -2146826265: '#REF!', -2146826273: '#VALUE!', -2146826215: '#SPILL!',
             -2146826277: '#CALC!'}
FIXED = 'DATE(2026,10,1)'
# Excel 2016/2019 에 없는 함수(이 PC 의 Excel 이 2019 계열이면 이런 수식은 건너뛴다)
NEW_FUNCS = {'XLOOKUP', 'XMATCH', 'FILTER', 'UNIQUE', 'SORT', 'SORTBY', 'SEQUENCE', 'LET', 'TEXTSPLIT', 'VSTACK',
             'HSTACK', 'RANDARRAY', 'LAMBDA'}


def excel_value(v):
    if isinstance(v, int) and v in XL_ERRORS:
        return fx.ERRORS.get(XL_ERRORS[v], fx.XLErr(XL_ERRORS[v]))
    if isinstance(v, dt.datetime):
        return fx.date_serial(v.replace(tzinfo=None))
    if isinstance(v, float) and v == int(v) and abs(v) < 1e15:
        return int(v)
    return v


def same(a, b):
    if isinstance(a, fx.XLErr) or isinstance(b, fx.XLErr):
        return isinstance(a, fx.XLErr) and isinstance(b, fx.XLErr) and a.code == b.code
    if a in (None, '') and b in (None, '', 0):
        return True
    if isinstance(a, str) or isinstance(b, str):
        return str(a) == str(b)
    if isinstance(a, bool) or isinstance(b, bool):
        return bool(a) == bool(b) and type(a) is type(b)
    try:
        return abs(float(a) - float(b)) <= 1e-7 * max(1, abs(float(a)), abs(float(b)))
    except (TypeError, ValueError):
        return a == b


def main():
    import pythoncom  # noqa: F401
    import win32com.client as w32
    cat = sys.argv[1] if len(sys.argv) > 1 else None
    probs = [p for p in content.problems(cat) if p['type'] == 'formula']
    xl = w32.DispatchEx('Excel.Application')
    xl.Visible = False
    xl.DisplayAlerts = False
    xl.ScreenUpdating = False
    bad, checked, skipped = [], 0, []
    global HAS_DYNAMIC
    HAS_DYNAMIC = int(xl.Build) >= 12000
    try:
        wb = xl.Workbooks.Add()
        ws = wb.Worksheets(1)
        for p in probs:
            cells, _ = content.sheet_spec(p)
            fills = content.fill_cells(p)
            variants = [p['answer']] + list(p.get('alts', []))
            exp = content.expected(p)
            for vi, text in enumerate(variants):
                if fx.functions_used(fx.parse(text)) & NEW_FUNCS and not HAS_DYNAMIC:
                    skipped.append(f"{p['id']} {text}")
                    continue
                ws.Cells.Clear()
                for (r, c), v in cells.items():
                    cell = ws.Cells(r, c)
                    if isinstance(v, str) and v.startswith('@'):
                        cell.Value = content._cell_value(v)
                    elif isinstance(v, str) and v.startswith('='):
                        cell.Formula = v
                    elif isinstance(v, str) and re.fullmatch(r'[-+]?\d[\d.,]*', v.strip()):
                        cell.NumberFormat = '@'
                        cell.Value = v
                    else:
                        cell.Value = v
                ws.Name = 'Sheet1'
                for n in list(wb.Names):
                    try:
                        n.Delete()
                    except Exception:  # noqa: BLE001 — 지울 수 없는 내장 이름은 둔다
                        pass
                for name, ref in ((p.get('sheet') or {}).get('names') or {}).items():
                    ref = ref.lstrip('=')
                    wb.Names.Add(name, '=' + (ref if '!' in ref else 'Sheet1!' + ref))
                ast = fx.parse(text)
                r0, c0 = fills[0]
                is_array = text.strip().startswith('{')
                for r, c in fills:
                    f = '=' + fx.unparse(fx.shift(ast, r - r0, c - c0))
                    f = re.sub(r'\bTODAY\(\)|\bNOW\(\)', FIXED, f, flags=re.I)
                    if is_array:
                        ws.Cells(r, c).FormulaArray = f
                    elif HAS_DYNAMIC:
                        ws.Cells(r, c).Formula2 = f
                    else:
                        ws.Cells(r, c).Formula = f
                xl.CalculateFull()
                for (r, c, ev, _), _cell in zip(exp, fills):
                    cell = ws.Cells(r, c)
                    if isinstance(ev, fx.Arr):
                        got = cell.SpillingToRange.Value if HAS_DYNAMIC and cell.HasSpill else cell.Value
                        rows = got if isinstance(got, tuple) else ((got,),)
                        ok = len(rows) == ev.h and all(same(excel_value(x), y) for gr, er in zip(rows, ev.rows)
                                                       for x, y in zip(gr, er))
                        shown = rows[:3]
                    else:
                        got = excel_value(cell.Value)
                        ok = same(got, ev)
                        shown = got
                    checked += 1
                    if not ok:
                        bad.append(f"{p['id']} {'정답' if vi == 0 else '다른 정답 ' + str(vi)} {fx.addr(r, c)}: "
                                   f"Excel={shown!r} 앱={fx.display(ev)!r}  수식 {text}")
                        break
        wb.Close(False)
    finally:
        build = xl.Build
        xl.Quit()
    print(f'수식 문제 {len(probs)}개, 비교한 칸 {checked}, 다른 곳 {len(bad)}, '
          f'이 Excel(빌드 {build})에 없는 함수라 건너뜀 {len(skipped)}')
    for b in bad:
        print(' X', b)
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
