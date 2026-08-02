"""
Онлайн-дашборд телеграм-бота Deckview: статистика, графики, логи событий.
Доступ: /dashboard. Опциональная защита: DASHBOARD_SECRET в .env (key=... или X-Dashboard-Key).
"""
from datetime import datetime, timedelta

from flask import Blueprint, redirect, render_template, request, jsonify, abort

from deckview.config import DASHBOARD_SECRET
from deckview.repositories.web import (
    init_db,
    get_bot_events,
    get_bot_events_count,
    get_bot_stats_for_dashboard,
    get_admin_overview,
    get_all_generated_decks,
    get_decks_by_day,
    get_class_distribution,
    get_mode_distribution_web,
    get_cost_distribution_web,
    get_all_bot_users,
    get_user_activity,
    get_web_db_schema,
    get_load_stats,
    get_deck_detail,
    get_publish_logs,
    get_publish_logs_count,
    get_all_archetypes,
    add_archetype,
    update_archetype,
    delete_archetype,
    import_archetypes_from_dict,
)

bp = Blueprint("dashboard", __name__, url_prefix="/dashboard")

# ── Public admin panel (no auth) at /admin ─────────────────────────────────
admin_bp = Blueprint("admin", __name__, url_prefix="/admin")


def _check_dashboard_auth() -> bool:
    if not DASHBOARD_SECRET:
        return True
    key = request.args.get("key") or request.headers.get("X-Dashboard-Key")
    return key == DASHBOARD_SECRET


def _require_auth():
    if not _check_dashboard_auth():
        abort(401)


@bp.route("/")
@bp.route("")  # /dashboard без слэша → редирект на /dashboard/
def index():
    if request.path.rstrip("/") == "/dashboard" and not request.path.endswith("/"):
        return redirect("/dashboard/", code=302)
    init_db()
    return render_template(
        "dashboard.html",
        dashboard_key="",
        key_required=False,
    )


@bp.route("/api/stats")
def api_stats():
    init_db()
    days = request.args.get("days", type=int, default=7)
    days = max(1, min(90, days))
    stats = get_bot_stats_for_dashboard(days=days)
    # Для графиков: массив по дням (дата, суммарно за день, по типам)
    by_day = stats.get("by_day", {})
    sorted_days = sorted(by_day.keys())
    labels = sorted_days
    totals_per_day = [sum(by_day[d].values()) for d in sorted_days]
    by_type = stats.get("by_type", {})
    type_labels = list(by_type.keys())
    type_values = list(by_type.values())
    return jsonify({
        "total_events": stats["total_events"],
        "days": stats["days"],
        "chart": {
            "labels": labels,
            "totals": totals_per_day,
            "by_type_labels": type_labels,
            "by_type_values": type_values,
        },
        "by_chat_type": stats.get("by_chat_type", {}),
    })


@bp.route("/admin")
@bp.route("/admin/")
def admin_index():
    _require_auth()
    init_db()
    return render_template(
        "admin.html",
        dashboard_key=request.args.get("key", ""),
        key_required=bool(DASHBOARD_SECRET),
    )


@bp.route("/admin/api/overview")
def admin_api_overview():
    _require_auth()
    init_db()
    return jsonify(get_admin_overview())


@bp.route("/admin/api/decks")
def admin_api_decks():
    _require_auth()
    init_db()
    page     = max(1, request.args.get("page", 1, type=int))
    per_page = min(100, max(1, request.args.get("per_page", 30, type=int)))
    return jsonify(get_all_generated_decks(
        page=page,
        per_page=per_page,
        deck_class=request.args.get("deck_class") or None,
        deck_mode=request.args.get("deck_mode") or None,
        source=request.args.get("source") or None,
        search=request.args.get("search") or None,
        sort_by=request.args.get("sort_by", "created_at"),
        sort_dir=request.args.get("sort_dir", "desc"),
    ))


