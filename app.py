#!/usr/bin/python3
"""Ubuntu native console preserving the factory 0.3.15 module topology."""
import colorsys
import json
import math
import sys
import threading
from collections import deque
from pathlib import Path
import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw, GLib, Gdk, Graphene
from backend import Backend
import firmware

APP_ID = 'io.local.JiaolongConsole'
ROOT = Path(__file__).resolve().parent
# MainWindow.baml: CPU / General, GPU / Lighting, RAM / Disk / Fan / Performance.
MODULE_LAYOUT = (
    ('cpu', 'CPU信息', 0, 0, 3), ('general', '常规设置', 3, 0, 1),
    ('gpu', 'GPU信息', 0, 1, 3), ('lighting', '灯光设置', 3, 1, 1),
    ('memory', '内存信息', 0, 2, 1), ('disk', '硬盘信息', 1, 2, 1),
    ('fan', '风扇信息', 2, 2, 1), ('performance', '性能设置', 3, 2, 1),
)
GENERAL_ORDER = ('独显直连', '数字键锁定', '大写键锁定', '功能键锁定', '触摸板锁定')
PERFORMANCE_ORDER = ('自定义模式', '办公模式', '游戏模式', '狂飙模式')
CUSTOM_ORDER = ('CPU温度墙', '长时运行功耗墙', '短时运行功耗墙', '显卡超频')
CSS = b'''
.module { padding: 15px; border-radius: 18px; background: @card_bg_color; border: 1px solid alpha(@window_fg_color,.08); }
.module-title { font-size: 18px; font-weight: 700; }
.caption { opacity: .6; font-size: 12px; }
.reading { font-size: 20px; font-weight: 600; }
.gauge-number { font-size: 26px; font-weight: 700; color: @accent_color; }
.small-gauge .gauge-number { font-size: 17px; }
.mode-button { padding: 5px 14px; border-radius: 9px; }
.mode-button:checked { background: alpha(@accent_bg_color,.15); color: @accent_color; }
.hue trough { background: linear-gradient(to right, #ff0000, #ffff00, #00ff00, #00ffff, #0000ff, #ff00ff, #ff0000); }
.hue highlight { background: transparent; }
'''


def label(text='', css=None, align=0):
    widget = Gtk.Label(label=text, xalign=align)
    if css: widget.add_css_class(css)
    return widget


def vertical(spacing=8):
    return Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=spacing)


def val(number, suffix='', digits=0):
    return '—' if number is None else f'{number:.{digits}f}{suffix}'


def rectangle(snapshot, x, y, width, height, color):
    rect = Graphene.Rect(); rect.init(x, y, width, height)
    rgba = Gdk.RGBA(); rgba.red, rgba.green, rgba.blue, rgba.alpha = color
    snapshot.append_color(rgba, rect)


def accent(widget):
    found,color = widget.get_style_context().lookup_color('accent_color')
    return (color.red,color.green,color.blue,color.alpha) if found else (.25,.55,.95,1)


class LoadChart(Gtk.Widget):
    def __init__(self, history, key):
        super().__init__(height_request=110, hexpand=True)
        self.history, self.key = history, key

    def do_snapshot(self, snapshot):
        width, height = self.get_width(), self.get_height()
        color = accent(self)
        for value in (0, 25, 50, 75, 100):
            rectangle(snapshot, 0, 4+(1-value/100)*(height-8), width, 1, (.5,.5,.5,.15))
        for x in range(0, width, 40):
            rectangle(snapshot, x, 4, 1, height-8, (.5,.5,.5,.1))
        last = None
        for index, item in enumerate(self.history):
            value = item.get(self.key)
            if value is None: last = None; continue
            x = index/89*(width-4)+2
            y = 4+(1-max(0,min(100,value))/100)*(height-8)
            if last:
                dx, dy = x-last[0], y-last[1]
                snapshot.save()
                point = Graphene.Point(); point.init(*last)
                snapshot.translate(point); snapshot.rotate(math.degrees(math.atan2(dy,dx)))
                rectangle(snapshot, 0, -1, math.hypot(dx,dy), 2, color)
                snapshot.restore()
            last = (x, y)


