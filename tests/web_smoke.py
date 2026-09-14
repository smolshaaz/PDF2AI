"""Run outside pytest's deliberately network-disabled OCR process."""
import json
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
from urllib.request import Request, urlopen
from urllib.error import HTTPError
import pymupdf


def main():
    with socket.socket() as s:
        s.bind(('127.0.0.1',0))
        port=s.getsockname()[1]
    proc=subprocess.Popen([sys.executable,'-c',
        f"import uvicorn; from pdf2ai.web.server import create_app; uvicorn.run(create_app('test-key'),host='127.0.0.1',port={port},access_log=False)"],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    def request(path, data=None, method=None, auth=True):
        headers={'x-file-name':'sample.pdf','Content-Type':'application/pdf'}
        if auth: headers['x-pdf2ai-token']='test-key'
        return urlopen(Request(f'http://127.0.0.1:{port}'+path,data=data,headers=headers,method=method),timeout=10)
    try:
        for _ in range(100):
            try:
                request('/').close();break
            except OSError: time.sleep(.1)
        try:
            request('/jobs/unknown',auth=False)
            raise AssertionError('Missing token accepted')
        except HTTPError as e: assert e.code==403
        with pymupdf.open() as doc:
            p=doc.new_page();p.insert_text((40,60),'A printed policy for browser testing.')
            pdf=doc.tobytes()
        bad=json.load(request('/jobs',b'bad pdf'))
        good=json.load(request('/jobs',pdf))
        for _ in range(200):
            status=json.load(request('/jobs/'+good['id']))
            if status['status'] in {'Done','Failed'}:break
            time.sleep(.1)
        assert status['status']=='Done',status
        assert json.load(request('/jobs/'+bad['id']))['status']=='Failed'
        source=request('/jobs/'+good['id']+'/output').read().decode()
        assert '<!-- PAGE 1 -->' in source and 'printed policy' in source
        view=request('/jobs/'+good['id']+'/output?mode=view').read().decode()
        assert '<p>' in view and 'printed policy' in view
        assert request('/jobs/'+good['id']+'/output?mode=download').read().decode()==source
        stop=json.load(request('/jobs',pdf))
        stopped=json.load(request('/jobs/'+stop['id']+'/stop',b'',method='POST'))
        assert stopped['status'] in {'Stopped','Done'}
        for job in [bad,good,stop]:
            assert json.load(request('/jobs/'+job['id'],method='DELETE'))['deleted']
        print('Browser worker: upload, auth, failure isolation, progress, preview, download, stop and cleanup passed')
    finally:
        proc.terminate()
        try:proc.wait(10)
        except subprocess.TimeoutExpired:proc.kill();proc.wait()

if __name__=='__main__':main()
