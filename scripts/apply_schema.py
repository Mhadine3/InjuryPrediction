import asyncio
import asyncpg

SCHEMA_FILE = r"C:\Users\Mouad\OneDrive\Desktop\Injuryprediction\schema_test_no_tsdb.sql"
DB_URL = "postgresql://postgres:postgres123@localhost:5432/injury_prediction"

async def apply_schema():
    conn = await asyncpg.connect(DB_URL)
    try:
        sql = open(SCHEMA_FILE).read()
        await conn.execute(sql)
        print("Schema applied successfully.")
        tables = await conn.fetch(
            "SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename"
        )
        print("Tables created:", [r["tablename"] for r in tables])
    except Exception as e:
        print(f"ERROR: {e}")
    finally:
        await conn.close()

asyncio.run(apply_schema())
