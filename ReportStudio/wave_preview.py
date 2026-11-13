"""Offline numeric MF4 viewer; never connects to CANape."""
import tkinter as tk
from tkinter import ttk, messagebox
import numpy as np

COLORS=['#00e5ff','#ff595e','#ffe66d','#80ff72','#d890ff','#ffad55',
        '#65a5ff','#ff8bd1','#e0ffff','#b8bb26','#ae81ff','#a5e5a5']


def read_waveforms(path,names):
    from asammdf import MDF
    if not names:raise ValueError('请先选择观测量')
    rows=[]
    with MDF(path) as mdf:
        for name in names:
            if len(mdf.whereis(name))!=1:raise ValueError('信号缺失或重名：'+name)
            signal=mdf.get(name,raw=True).physical(ignore_value2text_conversions=True)
            t=np.asarray(signal.timestamps);y=np.asarray(signal.samples)
            if y.ndim!=1 or y.dtype.kind not in 'biuf':raise ValueError('暂不支持非标量数值信号：'+name)
            if not len(t) or not np.all(np.isfinite(t)) or np.any(np.diff(t)<0):
                raise ValueError('时间戳为空或无效：'+name)
            y=y.astype(float)
            if signal.invalidation_bits is not None:y[np.asarray(signal.invalidation_bits,dtype=bool)]=np.nan
            if not np.any(np.isfinite(y)):raise ValueError('无有效样本：'+name)
            rows.append((name,t,y))
    lo=max(r[1][0] for r in rows);hi=min(r[1][-1] for r in rows)
    if lo>=hi:raise ValueError('所选信号没有共同的有效时间范围')
    return rows,(float(lo),float(hi))


def envelope(t,y,lo,hi,bins=900):
    """Preserve ordered min/max per bucket, including extrema in narrow pulses."""
    a=max(0,int(np.searchsorted(t,lo))-1);b=min(len(t),int(np.searchsorted(t,hi,side='right'))+1)
    ids=np.arange(a,b)
    if len(ids)<=bins*2:return t[ids],y[ids]
    selected=[]
    for group in np.array_split(ids,bins):
        valid=group[np.isfinite(y[group])]
        points=[int(group[0]),int(group[-1])]
        if len(valid):points.extend([int(valid[np.argmin(y[valid])]),int(valid[np.argmax(y[valid])])])
        invalid=group[~np.isfinite(y[group])]
        if len(invalid):points.append(int(invalid[0]))
        selected.extend(sorted(set(points)))
    return t[selected],y[selected]


