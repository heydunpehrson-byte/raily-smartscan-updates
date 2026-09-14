import io
import queue
import threading
import tkinter as tk
from tkinter import ttk
import httpx
from PIL import Image, ImageTk


class PreviewPane(ttk.Frame):
    def __init__(self, parent, url, headers):
        super().__init__(parent)
        self.url, self.headers = url, headers
        self.page, self.count, self.zoom = 0, 1, 1.0
        self.picture = None
        self.messages = queue.Queue()
        self.generation = 0
        bar = ttk.Frame(self)
        bar.pack(fill='x')
        for label, action in [('Previous', lambda: self.turn(-1)), ('Next', lambda: self.turn(1)), ('Zoom +', lambda: self.scale(1.25)), ('Zoom −', lambda: self.scale(.8)), ('Fit', self.fit), ('Reset', self.reset), ('Rotate', self.rotate)]:
            ttk.Button(bar, text=label, command=action).pack(side='left')
        self.status = ttk.Label(self, text='Loading document…')
        self.status.pack(fill='x')
        area = ttk.Frame(self)
        area.pack(fill='both', expand=True)
        self.canvas = tk.Canvas(area, background='#303840', highlightthickness=0)
        x = ttk.Scrollbar(area, orient='horizontal', command=self.canvas.xview)
        y = ttk.Scrollbar(area, command=self.canvas.yview)
        self.canvas.configure(xscrollcommand=x.set, yscrollcommand=y.set)
        self.canvas.grid(row=0, column=0, sticky='nsew')
        y.grid(row=0, column=1, sticky='ns'); x.grid(row=1, column=0, sticky='ew')
        area.rowconfigure(0, weight=1); area.columnconfigure(0, weight=1)
        self.load()
        self.poll_id = self.after(50, self.poll)
        self.bind('<Destroy>', self.closed)

    def closed(self, event):
        if event.widget is self:
            self.after_cancel(self.poll_id)
            self.headers = {}

    def load(self):
        self.generation += 1
        generation, page = self.generation, self.page
        self.status.configure(text=f'Loading page {page+1}…')
        def fetch():
            try:
                response = httpx.get(self.url, params={'page': page}, headers=self.headers, timeout=10)
                response.raise_for_status()
                picture = Image.open(io.BytesIO(response.content)).convert('RGB')
                self.messages.put((generation, picture, int(response.headers['X-Page-Count']), None))
            except Exception:
                self.messages.put((generation, None, 0, 'Preview unavailable. Check the Brain connection or document; metadata remains editable.'))
        threading.Thread(target=fetch, daemon=True).start()

    def poll(self):
        try:
            while True:
                generation, picture, count, error = self.messages.get_nowait()
                if generation != self.generation:
                    continue
                if error:
                    self.status.configure(text=error)
                else:
                    self.picture, self.count = picture, count
                    self.fit()
        except queue.Empty:
            pass
        self.poll_id = self.after(50, self.poll)

    def turn(self, offset):
        page = self.page + offset
        if 0 <= page < self.count:
            self.page = page
            self.load()

    def fit(self):
        if self.picture:
            self.zoom = min(max(1, self.canvas.winfo_width()-20)/self.picture.width, max(1, self.canvas.winfo_height()-20)/self.picture.height)
            self.draw()

    def scale(self, factor):
        self.zoom = min(3, max(.1, self.zoom*factor)); self.draw()

    def reset(self):
        self.zoom = 1; self.draw()

    def rotate(self):
        if self.picture:
            self.picture = self.picture.rotate(90, expand=True); self.fit()

    def draw(self):
        if not self.picture:
            return
        resized = self.picture.resize((max(1, int(self.picture.width*self.zoom)), max(1, int(self.picture.height*self.zoom))), Image.Resampling.LANCZOS)
        self.photo = ImageTk.PhotoImage(resized)
        self.canvas.delete('all')
        self.canvas.create_image(0, 0, anchor='nw', image=self.photo)
        self.canvas.configure(scrollregion=(0, 0, resized.width, resized.height))
        self.status.configure(text=f'Page {self.page+1} of {self.count} • {self.zoom:.0%} • EXIF/PDF orientation applied')