@bp.route("/admin/api/charts/daily")
def admin_api_charts_daily():
    _require_auth()
    init_db()
    days = min(365, max(1, request.args.get("days", 30, type=int)))
    return jsonify(get_decks_by_day(days=days))


@bp.route("/admin/api/charts/classes")
def admin_api_charts_classes():
    _require_auth()
    init_db()
    return jsonify(get_class_distribution())


@bp.route("/admin/api/charts/modes")
def admin_api_charts_modes():
    _require_auth()
    init_db()
    return jsonify(get_mode_distribution_web())


@bp.route("/admin/api/charts/costs")
def admin_api_charts_costs():
    _require_auth()
    init_db()
    return jsonify(get_cost_distribution_web())


@bp.route("/admin/api/users")
def admin_api_users():
    _require_auth()
    init_db()
    page     = max(1, request.args.get("page", 1, type=int))
    per_page = min(100, max(1, request.args.get("per_page", 50, type=int)))
    return jsonify(get_all_bot_users(
        page=page,
        per_page=per_page,
        search=request.args.get("search") or None,
    ))


@bp.route("/admin/api/schema")
def admin_api_schema():
    _require_auth()
    init_db()
    return jsonify(get_web_db_schema())


@admin_bp.route("/")
@admin_bp.route("")
def admin_root():
    init_db()
    return render_template("admin.html")


@admin_bp.route("/api/overview")
def admin_pub_overview():
    init_db()
    return jsonify(get_admin_overview())


@admin_bp.route("/api/decks")
def admin_pub_decks():
    init_db()
    page     = max(1, request.args.get("page", 1, type=int))
    per_page = min(100, max(1, request.args.get("per_page", 30, type=int)))
    return jsonify(get_all_generated_decks(
        page=page,
        per_page=per_page,
        deck_class=request.args.get("deck_class") or None,
        deck_mode=request.args.get("deck_mode") or None,
        source=request.args.get("source") or None,
        search=request.args.get("search") or None,
        sort_by=request.args.get("sort_by", "created_at"),
        sort_dir=request.args.get("sort_dir", "desc"),
    ))


@admin_bp.route("/api/decks/<int:deck_id>")
def admin_pub_deck_detail(deck_id):
    init_db()
    data = get_deck_detail(deck_id)
    if data is None:
        return jsonify({"error": "Not found"}), 404
    return jsonify(data)


@admin_bp.route("/api/charts/daily")
def admin_pub_daily():
    init_db()
    days = min(365, max(1, request.args.get("days", 30, type=int)))
    return jsonify(get_decks_by_day(days=days))


@admin_bp.route("/api/charts/classes")
def admin_pub_classes():
    init_db()
    return jsonify(get_class_distribution())


@admin_bp.route("/api/charts/modes")
def admin_pub_modes():
    init_db()
    return jsonify(get_mode_distribution_web())


@admin_bp.route("/api/charts/costs")
def admin_pub_costs():
    init_db()
    return jsonify(get_cost_distribution_web())


@admin_bp.route("/api/users")
def admin_pub_users():
    init_db()
    page     = max(1, request.args.get("page", 1, type=int))
    per_page = min(100, max(1, request.args.get("per_page", 50, type=int)))
    return jsonify(get_all_bot_users(
        page=page,
        per_page=per_page,
        search=request.args.get("search") or None,
    ))


@admin_bp.route("/api/users/<int:user_id>")
def admin_pub_user_detail(user_id):
    init_db()
    return jsonify(get_user_activity(user_id))


@admin_bp.route("/api/events")
def admin_pub_events():
    init_db()
    limit      = min(200, max(1, request.args.get("limit", 50, type=int)))
    offset     = max(0, request.args.get("offset", 0, type=int))
    event_type = request.args.get("type", "").strip() or None
    events = get_bot_events(limit=limit, offset=offset, event_type=event_type)
    total  = get_bot_events_count(event_type=event_type)
    return jsonify({"events": events, "total": total, "limit": limit, "offset": offset})


