"""Isolated v72.2 checks. Never import the app or read/write installed user data.

Run: python tests/test_v72_2.py --ocr --output <scratch-folder>
Requires the same Pillow, PyMuPDF and pytesseract dependencies as RAILY.
"""
import argparse
import ast
import hashlib
import json
import os
import re
import shutil
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from PIL import Image, ImageOps, ImageChops, ImageDraw, ImageFont
import pymupdf as fitz
import pytesseract

ROOT = Path(__file__).resolve().parents[1]

def load_release(name):
    path = ROOT / 'releases' / name
    tree = ast.parse(path.read_bytes())
    compile(tree, str(path), 'exec')
    env = dict(globals())
    chosen = {'MONTHS', 'DATE_ZONE_LABELS', 'DATE_FAST_OCR_LIMIT', 'DATE_FULL_OCR_LIMIT', 'STRUCTURED_FIELD_LABELS'}
    definitions = [n for n in tree.body if isinstance(n, ast.FunctionDef) or isinstance(n, ast.ClassDef) and n.name == 'StructuredOCRText']
    assignments = [n for n in tree.body if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id in chosen for t in n.targets)]
    module = ast.Module(body=[ast.ImportFrom(module='__future__', names=[ast.alias(name='annotations')], level=0)]+assignments+definitions, type_ignores=[])
    exec(compile(ast.fix_missing_locations(module), str(path), 'exec'), env)
    env.update(CFG={'date_confidence_threshold': 92, 'minimum_document_year': 2020,
                    'maximum_future_years': 1, 'ocr_language': 'eng'},
               _PAGE_RENDER_CACHE={}, _PAGE_RENDER_LOCK=threading.RLock(), _STRUCTURED_OCR_LOCK=threading.RLock(), _OCR_CACHE={}, _STRUCTURE_CACHE={},
               RAILY_MEMORY={'people': {}, 'locations': {}, 'railroads': {}}, logs=[])
    env['log'] = env['logs'].append
    return env, tree

def word(text, x, y, w=None, confidence=98):
    return {'text':text, 'box':(x,y,x+(w or len(text)*12),y+24), 'confidence':confidence, 'block':1, 'line':1}

