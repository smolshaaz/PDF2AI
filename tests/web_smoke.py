"""Verify that the web app is served purely client-side with no host-side document processing."""
from pathlib import Path
import socket
import subprocess
import sys
import time
from urllib.request import Request, urlopen
from urllib.error import HTTPError


def main():
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        port = s.getsockname()[1]

    proc = subprocess.Popen(
        [
            sys.executable,
            '-c',
            f"import uvicorn; from pdf2ai.web.server import create_app; uvicorn.run(create_app(), host='127.0.0.1', port={port}, access_log=False)",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    def request(path, data=None, method=None):
        return urlopen(
            Request(f'http://127.0.0.1:{port}' + path, data=data, method=method),
            timeout=10,
        )

    try:
        # Wait for server ready
        for _ in range(50):
            try:
                res = request('/')
                content = res.read().decode('utf-8')
                assert 'PDF2AI' in content, "Expected PDF2AI in HTML"
                assert 'Runs on this device' in content or 'drop-zone' in content
                break
            except OSError:
                time.sleep(0.1)
        else:
            raise RuntimeError("Server failed to start in time")

        # Verify that NO upload / jobs endpoints exist on the host
        try:
            request('/jobs', data=b'%PDF-1.4 sample', method='POST')
            raise AssertionError("Host server accepted a PDF upload! Uploads must be disabled.")
        except HTTPError as e:
            assert e.code in (404, 405), f"Expected 404/405 for /jobs, got {e.code}"

        print("Web server verified: serves in-browser client app, zero upload/processing endpoints on host.")
    finally:
        proc.terminate()
        try:
            proc.wait(5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()


if __name__ == '__main__':
    main()
