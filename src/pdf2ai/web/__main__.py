import argparse
import uvicorn
from .server import create_app


def main():
    parser = argparse.ArgumentParser(description="PDF2AI private in-browser web app")
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8765)
    args = parser.parse_args()
    print(f'PDF2AI Web App: http://{args.host}:{args.port}/', flush=True)
    print('All PDF processing runs locally in your browser. No files are uploaded to or stored on this host device.', flush=True)
    uvicorn.run(create_app(), host=args.host, port=args.port, access_log=False)


if __name__ == '__main__':
    main()
