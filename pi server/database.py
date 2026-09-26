import sqlite3

DB_NAME = 'plant_data.db'

def get_db():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with get_db() as conn:
        # Configuration table
        conn.execute('''
            CREATE TABLE IF NOT EXISTS device_config (
                id INTEGER PRIMARY KEY,
                low_batt REAL DEFAULT 3.65,
                night_lux REAL DEFAULT 50.0,
                day_sleep INTEGER DEFAULT 10,
                night_sleep INTEGER DEFAULT 30,
                config_version INTEGER DEFAULT 1,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM device_config")
        if cur.fetchone()[0] == 0:
            conn.execute('''
                INSERT INTO device_config (id, low_batt, night_lux, day_sleep, night_sleep, config_version)
                VALUES (1, 3.65, 50.0, 10, 30, 1)
            ''')

        # Telemetry storage
        conn.execute('''
            CREATE TABLE IF NOT EXISTS telemetry (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                battery_voltage REAL,
                battery_percent INTEGER,
                charging BOOLEAN,
                temperature REAL,
                humidity REAL,
                soil_moisture REAL,
                pump_triggered BOOLEAN,
                lux REAL DEFAULT 0,
                wifi_rssi INTEGER DEFAULT -70,
                vpd REAL DEFAULT 0,
                dew_point REAL DEFAULT 0,
                batt_rate_mvh REAL DEFAULT 0,
                soil_depletion_rate REAL DEFAULT 0,
                dli REAL DEFAULT 0
            )
        ''')
        
        # Safe column migrations
        cursor = conn.cursor()
        cursor.execute("PRAGMA table_info(telemetry)")
        cols = [c[1] for c in cursor.fetchall()]
        new_cols = [
            ("lux", "REAL DEFAULT 0"),
            ("wifi_rssi", "INTEGER DEFAULT -70"),
            ("vpd", "REAL DEFAULT 0"),
            ("dew_point", "REAL DEFAULT 0"),
            ("batt_rate_mvh", "REAL DEFAULT 0"),
            ("soil_depletion_rate", "REAL DEFAULT 0"),
            ("dli", "REAL DEFAULT 0")
        ]
        for col, col_type in new_cols:
            if col not in cols:
                cursor.execute(f"ALTER TABLE telemetry ADD COLUMN {col} {col_type}")
        conn.commit()
