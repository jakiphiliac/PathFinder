#!/usr/bin/env python3
"""Initialize PathFinder SQLite database."""

import asyncio
import sys

from app.db import init_db


async def main():
    try:
        await init_db()
        from app.config import settings

        print("✅ Database initialized successfully")
        print(f"   Path: {settings.database_path}")
        print("   Tables: trips, places, distance_cache")
    except Exception as e:
        print(f"❌ Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