def regression(env, old, tree, old_tree, output):
    sample = output/'blank.png'
    image = Image.new('RGB',(600,160),'white')
    image.save(sample)
    region={'x0':.1,'y0':.1,'x1':.9,'y1':.9}
    for fragments in (['09/12/2026','09/12/2026'], ['','',''], ['garbage','02/30/2026',''], ['9/12/26','09/12/2026']):
        results=[]
        for target in (old,env):
            ocr=Mock(side_effect=fragments)
            original=target['pytesseract']
            target['pytesseract']=SimpleNamespace(image_to_string=ocr)
            result=target['_read_taught_date'](sample,region,fast=True,page_image=image)
            results.append((result[:2] if result else None,ocr.call_count,[c.kwargs['config'] for c in ocr.call_args_list]))
            target['pytesseract']=original
        assert results[0]==results[1],results
    # All UI sizing/scroll helpers and all protected methods remain unchanged.
    before={n.name:n for n in old_tree.body if isinstance(n,(ast.FunctionDef,ast.ClassDef))}
    after={n.name:n for n in tree.body if isinstance(n,(ast.FunctionDef,ast.ClassDef))}
    for name in ('_learning_work_area','_fit_learning_window','_learning_form','_finish_learning_form',
                 '_flow_learning_actions','_learning_tree_scrollbars','save_family_date_region',
                 'save_manual_document_date','find_duplicate','manual_split_pdf_groups',
                 'ensure_release_backup','raily_full_page_signature'):
        assert ast.dump(before[name])==ast.dump(after[name]),name
    def methods(node):
        return {n.name:n for n in node.body if isinstance(n,ast.FunctionDef)}
    old_methods,new_methods=methods(before['App']),methods(after['App'])
    for name in old_methods:
        if name not in {'teach_file','open_duplicate_review_center'}:
            assert ast.dump(old_methods[name])==ast.dump(new_methods[name]),name
    assert env['DATE_FAST_OCR_LIMIT']==3 and env['DATE_FULL_OCR_LIMIT']==24
    # Coordinate extraction works independently of the order returned by OCR.
    words=[word('Name:',30,30),word('Jordan',180,30),word('Vale',275,30),
           word('Start',30,90),word('Date:',100,90),word('6/3/24',220,90),
           word('Location:',30,150),word('West',200,150),word('Yard',270,150)]
    fields,calls=env['_ocr_labeled_fields'](Image.new('RGB',(700,250),'white'),list(reversed(words)),None,time.monotonic()+5)
    assert fields['Name']['value']=='Jordan Vale' and fields['Name']['trusted'],fields
    assert fields['Location']['value']=='West Yard' and fields['Location']['trusted'],fields
    assert fields['Start Date']['value']=='2024-06-03' and fields['Start Date']['trusted'],fields
    assert calls==0
    # Low-confidence values must not become filing metadata via legacy regexes.
    text=env['StructuredOCRText']('Name: Scramble\nLocation: Uncertain',{'fields':{'Name':{'value':'Scramble','trusted':False},'Location':{'value':'Uncertain','trusted':False}},'classification_text':'Name\nLocation'})
    metadata=env['raily_extract_metadata'](text)
    assert not metadata['person'] and not metadata['location'],metadata
    fields['Name']['trusted']=fields['Location']['trusted']=True
    trusted=env['StructuredOCRText']('',{'fields':fields,'classification_text':''})
    metadata=env['raily_extract_metadata'](trusted)
    assert metadata['person']=='Jordan Vale' and metadata['location']=='West Yard',metadata
    assert metadata['start_date']=='2024-06-03'
    # Spatial Start Date is a last fallback, never an override of a preference.
    overrides = {'_family_profile': lambda *a: {}, '_date_profile': lambda *a, **k: {},
                 '_find_date_near_label': lambda *a: None,
                 'DATE_LABELS_BY_FAMILY': {}, 'GENERIC_SAFE_DATE_LABELS': []}
    saved = {key: env.get(key) for key in overrides}
    env.update(overrides)
    assert env['detect_family_date']('Forms', 'Work Log', trusted)[0] == datetime(2024,6,3)
    env['_date_profile'] = lambda *a, **k: {'preferred_date_label': 'end date'}
    assert env['detect_family_date']('Forms', 'Work Log', trusted)[0] is None
    env['_date_profile'] = lambda *a, **k: {}
    fields['Start Date']['trusted'] = False
    assert env['detect_family_date']('Forms', 'Work Log', trusted)[0] is None
    fields['Start Date']['trusted'] = True
    env.update(saved)
    # Deadline bounds OCR even if there are more fallback regions available.
    original_time,original_ocr=env['time'],env['pytesseract']
    ticks=iter(range(0,200,4))
    env['time']=SimpleNamespace(perf_counter=time.perf_counter,monotonic=lambda:next(ticks))
    env['pytesseract']=SimpleNamespace(image_to_string=Mock(return_value=''))
    assert env['_read_taught_date'](sample,region,page_image=image) is None
    assert env['pytesseract'].image_to_string.call_count<=5
    env['time'],env['pytesseract']=original_time,original_ocr
    # An actual PDF render is shared between visual, OCR and date consumers.
    pdf_path=output/'native.pdf'
    with fitz.open() as document:
        page=document.new_page()
        for row in range(8):
            page.insert_text((45,50+row*30),'Printed document content for native text and cache verification.',fontsize=12)
        document.save(pdf_path)
    loader=Mock(wraps=env['_uncached_load_page_image'])
    env['_uncached_load_page_image']=loader
    first=env['_load_page_image'](pdf_path,0,1.15)
    first.putpixel((0,0),(0,0,0))
    second=env['_load_page_image'](pdf_path,0,2.25)
    assert second.getpixel((0,0))==(255,255,255) and loader.call_count==1
    env['pytesseract']=SimpleNamespace(image_to_data=Mock(side_effect=AssertionError('Unexpected native PDF OCR')))
    native=env['get_cached_ocr'](pdf_path)
    assert 'Printed document content' in native and loader.call_count==1
    assert env['get_cached_ocr'](pdf_path) is native
    env['pytesseract']=original_ocr
    # Review preparation really runs on a worker and ignores a changed selection.
    callbacks=[]
    ready=threading.Event()
    class Window:
        def after(self,delay,callback):
            callbacks.append(callback);ready.set()
        def winfo_exists(self):
            return True
    window=Window()
    threads=[]
    cached=env['get_cached_ocr']
    env['get_cached_ocr']=lambda path: threads.append(threading.get_ident())
    continuation=Mock()
    assert env['_defer_review_ocr'](window,sample,continuation,lambda:None)
    assert ready.wait(3)
    callbacks.pop()()
    assert threads[0]!=threading.get_ident() and not continuation.called
    env['get_cached_ocr']=cached
    print('PASS fast-date equivalence, protected code, geometric fields and metadata confidence gating',flush=True)
    print('PASS date deadline, shared render, native PDF fast path and background review preparation',flush=True)

