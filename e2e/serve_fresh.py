"""Drop the throwaway LeadAI_e2e database, then start e2e/server.py on it.

    python e2e/serve_fresh.py --port 8765 --mongo mongodb://127.0.0.1:27099

Refuses any database other than LeadAI_e2e on the dedicated E2E mongod port.
"""
import argparse
import os
import subprocess
import sys

from pymongo import MongoClient

HERE = os.path.dirname(os.path.abspath(__file__))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--mongo", default="mongodb://127.0.0.1:27099")
    args = ap.parse_args()
    if "27099" not in args.mongo:
        sys.exit(f"refusing to reset {args.mongo}: the E2E mongod runs on port 27099")
    MongoClient(args.mongo, serverSelectionTimeoutMS=3000).drop_database("LeadAI_e2e")
    sys.exit(subprocess.call([sys.executable, os.path.join(HERE, "server.py"),
                              "--port", str(args.port), "--mongo", args.mongo]))


if __name__ == "__main__":
    main()
