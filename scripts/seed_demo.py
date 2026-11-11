"""Add about 2 months of labeled demo past sales from the command line.

Run from the project folder:  python -m scripts.seed_demo
(Same as the "Add demo history" button on the dashboard.)
"""
from app import db, demo, services

if __name__ == "__main__":
    db.init()
    n = demo.seed_history()
    for s in db.list_sales():
        services.rescore(s["id"])
    print(f"Added {n} demo past sales and re-scored {len(db.list_sales())} live sales.")
