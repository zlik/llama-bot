import sqlite3
import csv
import json
from datetime import datetime, timedelta

DB_PATH = "expenses.db"

def get_filtered_rows(conn, start_date=None, end_date=None):
    cursor = conn.cursor()
    if start_date and end_date:
        query = """
            SELECT * FROM expenses
            WHERE DATE(created_at) BETWEEN DATE(?) AND DATE(?)
        """
        cursor.execute(query, (start_date, end_date))
    elif start_date:
        query = "SELECT * FROM expenses WHERE DATE(created_at) >= DATE(?)"
        cursor.execute(query, (start_date,))
    else:
        query = "SELECT * FROM expenses"
        cursor.execute(query)
    return cursor.fetchall(), [desc[0] for desc in cursor.description]

def generate_csv_report(start_date=None, end_date=None):
    conn = sqlite3.connect(DB_PATH)
    rows, column_names = get_filtered_rows(conn, start_date, end_date)

    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    filter_suffix = f"{start_date}_to_{end_date}" if start_date and end_date else "full"
    csv_path = f"expense_report_{filter_suffix}_{timestamp}.csv"

    with open(csv_path, mode='w', newline='', encoding='utf-8') as csvfile:
        writer = csv.writer(csvfile)
        writer.writerow(column_names)

        for row in rows:
            row = list(row)
            for i, col in enumerate(column_names):
                if col in {"extracted_json", "line_items", "extra_data"}:
                    try:
                        row[i] = json.dumps(json.loads(row[i]), indent=2)
                    except Exception:
                        pass
            writer.writerow(row)

    conn.close()
    print(f"✅ CSV report generated: {csv_path}")

if __name__ == "__main__":
    print("📅 Expense Report Generator")
    mode = input("Choose mode:\n1) Full export\n2) Last 30 days\n3) Custom range\n> ")

    if mode == "1":
        generate_csv_report()
    elif mode == "2":
        start = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
        end = datetime.now().strftime("%Y-%m-%d")
        generate_csv_report(start_date=start, end_date=end)
    elif mode == "3":
        start = input("Enter start date (YYYY-MM-DD): ").strip()
        end = input("Enter end date (YYYY-MM-DD): ").strip()
        generate_csv_report(start_date=start, end_date=end)
    else:
        print("❌ Invalid option.")
