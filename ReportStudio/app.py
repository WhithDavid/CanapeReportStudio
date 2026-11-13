"""CANape Report Studio v0.1 — native offline export frontend."""
import json
import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, simpledialog
from pathlib import Path
import model


class Studio:
    def __init__(self, root):
        self.root=root
        root.title('CANape 标定报告出图助手 v0.1 · 预览版')
        root.geometry('1320x900'); root.minsize(1100,760)
        self.queue=queue.Queue();self.busy=False;self.channels=[];self.signals=[]
        self.last_case=None;self.last_output=None;self.current_run=None;self.image_ref=None
        self.images={};self.widgets=[]
        self.active_preset=None;self.saved_preset=None
        self.vars={key:tk.StringVar(root,value=value) for key,value in model.DEFAULTS.items()}
        self.search=tk.StringVar();self.preset=tk.StringVar();self.status=tk.StringVar(value='就绪 · 尚未连接CANape')
        style=ttk.Style(root);style.theme_use('clam')
        style.configure('.',font=('Microsoft YaHei UI',10))
        style.configure('Title.TLabel',font=('Microsoft YaHei UI',18,'bold'))
        style.configure('Hint.TLabel',foreground='#546579')
        shell=ttk.Frame(root,padding=16);shell.pack(fill='both',expand=True)
        ttk.Label(shell,text='标定报告出图助手',style='Title.TLabel').pack(anchor='w')
        ttk.Label(shell,text='离线 MF4 → 原生 CANape 图片    公共轴 · 黑底 · 无曲线编号    不启动测量 / 不写ECU',style='Hint.TLabel').pack(anchor='w',pady=(3,12))
        self.tabs=ttk.Notebook(shell);self.tabs.pack(fill='both',expand=True)
        setup=ttk.Frame(self.tabs,padding=12);result=ttk.Frame(self.tabs,padding=12)
        self.tabs.add(setup,text='  工况与图片配置  ');self.tabs.add(result,text='  图片与运行日志  ')
        self.setup=setup;self.result=result
        setup.columnconfigure(0,weight=1);setup.columnconfigure(1,weight=2);setup.rowconfigure(0,weight=1)
        left=ttk.Frame(setup);left.grid(row=0,column=0,sticky='nsew',padx=(0,14))
        right=ttk.Frame(setup);right.grid(row=0,column=1,sticky='nsew')
        self.build_left(left);self.build_signals(right);self.build_results(result)
        saving=ttk.Frame(self.tabs,padding=16);self.tabs.add(saving,text='  图片保存设置  ')
        ttk.Label(saving,text='最终图片保存目录（任务日志仍保留在工作目录）').pack(anchor='w')
        row=ttk.Frame(saving);row.pack(fill='x',pady=8)
        ttk.Entry(row,textvariable=self.vars['save_directory']).pack(side='left',fill='x',expand=True)
        self.button(row,'选择目录',self.choose_save_directory).pack(side='left',padx=8)
        self.filename_preview=tk.StringVar()
        ttk.Label(saving,text='文件名自动按车门、温度类别、实际温度、坡向、坡度生成：').pack(anchor='w',pady=8)
        ttk.Label(saving,textvariable=self.filename_preview).pack(anchor='w')
        ttk.Label(saving,text='未知条件标记待确认；同名自动添加_001等序号，不覆盖。目录设置随模板保存。').pack(anchor='w',pady=12)
        for key in ('door','temperature_label','temperature_value','slope_direction','slope_value','slope_unit'):
            self.vars[key].trace_add('write',lambda *_:self.filename_preview.set(model.condition_filename(self.collect())))
        bar=ttk.Frame(shell);bar.pack(fill='x',pady=(0,10),before=self.tabs)
        self.action_bar=bar
        self.button(bar,'离线波形预览 / 选段',self.open_wave_preview).pack(side='left',padx=(0,8))
        self.button(bar,'检查并生成任务',lambda:self.execute(False)).pack(side='left')
        self.button(bar,'连接CANape并导出',lambda:self.execute(True)).pack(side='left',padx=8)
        self.button(bar,'打开本次目录',self.open_run).pack(side='left')
        ttk.Label(bar,textvariable=self.status,style='Hint.TLabel').pack(side='right')
        self.set_settings(model.default_settings())
        self.refresh_presets();self.search.trace_add('write',lambda *_:self.filter_channels())
        for key in model.PRESET_KEYS:
            if key in self.vars:self.vars[key].trace_add('write',lambda *_:self.update_notice())
        root.protocol('WM_DELETE_WINDOW',self.close);root.after(100,self.poll)

    def button(self,parent,text,command):
        widget=ttk.Button(parent,text=text,command=command);self.widgets.append(widget);return widget

    def field(self,parent,row,label,key,values=None):
        ttk.Label(parent,text=label).grid(row=row,column=0,sticky='w',padx=6,pady=5)
        if values:
            widget=ttk.Combobox(parent,textvariable=self.vars[key],values=values,state='readonly',width=24)
        else:
            widget=ttk.Entry(parent,textvariable=self.vars[key],width=26)
        widget.grid(row=row,column=1,sticky='ew',padx=6,pady=5)
        return widget

    def build_left(self,parent):
        source=ttk.LabelFrame(parent,text='1  数据与工程',padding=8);source.pack(fill='x')
        for key,label,command in [('mf4','MF4 文件',self.choose_mf4),('project','CANape 工程目录',self.choose_project)]:
            ttk.Label(source,text=label).pack(anchor='w')
            row=ttk.Frame(source);row.pack(fill='x',pady=(2,7))
            ttk.Entry(row,textvariable=self.vars[key]).pack(side='left',fill='x',expand=True)
            self.button(row,'选择',command).pack(side='right',padx=(5,0))
        self.button(source,'读取此文件的观测量（不连接CANape）',self.scan).pack(fill='x')
        condition=ttk.LabelFrame(parent,text='2  工况标签（未知可留空，仅预览）',padding=5);condition.pack(fill='x',pady=10)
        condition.columnconfigure(1,weight=1)
        self.field(condition,0,'车门','door',['DPOD','PPOD','LPOD','RPOD'])
        self.field(condition,1,'温度类别','temperature_label',['待确认','常温','低温','高温'])
        self.field(condition,2,'实际温度 / ℃','temperature_value')
        self.field(condition,3,'坡向','slope_direction',['待确认','平地','上坡','下坡','左倾','右倾'])
        self.field(condition,4,'坡度数值','slope_value')
        self.field(condition,5,'坡度单位','slope_unit',['待确认','percent','degree'])
        ttk.Label(condition,text='percent = %    degree = °；不自动换算匹配',style='Hint.TLabel').grid(row=6,column=0,columnspan=2,pady=5)
        view=ttk.LabelFrame(parent,text='3  重点时间与版式',padding=5);view.pack(fill='x')
        view.columnconfigure(1,weight=1)
        self.field(view,0,'开始 / 秒','start');self.field(view,1,'结束 / 秒','end')
        self.field(view,2,'测量线 / 秒','cursor')
        self.field(view,3,'图片比例','ratio',list(model.RATIOS))
        self.field(view,4,'图例宽度 / px','legend_width')
        ttk.Label(view,text='所有信号共用Y轴；范围按选段数据计算。',style='Hint.TLabel').grid(row=5,column=0,columnspan=2,pady=5)

    def build_signals(self,parent):
        parent.columnconfigure(0,weight=1);parent.rowconfigure(5,weight=1)
        self.notice=tk.StringVar()
        ttk.Label(parent,textvariable=self.notice,foreground='#9a5b00',wraplength=670).grid(row=1,column=0,sticky='w',pady=8)
        search=ttk.Frame(parent);search.grid(row=4,column=0,sticky='ew',pady=4)
        self.signal_search_bar=search
        ttk.Label(search,text='搜索文件内观测量').pack(side='left')
        ttk.Entry(search,textvariable=self.search).pack(side='left',fill='x',expand=True,padx=8)
        boxes=ttk.Frame(parent);boxes.grid(row=5,column=0,sticky='nsew')
        self.signal_boxes=boxes
        boxes.columnconfigure(0,weight=1);boxes.columnconfigure(2,weight=1);boxes.rowconfigure(1,weight=1)
        ttk.Label(boxes,text='可用观测量（可多选）').grid(row=0,column=0,sticky='w')
        ttk.Label(boxes,text='已选观测量（即图例顺序）').grid(row=0,column=2,sticky='w')
        self.available=self.listbox(boxes,1,0)
        self.selected=self.listbox(boxes,1,2)
        middle=ttk.Frame(boxes,padding=6);middle.grid(row=1,column=1,sticky='ns')
        for text,command in [('添加 →',self.add_signals),('← 移除',self.remove_signals),('上移',lambda:self.move(-1)),('下移',lambda:self.move(1)),('清空',self.clear_signals)]:
            self.button(middle,text,command).pack(fill='x',pady=5)
        self.available.bind('<Double-1>',lambda _:self.add_signals())
        templates=ttk.LabelFrame(parent,text='配置模板',padding=8)
        templates.grid(row=2,column=0,sticky='ew',pady=(0,4))
        self.template_panel=templates
        self.preset_combo=ttk.Combobox(templates,textvariable=self.preset,width=26,state='readonly')
        self.preset_combo.pack(fill='x')
        actions=ttk.Frame(templates);actions.pack(fill='x',pady=(6,0))
        for title,command in [('载入',self.load_preset),('另存为新模板',self.save_preset),('更新当前模板',self.update_preset),('管理模板',self.manage_presets)]:
            self.button(actions,title,command).pack(side='left',padx=3)
        ttk.Label(parent,text='保存信号、工况、版式及目录；不保存MF4路径、时间段、测量线。',
                  style='Hint.TLabel',wraplength=670).grid(row=3,column=0,sticky='w',pady=4)

    def listbox(self,parent,row,column):
        frame=ttk.Frame(parent);frame.grid(row=row,column=column,sticky='nsew')
        box=tk.Listbox(frame,selectmode='extended',exportselection=False,font=('Consolas',10),width=28,
                       relief='solid',borderwidth=1,selectbackground='#2875ad')
        sy=ttk.Scrollbar(frame,orient='vertical',command=box.yview);sx=ttk.Scrollbar(frame,orient='horizontal',command=box.xview)
        box.configure(yscrollcommand=sy.set,xscrollcommand=sx.set)
        sy.pack(side='right',fill='y');sx.pack(side='bottom',fill='x');box.pack(fill='both',expand=True)
        return box

    def build_results(self,parent):
        parent.rowconfigure(1,weight=3);parent.rowconfigure(2,weight=1);parent.columnconfigure(0,weight=1)
        line=ttk.Frame(parent);line.grid(row=0,column=0,sticky='ew',pady=(0,8))
        self.image_choice=tk.StringVar()
        self.image_combo=ttk.Combobox(line,textvariable=self.image_choice,state='readonly',width=45)
        self.image_combo.pack(side='left');self.image_combo.bind('<<ComboboxSelected>>',lambda _:self.preview_image())
        self.button(line,'打开原图',self.open_image).pack(side='left',padx=8)
        self.preview=ttk.Label(parent,text='导出完成后在这里显示原生图片',anchor='center')
        self.preview.grid(row=1,column=0,sticky='nsew')
        self.log=tk.Text(parent,height=10,font=('Consolas',10),wrap='word',state='disabled')
        self.log.grid(row=2,column=0,sticky='nsew',pady=(10,0))

    def collect(self):
        return {**{k:v.get() for k,v in self.vars.items()},'signals':list(self.signals)}

    def choose_save_directory(self):
        path=filedialog.askdirectory(title='选择最终图片保存目录',initialdir=self.vars['save_directory'].get() or str(model.APP))
        if path:self.vars['save_directory'].set(path)

    def open_wave_preview(self):
        if self.busy:return
        from wave_preview import read_waveforms
        path=self.vars['mf4'].get();names=list(self.signals)
        self.start_worker(lambda:('wave',(path,names,read_waveforms(path,names))), '离线读取所选波形…')

    def apply_wave_selection(self,path,names,start,end,cursor):
        if self.busy or path!=self.vars['mf4'].get() or names!=self.signals:
            messagebox.showerror('配置已变化','文件或观测量已改变，或任务正在执行，请重新预览后选段。');return
        for key,value in [('start',start),('end',end),('cursor',cursor)]:
            self.vars[key].set(format(value,'.12g'))
        self.status.set('预览选段已回填；可检查生成任务')

    def set_settings(self,settings):
        for key,var in self.vars.items():
            var.set(settings.get(key,model.DEFAULTS[key]))
        self.signals=list(settings.get('signals',[]));self.render_selected();self.update_notice()

    def update_notice(self):
        changed=model.preset_settings(self.collect())!=self.saved_preset
        self.notice.set(('当前模板：'+self.active_preset if self.active_preset else '尚未载入模板')+(' · 未保存' if changed else ' · 已保存'))

    def choose_mf4(self):
        path=filedialog.askopenfilename(title='选择离线MF4',filetypes=[('MF4','*.mf4'),('All files','*.*')])
        if path:self.vars['mf4'].set(path);self.scan()

    def choose_project(self):
        path=filedialog.askdirectory(title='选择CANape工程目录',initialdir=self.vars['project'].get())
        if path:self.vars['project'].set(path)

    def scan(self):
        if self.busy:return
        path=self.vars['mf4'].get()
        self.start_worker(lambda:('channels',model.load_channels(path)),'读取MF4通道中…')

    def filter_channels(self):
        query=self.search.get().casefold()
        self.available.delete(0,'end')
        for name in self.channels:
            if query in name.casefold():self.available.insert('end',name)

    def render_selected(self):
        self.selected.delete(0,'end')
        for name in self.signals:self.selected.insert('end',name)
        self.update_notice()

    def add_signals(self):
        if self.busy:return
        for index in self.available.curselection():
            name=self.available.get(index)
            if name not in self.signals:self.signals.append(name)
        self.render_selected()

    def remove_signals(self):
        if self.busy:return
        for index in reversed(self.selected.curselection()):self.signals.pop(index)
        self.render_selected()

    def move(self,direction):
        if self.busy:return
        chosen=self.selected.curselection()
        if len(chosen)!=1:return
        index=chosen[0];target=index+direction
        if 0<=target<len(self.signals):
            self.signals[index],self.signals[target]=self.signals[target],self.signals[index]
            self.render_selected();self.selected.selection_set(target)

    def clear_signals(self):
        if not self.busy:self.signals=[];self.render_selected()

    def refresh_presets(self):
        folder=model.APP/'presets';folder.mkdir(exist_ok=True)
        self.preset_combo.configure(values=[p.stem for p in sorted(folder.glob('*.json'))])

    def save_preset(self):
        try:
            name=simpledialog.askstring('另存为新模板','模板名称',initialvalue=Path(model.condition_filename(self.collect())).stem.replace('.','-'))
            if not name:return
            if model.preset_path(name).exists():raise ValueError('模板已存在，请换名称或使用更新当前模板')
            path=model.save_preset(name,self.collect());self.refresh_presets();self.status.set('模板已保存：'+path.stem)
            self.active_preset=name;self.preset.set(name);self.saved_preset=model.preset_settings(self.collect());self.update_notice()
        except Exception as error:messagebox.showerror('不能保存',str(error))

    def update_preset(self):
        if not self.active_preset:messagebox.showinfo('未载入模板','请先载入或另存为新模板');return
        if not messagebox.askyesno('更新模板','覆盖模板“'+self.active_preset+'”的保存设置？'):return
        try:
            model.save_preset(self.active_preset,self.collect())
            self.saved_preset=model.preset_settings(self.collect());self.update_notice()
        except Exception as error:messagebox.showerror('不能更新',str(error))

    def manage_presets(self):
        from preset_manager import show_manager
        show_manager(self.root,self)

    def load_preset(self):
        try:
            name=self.preset.get()
            if name not in [p.stem for p in (model.APP/'presets').glob('*.json')]:raise ValueError('请选择已有模板')
            if model.preset_settings(self.collect())!=self.saved_preset and not messagebox.askyesno('载入模板','替换当前未保存的配置？MF4和时间保持不变。'):return
            self.set_settings({**self.collect(),**model.load_preset(model.preset_path(name))})
            self.active_preset=name;self.saved_preset=model.preset_settings(self.collect());self.update_notice()
            missing=[n for n in self.signals if self.channels and n not in self.channels]
            self.status.set('模板已载入；MF4和时间保持不变')
            if missing:messagebox.showwarning('缺少观测量','当前已读文件缺少：\n'+'\n'.join(missing))
        except Exception as error:messagebox.showerror('不能载入',str(error))

    def start_worker(self,work,status):
        if self.busy:return
        self.busy=True;self.status.set(status)
        for widget in self.widgets:widget.configure(state='disabled')
        def worker():
            try:self.queue.put(('done',work()))
            except Exception as error:self.queue.put(('error',str(error)))
        threading.Thread(target=worker,daemon=True).start()

    def execute(self,export):
        if self.busy:return
        if export and not self.vars['save_directory'].get():
            self.choose_save_directory()
            if not self.vars['save_directory'].get():return
        destination=self.vars['save_directory'].get()
        filename=model.condition_filename(self.collect())
        if export and not Path(destination).is_dir():
            messagebox.showerror('保存目录无效','请在图片保存设置中选择存在的目录');return
        try:case=model.build_case(self.collect())
        except Exception as error:messagebox.showerror('请检查设置',str(error));return
        if export and not messagebox.askyesno('原生离线导出',
            '请确认CANape已打开对应工程、ASAP3 Active已开启、测量停止。\n\n将替换FILE1离线文件并创建图窗，不启动测量，不写ECU。\n这是预览图，不自动填入正式报告。\n\n继续？'):return
        reuse=export and self.last_case==case and self.last_output is not None and (self.last_output/'manifest.json').is_file()
        try:
            if reuse:
                folder=self.last_output.parent;output=self.last_output
                args=['run',str(output/'manifest.json')]
            else:
                folder,case_path,output=model.prepare_run(case)
                args=['all' if export else 'prepare',str(case_path),str(output)]
        except Exception as error:
            messagebox.showerror('无法创建任务目录',str(error));return
        # Keep failed execution state too: retry must consult the backend's
        # initialization lock instead of creating a fresh toggle sequence.
        self.last_case=case;self.last_output=output
        self.current_run=folder;self.tabs.select(self.result)
        def work():
            python=Path(sys.executable).with_name('python.exe') if sys.executable.lower().endswith('pythonw.exe') else Path(sys.executable)
            command=[str(python),'-X','utf8','-u',str(model.BACKEND/'workflow.py'),*args]
            with (folder/'tool.log').open('a',encoding='utf-8') as log:
                process=subprocess.Popen(command,cwd=model.BACKEND,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,
                    text=True,encoding='utf-8',errors='replace',creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
                for line in process.stdout:
                    log.write(line);log.flush();self.queue.put(('log',line))
                rc=process.wait()
            return ('job',{'rc':rc,'output':str(output),'case':case,'export':export,
                          'destination':destination,'filename':filename})
        self.start_worker(work,'运行中…请勿关闭CANape或切换设置窗口')

    def append_log(self,text):
        self.log.configure(state='normal');self.log.insert('end',text);self.log.see('end');self.log.configure(state='disabled')

    def poll(self):
        try:
            while True:
                kind,payload=self.queue.get_nowait()
                if kind=='log':self.append_log(payload);continue
                self.busy=False
                for widget in self.widgets:widget.configure(state='normal')
                if kind=='error':
                    self.status.set('失败，请查看提示');self.append_log(payload+'\n');messagebox.showerror('运行失败',payload);continue
                action,data=payload
                if action=='wave':
                    from wave_preview import WavePreview
                    path,names,(rows,bounds)=data
                    viewer=WavePreview(self.root,rows,bounds,lambda a,b,c:self.apply_wave_selection(path,names,a,b,c))
                    viewer.mode.set(self.vars['preview_mode'].get());viewer.draw()
                    viewer.mode.trace_add('write',lambda *_,v=viewer:self.vars['preview_mode'].set(v.mode.get()))
                    self.status.set('离线预览已打开；不需要ASAP3')
                elif action=='channels':
                    self.channels=data;self.filter_channels()
                    missing=[n for n in self.signals if n not in self.channels]
                    self.status.set(f'读取到{len(data)}个通道；当前已选{len(self.signals)}个，缺失{len(missing)}个')
                    if missing:self.append_log('当前所选但文件中缺失：\n'+'\n'.join(missing)+'\n')
                elif action=='job':
                    if data['rc']!=0:
                        self.status.set('失败；详见日志。接口超时请检查ASAP3 Active。')
                        messagebox.showerror('未完成','请查看日志。若为连接超时，确认ASAP3 Active、关闭设置弹窗后再试。\n若提示初始化状态不确定，请保留日志，不盲目重试。')
                    else:
                        self.last_case=data['case'];self.last_output=Path(data['output'])
                        self.status.set('导出完成，仍需检查图片' if data['export'] else '检查通过，任务已生成；可点击导出')
                        if data['export']:
                            self.load_results(self.last_output)
                            try:
                                if not self.images:raise ValueError('本次任务没有通过校验的图片')
                                for key,source in list(self.images.items()):
                                    saved=model.save_image_copy(source,data['destination'],data['filename'])
                                    self.images[key]=str(saved)
                                    self.append_log('图片已保存：'+str(saved)+'\n')
                                self.preview_image()
                                self.status.set('图片已保存至所选目录；仍需检查图片')
                            except Exception as error:
                                self.status.set('原图已导出，另存失败；原图仍在任务目录')
                                messagebox.showerror('图片另存失败',str(error)+'\n原图保留在本次任务目录中。')
        except queue.Empty:pass
        self.root.after(100,self.poll)

    def load_results(self,output):
        self.images={}
        path=output/'summary.json'
        if not path.is_file():return
        rows=json.loads(path.read_text(encoding='utf-8'))
        self.images={r['id']:r['image'] for r in rows if r.get('image') and r.get('verification',{}).get('ok')}
        self.image_combo.configure(values=list(self.images))
        if self.images:self.image_choice.set(next(iter(self.images)));self.preview_image()

    def preview_image(self):
        path=self.images.get(self.image_choice.get())
        if not path:return
        from PIL import Image, ImageTk
        with Image.open(path) as source:
            image=source.copy()
        self.root.update_idletasks()
        image.thumbnail((max(500,self.preview.winfo_width()),max(250,self.preview.winfo_height())))
        self.image_ref=ImageTk.PhotoImage(image)
        self.preview.configure(image=self.image_ref,text='')

    def open_image(self):
        path=self.images.get(self.image_choice.get())
        if path and Path(path).is_file():os.startfile(path)

    def open_run(self):
        if self.current_run and self.current_run.is_dir():os.startfile(self.current_run)
        else:messagebox.showinfo('尚无结果','先检查生成任务或导出图片。')

    def close(self):
        if self.busy:
            messagebox.showinfo('任务运行中','请等待任务结束再关闭，避免留下不确定的CANape执行状态。');return
        self.root.destroy()


if __name__=='__main__':
    # Avoid Windows bitmap stretching the whole window beyond the screen at 150%.
    if os.name=='nt':
        import ctypes
        try:ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except (AttributeError,OSError):pass
    root=tk.Tk()
    root.tk.call('tk','scaling',1.333333)
    app=Studio(root)
    if '--smoke-test' in sys.argv:
        root.update();assert len(app.signals)==14
        assert root.winfo_width()>=1100
        for size in ('1320x900','1100x760'):
            root.geometry(size);root.update()
            assert app.action_bar.winfo_ismapped()
            assert app.action_bar.winfo_rooty()+app.action_bar.winfo_height()<root.winfo_rooty()+root.winfo_height()
            assert app.action_bar.winfo_rooty()<app.tabs.winfo_rooty()
            assert app.template_panel.winfo_rooty()<app.signal_search_bar.winfo_rooty()<app.signal_boxes.winfo_rooty()
        root.destroy();print('GUI_SMOKE_OK')
    else:root.mainloop()
