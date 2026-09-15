"""Client-side web distribution server.

Serves the offline in-browser PDF2AI application. All PDF parsing, OCR, and
Markdown generation execute entirely on the user's client device inside the
browser (via WebAssembly and Web Workers). No documents or outputs are ever
uploaded to, processed on, or stored on the host device.
"""
from pathlib import Path
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles


def get_dist_dir() -> Path:
    # Check bundled distribution in package first, fallback to web-local/dist
    pkg_dist = Path(__file__).parent / 'dist'
    if pkg_dist.is_dir() and (pkg_dist / 'index.html').is_file():
        return pkg_dist
    repo_dist = Path(__file__).resolve().parents[3] / 'web-local' / 'dist'
    if repo_dist.is_dir() and (repo_dist / 'index.html').is_file():
        return repo_dist
    return pkg_dist


def create_app(token: str = None) -> FastAPI:
    dist_dir = get_dist_dir()
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    @app.middleware('http')
    async def add_security_headers(request, call_next):
        response = await call_next(request)
        response.headers['Referrer-Policy'] = 'no-referrer'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        # Allow client-side WebAssembly, Web Workers, and blob URLs
        response.headers['Content-Security-Policy'] = (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-eval' 'wasm-unsafe-eval' blob:; "
            "worker-src 'self' blob:; "
            "style-src 'self' 'unsafe-inline'; "
            "img-src 'self' blob: data:; "
            "connect-src 'self' blob: data:; "
            "font-src 'self'; "
            "frame-src 'self'; "
            "object-src 'none'; "
            "base-uri 'none';"
        )
        return response

    if dist_dir.exists() and (dist_dir / 'index.html').is_file():
        app.mount('/', StaticFiles(directory=str(dist_dir), html=True), name='static')
    else:
        @app.get('/')
        async def fallback():
            return Response(
                "PDF2AI web assets not built. Run 'npm run build' inside web-local.",
                status_code=503,
                media_type="text/plain"
            )

    return app
