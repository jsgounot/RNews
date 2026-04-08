"""Quick smoke test for the RNews app."""
import json, sys
sys.path.insert(0, '.')

from app.main import app
from fastapi.testclient import TestClient

def check(name, r, expected_status=200, text_contains=None):
    if r.status_code != expected_status:
        print(f"FAIL {name}: got {r.status_code}, expected {expected_status}")
        if r.status_code >= 500:
            print(r.text[:600])
        return False
    if text_contains and text_contains not in r.text:
        print(f"FAIL {name}: '{text_contains}' not in response")
        return False
    print(f"OK   {name}: {r.status_code}")
    return True

ok = True
with TestClient(app) as client:
    ok &= check("GET /",              client.get("/"),              text_contains="RNews")
    ok &= check("GET /login",         client.get("/login"))
    ok &= check("GET /register",      client.get("/register"))
    ok &= check("GET /about",         client.get("/about"))
    ok &= check("GET /item/1",        client.get("/item/1"))
    ok &= check("GET /tag/genomics",  client.get("/tag/genomics"))
    ok &= check("GET /search",        client.get("/search?q=protein"))
    ok &= check("GET /api/tags",      client.get("/api/tags/suggest?q=gen"))
    ok &= check("GET /user/alice",    client.get("/user/alice"))

    tags = json.loads(client.get("/api/tags/suggest?q=gen").text)
    print(f"     tag suggestions: {[t['name'] for t in tags]}")

    r = client.post("/login",
        data={"email": "alice@example.com", "password": "password123"},
        follow_redirects=False)
    ok &= check("POST /login", r, expected_status=302)

    from datetime import date
    today = date.today().isoformat()
    ok &= check("GET /day/today",     client.get(f"/day/{today}"))

    # Settings (requires session) — login first then test
    with TestClient(app) as auth_client:
        auth_client.post("/login",
            data={"email": "alice@example.com", "password": "password123"})
        ok &= check("GET /settings",  auth_client.get("/settings"))
        ok &= check("GET /settings/teams", auth_client.get("/settings?tab=teams"))

print()
if ok:
    print("All smoke tests passed!")
else:
    print("Some tests FAILED")
    sys.exit(1)
