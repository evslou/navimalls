"""
Shop Route Optimizer — Flask Backend
=====================================
Endpoints:
  POST /api/search-shops   — find shops near a point via Yandex Geocoder
  POST /api/solve-tsp      — solve Traveling Salesman for a set of points
  GET  /                   — serve the main page
"""

import os
import math
import itertools
import logging
from typing import Optional

import requests
from flask import Flask, render_template, jsonify, request
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

app = Flask(__name__)

# ------------------------------------- API keys -------------------------------------
YANDEX_GEOCODER_KEY = os.environ.get("YANDEX_GEOCODER_KEY", "")
YANDEX_MAPS_JS_KEY  = os.environ.get("YANDEX_MAPS_JS_KEY", "")

GEOCODER_URL = "https://geocode-maps.yandex.ru/1.x/"
SEARCH_URL   = "https://search-maps.yandex.ru/v1/"   # Organizations search API

SEARCH_RADIUS_M = 10_000  # 10 km


# ----------------------------------------------------------------------
#  Geometry helpers
# ----------------------------------------------------------------------
def haversine(p1: tuple[float, float], p2: tuple[float, float]) -> float:
    """Return great-circle distance in metres between (lat,lon) pairs."""
    R = 6_371_000
    lat1, lon1 = math.radians(p1[0]), math.radians(p1[1])
    lat2, lon2 = math.radians(p2[0]), math.radians(p2[1])
    dlat, dlon = lat2 - lat1, lon2 - lon1
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return R * 2 * math.asin(math.sqrt(a))


def build_distance_matrix(points: list[tuple[float, float]]) -> list[list[float]]:
    """NxN Haversine distance matrix (metres)."""
    n = len(points)
    return [[haversine(points[i], points[j]) for j in range(n)] for i in range(n)]


# Approximate travel-speed factors per transport type (m/s)
SPEED = {"auto": 11.1, "pedestrian": 1.4, "masstransit": 5.5}  # ~40, 5, 20 km/h

def time_matrix(dist_matrix: list[list[float]], mode: str) -> list[list[float]]:
    """Convert distance matrix to estimated time matrix (seconds)."""
    spd = SPEED.get(mode, 5.0)
    return [[d / spd for d in row] for row in dist_matrix]


# ----------------------------------------------------------------------
#  TSP Solvers
# ----------------------------------------------------------------------
def tsp_brute_force(matrix: list[list[float]], start: int = 0) -> tuple[list[int], float]:
    """
    Exact TSP via brute-force (feasible for n ≤ 10 cities including start).
    Returns (best_tour, best_cost).
    """
    n = len(matrix)
    cities = [i for i in range(n) if i != start]
    best_cost = math.inf
    best_tour: list[int] = []

    for perm in itertools.permutations(cities):
        tour = [start] + list(perm) + [start]
        cost = sum(matrix[tour[i]][tour[i + 1]] for i in range(len(tour) - 1))
        if cost < best_cost:
            best_cost = cost
            best_tour = tour[:-1]   # exclude the repeated start at end

    return best_tour, best_cost


def tsp_nearest_neighbor(matrix: list[list[float]], start: int = 0) -> tuple[list[int], float]:
    """Greedy nearest-neighbour heuristic — O(n²), used as fallback for n>10."""
    n = len(matrix)
    unvisited = set(range(n)) - {start}
    tour = [start]
    current = start
    total = 0.0

    while unvisited:
        nearest = min(unvisited, key=lambda j: matrix[current][j])
        total += matrix[current][nearest]
        tour.append(nearest)
        current = nearest
        unvisited.remove(nearest)

    total += matrix[current][start]   # return to start (for cost only)
    return tour, total


def two_opt(tour: list[int], matrix: list[list[float]]) -> tuple[list[int], float]:
    """2-opt local search improvement pass."""
    improved = True
    best = tour[:]
    n = len(best)

    def tour_cost(t: list[int]) -> float:
        return sum(matrix[t[i]][t[(i + 1) % n]] for i in range(n))

    best_cost = tour_cost(best)
    while improved:
        improved = False
        for i in range(1, n - 1):
            for j in range(i + 1, n):
                new_tour = best[:i] + best[i:j+1][::-1] + best[j+1:]
                cost = tour_cost(new_tour)
                if cost < best_cost - 1e-9:
                    best = new_tour
                    best_cost = cost
                    improved = True
    return best, best_cost


def solve_tsp(points: list[tuple[float, float]], mode: str, criterion: str) -> dict:
    """
    Solve TSP for given points.

    Args:
        points:    list of (lat, lon) — index 0 is the start location
        mode:      'auto' | 'pedestrian' | 'masstransit'
        criterion: 'time' | 'distance'

    Returns:
        {
          "order":    [0, 3, 1, 2, ...],   # indices into points[]
          "cost":     <float>,              # metres or seconds
          "unit":     "m" | "s"
        }
    """
    n = len(points)
    dist_mat = build_distance_matrix(points)

    if criterion == "time":
        matrix = time_matrix(dist_mat, mode)
        unit = "s"
    else:
        matrix = dist_mat
        unit = "m"

    if n <= 10:
        tour, cost = tsp_brute_force(matrix, start=0)
    else:
        tour, cost = tsp_nearest_neighbor(matrix, start=0)
        tour, cost = two_opt(tour, matrix)

    return {"order": tour, "cost": round(cost, 1), "unit": unit}


