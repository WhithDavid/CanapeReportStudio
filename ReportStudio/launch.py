"""Desktop launcher with persistent startup/callback diagnostics."""
import ctypes
import runpy
import sys
import traceback
from pathlib import Path

ROOT=Path(__file__).resolve().parent


def report_error(exc_type, value, tb):
    folder=ROOT/'logs'
    folder.mkdir(exist_ok=True)
    path=folder/'application_errors.log'
    with path.open('a',encoding='utf-8') as log:
        traceback.print_exception(exc_type,value,tb,file=log)
    ctypes.windll.user32.MessageBoxW(None,
        f'程序遇到错误：{value}\n\n详情已保存到：\n{path}',
        'CANape 出图助手',0x10)


if __name__=='__main__':
    try:
        import tkinter
        tkinter.Tk.report_callback_exception=lambda self,*args:report_error(*args)
        runpy.run_path(str(ROOT/'app.py'),run_name='__main__')
    except Exception:
        report_error(*sys.exc_info())
