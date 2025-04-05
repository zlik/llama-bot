import aiosqlite

DB_FILE = "expenses.db"


async def init_db():
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute("DROP TABLE IF EXISTS expenses")
        await db.execute(
            """
            CREATE TABLE expenses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT,
                username TEXT,
                user_input_raw TEXT,
                requested_amount TEXT,
                user_reason TEXT,
                extracted_json TEXT,
                match_status TEXT,
                file_name TEXT,
                invoice_date TEXT,
                invoice_number TEXT,
                invoice_account_id TEXT,
                provider TEXT,
                billing_period TEXT,
                payment_method TEXT,
                tax_amount TEXT,
                total_amount TEXT,
                llm_total_amount TEXT,
                line_items TEXT,
                extra_data TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """
        )
        await db.commit()


async def insert_expense(data):
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute(
            """
            INSERT INTO expenses (
                user_id, username, user_input_raw, requested_amount, user_reason, extracted_json, match_status, file_name,
                invoice_date, invoice_number, invoice_account_id, provider, billing_period,
                payment_method, tax_amount, total_amount, llm_total_amount,
                line_items, extra_data
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
            data,
        )
        await db.commit()
