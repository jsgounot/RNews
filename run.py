#!/usr/bin/env python3
"""
RNews — launch script.

Usage:
    # Activate the conda env first, then:
    python run.py              # start on http://127.0.0.1:8000
    python run.py --seed       # seed demo data then start
    python run.py --port 9000  # custom port
    python run.py --reload     # auto-reload on code changes (dev mode)

Or without activating the environment:
    conda run -n rnews python run.py --seed
"""
import argparse
import sys
import os


def main():
    parser = argparse.ArgumentParser(description="Run the RNews server")
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", 8000)))
    _deployed = os.environ.get("RENDER") or os.environ.get("RAILWAY_ENVIRONMENT")
    parser.add_argument("--host", default="0.0.0.0" if _deployed else "127.0.0.1")
    parser.add_argument("--seed", action="store_true", help="Seed demo data before starting")
    parser.add_argument("--reload", action="store_true", help="Enable auto-reload (dev mode)")
    args = parser.parse_args()

    # Ensure we run from the project root so relative paths work
    script_dir = os.path.dirname(os.path.abspath(__file__))
    os.chdir(script_dir)
    sys.path.insert(0, script_dir)

    if args.seed:
        print("Seeding demo data…")
        import seed_data
        seed_data.seed()
        print()

    print(f"{'='*52}")
    print(f"  RNews  →  http://{args.host}:{args.port}")
    print(f"  Demo login: alice / password123")
    print(f"  Ctrl+C to stop")
    print(f"{'='*52}\n")

    import uvicorn
    uvicorn.run(
        "app.main:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
    )


if __name__ == "__main__":
    main()