# ----------------------------------------------------------------------
#  Yandex API helpers
# ----------------------------------------------------------------------
def geocode_query(query: str, origin: tuple[float, float]) -> Optional[dict]:
    """Geocode a free-text query, biased toward origin."""
    params = {
        "apikey": YANDEX_GEOCODER_KEY,
        "geocode": query,
        "ll": f"{origin[1]},{origin[0]}",   # lon,lat
        "spn": "0.15,0.15",                 # ~10 km search span
        "results": 1,
        "format": "json",
        "lang": "ru_RU",
    }
    try:
        resp = requests.get(GEOCODER_URL, params=params, timeout=8)
        resp.raise_for_status()
        data = resp.json()
        features = data["response"]["GeoObjectCollection"]["featureMember"]
        if not features:
            return None
        geo = features[0]["GeoObject"]
        lon, lat = map(float, geo["Point"]["pos"].split())
        return {
            "name": geo.get("name", query),
            "address": geo.get("description", ""),
            "lat": lat,
            "lon": lon,
        }
    except Exception as exc:
        log.warning("Geocoder error for %r: %s", query, exc)
        return None


def search_organizations(query: str, origin: tuple[float, float]) -> list[dict]:
    """
    Search for organizations (shops) via Yandex Places / Search API.
    Falls back to Geocoder if Places API key is not configured.
    """
    if not YANDEX_GEOCODER_KEY:
        return []

    params = {
        "apikey": YANDEX_GEOCODER_KEY,
        "geocode": f"{query} {origin[1]},{origin[0]}",
        "ll": f"{origin[1]},{origin[0]}",
        "spn": "0.09,0.09",    # ~10 km bounding box
        "results": 5,
        "format": "json",
        "lang": "ru_RU",
        "kind": "house",
    }
    try:
        resp = requests.get(GEOCODER_URL, params=params, timeout=8)
        resp.raise_for_status()
        data = resp.json()
        features = data["response"]["GeoObjectCollection"]["featureMember"]
        results = []
        for f in features:
            geo = f["GeoObject"]
            lon, lat = map(float, geo["Point"]["pos"].split())
            dist = haversine(origin, (lat, lon))
            if dist <= SEARCH_RADIUS_M:
                results.append({
                    "name": geo.get("name", query),
                    "address": geo.get("description", ""),
                    "lat": lat,
                    "lon": lon,
                    "distance_m": round(dist),
                })
        return results
    except Exception as exc:
        log.warning("Search error for %r: %s", query, exc)
        return []


# ----------------------------------------------------------------------
#  Flask routes
# ----------------------------------------------------------------------
@app.route("/")
def index():
    return render_template("index.html", maps_js_key=YANDEX_MAPS_JS_KEY)


@app.route("/api/search-shops", methods=["POST"])
def api_search_shops():
    """
    Body JSON:
      {
        "shops":   ["Пятёрочка", "Аптека", "Магнит"],
        "origin":  {"lat": 55.751244, "lon": 37.618423}
      }

    Response:
      {
        "shops": [
          {"query": "Пятёрочка", "results": [{"name":…, "address":…, "lat":…, "lon":…, "distance_m":…}]},
          …
        ]
      }
    """
    body = request.get_json(force=True)
    shops: list[str] = body.get("shops", [])
    origin_raw = body.get("origin", {})

    if not shops or "lat" not in origin_raw:
        return jsonify({"error": "Missing 'shops' or 'origin'"}), 400

    origin = (float(origin_raw["lat"]), float(origin_raw["lon"]))
    response_data = []

    for shop_query in shops[:10]:   # hard cap at 10 to stay in free tier
        results = search_organizations(shop_query, origin)
        response_data.append({"query": shop_query, "results": results})
        log.info("Search '%s' → %d result(s)", shop_query, len(results))

    return jsonify({"shops": response_data})


@app.route("/api/solve-tsp", methods=["POST"])
def api_solve_tsp():
    """
    Body JSON:
      {
        "points": [
          {"lat": 55.751244, "lon": 37.618423, "label": "Start"},
          {"lat": 55.762, "lon": 37.630, "label": "Пятёрочка #1"},
          …
        ]
      }
      Points[0] MUST be the start location.

    Response: results for all 6 combinations
      {
        "results": [
          {
            "mode":      "auto",
            "criterion": "distance",
            "order":     [0, 2, 1, 3],
            "cost":      4200.0,
            "unit":      "m",
            "waypoints": [{"lat":…,"lon":…,"label":…}, …]   # in optimized order
          },
          …  (6 items total)
        ]
      }
    """
    body = request.get_json(force=True)
    raw_points: list[dict] = body.get("points", [])

    if len(raw_points) < 2:
        return jsonify({"error": "Need at least 2 points (start + 1 shop)"}), 400
    if len(raw_points) > 11:   # start + 10 shops
        return jsonify({"error": "Maximum 10 shops supported"}), 400

    points = [(p["lat"], p["lon"]) for p in raw_points]
    results = []

    modes      = ["auto", "pedestrian", "masstransit"]
    criteria   = ["distance", "time"]

    for mode in modes:
        for criterion in criteria:
            tsp = solve_tsp(points, mode, criterion)
            ordered_waypoints = [raw_points[i] for i in tsp["order"]]
            results.append({
                "mode":       mode,
                "criterion":  criterion,
                "order":      tsp["order"],
                "cost":       tsp["cost"],
                "unit":       tsp["unit"],
                "waypoints":  ordered_waypoints,
            })
            log.info("TSP %s/%s → cost %.1f %s", mode, criterion, tsp["cost"], tsp["unit"])

    return jsonify({"results": results})


@app.route("/api/config")
def api_config():
    """Return public config to the frontend (JS API key)."""
    return jsonify({"mapsKey": YANDEX_MAPS_JS_KEY})


# ----------------------------------------------------------------------
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    debug = os.environ.get("FLASK_DEBUG", "false").lower() == "true"
    app.run(host="0.0.0.0", port=port, debug=debug)