@admin_bp.route("/api/schema")
def admin_pub_schema():
    init_db()
    return jsonify(get_web_db_schema())


@admin_bp.route("/api/load")
def admin_pub_load():
    init_db()
    return jsonify(get_load_stats())


@admin_bp.route("/api/hsguru")
def admin_pub_hsguru():
    format_id = request.args.get("format", 1, type=int)
    format_id = 1 if format_id not in (1, 2) else format_id
    try:
        from deckview.integrations.hsguru_meta import get_meta
        data = get_meta(format_id)
        return jsonify({"ok": True, "format_id": format_id, "data": data})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e), "data": []}), 200


@bp.route("/api/publish-logs")
def api_publish_logs():
    _require_auth()
    init_db()
    limit  = max(1, min(200, request.args.get("limit",  50, type=int)))
    offset = max(0,          request.args.get("offset",  0, type=int))
    logs   = get_publish_logs(limit=limit, offset=offset)
    total  = get_publish_logs_count()
    return jsonify({"logs": logs, "total": total, "limit": limit, "offset": offset})


@admin_bp.route("/api/publish-logs")
def admin_pub_publish_logs():
    init_db()
    limit  = max(1, min(200, request.args.get("limit",  50, type=int)))
    offset = max(0,          request.args.get("offset",  0, type=int))
    logs   = get_publish_logs(limit=limit, offset=offset)
    total  = get_publish_logs_count()
    return jsonify({"logs": logs, "total": total, "limit": limit, "offset": offset})


@admin_bp.route("/api/arena")
def admin_pub_arena():
    view = request.args.get("view", "tier")
    try:
        from deckview.integrations.arena_stats import (
            _RU_TO_CODE,
            get_arena_matrix,
            get_arena_stats,
        )

        if view == "matrix":
            data = get_arena_matrix()
            stats = sorted(
                data.get("stats", []),
                key=lambda x: float(x.get("_win_rate") or 0),
                reverse=True,
            )
            class_order = [s["playerClass"] for s in stats if s.get("playerClass") in _RU_TO_CODE]
            matchups = [
                m for m in data.get("matchups", [])
                if m.get("class_a") in _RU_TO_CODE and m.get("class_b") in _RU_TO_CODE
            ]
            lookup = {(m["class_a"], m["class_b"]): round(float(m.get("win_rate") or 0), 1) for m in matchups}
            matrix_rows = []
            for row_cls in class_order:
                matrix_rows.append({
                    "class_ru": row_cls,
                    "code": _RU_TO_CODE[row_cls],
                    "cells": [
                        {
                            "class_ru": col_cls,
                            "code": _RU_TO_CODE[col_cls],
                            "winrate": lookup.get((row_cls, col_cls)),
                        }
                        for col_cls in class_order
                    ],
                })
            top_pairs = sorted(matchups, key=lambda item: float(item.get("win_rate") or 0), reverse=True)[:5]
            return jsonify({
                "ok": True,
                "view": "matrix",
                "period_label": "Актуально",
                "classes": [{"class_ru": cls, "code": _RU_TO_CODE[cls]} for cls in class_order],
                "rows": matrix_rows,
                "top_pairs": [
                    {
                        "class_a": item["class_a"],
                        "class_b": item["class_b"],
                        "winrate": round(float(item.get("win_rate") or 0), 1),
                    }
                    for item in top_pairs
                ],
            })

        data = get_arena_stats()
        stats_raw = data.get("stats", [])
        stats = sorted(
            stats_raw,
            key=lambda x: float(x.get("_win_rate") or 0),
            reverse=True,
        )
        total_games = sum(s["totalGames"] for s in stats)
        rows = []
        for i, s in enumerate(stats, 1):
            cls_ru = s["playerClass"]
            wr = float(s.get("_win_rate") or 0)
            rows.append({
                "rank": i,
                "class_ru": cls_ru,
                "winrate": round(wr, 1),
                "games": s["totalGames"],
                "pct_7plus": round(float(s.get("_pct_7plus") or 0), 1),
            })
        return jsonify({
            "ok": True,
            "view": "tier",
            "period_label": "Актуально",
            "total_games": total_games,
            "rows": rows,
        })
    except Exception as e:
        return jsonify({"ok": False, "error": str(e), "rows": []}), 200