class WavePreview:
    def __init__(self,parent,rows,bounds,apply):
        self.rows=rows;self.bounds=bounds;self.view=list(bounds);self.apply=apply
        self.selection=list(bounds);self.cursor=sum(bounds)/2;self.anchor=None
        self.y_manual={};self.axis_ranges={};self.active_band=0
        self.win=tk.Toplevel(parent);self.win.title('MF4 离线预览 · 不连接CANape')
        self.win.geometry('1250x780');self.win.minsize(900,600)
        top=ttk.Frame(self.win,padding=8);top.pack(fill='x')
        self.mode=tk.StringVar(value='公共 Axis 1');self.tool=tk.StringVar(value='框选时间段')
        for var,values in [(self.mode,['公共 Axis 1','独立轴（上下分图）']),
                           (self.tool,['框选时间段','移动测量线','平移视图'])]:
            box=ttk.Combobox(top,textvariable=var,values=values,state='readonly',width=22)
            box.pack(side='left',padx=4);box.bind('<<ComboboxSelected>>',lambda e:self.draw())
        for label,callback in [('全范围',self.reset),('放大选段',self.focus),('应用到出图设置',self.commit)]:
            ttk.Button(top,text=label,command=callback).pack(side='left',padx=4)
        zoom_bar=ttk.Frame(self.win,padding=(8,0));zoom_bar.pack(fill='x')
        self.zoom_axis=tk.StringVar(value='X轴（时间）')
        ttk.Label(zoom_bar,text='滚轮缩放：').pack(side='left')
        ttk.Combobox(zoom_bar,textvariable=self.zoom_axis,values=['X轴（时间）','Y轴（幅值）'],state='readonly',width=16).pack(side='left')
        ttk.Button(zoom_bar,text='X放大',command=lambda:self.scale_x(.8)).pack(side='left',padx=4)
        ttk.Button(zoom_bar,text='X缩小',command=lambda:self.scale_x(1.25)).pack(side='left')
        ttk.Button(zoom_bar,text='Y放大',command=lambda:self.scale_y(.8)).pack(side='left',padx=4)
        ttk.Button(zoom_bar,text='Y缩小',command=lambda:self.scale_y(1.25)).pack(side='left')
        ttk.Button(zoom_bar,text='Y自适应',command=self.fit_y).pack(side='left',padx=4)
        self.info=tk.StringVar();ttk.Label(self.win,textvariable=self.info).pack(fill='x',padx=10)
        ttk.Label(self.win,text='拖底部时间轴平移X；拖左侧刻度平移Y；抓住青色线可拖动测量线。Ctrl+滚轮缩放Y，独立轴作用于鼠标所在分图。').pack(anchor='w',padx=10)
        body=ttk.Frame(self.win);body.pack(fill='both',expand=True)
        self.legend=tk.Listbox(body,width=46,font=('Consolas',10),bg='#18202b',fg='white')
        self.legend.pack(side='left',fill='y')
        self.canvas=tk.Canvas(body,bg='#070b0e',highlightthickness=0)
        self.canvas.pack(side='left',fill='both',expand=True)
        self.canvas.bind('<Configure>',lambda e:self.draw())
        self.canvas.bind('<ButtonPress-1>',self.press)
        self.canvas.bind('<ButtonRelease-1>',self.release)
        self.canvas.bind('<B1-Motion>',self.motion)
        self.canvas.bind('<Motion>',self.hover)
        self.canvas.bind('<MouseWheel>',self.zoom)
        self.draw()

    def reset(self):self.view=list(self.bounds);self.y_manual.clear();self.draw()
    def fit_y(self):self.y_manual.clear();self.draw()
    def focus(self):self.view=list(self.selection);self.draw()
    def time_at(self,x):
        return self.view[0]+np.clip((x-65)/max(1,self.canvas.winfo_width()-85),0,1)*(self.view[1]-self.view[0])
    def band_at(self,y):
        if not self.mode.get().startswith('独立'):return 0
        return int(np.clip((y-15)/max(1,self.canvas.winfo_height()-50)*len(self.rows),0,len(self.rows)-1))
    def axis_key(self,band):return ('independent',band) if self.mode.get().startswith('独立') else ('common',0)
    def hover(self,e):
        self.active_band=self.band_at(e.y)
        cursor_x=65+(self.cursor-self.view[0])/(self.view[1]-self.view[0])*max(1,self.canvas.winfo_width()-85)
        near=self.view[0]<=self.cursor<=self.view[1] and abs(e.x-cursor_x)<=7
        self.canvas.configure(cursor='sb_h_double_arrow' if near or e.y>=self.canvas.winfo_height()-35 else ('sb_v_double_arrow' if e.x<65 else 'crosshair'))
    def press(self,e):
        self.active_band=self.band_at(e.y)
        cx=65+(self.cursor-self.view[0])/(self.view[1]-self.view[0])*max(1,self.canvas.winfo_width()-85)
        if e.y>=self.canvas.winfo_height()-35:kind='pan_x'
        elif e.x<65:kind='pan_y'
        elif self.view[0]<=self.cursor<=self.view[1] and abs(e.x-cx)<=7:kind='cursor'
        elif self.tool.get()=='移动测量线':kind='cursor'
        elif self.tool.get()=='平移视图':kind='pan_x'
        else:kind='select'
        key=self.axis_key(self.active_band)
        self.anchor={'x':e.x,'y':e.y,'time':float(self.time_at(e.x)),
                     'view':list(self.view),'kind':kind,'key':key,'yrange':self.axis_ranges.get(key)}
    def motion(self,e):
        a=self.anchor
        if a is None:return
        kind=a['kind']
        if kind=='pan_x':
            width=a['view'][1]-a['view'][0]
            delta=(e.x-a['x'])/max(1,self.canvas.winfo_width()-85)*width
            left=float(np.clip(a['view'][0]-delta,self.bounds[0],self.bounds[1]-width))
            self.view=[left,left+width]
        elif kind=='pan_y' and a['yrange']:
            low,high,ytop,ybottom=a['yrange']
            delta=(e.y-a['y'])/max(1,ybottom-ytop)*(high-low)
            self.y_manual[a['key']]=(low+delta,high+delta)
        elif kind=='cursor':self.cursor=float(self.time_at(e.x))
        elif kind=='select' and abs(e.x-a['x'])>=4:
            end=float(self.time_at(e.x))
            if end!=a['time']:
                self.selection=sorted([a['time'],end]);self.cursor=float(np.clip(self.cursor,*self.selection))
        self.draw()
    def release(self,e):
        if self.anchor is None:return
        self.motion(e)
        if self.anchor['kind']=='select' and abs(e.x-self.anchor['x'])<4:self.cursor=float(self.time_at(e.x))
        self.anchor=None;self.draw()
    def zoom(self,e):
        if self.anchor is not None or e.delta==0:return
        self.active_band=self.band_at(e.y)
        factor=.8 if e.delta>0 else 1.25
        if e.state & 4 or self.zoom_axis.get().startswith('Y'):self.scale_y(factor,e.y)
        else:self.scale_x(factor,float(self.time_at(e.x)))
    def scale_x(self,factor,middle=None):
        middle=sum(self.view)/2 if middle is None else middle
        ratio=(middle-self.view[0])/(self.view[1]-self.view[0])
        width=(self.view[1]-self.view[0])*factor
        width=min(self.bounds[1]-self.bounds[0],max(1e-6,width))
        left=float(np.clip(middle-width*ratio,self.bounds[0],self.bounds[1]-width))
        self.view=[left,left+width];self.draw()
    def scale_y(self,factor,pixel=None):
        key=self.axis_key(self.active_band)
        if key not in self.axis_ranges:return
        low,high,top,bottom=self.axis_ranges[key]
        ratio=.5 if pixel is None else float(np.clip((bottom-pixel)/max(1,bottom-top),0,1))
        center=low+ratio*(high-low);span=max(1e-9,(high-low)*factor)
        self.y_manual[key]=(center-ratio*span,center+(1-ratio)*span);self.draw()
    def commit(self):
        if not self.selection[0]<=self.cursor<=self.selection[1]:
            messagebox.showerror('测量线超出选段','请将测量线移入所选时间段',parent=self.win);return
        self.apply(*self.selection,self.cursor)
        self.info.set(self.info.get()+'  · 已回填，可返回主窗口导出')
    def draw(self):
        c=self.canvas;c.delete('all');w=c.winfo_width();h=c.winfo_height()
        if w<100 or h<100:return
        left=65;right=w-20;top=15;bottom=h-35;lo,hi=self.view
        independent=self.mode.get().startswith('独立');count=len(self.rows) if independent else 1
        def xx(t):return left+(t-lo)/(hi-lo)*(right-left)
        curves=[envelope(t,y,lo,hi) for _,t,y in self.rows]
        def limits(arrays):
            valid=[y[np.isfinite(y)] for y in arrays if np.any(np.isfinite(y))]
            if not valid:return -1,1
            low=min(float(y.min()) for y in valid);high=max(float(y.max()) for y in valid)
            pad=max((high-low)*.08,0.1);return low-pad,high+pad
        common=limits([y for _,y in curves]);self.legend.delete(0,'end');self.axis_ranges={}
        for i,((name,t,y),(ts,ys)) in enumerate(zip(self.rows,curves)):
            color=COLORS[i%len(COLORS)];band=i if independent else 0
            ytop=top+band*(bottom-top)/count;ybottom=top+(band+1)*(bottom-top)/count-8
            low,high=limits([ys]) if independent else common
            key=self.axis_key(band)
            low,high=self.y_manual.get(key,(low,high))
            self.axis_ranges[key]=(low,high,ytop,ybottom)
            if independent or i==0:
                for tick in np.linspace(low,high,4):
                    py=ybottom-(tick-low)/(high-low)*(ybottom-ytop)
                    c.create_line(left,py,right,py,fill='#174d33',dash=(2,4))
                    c.create_text(left-5,py,text=f'{tick:.3g}',fill='#bbbbbb',anchor='e')
            points=[]
            for a,b in zip(ts,ys):
                if not np.isfinite(b):
                    if len(points)>=4:self.clipped_line(points,color,left,right,ytop,ybottom)
                    points=[];continue
                px=float(np.clip(xx(a),left,right));py=ybottom-(b-low)/(high-low)*(ybottom-ytop)
                # Previous-value hold: no fabricated ramps between state samples.
                if points:points.extend([px,points[-1]])
                points.extend([px,float(py)])
            if len(points)>=4:self.clipped_line(points,color,left,right,ytop,ybottom)
            index=max(0,int(np.searchsorted(t,self.cursor,side='right'))-1)
            self.legend.insert('end',f'{name} = {y[index]:.6g}');self.legend.itemconfig(i,fg=color)
        for tick in np.linspace(lo,hi,6):
            x=xx(tick);c.create_line(x,top,x,bottom,fill='#174d33',dash=(2,4))
            c.create_text(x,h-15,text=f'{tick:.3f}s',fill='white')
        for t in self.selection:
            if lo<=t<=hi:c.create_line(xx(t),top,xx(t),bottom,fill='#ffbb55',dash=(5,3))
        if lo<=self.cursor<=hi:c.create_line(xx(self.cursor),top,xx(self.cursor),bottom,fill='#00e5ff',width=2)
        self.info.set(f'共同时间范围 {self.bounds[0]:.6f}～{self.bounds[1]:.6f}s  |  选段 {self.selection[0]:.6f}～{self.selection[1]:.6f}s  |  测量线 {self.cursor:.6f}s')

    def clipped_line(self,points,color,left,right,top,bottom):
        # Clip stair segments without drawing fake plateaus on Y zoom boundaries.
        visible=[]
        def flush():
            if len(visible)>=4:self.canvas.create_line(*visible,fill=color)
            visible.clear()
        for j in range(0,len(points)-2,2):
            x1,y1,x2,y2=points[j:j+4]
            if max(y1,y2)<top or min(y1,y2)>bottom:flush();continue
            y1=float(np.clip(y1,top,bottom));y2=float(np.clip(y2,top,bottom))
            if visible and visible[-2:]!=[x1,y1]:flush()
            if not visible:visible.extend([x1,y1])
            visible.extend([x2,y2])
        flush()
