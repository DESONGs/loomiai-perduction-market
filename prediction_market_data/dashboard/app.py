import os
import sys
from pathlib import Path

from flask import Flask, abort, g, jsonify, request

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.api.dashboard_routes import register_dashboard_routes
from app.auth import load_api_tokens, resolve_auth_context
from app.common import RUNS_DIR


app = Flask(__name__)


@app.before_request
def bind_auth_context():
    token_source = request.headers.get("Authorization", "") or request.args.get("auth_token", "")
    g.auth_context = resolve_auth_context(token_source)
    if bool(load_api_tokens()) and request.path.startswith("/api/") and g.auth_context is None:
        abort(401, description="unauthorized")


@app.errorhandler(401)
def handle_401(err):
    return jsonify({"error": getattr(err, "description", "unauthorized")}), 401


@app.errorhandler(403)
def handle_403(err):
    return jsonify({"error": getattr(err, "description", "forbidden")}), 403


register_dashboard_routes(app)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5050"))
    print("Dashboard 启动中...")
    print(f"  RUNS_DIR: {RUNS_DIR}")
    print(f"  访问: http://localhost:{port}")
    app.run(host="0.0.0.0", port=port, debug=False)
