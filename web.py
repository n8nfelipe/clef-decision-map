"""Servidor web local para o painel de trânsito."""

from __future__ import annotations

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from app import WorkersAIError, analyze_traffic_with_cloudflare, load_dotenv
from traffic import GoogleMapsError, get_traffic_route


ROOT = Path(__file__).parent


class DashboardHandler(BaseHTTPRequestHandler):
    def _send_json(self, status: int, payload: dict) -> None:
        content = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(content)

    def _send_file(self, path: Path, content_type: str) -> None:
        try:
            content = path.read_bytes()
        except FileNotFoundError:
            self._send_json(404, {"error": "Arquivo não encontrado."})
            return
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path in {"/", "/index.html"}:
            self._send_file(ROOT / "dashboard.html", "text/html; charset=utf-8")
            return
        if parsed.path == "/healthz":
            self._send_json(200, {"ok": True})
            return
        if parsed.path == "/api/traffic":
            self._traffic(parse_qs(parsed.query))
            return
        self._send_json(404, {"error": "Rota não encontrada."})

    def _traffic(self, query: dict[str, list[str]]) -> None:
        origin = query.get("origin", [None])[0]
        destination = query.get("destination", [None])[0]
        try:
            route = get_traffic_route(origin, destination) if origin and destination else get_traffic_route()
            result = {
                "route": route,
                "clef": analyze_traffic_with_cloudflare(route),
            }
        except (GoogleMapsError, WorkersAIError) as exc:
            self._send_json(503, {"error": str(exc)})
            return
        self._send_json(200, result)

    def log_message(self, format: str, *args: object) -> None:
        print(f"[dashboard] {self.address_string()} - {format % args}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Painel local de trânsito com Clef")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    load_dotenv()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), DashboardHandler)
    print(f"Painel disponível em http://127.0.0.1:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nPainel encerrado.")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