@admin_bp.route("/api/comps")
def admin_pub_comps():
    period = request.args.get("period", "last-patch")
    try:
        from deckview.integrations.battlegrounds_stats import (
            ARCHETYPE_TRIBE,
            MIN_GAMES,
            PERIOD_LABEL as COMPS_PERIOD_LABEL,
            get_bgs_comps,
        )
        data = get_bgs_comps(period)
        all_comps = data.get("compStats", [])
        total_dp = data.get("dataPoints", 0)
        comps = [c for c in all_comps if c.get("dataPoints", 0) >= MIN_GAMES]
        comps.sort(key=lambda x: x.get("averagePlacement", 99))
        rows = []
        for i, comp in enumerate(comps, 1):
            archetype = comp.get("archetype", "")
            display_name = archetype.replace("_", " ").replace("-", " ").title()
            tribe = ARCHETYPE_TRIBE.get(archetype, "general")
            dist = comp.get("placementDistribution") or []
            total = sum(d.get("totalMatches", 0) for d in dist)
            top4 = sum(d.get("totalMatches", 0) for d in dist if d.get("rank", 9) <= 4)
            top4_pct = round(top4 / total * 100, 1) if total > 0 else None
            rows.append({
                "rank": i,
                "archetype": archetype,
                "display_name": display_name,
                "tribe": tribe,
                "avg_placement": round(comp.get("averagePlacement", 0), 2),
                "games": comp.get("dataPoints", 0),
                "top4_pct": top4_pct,
            })
        return jsonify({
            "ok": True,
            "period": period,
            "period_label": COMPS_PERIOD_LABEL.get(period, period),
            "total_games": total_dp,
            "rows": rows,
        })
    except Exception as e:
        return jsonify({"ok": False, "error": str(e), "rows": []}), 200


@bp.route("/api/logs")
def api_logs():
    init_db()
    limit = request.args.get("limit", type=int, default=50)
    limit = max(1, min(200, limit))
    offset = request.args.get("offset", type=int, default=0)
    offset = max(0, offset)
    event_type = request.args.get("type", "").strip() or None
    since = request.args.get("since", "").strip() or None
    events = get_bot_events(limit=limit, offset=offset, event_type=event_type, since=since)
    total = get_bot_events_count(event_type=event_type, since=since)
    return jsonify({"events": events, "total": total, "limit": limit, "offset": offset})


# ─── Archetypes (admin_bp) ────────────────────────────────────────────────────

@admin_bp.route("/archetypes")
def admin_archetypes_page():
    init_db()
    return render_template("archetypes.html")


@admin_bp.route("/api/archetypes")
def admin_api_archetypes():
    init_db()
    search = request.args.get("search", "").strip() or None
    sort_by = request.args.get("sort_by", "name_en")
    sort_dir = request.args.get("sort_dir", "asc")
    items = get_all_archetypes(search=search, sort_by=sort_by, sort_dir=sort_dir)
    return jsonify({"items": items, "total": len(items)})


@admin_bp.route("/api/archetypes", methods=["POST"])
def admin_api_archetypes_create():
    init_db()
    data = request.get_json(force=True)
    name_en = (data.get("name_en") or "").strip()
    name_ru = (data.get("name_ru") or "").strip()
    if not name_en or not name_ru:
        return jsonify({"error": "name_en and name_ru are required"}), 400
    try:
        arch_id = add_archetype(name_en, name_ru)
    except ValueError as e:
        return jsonify({"error": str(e)}), 409
    return jsonify({"id": arch_id, "name_en": name_en, "name_ru": name_ru}), 201


