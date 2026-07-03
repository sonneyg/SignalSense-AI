import zipfile
import xml.etree.ElementTree as ET
import os
import sqlite3
import datetime

def excel_date_to_str(val):
    if not val or val == "NULL" or val == "None":
        return None
    try:
        serial = int(float(val))
        d = datetime.date(1899, 12, 30) + datetime.timedelta(days=serial)
        return d.isoformat()
    except Exception:
        return str(val)

def parse_xlsx(path):
    if not os.path.exists(path):
        raise FileNotFoundError(f"Excel file not found at: {path}")

    archive = zipfile.ZipFile(path)
    
    # 1. Read workbook.xml to get sheet names and ids
    workbook_xml = archive.read('xl/workbook.xml')
    workbook_root = ET.fromstring(workbook_xml)
    
    ns = {
        'main': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main',
        'r': 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
    }
    
    sheets = []
    for sheet in workbook_root.findall('.//main:sheet', ns):
        name = sheet.attrib.get('name')
        r_id = sheet.attrib.get('{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id')
        sheets.append({'name': name, 'r_id': r_id})
        
    # 2. Read rels
    rels_xml = archive.read('xl/_rels/workbook.xml.rels')
    rels_root = ET.fromstring(rels_xml)
    rel_map = {}
    for rel in rels_root.findall('.//{http://schemas.openxmlformats.org/package/2006/relationships}Relationship'):
        rel_id = rel.attrib.get('Id')
        target = rel.attrib.get('Target')
        rel_map[rel_id] = target

    # 3. Read shared strings
    shared_strings = []
    if 'xl/sharedStrings.xml' in archive.namelist():
        sst_xml = archive.read('xl/sharedStrings.xml')
        sst_root = ET.fromstring(sst_xml)
        for si in sst_root.findall('.//main:si', ns):
            text_parts = []
            for t in si.findall('.//main:t', ns):
                if t.text:
                    text_parts.append(t.text)
            shared_strings.append("".join(text_parts))

    sheet_data = {}
    for s in sheets:
        sheet_name = s['name']
        r_id = s['r_id']
        target_path = rel_map.get(r_id)
        if not target_path:
            continue
        
        if not target_path.startswith('xl/'):
            target_path = 'xl/' + target_path
            
        sheet_xml = archive.read(target_path)
        sheet_root = ET.fromstring(sheet_xml)
        
        rows = []
        for row in sheet_root.findall('.//main:row', ns):
            row_idx = int(row.attrib.get('r', 0))
            row_data = {}
            for c in row.findall('.//main:c', ns):
                cell_ref = c.attrib.get('r')
                cell_type = c.attrib.get('t')
                
                v_elem = c.find('main:v', ns)
                val = None
                if v_elem is not None and v_elem.text:
                    val_str = v_elem.text
                    if cell_type == 's':
                        idx = int(val_str)
                        val = shared_strings[idx] if idx < len(shared_strings) else f"[ERR_SST_{idx}]"
                    elif cell_type == 'b':
                        val = True if val_str == "1" else False
                    else:
                        val = val_str
                row_data[cell_ref] = val
            rows.append((row_idx, row_data))
            
        if not rows:
            continue
            
        col_letters = set()
        for r_idx, r_val in rows:
            for cell_ref in r_val.keys():
                col_let = "".join([char for char in cell_ref if char.isalpha()])
                col_letters.add(col_let)
        
        def col_key(s_val):
            return (len(s_val), s_val)
        sorted_cols = sorted(list(col_letters), key=col_key)
        
        headers = []
        header_row = rows[0][1]
        for col in sorted_cols:
            cell_ref = f"{col}1"
            headers.append(header_row.get(cell_ref, col))
            
        data_rows = []
        for r_idx, r_val in rows[1:]:
            row_cells = []
            # Check if entire row is empty
            row_is_empty = True
            for col in sorted_cols:
                cell_ref = f"{col}{r_idx}"
                v = r_val.get(cell_ref)
                if v is not None and v != "":
                    row_is_empty = False
                row_cells.append(v)
            if not row_is_empty:
                data_rows.append(row_cells)
            
        sheet_data[sheet_name] = {
            'headers': headers,
            'rows': data_rows
        }
        
    return sheet_data