def fixtures(output):
    fontdir=Path(os.environ.get('WINDIR','C:/Windows'))/'Fonts'
    printed=ImageFont.truetype(str(fontdir/'arial.ttf'),28)
    title=ImageFont.truetype(str(fontdir/'arial.ttf'),40)
    pen=ImageFont.truetype(str(fontdir/'segoepr.ttf'),29)
    made=[]
    for kind in ('printed','handwriting-style','table','rotated','poor-contrast','crossed-date','columns-below'):
        image=Image.new('RGB',(1200,1050),'white')
        draw=ImageDraw.Draw(image)
        draw.text((45,25),'RAILROAD WORK LOG',font=title,fill='black')
        for y,label,value in ([] if kind=='columns-below' else [(125,'Name:','Jordan Vale'),(225,'Start Date:','6/3/24'),(325,'Location:','West Yard')]):
            draw.text((45,y),label,font=printed,fill='black')
            draw.text((295,y-4),value,font=printed if kind=='printed' else pen,fill='black' if kind=='printed' else '#183685')
        if kind=='columns-below':
            for x,label,value in [(45,'Name:','Jordan Vale'),(430,'Start Date:','6/3/24'),(810,'Location:','West Yard')]:
                draw.text((x,125),label,font=printed,fill='black')
                draw.text((x,180),value,font=pen,fill='#183685')
        draw.text((45,425),'Daily work record. Complete fields before filing.',font=printed,fill='black')
        if kind in ('table','crossed-date'):
            for y in (500,560,620,680,740):
                draw.line((45,y,1150,y),fill='black',width=2)
            for x in (45,400,760,1150):
                draw.line((x,500,x,740),fill='black',width=2)
            for x,text in [(70,'Work'),(430,'Hours'),(790,'Remarks')]:
                draw.text((x,512),text,font=printed,fill='black')
            for x,text in [(70,'Track inspection'),(430,'8'),(790,'Completed')]:
                draw.text((x,572),text,font=pen,fill='#183685')
        if kind=='crossed-date':
            draw.line((280,245,650,245),fill='black',width=2)
        if kind=='rotated':
            image=image.rotate(3,expand=True,fillcolor='white')
        if kind=='poor-contrast':
            image=ImageOps.grayscale(image).point(lambda p: int(155+p*.35)).convert('RGB')
        path=output/(kind+'.png')
        image.save(path)
        made.append((kind,path))
    return made,pen

