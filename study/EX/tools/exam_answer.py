"""모의고사 정답 파일을 실제 Excel(COM)로 만들어 채점해 보는 검증 도구 (이 PC 에 Excel 과 pywin32 필요).

    python tools/exam_answer.py c2-01            # 정답 파일 만들고 채점 결과 출력
    python tools/exam_answer.py c2-01 --keep out.xlsx

각 과제의 "answer" 단계(op)를 Excel 에서 그대로 실행한다. 매크로·VBA(vba 단계)는 Excel 보안 센터의
'VBA 프로젝트 개체 모델에 안전하게 액세스'(HKCU ...\\Excel\\Security\\AccessVBOM=1)가 켜져 있어야 만들 수 있고,
꺼져 있으면 needs_vba 항목을 채점에서 빼고 보고한다(--no-vba 로도 뺄 수 있음).
"""
import argparse
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import exam as ex  # noqa: E402
from core import formula as fx  # noqa: E402

XL = {'center': -4108, 'left': -4131, 'right': -4152, 'top': -4160, 'bottom': -4107, 'general': 1,
      'centerContinuous': 7}
FUNC = {'sum': -4157, 'count': -4112, 'average': -4106, 'max': -4136, 'min': -4139, 'product': -4149}
PIVOT_ORIENT = {'rows': 1, 'cols': 2, 'filters': 3}
CHART = {'col': 51, 'bar': 57, 'line': 4, 'linem': 65, 'pie': 5, 'col_stacked': 52, 'bar_stacked': 58, 'area': 1,
         'doughnut': -4120, 'scatter': -4169, 'col3d': 54}
LEGEND = {'b': -4107, 't': -4160, 'r': -4152, 'l': -4131}
DV_TYPE = {'whole': 1, 'decimal': 2, 'list': 3, 'date': 4, 'time': 5, 'textLength': 6, 'custom': 7}
DV_OP = {'between': 1, 'notBetween': 2, 'equal': 3, 'notEqual': 4, 'greaterThan': 5, 'lessThan': 6,
         'greaterThanOrEqual': 7, 'lessThanOrEqual': 8}
DV_STYLE = {'stop': 1, 'warning': 2, 'information': 3}


def bgr(hexrgb):
    h = hexrgb.lstrip('#')
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return r + g * 256 + b * 65536


