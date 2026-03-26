from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from flask import Response, abort, g, jsonify, render_template, request, send_file

from app.auth import load_api_tokens
from app.common import ROOT_DIR, RUNS_DIR
from app.schemas import ALLOWED_RUNTIME_ENV_VARS, SUPPORTED_AXES
from app.services.preflight_service import build_runtime_readiness, build_task_preflight
from app.services.run_query_service import (
    find_run,
    get_artifacts,
    get_iterations,
    get_log,
    get_orchestrator,
    get_results,
    get_run_summary,
    get_tokens,
    list_runs,
    resolve_download_path,
    stream_live_events,
)
from app.services.task_submission_service import create_task
from app.task_manager import audit_event, cleanup_expired_runs, discover_workers, stop_run

SOURCE_AUTORESEARCH_DIR = os.environ.get("SOURCE_AUTORESEARCH_DIR", str(ROOT_DIR.parent / "autoresearch"))
UPLOAD_STAGING_DIR = RUNS_DIR / "_uploads"


def auth_is_enabled() -> bool:
    return bool(load_api_tokens())


def current_auth() -> dict[str, Any] | None:
    value = getattr(g, "auth_context", None)
    return value if isinstance(value, dict) else None


def require_auth_for_write() -> dict[str, Any] | None:
    if not auth_is_enabled():
        return None
    auth = current_auth()
    if auth is None:
        abort(401, description="unauthorized")
    return auth


def ensure_submit_allowed(auth: dict[str, Any] | None) -> None:
    if auth and not auth.get("can_submit", True):
        abort(403, description="submit forbidden")


def ensure_cleanup_allowed(auth: dict[str, Any] | None) -> None:
    if auth and not auth.get("can_cleanup", False):
        abort(403, description="cleanup forbidden")


def ensure_probe_allowed(auth: dict[str, Any] | None) -> None:
    if auth and not auth.get("can_probe_model", True):
        abort(403, description="probe forbidden")


def ensure_manage_workers_allowed(auth: dict[str, Any] | None) -> None:
    if auth and not auth.get("can_manage_workers", False):
        abort(403, description="worker management forbidden")


def ensure_run_access(run_detail: dict[str, Any], auth: dict[str, Any] | None, stop: bool = False) -> None:
    if auth is None:
        return
    if auth.get("can_stop_any_run", False):
        return
    if auth.get("can_read_all_runs", False):
        run_tenant_id = str(run_detail.get("tenant_id", "") or "")
        auth_tenant_id = str(auth.get("tenant_id", "") or "")
        if run_tenant_id and auth_tenant_id and run_tenant_id == auth_tenant_id:
            return
    if str(run_detail.get("user_id", "")) != str(auth.get("user_id", "")):
        if str(run_detail.get("tenant_id", "")) != str(auth.get("tenant_id", "")):
            abort(403, description="forbidden for this run")
    if stop and not auth.get("can_submit", True):
        abort(403, description="stop forbidden")


def visible_runs() -> list[dict[str, Any]]:
    auth = current_auth()
    items: list[dict[str, Any]] = []
    for item in list_runs():
        try:
            ensure_run_access(item, auth)
        except Exception:
            continue
        items.append(item)
    return items


def require_run(run_id: str) -> dict[str, Any]:
    item = find_run(run_id)
    if not item:
        abort(404, description="run not found")
    ensure_run_access(item, current_auth())
    return item


def deprecated_query_response(route: str):
    return (
        jsonify({"error": f"deprecated query endpoint; use /api/runs/<run_id>/{route}"}),
        410,
    )