def ocr_tests(env,old,output):
    tesseract=shutil.which('tesseract') or r'C:\Program Files\Tesseract-OCR\tesseract.exe'
    pytesseract.pytesseract.tesseract_cmd=tesseract
    made,pen=fixtures(output)
    results=[]
    for kind,path in made:
        raw=old['ocr_file'](path)
        value=env['ocr_file'](path)
        preview=env['structured_ocr_preview'](value)
        (output/(kind+'-old.txt')).write_text(raw,encoding='utf-8')
        (output/(kind+'-structured.txt')).write_text(preview,encoding='utf-8')
        fields=value.structured['fields']
        for label,expected in {'Name':'Jordan Vale','Start Date':'2024-06-03','Location':'West Yard'}.items():
            assert fields.get(label,{}).get('trusted') and fields[label]['value']==expected,(kind,label,fields)
        assert 'Daily work record.' in preview,(kind,preview)
        old_metadata=old['raily_extract_metadata'](raw)
        old_date=old['_find_date_near_label'](raw,'start date',99)
        summary={label:{'value':item.get('value'),'trusted':item.get('trusted'),'method':item.get('method')} for label,item in fields.items()}
        results.append({'fixture':kind,'fields':summary,'old_location':old_metadata['location'],
                        'old_start_date':str(old_date[0].date()) if old_date else None,
                        'seconds':sum(p['seconds'] for p in value.structured['pages']),
                        'field_calls':sum(p['field_calls'] for p in value.structured['pages'])})
        print(kind,json.dumps(results[-1]),flush=True)
    for text in ('6/3/24','06/03/2024','9/12/26','6-17-26'):
        image=Image.new('RGB',(430,100),'white')
        ImageDraw.Draw(image).text((15,18),text,font=pen,fill='#183685')
        path=output/('date-'+text.replace('/','_')+'.png')
        image.save(path)
        result=env['_ocr_date_region'](path,{'x0':0,'y0':0,'x1':1,'y1':1})
        expected=env['parse_date'](text)
        assert result and result[0]==expected,(text,result)
        print('date',text,result[:2] if result else 'review required',env['logs'][-1],flush=True)
        results.append({'date':text,'recognized':str(result[0].date()) if result else None,'log':env['logs'][-1]})
    # The same line-crossed sample also exercises the taught-area reader.
    path=output/'crossed-date.png'
    result=env['_ocr_date_region'](path,{'x0':280/1200,'y0':215/1050,'x1':670/1200,'y1':280/1050})
    assert result and result[0]==datetime(2024,6,3),result
    results.append({'date':'line-crossed 6/3/24','recognized':str(result[0].date()),'log':env['logs'][-1]})
    print('crossed taught area',env['logs'][-1],flush=True)
    fontdir=Path(os.environ.get('WINDIR','C:/Windows'))/'Fonts'
    printed=ImageFont.truetype(str(fontdir/'arial.ttf'),36)
    for text in ('09/12/2026','9/12/26','09-12-2026','2026-09-12'):
        image=Image.new('RGB',(520,120),'white')
        ImageDraw.Draw(image).text((25,30),text,font=printed,fill='black')
        path=output/'fast-date.png';image.save(path)
        counts=[]
        for target in (old,env):
            original=target['pytesseract']
            counter=Mock(wraps=pytesseract.image_to_string)
            target['pytesseract']=SimpleNamespace(image_to_string=counter)
            result=target['_read_taught_date'](path,{'x0':0,'y0':0,'x1':1,'y1':1},fast=True,page_image=image)
            assert result and result[0]==datetime(2026,9,12),(text,result)
            counts.append(counter.call_count)
            target['pytesseract']=original
        assert counts==[2,2],(text,counts)
        results.append({'fast_date':text,'v72_1_calls':counts[0],'v72_2_calls':counts[1]})
    print('PASS real printed fast path: four formats, two calls in both v72.1 and v72.2',flush=True)
    (output/'results.json').write_text(json.dumps(results,indent=2),encoding='utf-8')
    return results

if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--ocr',action='store_true')
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    args.output.mkdir(parents=True,exist_ok=True)
    env,tree=load_release('RAILY_SmartScan_v72_2.py')
    old,old_tree=load_release('RAILY_SmartScan_v72_1.py')
    regression(env,old,tree,old_tree,args.output)
    if args.ocr:
        ocr_tests(env,old,args.output)