class Runner:
    def __init__(self, xl, wb, exam=None):
        self.xl, self.wb = xl, wb
        self.exam = exam
        self.skip_vba = False

    def ws(self, name):
        return self.wb.Worksheets(name)

    def run(self, op, sheet):
        sh = op.get('sheet', sheet)
        getattr(self, 'op_' + op['op'])(op, self.ws(sh) if sh else None)

    def op_values(self, op, ws):
        r1, c1, _, _ = fx.parse_range(op['range'])
        for i, row in enumerate(op['values']):
            for j, v in enumerate(row):
                cell = ws.Cells(r1 + i, c1 + j)
                if isinstance(v, str) and v.startswith('@'):
                    cell.Formula = '=DATEVALUE("' + v[1:] + '")'
                    cell.Value = cell.Value
                    cell.NumberFormat = 'yyyy-mm-dd'
                else:
                    cell.Value = v

    def op_formula(self, op, ws):
        if op.get('array'):
            ast = fx.parse(op['formula'])
            cells = ex._cells(op['range'])
            r0, c0 = cells[0]
            for r, c in cells:
                ws.Cells(r, c).FormulaArray = '=' + fx.unparse(fx.shift(ast, r - r0, c - c0))
        else:
            ws.Range(op['range']).Formula = op['formula']

    def op_style(self, op, ws):
        rng = ws.Range(op['range'])
        if op.get('merge'):
            rng.Merge()
        f = op.get('font') or {}
        if 'name' in f:
            rng.Font.Name = f['name']
        if 'size' in f:
            rng.Font.Size = f['size']
        if 'bold' in f:
            rng.Font.Bold = f['bold']
        if 'italic' in f:
            rng.Font.Italic = f['italic']
        if 'underline' in f:
            rng.Font.Underline = {'single': 2, 'double': -4119}[f['underline']]
        if 'color' in f:
            rng.Font.Color = bgr(f['color'])
        if 'fill' in op:
            rng.Interior.Color = bgr(op['fill'])
        if 'halign' in op:
            rng.HorizontalAlignment = XL[op['halign']]
        if 'valign' in op:
            rng.VerticalAlignment = XL[op['valign']]
        if 'wrap' in op:
            rng.WrapText = op['wrap']
        if 'numfmt' in op:
            rng.NumberFormat = op['numfmt']
        if op.get('border') == 'all':
            rng.Borders.LineStyle = 1
        if 'height' in op:
            rng.RowHeight = op['height']

    def op_comment(self, op, ws):
        c = ws.Range(op['cell'])
        if c.Comment is not None:
            c.Comment.Delete()
        cm = c.AddComment(op['text'])
        if op.get('autosize'):
            cm.Shape.TextFrame.AutoSize = True
        if op.get('visible'):
            cm.Visible = True

    def op_name(self, op, ws):
        self.wb.Names.Add(op['name'], '=' + op['ref'])

    def op_cf(self, op, ws):
        fc = ws.Range(op['range']).FormatConditions.Add(2, 0, op['formula'])
        if 'font_color' in op:
            fc.Font.Color = bgr(op['font_color'])
        if 'bold' in op:
            fc.Font.Bold = op['bold']
        if 'italic' in op:
            fc.Font.Italic = op['italic']
        if 'fill' in op:
            fc.Interior.Color = bgr(op['fill'])

    def op_dv(self, op, ws):
        v = ws.Range(op['range']).Validation
        v.Delete()
        v.Add(DV_TYPE[op['type']], DV_STYLE[op.get('style', 'stop')], DV_OP.get(op.get('operator', 'between'), 1),
              op.get('formula1'), op.get('formula2'))
        for key, attr in (('error_title', 'ErrorTitle'), ('error', 'ErrorMessage'), ('prompt_title', 'InputTitle'),
                          ('prompt', 'InputMessage')):
            if key in op:
                setattr(v, attr, op[key])

    def op_advfilter(self, op, ws):
        crit = op['criteria']
        r, c = fx.parse_addr(crit['at'])
        for i, row in enumerate(crit['rows']):
            for j, v in enumerate(row):
                if v is not None:
                    ws.Cells(r + i, c + j).Value = v
        cr = ws.Range(ws.Cells(r, c), ws.Cells(r + len(crit['rows']) - 1, c + len(crit['rows'][0]) - 1))
        out = ws.Range(op['out'])
        if op.get('columns'):
            orr, oc = fx.parse_addr(op['out'])
            for j, name in enumerate(op['columns']):
                ws.Cells(orr, oc + j).Value = name
            out = ws.Range(ws.Cells(orr, oc), ws.Cells(orr, oc + len(op['columns']) - 1))
        ws.Range(op['source']).AdvancedFilter(2, cr, out, False)

    def op_sort(self, op, ws):
        rng = ws.Range(op['range'])
        ws.Sort.SortFields.Clear()
        r1, c1, r2, _ = fx.parse_range(op['range'])
        heads = [ws.Cells(r1, c).Value for c in range(c1, c1 + rng.Columns.Count)]
        for name, order in op['keys']:
            col = c1 + heads.index(name)
            key = ws.Range(ws.Cells(r1 + 1, col), ws.Cells(r2, col))
            if isinstance(order, list):
                ws.Sort.SortFields.Add(key, 0, 1, ','.join(order))
            else:
                ws.Sort.SortFields.Add(key, 0, 2 if order == 'desc' else 1)
        ws.Sort.SetRange(rng)
        ws.Sort.Header = 1
        ws.Sort.Apply()

    def op_subtotal(self, op, ws):
        rng = ws.Range(op['range']).CurrentRegion if op.get('current_region') else ws.Range(op['range'])
        rng.Subtotal(op['group_col'], FUNC[op['func']], tuple(op['cols']), op.get('replace', True), False, True)

    def op_pivot(self, op, ws):
        dest = self.ws(op.get('dest_sheet', ws.Name)).Range(op['at'])
        if op.get('external_csv'):
            pt = self._external_pivot(op, dest)
        else:
            src = self.ws(op['source_sheet']).Range(op['source'])
            pc = self.wb.PivotCaches().Create(1, src)
            pt = pc.CreatePivotTable(dest, op.get('name', 'PT'))
            for area in ('filters', 'rows', 'cols'):
                for i, f in enumerate(op.get(area, []), 1):
                    pf = pt.PivotFields(f)
                    pf.Orientation = PIVOT_ORIENT[area]
                    pf.Position = i
            for f, func, *rest in op.get('values', []):
                df = pt.AddDataField(pt.PivotFields(f), rest[0] if rest else f'{f} {func}', FUNC[func])
                if op.get('numfmt'):
                    df.NumberFormat = op['numfmt']
        if op.get('layout') == 'tabular':
            pt.RowAxisLayout(1)
        elif op.get('layout') == 'outline':
            pt.RowAxisLayout(2)
        if op.get('no_grand_rows'):
            pt.RowGrand = False
        if op.get('no_grand_cols'):
            pt.ColumnGrand = False
        for g in op.get('group_dates', []):
            pt.PivotFields(g['field']).DataRange.Cells(1).Group(Start=True, End=True,
                                                               Periods=tuple(g['periods']))

    def _external_pivot(self, op, dest):
        """외부 데이터(csv) → 데이터 모델 피벗(시험의 [외부 데이터 원본 사용]과 같은 형태)."""
        data = ex.data_file(self.exam, op['external_csv'])
        path = Path(tempfile.mkdtemp(prefix='exam-csv-')) / op['external_csv']
        path.write_bytes(data)
        m = ('let Source = Csv.Document(File.Contents("' + str(path) + '"),[Delimiter=",", Encoding=65001, '
             'QuoteStyle=QuoteStyle.Csv]), H = Table.PromoteHeaders(Source, [PromoteAllScalars=true]), '
             'T = Table.TransformColumnTypes(H, {' + ', '.join('{"' + f + '", type number}' for f, *_ in op.get('values', [])) +
             '}) in T')
        name = Path(op['external_csv']).stem
        self.wb.Queries.Add(name, m)
        # 사용자 정의 함수(fn…)가 있는 통합 문서는 데이터 모델을 채우며 다시 계산하다 멈출 수 있어 잠시 수동 계산
        calc = self.xl.Calculation
        self.xl.Calculation = -4135
        try:
            self.wb.Connections.Add2(name, '', 'OLEDB;Provider=Microsoft.Mashup.OleDb.1;Data Source=$Workbook$;Location=' + name,
                                     'SELECT * FROM [' + name + ']', 2, True, False)
        finally:
            self.xl.Calculation = calc
        pc = self.wb.PivotCaches().Create(2, self.wb.Connections('ThisWorkbookDataModel'), 6)
        pt = pc.CreatePivotTable(dest, op.get('name', 'PT'))
        fields = {cf.Name.split('.')[-1].strip('[]'): cf.Name for cf in pt.CubeFields}
        for area in ('filters', 'rows', 'cols'):
            for f in op.get(area, []):
                pt.CubeFields(fields[f]).Orientation = PIVOT_ORIENT[area]
        for f, func, *rest in op.get('values', []):
            meas = pt.CubeFields.GetMeasure(fields[f], FUNC[func], rest[0] if rest else f'{f} {func}')
            pt.AddDataField(meas, rest[0] if rest else f'{f} {func}')
            if op.get('numfmt'):
                pt.DataFields(1).NumberFormat = op['numfmt']
        return pt

    def op_scenario(self, op, ws):
        for name, vals in op['scenarios']:
            ws.Scenarios().Add(name, ws.Range(op['changing']), tuple(vals))
        if op.get('result'):
            ws.Scenarios().CreateSummary(1, ws.Range(op['result']))

    def op_goalseek(self, op, ws):
        ws.Range(op['cell']).GoalSeek(op['value'], ws.Range(op['changing']))

    def op_datatable(self, op, ws):
        row_in = ws.Range(op['row_input']) if op.get('row_input') else None
        col_in = ws.Range(op['col_input']) if op.get('col_input') else None
        if op.get('corner'):
            r, c = fx.parse_addr(op['corner_at'])
            ws.Cells(r, c).Formula = op['corner']
        ws.Range(op['range']).Table(row_in, col_in)

    def op_consolidate(self, op, ws):
        def r1c1(ref):          # Consolidate 는 R1C1 참조만 받는다: 'S3'!$A$2:$B$4 → 'S3'!R2C1:R4C2
            sheet, rng = ref.rsplit('!', 1)
            r1, c1, r2, c2 = fx.parse_range(rng.replace('$', ''))
            return f'{sheet}!R{r1}C{c1}:R{r2}C{c2}'
        ws.Range(op['target']).Consolidate(tuple(r1c1(s) for s in op['sources']), FUNC[op['func']],
                                           op.get('top', True), op.get('left', True), False)

    def op_chart(self, op, ws):
        for i in range(ws.ChartObjects().Count, 0, -1):
            if op.get('replace', True):
                ws.ChartObjects(i).Delete()
        a = ws.Range(op['at'])
        co = ws.ChartObjects().Add(a.Left, a.Top, a.Width, a.Height)
        ch = co.Chart
        ch.SetSourceData(ws.Range(op['source']), 2 if op.get('by') == 'cols' else 2)
        ch.ChartType = CHART[op['type']]
        if op.get('title'):
            ch.HasTitle = True
            ch.ChartTitle.Text = op['title']
        names = [ch.SeriesCollection(i).Name for i in range(1, ch.SeriesCollection().Count + 1)]
        for n in op.get('line_series', []):
            ch.SeriesCollection(names.index(n) + 1).ChartType = CHART['linem' if op.get('markers') else 'line']
        for n in op.get('secondary', []):
            ch.SeriesCollection(names.index(n) + 1).AxisGroup = 2
        for n in op.get('labels', []):
            ch.SeriesCollection(names.index(n) + 1).HasDataLabels = True
        for n in op.get('trend', []):
            ch.SeriesCollection(names.index(n) + 1).Trendlines().Add(-4132)
        if op.get('y_title'):
            ax = ch.Axes(2, 1)
            ax.HasTitle = True
            ax.AxisTitle.Text = op['y_title']
        if op.get('x_title'):
            ax = ch.Axes(1, 1)
            ax.HasTitle = True
            ax.AxisTitle.Text = op['x_title']
        if 'legend' in op:
            if op['legend'] is None:
                ch.HasLegend = False
            else:
                ch.HasLegend = True
                ch.Legend.Position = LEGEND[op['legend']]

    def op_page(self, op, ws):
        ps = ws.PageSetup
        if 'orientation' in op:
            ps.Orientation = 2 if op['orientation'] == 'landscape' else 1
        if op.get('center_h'):
            ps.CenterHorizontally = True
        if op.get('center_v'):
            ps.CenterVertically = True
        if 'print_area' in op:
            ps.PrintArea = op['print_area']
        if 'title_rows' in op:
            ps.PrintTitleRows = op['title_rows']
        for key, attr in (('header_center', 'CenterHeader'), ('footer_center', 'CenterFooter'),
                          ('header_right', 'RightHeader'), ('footer_right', 'RightFooter')):
            if key in op:
                setattr(ps, attr, op[key])
        if 'fit_width' in op:
            ps.Zoom = False
            ps.FitToPagesWide = op['fit_width']
            ps.FitToPagesTall = False

    def op_protect(self, op, ws):
        if op.get('unlocked'):
            ws.Range(op['unlocked']).Locked = False
        if op.get('hidden'):
            ws.Range(op['hidden']).FormulaHidden = True
        ws.Protect(op.get('password', ''), True, True, True, False,
                   op.get('allow_format_cells', False))
        if op.get('allow_select_locked') is False:
            ws.EnableSelection = 1

    def op_vba(self, op, ws):
        """VBA 코드 넣기(표준 모듈 또는 시트 모듈) → ActiveX 명령 단추·양식 단추 → 매크로 실행."""
        if self.skip_vba:
            raise SkipVba()
        if op.get('test'):                       # 폼을 손으로 누르는 대신: 임시 모듈에서 값 넣고 단추 프로시저 실행
            ws.Activate()
            tmp = self.wb.VBProject.VBComponents.Add(1)
            tmp.CodeModule.AddFromString('Sub zz_answer_test()\n    ' + op['test'] + '\nEnd Sub')
            try:
                self.xl.Run(f"'{self.wb.Name}'!zz_answer_test")
            finally:
                self.wb.VBProject.VBComponents.Remove(tmp)
            return
        module = op.get('module', '')
        if module == 'sheet':
            comp = self.wb.VBProject.VBComponents(ws.CodeName)
        elif module.startswith('form:'):
            comp = self.wb.VBProject.VBComponents(module[5:])
        else:
            comp = self.wb.VBProject.VBComponents.Add(1)
        for c in op.get('commands', []):
            rng = ws.Range(c['range'])
            ole = ws.OLEObjects().Add('Forms.CommandButton.1', None, False, False, None, None, None,
                                      rng.Left, rng.Top, rng.Width, rng.Height)
            ole.Name = c['name']
            ole.Object.Caption = c['caption']
        comp.CodeModule.AddFromString(op['code'])
        for b in op.get('buttons', []):
            rng = ws.Range(b['range'])
            button = ws.Buttons().Add(rng.Left, rng.Top, rng.Width, rng.Height)
            button.Text = b['text']
            button.OnAction = b['macro']
        ws.Activate()
        for name in op.get('run', []):
            target = f"{ws.CodeName}.{name}" if op.get('module') == 'sheet' else name
            self.xl.Run(f"'{self.wb.Name}'!{target}")
        if op.get('activate'):                      # Worksheet_Activate 가 실행되도록 다른 시트에 갔다 오기
            other = next(s for s in self.wb.Worksheets if s.Name != ws.Name)
            other.Activate()
            ws.Activate()

    def op_textsplit(self, op, ws):
        ws.Range(op['range']).TextToColumns(ws.Range(op['range']).Cells(1), 1, 1, False, False, False,
                                            op.get('delimiter') == ',', op.get('delimiter') == ' ', True,
                                            op.get('delimiter') if op.get('delimiter') not in (',', ' ') else None)


