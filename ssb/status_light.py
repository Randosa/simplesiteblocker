"""A small status light whose label is shown only while hovered."""
import tkinter as tk
from tkinter import ttk


class StatusLight:
    STATES = {
        'primed': ('#D49A00', 'Blocking primed'),
        'active': ('#238636', 'Blocking active'),
        'disabled': ('#CF3038', 'Blocking disabled'),
    }

    def __init__(self, parent, refresh):
        background = ttk.Style(parent).lookup('TFrame', 'background') or 'SystemButtonFace'
        self.widget = tk.Canvas(parent, width=18, height=18, highlightthickness=0,
                                borderwidth=0, background=background, cursor='hand2', takefocus=True)
        self.dot = self.widget.create_oval(3, 3, 15, 15, outline='')
        self.tooltip = None
        self.tooltip_label = None
        self.pending = None
        self.set_state('disabled')
        self.widget.bind('<Enter>', self._enter)
        self.widget.bind('<Leave>', self.hide)
        self.widget.bind('<Destroy>', self.hide)
        self.widget.bind('<Button-1>', lambda _event: refresh())
        self.widget.bind('<Return>', lambda _event: refresh())
        self.widget.bind('<space>', lambda _event: refresh())

    def set_state(self, state):
        self.state = state
        color, self.text = self.STATES[state]
        self.widget.itemconfigure(self.dot, fill=color)
        if self.tooltip_label is not None:
            self.tooltip_label.configure(text=self.text)

    def _enter(self, _event=None):
        if self.pending is None and self.tooltip is None:
            self.pending = self.widget.after(250, self.show)

    def show(self):
        self.pending = None
        if self.tooltip is not None:
            return
        self.tooltip = tk.Toplevel(self.widget)
        self.tooltip.withdraw()
        self.tooltip.overrideredirect(True)
        self.tooltip.attributes('-topmost', True)
        self.tooltip_label = tk.Label(self.tooltip, text=self.text, background='#202124',
                                      foreground='white', padx=9, pady=5, font=('Segoe UI', 9))
        self.tooltip_label.pack()
        self.tooltip.update_idletasks()
        x = max(0, min(self.widget.winfo_rootx(),
                       self.widget.winfo_screenwidth() - self.tooltip.winfo_reqwidth() - 8))
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 6
        self.tooltip.geometry(f'+{x}+{y}')
        self.tooltip.deiconify()

    def hide(self, _event=None):
        try:
            if self.pending is not None:
                self.widget.after_cancel(self.pending)
            if self.tooltip is not None:
                self.tooltip.destroy()
        except tk.TclError:
            pass
        self.pending = None
        self.tooltip = None
        self.tooltip_label = None