@admin_bp.route("/api/archetypes/<int:arch_id>", methods=["PUT"])
def admin_api_archetypes_update(arch_id):
    init_db()
    data = request.get_json(force=True)
    name_en = (data.get("name_en") or "").strip()
    name_ru = (data.get("name_ru") or "").strip()
    if not name_en or not name_ru:
        return jsonify({"error": "name_en and name_ru are required"}), 400
    try:
        ok = update_archetype(arch_id, name_en, name_ru)
    except ValueError as e:
        return jsonify({"error": str(e)}), 409
    if not ok:
        return jsonify({"error": "Not found"}), 404
    return jsonify({"ok": True, "id": arch_id, "name_en": name_en, "name_ru": name_ru})


@admin_bp.route("/api/archetypes/<int:arch_id>", methods=["DELETE"])
def admin_api_archetypes_delete(arch_id):
    init_db()
    ok = delete_archetype(arch_id)
    if not ok:
        return jsonify({"error": "Not found"}), 404
    return jsonify({"ok": True})


@admin_bp.route("/api/archetypes/import", methods=["POST"])
def admin_api_archetypes_import():
    """Import from Google Sheet. Calls load_archetypes() from hsguru_fetch and imports into DB."""
    init_db()
    try:
        from deckview.integrations.hsguru_fetch import load_archetypes as _fetch_archetypes
        # Force loading from Google Sheet (bypass DB)
        from deckview.config import ARCHETYPES_SHEET_URL
        if not ARCHETYPES_SHEET_URL:
            return jsonify({"error": "ARCHETYPES_SHEET_URL not configured"}), 400

        import csv, io
        from bs4 import BeautifulSoup

        from framework.http_session import get_http_session

        translations = {}

        def _parse_csv(text):
            out = {}
            for row in csv.reader(io.StringIO(text)):
                if len(row) >= 3:
                    eng = row[1].strip().strip('"')
                    rus = row[2].strip().strip('"')
                    if eng and rus and "англ" not in eng.lower() and "названия" not in eng.lower():
                        out[eng] = rus
            return out

        def _parse_html(html):
            out = {}
            soup = BeautifulSoup(html, "html.parser")
            for table in soup.find_all("table"):
                for tr in table.find_all("tr"):
                    cells = tr.find_all(["td", "th"])
                    if len(cells) >= 3:
                        eng = cells[1].get_text(strip=True).strip('"')
                        rus = cells[2].get_text(strip=True).strip('"')
                        if eng and rus and "англ" not in eng.lower() and "названия" not in eng.lower():
                            out[eng] = rus
            return out

        if "pubhtml" in ARCHETYPES_SHEET_URL:
            csv_url = ARCHETYPES_SHEET_URL.replace("/pubhtml", "/pub?output=csv")
            resp = get_http_session().get(
                csv_url, headers={"User-Agent": "Mozilla/5.0 (compatible; Deckview/1.0)"}, timeout=15
            )
            if resp.status_code == 200:
                resp.encoding = "utf-8"
                if resp.text.strip():
                    translations = _parse_csv(resp.text)

        if not translations:
            resp = get_http_session().get(
                ARCHETYPES_SHEET_URL, headers={"User-Agent": "Mozilla/5.0 (compatible; Deckview/1.0)"}, timeout=15
            )
            resp.raise_for_status()
            resp.encoding = "utf-8"
            translations = _parse_html(resp.text)

        if not translations:
            return jsonify({"error": "No translations found in Google Sheet"}), 400

        added, updated = import_archetypes_from_dict(translations)
        return jsonify({"ok": True, "added": added, "updated": updated, "total_fetched": len(translations)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500
