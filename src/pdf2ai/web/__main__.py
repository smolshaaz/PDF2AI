import argparse
import secrets
from pathlib import Path
import uvicorn
from .server import create_app


def main():
    parser = argparse.ArgumentParser(description="PDF2AI private browser trial")
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8765)
    args = parser.parse_args()
    token = secrets.token_urlsafe(32)
    print(f'PDF2AI: http://{args.host}:{args.port}/#{token}', flush=True)
    uvicorn.run(create_app(token), host=args.host, port=args.port, access_log=False)


if __name__ == '__main__':
    main()