def register_dashboard_routes(app) -> None:
    @app.route("/")
    def index():
        return render_template("index.html")

    @app.route("/api/runs")
    def api_runs():
        return jsonify(visible_runs())

    @app.route("/api/runs/<run_id>")
    def api_run_detail(run_id: str):
        return jsonify(require_run(run_id))

    @app.route("/api/runs/<run_id>/summary")
    def api_run_summary(run_id: str):
        require_run(run_id)
        return jsonify(get_run_summary(run_id))

    @app.route("/api/runtime/readiness")
    def api_runtime_readiness():
        return jsonify(
            build_runtime_readiness(
                root_dir=ROOT_DIR,
                runs_dir=RUNS_DIR,
                source_autoresearch_dir=SOURCE_AUTORESEARCH_DIR,
                auth_enabled=auth_is_enabled(),
            )
        )

    @app.route("/api/runtime/probe-model", methods=["POST"])
    def api_runtime_probe_model():
        auth = require_auth_for_write()
        ensure_submit_allowed(auth)
        ensure_probe_allowed(auth)
        try:
            report = build_task_preflight(
                form=request.form,
                files=request.files,
                auth=auth,
                upload_staging_dir=UPLOAD_STAGING_DIR,
                source_autoresearch_dir=SOURCE_AUTORESEARCH_DIR,
            )
            audit_event(
                "runtime_probe_model",
                user_id=(auth or {}).get("user_id", request.form.get("user_id", "")),
                tenant_id=(auth or {}).get("tenant_id", request.form.get("tenant_id", "")),
                ok=bool((report.get("probe") or {}).get("ok")),
            )
            probe = report.get("probe") or {"ok": False, "error": "probe_model not requested"}
            status = 200 if probe.get("ok") else 400
            return jsonify({"probe": probe, "checks": report.get("checks", {}), "run_spec": report.get("run_spec", {})}), status
        except ValueError as exc:
            return jsonify({"error": str(exc), "supported_axes": SUPPORTED_AXES, "allowed_env_vars": ALLOWED_RUNTIME_ENV_VARS}), 400

    @app.route("/api/tasks/preflight", methods=["POST"])
    def api_task_preflight():
        auth = require_auth_for_write()
        ensure_submit_allowed(auth)
        ensure_probe_allowed(auth)
        try:
            report = build_task_preflight(
                form=request.form,
                files=request.files,
                auth=auth,
                upload_staging_dir=UPLOAD_STAGING_DIR,
                source_autoresearch_dir=SOURCE_AUTORESEARCH_DIR,
            )
            audit_event(
                "task_preflight",
                user_id=(auth or {}).get("user_id", request.form.get("user_id", "")),
                tenant_id=(auth or {}).get("tenant_id", request.form.get("tenant_id", "")),
                ok=report.get("ok", False),
                real_execution=report.get("real_execution", False),
                probe_model=report.get("probe_model", False),
            )
            status = 200 if report.get("ok") else 400
            return jsonify(report), status
        except ValueError as exc:
            return jsonify({"error": str(exc), "supported_axes": SUPPORTED_AXES, "allowed_env_vars": ALLOWED_RUNTIME_ENV_VARS}), 400

    @app.route("/api/tasks", methods=["POST"])
    def api_create_task():
        auth = require_auth_for_write()
        ensure_submit_allowed(auth)
        try:
            manifest = create_task(
                form=request.form,
                files=request.files,
                auth=auth,
                upload_staging_dir=UPLOAD_STAGING_DIR,
            )
            audit_event(
                "task_created",
                run_id=manifest["run_id"],
                user_id=manifest.get("user_id", ""),
                tenant_id=manifest.get("tenant_id", ""),
            )
            item = find_run(manifest["run_id"])
            return jsonify(item or {"run_id": manifest["run_id"]}), 201
        except ValueError as exc:
            return jsonify({"error": str(exc), "supported_axes": SUPPORTED_AXES, "allowed_env_vars": ALLOWED_RUNTIME_ENV_VARS}), 400

    @app.route("/api/runs/<run_id>/stop", methods=["POST"])
    def api_stop_run(run_id: str):
        auth = require_auth_for_write()
        item = require_run(run_id)
        ensure_run_access(item, auth, stop=True)
        stop_run(run_id)
        refreshed = find_run(run_id)
        return jsonify(refreshed or {"run_id": run_id, "status": "stopped"})

    @app.route("/api/runs/cleanup", methods=["POST"])
    def api_cleanup_runs():
        auth = require_auth_for_write()
        ensure_cleanup_allowed(auth)
        force = (request.args.get("force") or "").strip().lower() in {"1", "true", "yes"}
        cleaned = cleanup_expired_runs(force=force)
        return jsonify({"cleaned": cleaned, "count": len(cleaned)})

    @app.route("/api/workers")
    def api_workers():
        auth = require_auth_for_write() if auth_is_enabled() else None
        ensure_manage_workers_allowed(auth)
        return jsonify(discover_workers())

    @app.route("/api/runs/<run_id>/artifacts")
    def api_run_artifacts(run_id: str):
        require_run(run_id)
        return jsonify(get_artifacts(run_id))

    @app.route("/api/runs/<run_id>/download/<path:relative_path>")
    def api_run_download(run_id: str, relative_path: str):
        require_run(run_id)
        try:
            target = resolve_download_path(run_id, relative_path)
        except FileNotFoundError:
            abort(404)
        audit_event("artifact_downloaded", run_id=run_id, path=relative_path)
        return send_file(target, as_attachment=True)

    @app.route("/api/runs/<run_id>/results")
    def api_run_results(run_id: str):
        require_run(run_id)
        return jsonify(get_results(run_id))

    @app.route("/api/runs/<run_id>/iterations")
    def api_run_iterations(run_id: str):
        require_run(run_id)
        return jsonify(get_iterations(run_id))

    @app.route("/api/runs/<run_id>/orchestrator")
    def api_run_orchestrator(run_id: str):
        require_run(run_id)
        return jsonify(get_orchestrator(run_id))

    @app.route("/api/runs/<run_id>/tokens")
    def api_run_tokens(run_id: str):
        require_run(run_id)
        return jsonify(get_tokens(run_id))

    @app.route("/api/runs/<run_id>/log")
    def api_run_log(run_id: str):
        require_run(run_id)
        return jsonify(get_log(run_id))

    @app.route("/api/runs/<run_id>/stream")
    def api_run_stream(run_id: str):
        require_run(run_id)
        return Response(
            stream_live_events(run_id),
            mimetype="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.route("/api/results")
    def api_results():
        return deprecated_query_response("results")

    @app.route("/api/iterations")
    def api_iterations():
        return deprecated_query_response("iterations")

    @app.route("/api/orchestrator")
    def api_orchestrator():
        return deprecated_query_response("orchestrator")

    @app.route("/api/tokens")
    def api_tokens():
        return deprecated_query_response("tokens")

    @app.route("/api/log")
    def api_log():
        return deprecated_query_response("log")

    @app.route("/api/stream")
    def api_stream():
        return deprecated_query_response("stream")
