"""Temporary HTTPS gate for an explicitly marked Linux staging VM.

Not a production proxy. Never exposes login or administrative routes. Stop its
transient unit and delete the temporary key after the Windows service exercise.
"""
import argparse
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import re
import ssl


MAX_BODY = 21 * 1024 * 1024
ROUTES = {
    'GET': re.compile(r'/health|/api/files/status/[a-f0-9]{32}|/api/delivery/files/[a-f0-9]{32}'),
    'POST': re.compile(r'/api/activate|/api/delivery/lease|/api/delivery/ack/[a-f0-9]{32}'),
    'PUT': re.compile(r'/api/files/[A-Za-z0-9_.% -]{1,360}'),
}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def forward(self):
        self.connection.settimeout(30)
        if not ROUTES[self.command].fullmatch(self.path):
            self.send_error(404)
            return
        raw = self.headers.get('Content-Length', '0')
        if self.headers.get('Transfer-Encoding') or not re.fullmatch('[0-9]{1,9}', raw):
            self.send_error(400)
            return
        length = int(raw)
        if length > MAX_BODY:
            self.send_error(413)
            return
        connection = http.client.HTTPConnection('127.0.0.1', 8765, timeout=30)
        try:
            body = self.rfile.read(length)
            if len(body) != length:
                self.send_error(400)
                return
            headers = {k: v for k, v in self.headers.items()
                       if k.lower() in {'authorization', 'content-type', 'x-content-sha256', 'x-delivery-lease'}}
            headers['Content-Length'] = str(length)
            connection.request(self.command, self.path, body=body, headers=headers)
            response = connection.getresponse()
            payload = response.read(MAX_BODY + 1)
            if len(payload) > MAX_BODY:
                raise ValueError('Oversized upstream response')
            self.send_response(response.status)
            self.send_header('Content-Type', response.getheader('Content-Type', 'application/octet-stream'))
            self.send_header('Content-Length', str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
        except (OSError, ValueError, http.client.HTTPException):
            # Never print headers, bodies or exceptions containing credentials.
            self.close_connection = True
        finally:
            connection.close()

    do_GET = do_POST = do_PUT = forward


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--cert', required=True)
    parser.add_argument('--key', required=True)
    args = parser.parse_args()
    if not Path('/var/lib/tracer-transfer/.staging-deployment').is_file():
        raise RuntimeError('Explicit staging deployment required')
    server = ThreadingHTTPServer(('127.0.0.1', 18443), Handler)
    server.daemon_threads = True
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(args.cert, args.key)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
