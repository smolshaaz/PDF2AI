"""Single-user trial. OCR stays in the same offline child process as desktop."""
import asyncio
from contextlib import asynccontextmanager
import json
import os
from pathlib import Path
import secrets
import re
import shutil
import subprocess
import sys
import tempfile
import time

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, Response
from pdf2ai.utils.markdown import rendered_html, plain_text

LIMIT = 250 * 1024 * 1024


def create_app(token):
    jobs = {}
    lock = asyncio.Lock()
    root = Path(tempfile.mkdtemp(prefix='pdf2ai-trial-'))

    async def stop(job):
        job['cancelled'] = True
        proc = job.get('process')
        if proc and proc.poll() is None:
            proc.terminate()
            try:
                await asyncio.to_thread(proc.wait, 3)
            except subprocess.TimeoutExpired:
                proc.kill()
                await asyncio.to_thread(proc.wait)
        task = job.get('task')
        if task and not task.done():
            if proc is None:
                task.cancel()
                job['status'] = 'Stopped'
            try:
                await task
            except asyncio.CancelledError:
                pass

    async def expire():
        while True:
            await asyncio.sleep(60)
            for key, job in list(jobs.items()):
                if job['status'] in {'Done', 'Failed', 'Stopped'} and time.monotonic()-job['updated'] > 3600:
                    jobs.pop(key, None)
                    shutil.rmtree(job['folder'], ignore_errors=True)

    @asynccontextmanager
    async def lifespan(app):
        cleanup = asyncio.create_task(expire())
        yield
        cleanup.cancel()
        for job in list(jobs.values()):
            await stop(job)
        shutil.rmtree(root, ignore_errors=True)

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

    @app.middleware('http')
    async def private(request, call_next):
        if request.url.path != '/' and not secrets.compare_digest(request.headers.get('x-pdf2ai-token', ''), token):
            return Response('Open the private trial link to continue.', status_code=403)
        length = request.headers.get('content-length')
        if length and (not length.isdigit() or int(length) > LIMIT):
            return Response('PDF must be smaller than 250 MB.', status_code=413)
        response = await call_next(request)
        response.headers['Cache-Control'] = 'no-store'
        response.headers['Referrer-Policy'] = 'no-referrer'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'none'; frame-src 'none'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'"
        return response

    def get_job(key):
        if key not in jobs:
            raise HTTPException(404, 'This session has expired. Add the PDF again.')
        return jobs[key]

    def snapshot(job):
        return {k: job.get(k) for k in ('id', 'name', 'status', 'done', 'total', 'stage', 'seconds', 'warnings', 'error')}

    async def run(job):
        async with lock:
            if job['cancelled']:
                job['status'] = 'Stopped'
                return
            job['status'] = 'Processing'
            folder = job['folder']
            events = folder/'events.jsonl'
            spec = folder/'job.json'
            spec.write_text(json.dumps({'paths': [str(job['source'])], 'event_file': str(events),
                'stop_file': str(folder/'stop'), 'output_dir': str(folder/'output')}))
            proc = None
            try:
                env = os.environ.copy()
                env['PYTHONUNBUFFERED'] = '1'
                proc = subprocess.Popen([sys.executable, '-m', 'pdf2ai.workers.worker_cli', str(spec)],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env)
                job['process'] = proc
                offset = 0
                while True:
                    ended = proc.poll() is not None
                    if events.exists():
                        with events.open(encoding='utf-8') as stream:
                            stream.seek(offset)
                            while line := stream.readline():
                                if not line.endswith('\n'):
                                    break
                                offset = stream.tell()
                                event = json.loads(line)
                                if event[0] == 'progress':
                                    job.update(done=event[1], total=event[2], stage=event[3], seconds=round(event[4],1))
                                elif event[0] == 'result':
                                    result = event[2]
                                    if result['ok']:
                                        job.update(status='Done', output=Path(result['output']), warnings=result['warnings'], seconds=result['seconds'])
                                    else:
                                        job.update(status='Failed', error=result['error'])
                                elif event[0] == 'fatal':
                                    job.update(status='Failed', error='Local processing could not finish: '+event[1])
                    if ended:
                        break
                    await asyncio.sleep(.2)
                if job['cancelled']:
                    job['status'] = 'Stopped'
                elif job['status'] == 'Processing':
                    job.update(status='Failed', error='The processing worker stopped unexpectedly. Please retry.')
            except Exception as exc:
                job.update(status='Failed', error='Local processing could not finish: '+type(exc).__name__)
            finally:
                if proc and proc.poll() is None:
                    proc.kill()
                    await asyncio.to_thread(proc.wait)
                job['updated'] = time.monotonic()

    @app.get('/')
    async def home():
        return HTMLResponse(Path(__file__).with_name('index.html').read_text())

    @app.post('/jobs')
    async def upload(request: Request):
        if len(jobs) >= 20:
            raise HTTPException(429, 'Remove finished PDFs before adding more.')
        name = request.headers.get('x-file-name', 'document.pdf')
        from urllib.parse import unquote
        name = Path(unquote(name).replace('\\', '/')).name
        if not name.lower().endswith('.pdf') or name in {'.pdf', '..pdf'}:
            raise HTTPException(400, 'Please choose a PDF file.')
        key = secrets.token_hex(16)
        folder = root/key
        folder.mkdir()
        source = folder/name
        try:
            size = 0
            with source.open('wb') as stream:
                async for chunk in request.stream():
                    size += len(chunk)
                    if size > LIMIT:
                        raise HTTPException(413, 'PDF must be smaller than 250 MB.')
                    stream.write(chunk)
            if size == 0:
                raise HTTPException(400, 'This file is empty.')
        except BaseException:
            shutil.rmtree(folder, ignore_errors=True)
            raise
        job = dict(id=key, name=name, folder=folder, source=source, status='Queued', done=0,
            total=0, stage='Waiting', seconds=0, warnings=[], error='', cancelled=False, updated=time.monotonic())
        jobs[key] = job
        job['task'] = asyncio.create_task(run(job))
        return snapshot(job)

    @app.get('/jobs/{key}')
    async def status(key: str):
        return snapshot(get_job(key))

    @app.post('/jobs/{key}/stop')
    async def cancel(key: str):
        job = get_job(key)
        await stop(job)
        return snapshot(job)

    @app.delete('/jobs/{key}')
    async def delete(key: str):
        job = get_job(key)
        await stop(job)
        jobs.pop(key, None)
        shutil.rmtree(job['folder'], ignore_errors=True)
        return {'deleted': True}

    @app.get('/jobs/{key}/output')
    async def output(key: str, mode: str = 'source'):
        job = get_job(key)
        if job['status'] != 'Done':
            raise HTTPException(409, 'The output is not ready yet.')
        path = job['output']
        if mode == 'download':
            return FileResponse(path, media_type='text/markdown', filename=path.name)
        text = await asyncio.to_thread(path.read_text, encoding='utf-8')
        if mode == 'view':
            text = re.sub(r'^<!-- PAGE (\d+) -->$', r'<h2>Page \1</h2>', text, flags=re.M)
            return HTMLResponse(await asyncio.to_thread(rendered_html, text))
        if mode == 'text':
            text = await asyncio.to_thread(plain_text, text)
        return Response(text, media_type='text/plain; charset=utf-8')

    return app
