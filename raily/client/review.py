"""Persistent, resizable review window with independent document preview."""
import json
import tkinter as tk
from tkinter import ttk, messagebox, simpledialog
import httpx
from .preview import PreviewPane
from raily.desktop.ui import ScrollFrame, center, apply_icon
from raily.filing import filing_components


def open_review(owner, base_url):
    window = tk.Toplevel(owner); window.title('RAILY • Needs Conductor Review')
    window.transient(owner); apply_icon(window); center(window, 1380, 850)
    heading = ttk.Frame(window, padding=10); heading.pack(fill='x')
    ttk.Label(heading, text='Select a document to review', font=('Segoe UI', 14, 'bold')).pack(side='left')
    selector = ttk.Combobox(heading, state='readonly', width=45); selector.pack(side='right')
    status = ttk.Label(window, text='Connecting to the review yard…', padding=8); status.pack(fill='x')
    panes = ttk.Panedwindow(window, orient='horizontal'); panes.pack(fill='both', expand=True)
    jobs = []
    def selected(_=None):
        for child in panes.winfo_children(): child.destroy()
        if selector.current() < 0: return
        job = jobs[selector.current()]
        ocr = job.get('ocr') or {}
        preview = PreviewPane(panes, f"{base_url}/review/{job['id']}/preview", owner.auth_headers())
        panes.add(preview, weight=3)
        scroll = ScrollFrame(panes); panes.add(scroll, weight=2)
        form = scroll.body; form.configure(padding=12); form.columnconfigure(1, weight=1)
        reason = job.get('review_reason') or job.get('error_message') or 'Verify the document type before filing.'
        confidence = ocr.get('ocr_confidence')
        confidence_text = 'Not recorded' if confidence is None else str(confidence)
        ttk.Label(form, text=f"{job.get('original_name') or job.get('document_name')}\n{reason}\nOCR confidence: {confidence_text}", wraplength=430, justify='left').grid(row=0, column=0, columnspan=2, sticky='ew', pady=8)
        entries = {}
        defaults = {'railroad':ocr.get('railroad'), 'location':ocr.get('location'), 'document_type':ocr.get('category'), 'date':ocr.get('date'), 'name':ocr.get('name')}
        for row, (key, label) in enumerate([('railroad','Railroad (optional)'),('location','Location (optional)'),('document_type','Document Type / Category *'),('date','Document Date (optional)'),('name','Person / Name (optional)')], 1):
            ttk.Label(form, text=label).grid(row=row, column=0, sticky='w', padx=(0,8), pady=7)
            var = tk.StringVar(value=defaults[key] or '')
            entry = ttk.Entry(form, textvariable=var, width=26); entry.grid(row=row, column=1, sticky='ew', pady=7)
            entries[key] = (var, entry)
        proposed = ttk.Label(form, wraplength=430, justify='left'); proposed.grid(row=6, column=0, columnspan=2, sticky='ew', pady=10)
        ttk.Label(form, text='What RAILY read • compare with the page', font=('Segoe UI', 11, 'bold')).grid(row=7, column=0, columnspan=2, sticky='w')
        context = tk.Text(form, wrap='word', height=10, width=42, font=('Segoe UI',10))
        context.grid(row=8, column=0, columnspan=2, sticky='ew', pady=6)
        context.insert('end', 'Raw OCR\n'+(job.get('raw_ocr_context') or 'No stored OCR context for this older job.')+'\n\nCleaned / extracted\n'+(job.get('cleaned_ocr_context') or json.dumps(defaults, indent=2)))
        context.configure(state='disabled')
        scope = tk.StringVar(value='document')
        ttk.Radiobutton(form, text='Apply to this document only', variable=scope, value='document').grid(row=9,column=0,columnspan=2,sticky='w',pady=4)
        ttk.Radiobutton(form, text='Teach RAILY / Save as learned rule', variable=scope, value='learn').grid(row=10,column=0,columnspan=2,sticky='w',pady=4)
        controls = ttk.Frame(form); controls.grid(row=11,column=0,columnspan=2,sticky='ew',pady=12)
        approve = ttk.Button(controls, text='Approve / File'); approve.pack(side='left',padx=4)
        ttk.Button(controls, text='Close', command=window.destroy).pack(side='right')
        def validate(*_):
            missing = not entries['document_type'][0].get().strip()
            entries['document_type'][1].configure(style='Missing.TEntry' if missing else 'TEntry')
            try:
                railroad, location = filing_components(entries['railroad'][0].get(), entries['location'][0].get())
                proposed.configure(text=f"Proposed filename: {job.get('proposed_filename')}\nFiling destination: Railroads / {railroad} / {location}")
            except ValueError:
                missing = True; proposed.configure(text='Please correct the unsafe Railroad or Location name.')
            approve.state(['disabled'] if missing else ['!disabled'])
        def submit():
            payload = {key: pair[0].get().strip() for key, pair in entries.items()}
            if not payload['document_type']: validate(); return
            payload['teach'] = scope.get() == 'learn'
            if payload['teach']:
                pattern = simpledialog.askstring('Teach RAILY', 'Stable printed text to recognize (at least 8 characters):', parent=window)
                if not pattern or len(pattern.strip()) < 8: return
                payload['pattern'] = pattern.strip()
                if not messagebox.askyesno('Confirm learning', f"Save a document-type rule for:\n{pattern}\n\nFuture documents will still need clear OCR evidence. Personal and date values are not copied.", parent=window): return
            if not messagebox.askyesno('Confirm filing', 'File this existing document with the values shown?', parent=window): return
            approve.state(['disabled']); selector.state(['disabled']); status.configure(text='RAILY is tying down the paperwork…')
            def operation():
                response = httpx.post(f"{base_url}/review/{job['id']}", headers=owner.auth_headers(), json=payload, timeout=10)
                response.raise_for_status(); return response.json()
            def done(result):
                if not window.winfo_exists(): return
                status.configure(text='Duplicate routed to siding' if result['status']=='DUPLICATE SIDING' else 'Filed and tied down')
                owner.refresh_dashboard(); load()
            def failed():
                if not window.winfo_exists(): return
                status.configure(text='Filing could not complete. Your edits remain here. Check Diagnostics.'); selector.state(['!disabled']); validate()
            owner.background(operation, done, failed)
        approve.configure(command=submit)
        for var, entry in entries.values(): var.trace_add('write', validate)
        validate()
    def loaded(result):
        nonlocal jobs
        if not window.winfo_exists(): return
        jobs = result
        selector.configure(values=[f"Job {job['id']} • {job.get('original_name') or job.get('document_name')}" for job in jobs])
        selector.state(['!disabled'])
        status.configure(text='Select a document. Optional fields may stay blank.' if jobs else 'All clear — no documents need review.')
        if jobs: selector.current(0); selected()
        else:
            for child in panes.winfo_children(): child.destroy()
    def load():
        def operation():
            response = httpx.get(base_url+'/review', headers=owner.auth_headers(), timeout=5)
            response.raise_for_status(); return response.json()
        owner.background(operation, loaded)
    selector.bind('<<ComboboxSelected>>', selected)
    load()
