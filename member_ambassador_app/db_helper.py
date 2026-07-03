import os
import sqlite3

def get_db_path():
    workspace_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    db_path = os.path.join(workspace_root, "enterprise_db", "enterprise.db")
    if os.path.exists(db_path):
        return db_path
    
    paths = [
        "enterprise_db/enterprise.db",
        "../enterprise_db/enterprise.db",
        "../../enterprise_db/enterprise.db",
        "enterprise.db"
    ]
    for p in paths:
        if os.path.exists(p):
            return p
    return "enterprise.db"

def get_db_conn():
    url = os.environ.get("TURSO_DATABASE_URL")
    token = os.environ.get("TURSO_AUTH_TOKEN")
    
    if url and token:
        from libsql import connect as libsql_connect
        conn = libsql_connect(url=url, auth_token=token)
        return conn
    else:
        db_path = get_db_path()
        conn = sqlite3.connect(db_path)
        conn.execute("PRAGMA busy_timeout = 30000;")
        return conn

def query_db(query, args=(), one=False):
    url = os.environ.get("TURSO_DATABASE_URL")
    token = os.environ.get("TURSO_AUTH_TOKEN")
    
    if url and token:
        from libsql import connect as libsql_connect
        conn = libsql_connect(url=url, auth_token=token)
        cur = conn.cursor()
        cur.execute(query, args)
        rv = cur.fetchall()
        
        cols = [col[0] for col in cur.description] if cur.description else []
        conn.close()
        
        results = []
        for row in rv:
            results.append(dict(zip(cols, row)))
        return (results[0] if results else None) if one else results
    else:
        db_path = get_db_path()
        conn = sqlite3.connect(db_path)
        conn.execute("PRAGMA busy_timeout = 30000;")
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute(query, args)
        rv = cur.fetchall()
        conn.close()
        
        results = []
        for row in rv:
            results.append(dict(row))
        return (results[0] if results else None) if one else results

def execute_db(query, args=()):
    url = os.environ.get("TURSO_DATABASE_URL")
    token = os.environ.get("TURSO_AUTH_TOKEN")
    
    if url and token:
        from libsql import connect as libsql_connect
        conn = libsql_connect(url=url, auth_token=token)
        cur = conn.cursor()
        cur.execute(query, args)
        conn.commit()
        conn.close()
    else:
        db_path = get_db_path()
        conn = sqlite3.connect(db_path)
        conn.execute("PRAGMA busy_timeout = 30000;")
        cur = conn.cursor()
        cur.execute(query, args)
        conn.commit()
        conn.close()