def seed_db():
    print("Parsing Excel file...")
    excel_path = "EnterpriseDatabase.xlsx"
    
    data = parse_xlsx(excel_path)
    
    url = os.environ.get("TURSO_DATABASE_URL")
    token = os.environ.get("TURSO_AUTH_TOKEN")
    
    if url and token:
        print(f"Connecting to remote Turso database at {url}...")
        from libsql import connect as libsql_connect
        conn = libsql_connect(url=url, auth_token=token)
        cursor = conn.cursor()
    else:
        db_dir = "enterprise_db"
        db_path = os.path.join(db_dir, "enterprise.db")
        os.makedirs(db_dir, exist_ok=True)
        print(f"Connecting to local SQLite database at {db_path}...")
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        try:
            cursor.execute("PRAGMA foreign_keys = ON;")
        except Exception:
            pass
    
    # 1. Create tables
    print("Creating tables...")
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS members (
        MemberID TEXT PRIMARY KEY,
        Name TEXT,
        Address TEXT,
        City TEXT,
        State TEXT,
        Zip TEXT,
        TrustScore INTEGER,
        SamsPoints INTEGER,
        Ambassador TEXT,
        JoinDate TEXT
    );
    """)
    
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS clubs (
        ClubID TEXT PRIMARY KEY,
        ClubName TEXT,
        City TEXT,
        State TEXT
    );
    """)
    
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS items (
        ItemID TEXT PRIMARY KEY,
        ItemDescription TEXT,
        Department TEXT,
        Active TEXT
    );
    """)
    
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS club_inventories (
        ClubID TEXT,
        ItemID TEXT,
        OnHand INTEGER,
        BackRoom INTEGER,
        ShelfCapacity INTEGER,
        LostSalesToday INTEGER,
        OOSFlag TEXT,
        ReorderPoint INTEGER,
        LastRestocked TEXT,
        PRIMARY KEY (ClubID, ItemID)
    );
    """)
    
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS candidate_products (
        CandidateID TEXT PRIMARY KEY,
        ItemDescription TEXT,
        PhotoURL TEXT,
        StoreWhereFound TEXT,
        MemberIDProposer TEXT,
        ProposalDate TEXT,
        UpVotes INTEGER,
        Status TEXT,
        Threshold INTEGER
    );
    """)
    
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS member_receipts (
        ReceiptID TEXT PRIMARY KEY,
        MemberID TEXT,
        ClubID TEXT,
        PurchaseDate TEXT
    );
    """)
    
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS receipt_details (
        ReceiptID TEXT,
        ItemID TEXT,
        Qty INTEGER,
        Price REAL,
        PRIMARY KEY (ReceiptID, ItemID)
    );
    """)
    
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS signals (
        SignalID TEXT PRIMARY KEY,
        MemberID TEXT,
        ClubID TEXT,
        SignalType TEXT,
        ItemID TEXT,
        CandidateID TEXT,
        Status TEXT,
        AssignedRole TEXT,
        Created TEXT
    );
    """)
    
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS reward_rules (
        RuleID TEXT PRIMARY KEY,
        Event TEXT,
        Points INTEGER,
        TrustIncrease INTEGER,
        Notes TEXT
    );
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS checkout_sessions (
        MemberID TEXT PRIMARY KEY,
        Status TEXT,
        AssociateQuestion TEXT,
        MemberResponse TEXT,
        EnrollmentAnswer TEXT,
        MatchedItemID TEXT,
        LastUpdated TEXT
    );
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS shared_tokens (
        token_id TEXT PRIMARY KEY,
        role TEXT NOT NULL,
        max_uses INTEGER DEFAULT 1000,
        current_uses INTEGER DEFAULT 0,
        contact_type TEXT,
        contact_info TEXT
    );
    """)
    
    # 2. Populate tables
    for sheet_name, content in data.items():
        headers = content['headers']
        rows = content['rows']
        
        table_name = None
        if sheet_name == "Member Table":
            table_name = "members"
        elif sheet_name == "Club Table":
            table_name = "clubs"
        elif sheet_name == "Item Master":
            table_name = "items"
        elif sheet_name == "Club Inventory":
            table_name = "club_inventories"
        elif sheet_name == "Candidate Product Table":
            table_name = "candidate_products"
        elif sheet_name == "Member Receipt":
            table_name = "member_receipts"
        elif sheet_name == "Receipt Detail":
            table_name = "receipt_details"
        elif sheet_name == "Signal Table":
            table_name = "signals"
        elif sheet_name == "Reward Rules":
            table_name = "reward_rules"
            
        if not table_name:
            print(f"Skipping unknown sheet: {sheet_name}")
            continue
            
        print(f"Seeding table '{table_name}' with {len(rows)} rows...")
        
        # Determine query columns based on actual excel headers
        # We need to map them to table columns
        cols_query = []
        for h in headers:
            # strip spaces or keep exactly
            cols_query.append(h.strip())
            
        placeholders = ", ".join(["?"] * len(cols_query))
        insert_sql = f"INSERT OR REPLACE INTO {table_name} ({', '.join(cols_query)}) VALUES ({placeholders})"
        
        # Prepare rows (date conversions)
        cleaned_rows = []
        for r in rows:
            cleaned_row = []
            for col_idx, col_name in enumerate(cols_query):
                val = r[col_idx] if col_idx < len(r) else None
                
                # Check for dates
                if col_name in ["JoinDate", "LastRestocked", "ProposalDate", "PurchaseDate", "Created"]:
                    val = excel_date_to_str(val)
                # Check for NULLs
                elif val == "NULL" or val == "None" or val == "":
                    val = None
                # Check for integers
                elif col_name in ["TrustScore", "SamsPoints", "OnHand", "BackRoom", "ShelfCapacity", "LostSalesToday", "ReorderPoint", "UpVotes", "Threshold", "Qty", "Points", "TrustIncrease"]:
                    if val is not None:
                        try:
                            val = int(float(val))
                        except ValueError:
                            pass
                # Check for reals
                elif col_name in ["Price"]:
                    if val is not None:
                        try:
                            val = float(val)
                        except ValueError:
                            pass
                            
                cleaned_row.append(val)
            cleaned_rows.append(cleaned_row)
            
        cursor.executemany(insert_sql, cleaned_rows)
        
    print("Seeding initial shared tokens...")
    cursor.execute("""
    INSERT OR REPLACE INTO shared_tokens (token_id, role, max_uses, current_uses, contact_type, contact_info)
    VALUES (?, ?, ?, ?, ?, ?);
    """, ('capstone-test-token-2026', 'Associate', 1000, 0, 'email', 'sonneyg@gmail.com'))
    
    cursor.execute("""
    INSERT OR REPLACE INTO shared_tokens (token_id, role, max_uses, current_uses, contact_type, contact_info)
    VALUES (?, ?, ?, ?, ?, ?);
    """, ('linkedin-demo-token-2026', 'Member', 1000, 0, 'linkedin', 'https://www.linkedin.com/in/sonneygeorge'))
        
    conn.commit()
    conn.close()
    print("Database seeding completed successfully!")

if __name__ == "__main__":
    seed_db()
