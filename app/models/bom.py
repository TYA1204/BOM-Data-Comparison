def init_bom_tables(db):
    """Create all tables if not exist."""
    db.execute('''
        CREATE TABLE IF NOT EXISTS bom_header (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            bom_name TEXT NOT NULL,
            bom_version TEXT DEFAULT '',
            source_type TEXT DEFAULT 'Excel',
            source_file TEXT DEFAULT '',
            total_items INTEGER DEFAULT 0,
            total_quantity INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    db.execute('''
        CREATE TABLE IF NOT EXISTS bom_item (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            bom_id INTEGER NOT NULL,
            line_no INTEGER DEFAULT 0,
            level INTEGER DEFAULT 0,
            parent_pn TEXT DEFAULT '',
            part_number TEXT NOT NULL,
            part_name TEXT DEFAULT '',
            specification TEXT DEFAULT '',
            quantity REAL DEFAULT 0,
            unit TEXT DEFAULT '',
            reference TEXT DEFAULT '',
            version TEXT DEFAULT '',
            manufacturer TEXT DEFAULT '',
            mpn TEXT DEFAULT '',
            alternative TEXT DEFAULT '',
            FOREIGN KEY (bom_id) REFERENCES bom_header(id) ON DELETE CASCADE
        )
    ''')

    db.execute('''
        CREATE TABLE IF NOT EXISTS comparison_task (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_name TEXT DEFAULT '',
            source_bom_id INTEGER NOT NULL,
            target_bom_id INTEGER NOT NULL,
            comparison_type TEXT DEFAULT 'version',
            status TEXT DEFAULT 'pending',
            summary TEXT DEFAULT '',
            created_by TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            completed_at TIMESTAMP,
            FOREIGN KEY (source_bom_id) REFERENCES bom_header(id),
            FOREIGN KEY (target_bom_id) REFERENCES bom_header(id)
        )
    ''')

    db.execute('''
        CREATE TABLE IF NOT EXISTS comparison_result (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id INTEGER NOT NULL,
            diff_type TEXT NOT NULL,
            diff_category TEXT NOT NULL,
            severity TEXT DEFAULT 'medium',
            part_number_a TEXT DEFAULT '',
            part_number_b TEXT DEFAULT '',
            part_name_a TEXT DEFAULT '',
            part_name_b TEXT DEFAULT '',
            field_name TEXT DEFAULT '',
            old_value TEXT DEFAULT '',
            new_value TEXT DEFAULT '',
            reference_a TEXT DEFAULT '',
            reference_b TEXT DEFAULT '',
            quantity_a REAL DEFAULT 0,
            quantity_b REAL DEFAULT 0,
            match_confidence REAL DEFAULT 100,
            is_confirmed INTEGER DEFAULT 0,
            confirmed_by TEXT DEFAULT '',
            confirmed_at TIMESTAMP,
            FOREIGN KEY (task_id) REFERENCES comparison_task(id) ON DELETE CASCADE
        )
    ''')
