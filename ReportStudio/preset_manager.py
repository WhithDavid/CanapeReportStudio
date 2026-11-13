"""Manage configuration templates; removal is recoverable archival."""
import json
import tkinter as tk
from tkinter import ttk,messagebox,simpledialog
import model

def show_manager(parent,studio):
    win=tk.Toplevel(parent);win.title('管理配置模板');win.geometry('750x550')
    win.transient(parent);win.grab_set()
    box=tk.Listbox(win,height=8);box.pack(fill='x',padx=10,pady=10)
    detail=tk.Text(win,height=15,wrap='word');detail.pack(fill='both',expand=True,padx=10)
    def refresh():
        box.delete(0,'end')
        for p in sorted((model.APP/'presets').glob('*.json')):box.insert('end',p.stem)
        studio.refresh_presets()
    def selected():
        if not box.curselection():raise ValueError('请先选择模板')
        return box.get(box.curselection()[0])
    def inspect(event=None):
        try:content=json.dumps(model.load_preset(model.preset_path(selected())),ensure_ascii=False,indent=2)
        except Exception as error:content=str(error)
        detail.configure(state='normal');detail.delete('1.0','end');detail.insert('end',content);detail.configure(state='disabled')
    def act(operation):
        try:
            old=selected();source=model.preset_path(old)
            if operation=='删除':
                if not messagebox.askyesno('删除模板','将“'+old+'”移入presets/archive，可恢复。',parent=win):return
                model.archive_preset(old)
                if studio.active_preset==old:studio.active_preset=None;studio.saved_preset=None
            else:
                name=simpledialog.askstring(operation,'新模板名称',initialvalue=old+' 副本' if operation=='复制' else old,parent=win)
                if not name or name==old:return
                target=model.preset_path(name)
                if target.exists():raise ValueError('同名模板已存在')
                if operation=='复制':model.save_preset(name,model.load_preset(source))
                else:
                    source.rename(target)
                    if studio.active_preset==old:studio.active_preset=name
                studio.preset.set(name)
            studio.update_notice();refresh()
        except Exception as error:messagebox.showerror('模板操作失败',str(error),parent=win)
    bar=ttk.Frame(win);bar.pack(fill='x',padx=10,pady=10)
    for op in ('重命名','复制','删除'):ttk.Button(bar,text=op,command=lambda o=op:act(o)).pack(side='left',padx=4)
    box.bind('<<ListboxSelect>>',inspect);refresh()