class SkipVba(Exception):
    pass


def vba_allowed():
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r'Software\Microsoft\Office\16.0\Excel\Security') as k:
            return winreg.QueryValueEx(k, 'AccessVBOM')[0] == 1
    except OSError:
        return False


def build(exam, keep=None, skip_vba=False):
    import win32com.client as w32
    tmp = Path(tempfile.mkdtemp(prefix='exam-'))
    data, ext = ex.problem_file(exam)           # 폼이 있는 1급은 Excel 로 만든 .xlsm 문제 파일에서 시작
    src = tmp / f'problem.{ext}'
    src.write_bytes(data)
    has_vba = not skip_vba and any(op['op'] == 'vba' for t in exam['tasks'] for op in t.get('answer', []))
    out = Path(keep).resolve() if keep else tmp / ('answer.xlsm' if has_vba else 'answer.xlsx')
    xl = w32.DispatchEx('Excel.Application')
    xl.Visible = False
    xl.DisplayAlerts = False
    try:
        wb = xl.Workbooks.Open(str(src))
        run = Runner(xl, wb, exam)
        run.skip_vba = skip_vba
        # 외부 데이터 피벗을 먼저: 사용자 정의 함수 수식이 생긴 뒤에 데이터 모델을 채우면 Excel 이 멈춘다
        ext = [t for t in exam['tasks'] if any(op.get('external_csv') for op in t.get('answer', []))]
        for t in ext + [t for t in exam['tasks'] if t not in ext]:
            for op in t.get('answer', []):
                try:
                    run.run(op, t['sheet'])
                except SkipVba:
                    break                            # 이 과제의 나머지 단계(사용자 정의 함수 수식 등)도 건너뜀
                except Exception as e:  # noqa: BLE001 — 어느 단계에서 막혔는지 보고
                    raise RuntimeError(f"{t['no']} {op['op']}: {e}") from e
        wb.Worksheets(1).Activate()
        if out.exists():
            out.unlink()                         # 같은 이름이 있으면 SaveAs 가 덮어쓰기 확인 창에서 멈춘다
        wb.SaveAs(str(out), 52 if has_vba else 51)
        wb.Close(False)
    finally:
        xl.Quit()
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('exam')
    ap.add_argument('--keep')
    ap.add_argument('--no-vba', action='store_true', help='매크로·VBA 단계를 건너뛰고 needs_vba 항목은 빼고 채점')
    a = ap.parse_args()
    exam = ex.get(a.exam)
    if not exam:
        print('시험 없음:', a.exam)
        return 2
    errs = ex.validate(exam)
    if errs:
        print('정의 오류:', *errs, sep='\n - ')
        return 1
    skip = a.no_vba or not vba_allowed()
    out = build(exam, a.keep, skip_vba=skip)
    res = ex.grade(exam, out.read_bytes(), out.name)
    vba_pts, fails = 0, []
    for t in res['tasks']:
        src = next(x for x in exam['tasks'] if x['no'] == t['no'])
        for it, chk in zip(t['items'], src['checks']):
            if skip and chk.get('needs_vba'):
                vba_pts += chk['points']
                continue
            if not it['ok']:
                fails.append(f"{t['no']} {it['label']}: {'; '.join(it['msgs'])}")
    note = f" (매크로·VBA {vba_pts}점은 VBA 접근 설정이 꺼져 자동 확인 제외)" if skip else ' (매크로·VBA 포함)'
    print(f"{exam['id']}: {res['score']}/{res['total']}{note}")
    for f in fails:
        print(' X', f)
    if a.keep:
        print('정답 파일:', out)
    return 1 if fails else 0


if __name__ == '__main__':
    os.environ.setdefault('PYTHONIOENCODING', 'utf-8')
    sys.exit(main())