class Ring(Gtk.Widget):
    def __init__(self, size):
        super().__init__(width_request=size, height_request=size)
        self.value = None

    def do_snapshot(self, snapshot):
        width, height = self.get_width(), self.get_height()
        color = accent(self)
        radius = min(width,height)/2-7
        active = 0 if self.value is None else round(max(0,min(100,self.value))*72/100)
        for index in range(72):
            angle = math.radians(index*5-90)
            snapshot.save()
            point = Graphene.Point(); point.init(width/2+radius*math.cos(angle), height/2+radius*math.sin(angle))
            snapshot.translate(point); snapshot.rotate(index*5)
            rectangle(snapshot,-1.7,-3,3.4,6,color if index<active else (.5,.5,.5,.17))
            snapshot.restore()


class Console(Adw.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID)
        self.backend = Backend()
        self.busy = self.updating = self.fw_busy = False
        self.latest = None
        self.history = deque(maxlen=90)
        self.hw_widgets, self.fw_buttons = [], {}
        self.pending, self.dirty = {}, set()
        self.modules, self.readings, self.gauges, self.charts = {}, {}, {}, {}
        self.get_style_manager().set_color_scheme(Adw.ColorScheme.DEFAULT)

    def do_activate(self):
        if hasattr(self, 'win'): self.win.present(); return
        self.updating = True
        provider = Gtk.CssProvider(); provider.load_from_data(CSS)
        Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        self.win = Adw.ApplicationWindow(application=self, title='蛟龙游戏控制中心', default_width=1200, default_height=855)
        self.win.set_size_request(900,650)
        self.toasts = Adw.ToastOverlay()
        view = Adw.ToolbarView(); header = Adw.HeaderBar()
        self.title = Adw.WindowTitle(title='蛟龙游戏控制中心')
        header.set_title_widget(self.title)
        icon = Gtk.Image.new_from_file(str(ROOT/'io.local.JiaolongConsole.svg')); icon.set_pixel_size(24)
        header.pack_start(icon)
        menu = Gtk.MenuButton(icon_name='open-menu-symbolic')
        popover = Gtk.Popover(); update = Gtk.Button(label='检查更新')
        update.set_sensitive(False); update.set_tooltip_text('Ubuntu 版本暂未提供在线更新')
        popover.set_child(update); menu.set_popover(popover); header.pack_end(menu)
        view.add_top_bar(header)
        self.grid = Gtk.Grid(column_spacing=14, row_spacing=14, hexpand=True)
        for side in ('start','end','top','bottom'): getattr(self.grid,'set_margin_'+side)(18)
        builders = {'cpu':lambda:self.processor_card('cpu'), 'gpu':lambda:self.processor_card('gpu'),
                    'general':self.general_card, 'lighting':self.lighting_card,
                    'memory':self.memory_card, 'disk':self.disk_card, 'fan':self.fan_card,
                    'performance':self.performance_card}
        for key, title, column, row, span in MODULE_LAYOUT:
            card = vertical(10); card.add_css_class('module'); card.set_hexpand(column<3)
            card.set_size_request(345 if column==3 else 225, 190 if row<2 else 180)
            heading = Gtk.Box(spacing=12); heading.append(label(title,'module-title'))
            if key in ('cpu','gpu'):
                name = label('', 'caption', 1); name.set_hexpand(True); name.set_ellipsize(3)
                heading.append(name); self.readings[key+'_name'] = name
            card.append(heading); card.append(builders[key]())
            self.modules[key] = card; self.grid.attach(card,column,row,span,1)
        scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.AUTOMATIC)
        scroll.set_child(self.grid); view.set_content(scroll)
        self.toasts.set_child(view); self.win.set_content(self.toasts)
        for widget in self.hw_widgets: widget.set_sensitive(False)
        self.updating = False
        self.win.present(); self.refresh(); GLib.timeout_add_seconds(3,self.refresh)

    def gauge(self, key, size):
        overlay = Gtk.Overlay(); ring = Ring(size); overlay.set_child(ring)
        number = label('—','gauge-number',.5); number.set_valign(Gtk.Align.CENTER); overlay.add_overlay(number)
        if size<100: overlay.add_css_class('small-gauge')
        self.gauges[key] = ring,number
        return overlay

    def reading(self, box, key, title, large=False):
        row = Gtk.Box(spacing=8); row.append(label(title,'caption'))
        value = label('—','reading' if large else None,1); value.set_hexpand(True)
        row.append(value); box.append(row); self.readings[key] = value
        return row

    def processor_card(self, key):
        content = Gtk.Box(spacing=28, valign=Gtk.Align.CENTER, vexpand=True)
        content.append(self.gauge(key,126))
        details = vertical(15); details.set_size_request(175,-1); details.set_hexpand(False); details.set_valign(Gtk.Align.CENTER)
        self.reading(details,key+'_temp','温度',True)
        self.reading(details,key+'_freq','频率',True)
        self.reading(details,key+'_cores' if key=='cpu' else 'gpu_memory','核心数' if key=='cpu' else '显存',True)
        content.append(details)
        chart = LoadChart(self.history,key+'_load'); chart.set_valign(Gtk.Align.CENTER)
        chart.set_tooltip_text('实时使用率 · 每 3 秒更新'); self.charts[key] = chart
        content.append(chart); return content

    def switch(self, box, title, action=None, tooltip=None):
        row = Gtk.Box(spacing=8); row.add_css_class('control-row')
        text = label(title); text.set_hexpand(True); row.append(text)
        switch = Gtk.Switch(valign=Gtk.Align.CENTER)
        if action:
            switch.connect('notify::active',lambda widget,_: self.fw_setting(action,int(widget.get_active())))
            self.hw_widgets.append(switch)
        elif title not in ('独显直连','自定义转速'):
            switch.set_sensitive(False)
            text.add_css_class('dim-label')
            row.set_tooltip_text(tooltip or '当前 Ubuntu 版本暂不可用')
        row.append(switch); box.append(row)
        if tooltip: row.set_tooltip_text(tooltip)
        return switch

    def general_card(self):
        box = vertical(0)
        self.gpu_switch = self.switch(box,'独显直连',tooltip='切换后需要自行重启电脑')
        self.gpu_switch.connect('state-set',self.gpu_requested); self.hw_widgets.append(self.gpu_switch)
        self.num_switch = self.switch(box,'数字键锁定','num_lock',tooltip='切换 Num Lock，并读取桌面确认的锁定状态')
        self.caps_switch = self.switch(box,'大写键锁定','caps_lock',tooltip='切换 Caps Lock，并读取桌面确认的锁定状态')
        self.fn_switch = self.switch(box,'功能键锁定','fn_lock')
        self.tp_switch = self.switch(box,'触摸板锁定','touchpad_lock')
        return box

    def scale(self, lo, hi, key, changed, width=-1):
        widget = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL,lo,hi,1)
        widget.set_draw_value(False); widget.set_hexpand(True); widget.set_size_request(width,-1)
        widget.connect('value-changed',changed,key); self.hw_widgets.append(widget)
        return widget

    def lighting_card(self):
        box = vertical(8)
        row = Gtk.Box(); row.append(label('键盘灯光亮度'))
        self.kb_value = label('—','caption',1); self.kb_value.set_hexpand(True); row.append(self.kb_value); box.append(row)
        self.kb_brightness = self.scale(0,3,'kb_brightness',self.light_changed)
        self.kb_brightness.set_round_digits(0)
        for value in range(4): self.kb_brightness.add_mark(value,Gtk.PositionType.BOTTOM,None)
        box.append(self.kb_brightness)
        self.ambient_switch = self.switch(box,'氛围灯','ambient_light')
        row = Gtk.Box(spacing=8); name = label('键盘背光颜色'); name.set_hexpand(True); row.append(name)
        self.color_mode = Gtk.DropDown.new_from_strings(['快捷键控制','软件控制'])
        self.color_mode.set_selected(Gtk.INVALID_LIST_POSITION)
        self.color_mode.set_tooltip_text('控制方式')
        self.color_mode.connect('notify::selected',self.color_mode_changed)
        row.append(self.color_mode); box.append(row); self.hw_widgets.append(self.color_mode)
        self.hue = self.scale(0,360,'kb_color',self.light_changed); self.hue.add_css_class('hue')
        box.append(self.hue)
        return box

    def light_changed(self, widget, key):
        if key=='kb_brightness': self.kb_value.set_label(str(round(widget.get_value())))
        if self.updating: return
        value = round(widget.get_value())
        if key=='kb_color': value = ','.join(str(round(v*255)) for v in colorsys.hsv_to_rgb(value/360,1,1))
        self.schedule(key,lambda:self.fw_action(key,value,'灯光设置已应用'))

    def color_mode_changed(self, widget, _):
        if self.updating: return
        if widget.get_selected()==0: self.fw_action('kb_mode',1,'已切换为快捷键控制')
        elif widget.get_selected()==1:
            rgb = ','.join(str(round(v*255)) for v in colorsys.hsv_to_rgb(self.hue.get_value()/360,1,1))
            self.fw_action('kb_color',rgb,'已切换为软件控制')

    def memory_card(self):
        box = Gtk.Box(spacing=12, vexpand=True, valign=Gtk.Align.CENTER)
        box.append(self.gauge('memory',80)); details = vertical(10); details.set_hexpand(True)
        self.reading(details,'memory_total','系统容量',True)
        self.reading(details,'memory_freq','频率')
        details.set_tooltip_text('系统容量为 Linux 可用内存；频率接口不可读时显示 —')
        box.append(details); return box

    def disk_card(self):
        box = Gtk.Box(spacing=12,vexpand=True,valign=Gtk.Align.CENTER)
        box.append(self.gauge('disk',80)); details = vertical(10); details.set_hexpand(True)
        self.reading(details,'disk_available','可用容量',True)
        self.reading(details,'disk_total','总容量')
        self.disk_details = details
        box.append(details); return box

    def fan_card(self):
        box = vertical(4)
        current = Gtk.Box(spacing=12); current.append(self.gauge('fan',60))
        details = vertical(4); details.set_hexpand(True)
        details.append(label('当前转速','caption'))
        self.fan_current = label('—','reading'); details.append(self.fan_current)
        current.append(details); box.append(current)
        self.fan_custom = self.switch(box,'自定义转速',tooltip='打开后设定最大转速，关闭后恢复固件自动散热')
        self.fan_custom.set_sensitive(False); self.hw_widgets.append(self.fan_custom)
        self.fan_custom.connect('notify::active',self.fan_mode_changed)
        controls = Gtk.Box(spacing=5)
        minus = Gtk.Button(label='−'); minus.connect('clicked',lambda _:self.fan_step(-100))
        plus = Gtk.Button(label='+'); plus.connect('clicked',lambda _:self.fan_step(100))
        target = vertical(1); target.set_hexpand(True); target.append(label('最大转速','caption',.5))
        self.fan_max = label('—',align=.5); target.append(self.fan_max)
        controls.append(minus); controls.append(target); controls.append(plus); box.append(controls)
        self.hw_widgets.extend([minus,plus])
        self.fan_slider = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL,2200,5800,100)
        self.fan_slider.set_draw_value(False); self.fan_slider.set_hexpand(True)
        self.fan_slider.connect('value-changed',self.fan_slider_changed); box.append(self.fan_slider)
        self.hw_widgets.append(self.fan_slider); self.fan_adjusters = (minus,plus,self.fan_slider)
        endpoints = Gtk.Box(); endpoints.append(label('办公最低 2200','caption'))
        high = label('狂飙最高 5800','caption',1); high.set_hexpand(True); endpoints.append(high); box.append(endpoints)
        behavior = label('设定最大转速，实际由温控调节','caption'); behavior.set_wrap(True); box.append(behavior)
        self.fan_card_body = box
        return box

    def fan_mode_changed(self, widget, _):
        if self.updating: return
        if widget.get_active():
            rpm = max(2200,min(5800,round(self.fan_slider.get_value()/100)*100))
            self.fw_action('fan_custom_rpm',rpm,'自定义风扇转速已启用')
        else:
            if 'fan_custom_rpm' in self.pending: GLib.source_remove(self.pending.pop('fan_custom_rpm'))
            self.dirty.discard('fan_custom_rpm')
            self.fw_action('fan_auto',1,'已恢复固件自动散热')

    def fan_slider_changed(self, widget):
        if self.updating: return
        rpm = max(2200,min(5800,round(widget.get_value()/100)*100))
        self.fan_max.set_label(val(rpm,' RPM'))
        self.schedule('fan_custom_rpm',lambda:self.fw_action('fan_custom_rpm',rpm,'自定义风扇转速已应用'))

    def fan_step(self, step):
        fw = (self.latest or {}).get('firmware',{})
        lo,hi = firmware.CUSTOM_FAN_LIMITS
        current = fw.get('fan_max_rpm')
        if not isinstance(current,int): return
        value = max(lo,min(hi,current+step))
        if value!=current: self.fw_action('fan_custom_rpm',value,'最大转速已应用')

    def performance_card(self):
        self.performance_stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE)
        home = Gtk.Box(spacing=22,vexpand=True,valign=Gtk.Align.CENTER)
        self.profile_image = Gtk.Image.new_from_icon_name('power-profile-power-saver-symbolic')
        self.profile_image.set_pixel_size(64); self.profile_image.set_hexpand(True); home.append(self.profile_image)
        buttons = vertical(7)
        custom = Gtk.Button(label='自定义模式'); custom.add_css_class('mode-button')
        custom.connect('clicked',self.custom_clicked); buttons.append(custom); self.hw_widgets.append(custom)
        previous = None
        for key,title in [(2,'办公模式'),(0,'游戏模式'),(1,'狂飙模式')]:
            button = Gtk.ToggleButton(label=title); button.add_css_class('mode-button')
            if previous: button.set_group(previous)
            previous = button
            button.connect('toggled',self.profile_clicked,key)
            buttons.append(button); self.fw_buttons[key] = button; self.hw_widgets.append(button)
        home.append(buttons); self.performance_stack.add_named(home,'home')
        custom_box = vertical(2); self.cpu_scales = {}
        # The OEM reduces SPL to 75–85 W for Ryzen 7. Other limits are from its XAML.
        for key,title,lo,hi,unit in [('cpu_temp_wall','CPU温度墙',89,100,'°C'),
            ('cpu_spl_w','长时运行功耗墙',75,85,'W'), ('cpu_sppt_w','短时运行功耗墙',105,120,'W')]:
            row = Gtk.Box(spacing=6); text = label(title); text.set_size_request(116,-1); row.append(text)
            widget = self.scale(lo,hi,key,self.cpu_changed,90)
            widget.set_round_digits(0); row.append(widget)
            reading = label('—','caption',1); reading.set_size_request(43,-1); row.append(reading)
            self.cpu_scales[key] = widget,reading,unit; custom_box.append(row)
        row = Gtk.Box(spacing=6); text = label('显卡超频'); text.set_size_request(116,-1); row.append(text)
        self.gpu_offset = self.scale(0,200,'gpu_offset',self.gpu_offset_changed,90)
        self.gpu_offset.set_round_digits(0); row.append(self.gpu_offset)
        self.gpu_offset_value = label('—','caption',1); self.gpu_offset_value.set_size_request(43,-1); row.append(self.gpu_offset_value)
        row.set_tooltip_text('NVIDIA GPU 核心频率偏移 · 0–200 MHz'); custom_box.append(row)
        exit_button = Gtk.Button(label='退出',halign=Gtk.Align.START)
        exit_button.connect('clicked',self.custom_exit); custom_box.append(exit_button); self.hw_widgets.append(exit_button)
        self.performance_stack.add_named(custom_box,'custom')
        return self.performance_stack

    def custom_clicked(self, _):
        if (self.latest or {}).get('firmware',{}).get('cpu_custom_mode')==1:
            self.show_custom(); return
        dialog = Adw.AlertDialog(heading='启用自定义模式？',body='使用原厂 CPU 温度墙和功耗墙。需要连接足够功率的电源适配器；点击“退出”可恢复原厂模式。')
        dialog.add_response('cancel','取消'); dialog.add_response('apply','启用')
        dialog.set_default_response('cancel'); dialog.set_close_response('cancel')
        dialog.set_response_appearance('apply',Adw.ResponseAppearance.SUGGESTED)
        dialog.connect('response',lambda _,response:self.fw_action('cpu_custom_mode',1,'自定义模式已启用',self.show_custom) if response=='apply' else None)
        dialog.present(self.win)

    def show_custom(self): self.performance_stack.set_visible_child_name('custom')

    def custom_exit(self, _):
        for key in tuple(self.pending):
            if key.startswith('cpu_'): GLib.source_remove(self.pending.pop(key)); self.dirty.discard(key)
        if (self.latest or {}).get('firmware',{}).get('cpu_custom_mode')==1:
            self.fw_action('cpu_custom_mode',0,'已退出自定义模式',lambda:self.performance_stack.set_visible_child_name('home'))
        else: self.performance_stack.set_visible_child_name('home')

    def cpu_changed(self, widget, key):
        _,reading,unit = self.cpu_scales.get(key,(None,None,''))
        if reading: reading.set_label(str(round(widget.get_value()))+unit)
        if self.updating: return
        self.schedule(key,self.commit_cpu)

    def gpu_offset_changed(self, widget, key):
        value = round(widget.get_value()); self.gpu_offset_value.set_label(str(value)+'MHz')
        if not self.updating: self.schedule(key,lambda:self.fw_action(key,value,'显卡超频参数已应用'))

    def commit_cpu(self):
        for key in tuple(self.pending):
            if key.startswith('cpu_'): GLib.source_remove(self.pending.pop(key)); self.dirty.discard(key)
        if (self.latest or {}).get('firmware',{}).get('cpu_custom_mode')!=1:
            self.toast('请先启用自定义模式'); self.refresh(); return
        values = [round(self.cpu_scales[key][0].get_value()) for key in ('cpu_spl_w','cpu_sppt_w','cpu_temp_wall')]
        self.fw_action('cpu_limits',','.join(map(str,values)),'自定义参数已应用')

    def profile_clicked(self, button, key):
        if not self.updating and button.get_active(): self.fw_action('profile',key,'已切换为'+firmware.PROFILES[key]+'模式')

    def gpu_requested(self, widget, enabled):
        if self.updating: return False
        fw = (self.latest or {}).get('firmware',{})
        if self.fw_busy or fw.get('gpu_mode') not in (0,1): return True
        if int(enabled)==fw['gpu_mode']: return True
        dialog = Adw.AlertDialog(heading='切换显卡连接模式？',body='切换到'+('独显直连' if enabled else '混合输出')+'，重启后生效。请保存工作后自行重启。')
        dialog.add_response('cancel','取消'); dialog.add_response('apply','切换')
        dialog.set_default_response('cancel'); dialog.set_close_response('cancel')
        dialog.set_response_appearance('apply',Adw.ResponseAppearance.SUGGESTED)
        dialog.connect('response',lambda _,response:self.fw_action('gpu_mode',int(enabled),'显卡模式已写入，重启后生效') if response=='apply' else self.refresh())
        dialog.present(self.win); return True

    def schedule(self, key, callback):
        if key in self.pending: GLib.source_remove(self.pending.pop(key))
        self.dirty.add(key)
        def commit():
            self.pending.pop(key,None); self.dirty.discard(key); callback(); return False
        self.pending[key] = GLib.timeout_add(500,commit)

    def fw_setting(self, key, value):
        if not self.updating: self.fw_action(key,value,'设置已应用')

    def fw_action(self, key, value, message, success=None):
        if self.fw_busy: return
        current = (self.latest or {}).get('firmware',{})
        if key in ('num_lock','caps_lock'):
            current = self.latest or {}
            if current.get(key) is not None and bool(current[key])==bool(int(value)): return
        if key=='gpu_offset' and (self.latest or {}).get('gpu_clock',{}).get('offset_mhz')==int(value): return
        if key=='fan_custom_rpm' and current.get('fan_override')==1 and current.get('fan_max_rpm')==int(value): return
        if key=='fan_auto' and current.get('fan_override')==0: return
        # GTK may finish a control's state transition after a telemetry update.
        # Commands matching the state already read from firmware need no write.
        if key in current and str(current[key])==str(value):
            if success: success()
            return
        if key=='kb_color' and current.get('kb_mode')==2:
            if ','.join(map(str,current.get('kb_color',[])))==str(value): return
        self.fw_busy = True
        for widget in self.hw_widgets: widget.set_sensitive(False)
        def worker():
            try: firmware.control(key,value); error = None
            except Exception as exception: error = str(exception)
            def finish():
                self.fw_busy = False
                self.toast('未能应用：'+error if error else message)
                if not error and success: success()
                self.refresh(); return False
            GLib.idle_add(finish)
        threading.Thread(target=worker,daemon=True).start()

    def toast(self, text): self.toasts.add_toast(Adw.Toast(title=text,timeout=4))

    def refresh(self):
        if self.busy: return True
        self.busy = True
        def worker():
            try: state,error = self.backend.sample(),None
            except Exception as exception: state,error = None,str(exception)
            GLib.idle_add(self.render,state,error)
        threading.Thread(target=worker,daemon=True).start(); return True

    def update_gauge(self, key, value):
        ring,reading = self.gauges[key]; ring.value = value; ring.queue_draw()
        reading.set_label(val(value,'%'))

    def render(self, state, error):
        self.busy = False
        if error:
            self.title.set_tooltip_text('读取失败：'+error)
            for widget in self.hw_widgets: widget.set_sensitive(False)
            return False
        self.latest = state; self.updating = True
        try:
            gpu,fw = state.get('gpu') or {},state.get('firmware') or {}
            self.readings['cpu_name'].set_label(state.get('cpu_name') or '—')
            self.readings['gpu_name'].set_label(gpu.get('name') or '—')
            self.readings['cpu_temp'].set_label(val(state.get('cpu_temp'),'°C'))
            self.readings['gpu_temp'].set_label(val(gpu.get('temp'),'°C'))
            self.readings['cpu_freq'].set_label(val(state.get('cpu_freq_ghz'),' GHz',2))
            self.readings['gpu_freq'].set_label(val(gpu.get('freq_ghz'),' GHz',2))
            self.readings['cpu_cores'].set_label(val(state.get('cpu_cores'),' 核'))
            self.readings['gpu_memory'].set_label(val(gpu.get('mem_total',0)/1024 if gpu.get('mem_total') is not None else None,' GB',1))
            self.readings['memory_total'].set_label(val(state.get('memory_total_gib'),' GB',1))
            self.readings['memory_freq'].set_label(val(state.get('memory_freq_mhz'),' MHz'))
            self.readings['disk_available'].set_label(val(state.get('disk_free_gib'),' GB'))
            self.readings['disk_total'].set_label(val(state.get('disk_total_gib'),' GB'))
            self.disk_details.set_tooltip_text(state.get('disk_note','系统硬盘容量'))
            for key,value in [('cpu',state.get('cpu_load')),('gpu',gpu.get('load')),('memory',state.get('memory_load')),('disk',state.get('disk_load')),('fan',fw.get('fan_cpu_rpm',0)/60 if fw.get('connected') else None)]:
                self.update_gauge(key,value)
            self.history.append({'cpu_load':state.get('cpu_load'),'gpu_load':gpu.get('load')})
            for chart in self.charts.values(): chart.queue_draw()
            for switch,key in [(self.num_switch,'num_lock'),(self.caps_switch,'caps_lock')]:
                if state.get(key) is not None: switch.set_active(state[key])
            connected = fw.get('connected',False)
            for widget in self.hw_widgets: widget.set_sensitive(connected and not self.fw_busy)
            for switch,key in [(self.num_switch,'num_lock'),(self.caps_switch,'caps_lock')]:
                switch.set_sensitive(state.get('native_ready',False) and state.get(key) is not None and not self.fw_busy)
            self.title.set_tooltip_text('Ubuntu 0.4 · '+('原厂通信已连接' if connected else fw.get('error','原厂通信未连接')))
            if not connected:
                self.fan_current.set_label('—'); self.fan_max.set_label('—'); return False
            for switch,key in [(self.gpu_switch,'gpu_mode'),(self.fn_switch,'fn_lock'),(self.tp_switch,'touchpad_lock'),(self.ambient_switch,'ambient_light')]:
                if fw.get(key) in (0,1): switch.set_active(bool(fw[key]))
                else: switch.set_sensitive(False)
            profile = fw.get('profile')
            if profile in self.fw_buttons: self.fw_buttons[profile].set_active(True)
            self.profile_image.set_from_icon_name({2:'power-profile-power-saver-symbolic',0:'power-profile-balanced-symbolic',1:'power-profile-performance-symbolic'}.get(profile,'computer-symbolic'))
            rpm = fw.get('fan_cpu_rpm'); self.fan_current.set_label(val(rpm,' RPM'))
            self.fan_current.set_tooltip_text('CPU '+val(rpm,' RPM')+' / GPU '+val(fw.get('fan_gpu_rpm'),' RPM'))
            maximum = fw.get('fan_max_rpm')
            if 'fan_custom_rpm' not in self.dirty:
                self.fan_max.set_label(val(maximum,' RPM'))
                if isinstance(maximum,int) and 2200<=maximum<=5800: self.fan_slider.set_value(maximum)
            custom = fw.get('fan_override')==1
            self.fan_custom.set_active(custom)
            supported = fw.get('fan_custom_supported',False)
            self.fan_custom.set_sensitive(supported and not self.fw_busy)
            for widget in self.fan_adjusters: widget.set_sensitive(supported and custom and not self.fw_busy)
            self.fan_card_body.set_tooltip_text(('自定义转速' if custom else '固件自动控制')+' · 2200–5800 RPM · 步进 100 RPM\n设定原厂最大转速参数；实际转速会波动，固件的温控逻辑继续生效。')
            if 'kb_brightness' not in self.dirty:
                if fw.get('kb_brightness') in range(4): self.kb_brightness.set_value(fw['kb_brightness']); self.kb_value.set_label(str(fw['kb_brightness']))
                else: self.kb_brightness.set_sensitive(False)
            mode = fw.get('kb_mode')
            self.color_mode.set_selected(1 if mode in (2,3) else 0 if mode==1 else Gtk.INVALID_LIST_POSITION)
            self.hue.set_sensitive(connected and not self.fw_busy and mode in (2,3))
            rgb = fw.get('kb_color')
            if 'kb_color' not in self.dirty and isinstance(rgb,list) and len(rgb)==3 and all(0<=v<=255 for v in rgb):
                self.hue.set_value(colorsys.rgb_to_hsv(*(value/255 for value in rgb))[0]*360)
            for key,(widget,reading,unit) in self.cpu_scales.items():
                value = fw.get(key)
                if isinstance(value,int) and widget.get_adjustment().get_lower()<=value<=widget.get_adjustment().get_upper():
                    if key not in self.dirty: widget.set_value(value); reading.set_label(str(value)+unit)
                    widget.set_sensitive(not self.fw_busy and fw.get('cpu_custom_mode')==1)
                else: widget.set_sensitive(False); reading.set_label('—')
            gpu_clock = state.get('gpu_clock',{})
            if gpu_clock.get('supported'):
                self.gpu_offset.set_range(gpu_clock['minimum_mhz'],gpu_clock['maximum_mhz'])
                if 'gpu_offset' not in self.dirty:
                    self.gpu_offset.set_value(gpu_clock['offset_mhz']); self.gpu_offset_value.set_label(str(gpu_clock['offset_mhz'])+'MHz')
                self.gpu_offset.set_sensitive(state.get('native_ready',False) and not self.fw_busy and fw.get('cpu_custom_mode')==1)
            else:
                self.gpu_offset.set_sensitive(False); self.gpu_offset_value.set_label('不可用')
        finally: self.updating = False
        return False


if __name__=='__main__':
    if '--status' in sys.argv:
        print(json.dumps(Backend().sample(),ensure_ascii=False,indent=2)); sys.exit(0)
    Console().run([sys.argv[0]])
