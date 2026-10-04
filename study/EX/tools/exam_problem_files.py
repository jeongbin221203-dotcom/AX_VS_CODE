"""사용자 정의 폼·ActiveX 단추가 있는 모의고사(1급)의 문제 파일을 Excel(COM)로 만든다.

    python tools/exam_problem_files.py          # forms·commands 가 있는 시험 모두 → content/exams/files/<id>.xlsm

openpyxl 로는 폼(UserForm)을 만들 수 없어, problem_workbook() 으로 만든 시트에 Excel 이 폼·단추를 더해 .xlsm 으로 저장한다.
폼에는 컨트롤만 있고 코드는 없다(수험자가 작성). 시험 정의(JSON)를 바꾸면 다시 실행할 것(tests 가 내용이 같은지 확인).
Excel 보안 센터의 'VBA 프로젝트 개체 모델에 안전하게 액세스'가 켜져 있어야 한다.
"""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import exam as ex  # noqa: E402


def build(exam, xl):
    tmp = Path(tempfile.mkdtemp(prefix='exam-file-'))
    src = tmp / 'problem.xlsx'
    src.write_bytes(ex.problem_workbook(exam))
    wb = xl.Workbooks.Open(str(src))
    try:
        for f in exam.get('forms', []):
            comp = wb.VBProject.VBComponents.Add(3)          # 3 = 사용자 정의 폼
            comp.Name = f['name']
            comp.Properties('Caption').Value = f.get('caption', f['name'])
            comp.Properties('Width').Value = f.get('width', 260)
            comp.Properties('Height').Value = f.get('height', 200)
            for c in f['controls']:
                ctl = comp.Designer.Controls.Add(f"Forms.{c['type']}.1", c['name'])
                ctl.Left, ctl.Top, ctl.Width, ctl.Height = c['left'], c['top'], c['width'], c['height']
                if c.get('caption'):
                    ctl.Caption = c['caption']
        for c in exam.get('commands', []):
            ws = wb.Worksheets(c['sheet'])
            rng = ws.Range(c['range'])
            ole = ws.OLEObjects().Add('Forms.CommandButton.1', None, False, False, None, None, None,
                                      rng.Left, rng.Top, rng.Width, rng.Height)
            ole.Name = c['name']
            ole.Object.Caption = c['caption']
        wb.Worksheets(1).Activate()
        out = ex.FILES / f"{exam['id']}.xlsm"
        out.parent.mkdir(parents=True, exist_ok=True)
        if out.exists():
            out.unlink()
        wb.SaveAs(str(out), 52)
        return out
    finally:
        wb.Close(False)


def main():
    import win32com.client as w32
    todo = [e for e in ex.exams() if e.get('forms') or e.get('commands')]
    xl = w32.DispatchEx('Excel.Application')
    xl.Visible = False
    xl.DisplayAlerts = False
    try:
        for e in todo:
            print(e['id'], '→', build(e, xl))
    finally:
        xl.Quit()
    return 0


if __name__ == '__main__':
    os.environ.setdefault('PYTHONIOENCODING', 'utf-8')
    sys.exit(main())
