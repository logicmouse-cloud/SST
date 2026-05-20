import os
import re
import shutil
import csv
import sqlite3
import string
import platform
import urllib.parse
import uuid
from functools import wraps
from datetime import datetime, timedelta
from flask import Flask, render_template, request, redirect, url_for, send_file, send_from_directory, flash, session, jsonify, Response
from math import ceil

# --- PATH CONFIGURATION ---
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
app = Flask(__name__, template_folder=BASE_DIR)
app.secret_key = "sst_ultra_secure_2026_key"

# UNIFIED DATABASE PATH: Everything now points to sst.db
DB_NAME = os.path.join(BASE_DIR, 'sst.db') 
BACKUP_DIR = os.path.join(BASE_DIR, 'backups')
REPORT_DIR = os.path.join(BACKUP_DIR, 'monthly_reports')

@app.context_processor
def inject_user():
    user_id = session.get('user_id')
    if user_id:
        conn = get_db_connection()
        user = conn.execute('SELECT * FROM users WHERE id = ?', (user_id,)).fetchone()
        conn.close()
        return {'user': user}
    return {'user': None}

# --- DATABASE LOGIC ---
def get_db_connection():
    conn = sqlite3.connect(DB_NAME, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA journal_mode=WAL;')
    return conn

def init_db():
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Users are referenced by pet voting, walking challenge, and most admin flows.
    cursor.execute('''CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        card_id TEXT UNIQUE,
        name TEXT NOT NULL,
        email TEXT UNIQUE,
        role TEXT DEFAULT 'Customer',
        balance REAL DEFAULT 0.0,
        is_vip INTEGER DEFAULT 0,
        points INTEGER DEFAULT 0,
        height INTEGER DEFAULT 0,
        target_may INTEGER DEFAULT 0,
        target_july INTEGER DEFAULT 0,
        target_sep INTEGER DEFAULT 0,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )''')

    # 1. Products Table
    cursor.execute('''CREATE TABLE IF NOT EXISTS products (
        barcode TEXT PRIMARY KEY, name TEXT NOT NULL, category TEXT, price REAL DEFAULT 0.0, 
        selling_price REAL DEFAULT 0.0, storage_qty INTEGER DEFAULT 0, shelf_qty INTEGER DEFAULT 0, 
        shelf_capacity INTEGER DEFAULT 20, min_threshold INTEGER DEFAULT 5, is_quick_access INTEGER DEFAULT 0)''')
    
    # 3. Logs Table
    cursor.execute('''CREATE TABLE IF NOT EXISTS logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp DATETIME DEFAULT CURRENT_TIMESTAMP, 
        barcode TEXT, product_name TEXT, action_type TEXT, details TEXT, quantity INTEGER DEFAULT 0, user_id INTEGER, user_name TEXT)''')
    
    cursor.execute('''CREATE TABLE IF NOT EXISTS walking_challenge (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    steps INTEGER,
    log_date DATE DEFAULT (date('now', 'localtime')),
    FOREIGN KEY (user_id) REFERENCES users(id)
    )''')
    # 4. Ledger Table
    conn.execute('''
        CREATE TABLE IF NOT EXISTS ledger (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            user_name TEXT,
            user_card TEXT,
            action_type TEXT,
            details TEXT,
            amount REAL
        )
    ''')
    # 5. Pets Table (New)
    cursor.execute('''CREATE TABLE IF NOT EXISTS pets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER UNIQUE, -- 确保一个用户 ID 只能在表中出现一次
    pet_name TEXT NOT NULL,
    owner_name TEXT,
    photo_filename TEXT NOT NULL,
    total_votes INTEGER DEFAULT 0,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id)
    )''')
    # Add display_number column if it doesn't exist
    try:
        cursor.execute('ALTER TABLE pets ADD COLUMN display_number INTEGER DEFAULT 0')
    except sqlite3.OperationalError:
        pass  # Column already exists

    # 6. Pet Votes Tracking (New)
    cursor.execute('''CREATE TABLE IF NOT EXISTS pet_votes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        pet_id INTEGER,
        award_id INTEGER DEFAULT 1,
        is_free_vote INTEGER DEFAULT 0,
        points_used INTEGER DEFAULT 0,
        timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
    )''')
    # 7. Awards Table (New)
    cursor.execute('''CREATE TABLE IF NOT EXISTS awards (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        award_name TEXT NOT NULL,
        description TEXT,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )''')
    # 8. Promotions Table (促销活动表 - 包含所有字段的建表语句)
    cursor.execute('''CREATE TABLE IF NOT EXISTS promotions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        start_date TEXT NOT NULL, 
        end_date TEXT NOT NULL,   
        barcode TEXT, 
        promo_type TEXT DEFAULT 'discount', 
        required_payment_method TEXT DEFAULT 'ALL',
        discount_ratio REAL DEFAULT 1.0, 
        special_price REAL, 
        bogo_buy INTEGER DEFAULT 1,
        bogo_free INTEGER DEFAULT 1,
        threshold_amount REAL DEFAULT 0,
        discount_amount REAL DEFAULT 0,
        vip_point_multiplier REAL DEFAULT 1.0, 
        extra_vip_points INTEGER DEFAULT 0, 
        is_active INTEGER DEFAULT 1
    )''')

    cursor.execute('''CREATE TABLE IF NOT EXISTS reimbursements (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp TEXT NOT NULL,
        requested_by TEXT,
        requested_by_role TEXT,
        reimbursement_type TEXT,
        event_name TEXT,
        amount REAL DEFAULT 0.0,
        notes TEXT,
        status TEXT DEFAULT 'Pending',
        receipt_path TEXT,
        approved_by TEXT,
        approved_at TEXT
    )''')

    # ==========================================
    # 👇 请将以下【数据库无损迁移】代码粘贴到这里 👇
    # ==========================================
    
    # 检查 promotions 表的现有结构
    cursor.execute("PRAGMA table_info(promotions)")
    promo_columns = [c[1] for c in cursor.fetchall()]
    
    # 如果发现缺少新字段，则安全地追加它们
    if 'promo_type' not in promo_columns:
        cursor.execute("ALTER TABLE promotions ADD COLUMN promo_type TEXT DEFAULT 'discount'")
        print("Added promo_type column to promotions.")
        
    if 'required_payment_method' not in promo_columns:
        cursor.execute("ALTER TABLE promotions ADD COLUMN required_payment_method TEXT DEFAULT 'ALL'")
        print("Added required_payment_method column to promotions.")
        
    if 'bogo_buy' not in promo_columns:
        cursor.execute("ALTER TABLE promotions ADD COLUMN bogo_buy INTEGER DEFAULT 1")
        print("Added bogo_buy column to promotions.")
        
    if 'bogo_free' not in promo_columns:
        cursor.execute("ALTER TABLE promotions ADD COLUMN bogo_free INTEGER DEFAULT 1")
        print("Added bogo_free column to promotions.")
        
    if 'threshold_amount' not in promo_columns:
        cursor.execute("ALTER TABLE promotions ADD COLUMN threshold_amount REAL DEFAULT 0")
        print("Added threshold_amount column to promotions.")
        
    if 'discount_amount' not in promo_columns:
        cursor.execute("ALTER TABLE promotions ADD COLUMN discount_amount REAL DEFAULT 0")
        print("Added discount_amount column to promotions.")
    
    if 'details' not in promo_columns:
        cursor.execute("ALTER TABLE promotions ADD COLUMN details TEXT")
        print("Added details column to promotions.")

    # Ensure pet_votes tracks awards after schema migration
    cursor.execute("PRAGMA table_info(pet_votes)")
    pet_vote_columns = [c[1] for c in cursor.fetchall()]
    if 'award_id' not in pet_vote_columns:
        cursor.execute("ALTER TABLE pet_votes ADD COLUMN award_id INTEGER DEFAULT 1")
        print("Added award_id column to pet_votes.")

    # Ensure there is at least one award category
    cursor.execute('SELECT id FROM awards LIMIT 1')
    if not cursor.fetchone():
        cursor.execute('INSERT INTO awards (award_name, description) VALUES (?, ?)',
                       ("Pet of the Year", "Classic award for top voted pet."))
        print("Added default award: Pet of the Year.")

    # ==========================================
    # 👆 迁移代码结束 👆
    # ==========================================

    # --- SCHEMA FIXES FOR EXISTING DATABASES ---
    cursor.execute("PRAGMA table_info(users)")
    user_columns = [c[1] for c in cursor.fetchall()]
    user_column_defaults = {
        'is_vip': 'INTEGER DEFAULT 0',
        'balance': 'REAL DEFAULT 0.0',
        'points': 'INTEGER DEFAULT 0',
        'role': "TEXT DEFAULT 'Customer'",
        'height': 'INTEGER DEFAULT 0',
        'target_may': 'INTEGER DEFAULT 0',
        'target_july': 'INTEGER DEFAULT 0',
        'target_sep': 'INTEGER DEFAULT 0',
    }
    for column_name, column_definition in user_column_defaults.items():
        if column_name not in user_columns:
            cursor.execute(f'ALTER TABLE users ADD COLUMN {column_name} {column_definition}')
            print(f"Added {column_name} column to users.")

    # Check for 'user_id' and 'user_name' in logs table
    cursor.execute("PRAGMA table_info(logs)")
    log_columns = [c[1] for c in cursor.fetchall()]
    if 'user_id' not in log_columns:
        cursor.execute('ALTER TABLE logs ADD COLUMN user_id INTEGER')
        print("Added user_id column to logs.")
    if 'user_name' not in log_columns:
        cursor.execute('ALTER TABLE logs ADD COLUMN user_name TEXT')
        print("Added user_name column to logs.")

    # Check for 'is_quick_access' in products table
    cursor.execute("PRAGMA table_info(products)")
    product_columns = [c[1] for c in cursor.fetchall()]
    if 'is_quick_access' not in product_columns:
        cursor.execute('ALTER TABLE products ADD COLUMN is_quick_access INTEGER DEFAULT 0')
        print("Added is_quick_access column to products.")
    
    # Default Master Admin (Card ID: 0000)
    cursor.execute("INSERT OR IGNORE INTO users (card_id, name, email, role, is_vip) VALUES ('0000', 'SST Master', 'admin@sst.com', 'Master', 1)")
    
    conn.commit()
    conn.close()


def ensure_reimbursements_table(conn):
    conn.execute('''CREATE TABLE IF NOT EXISTS reimbursements (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp TEXT NOT NULL,
        requested_by TEXT,
        requested_by_role TEXT,
        reimbursement_type TEXT,
        event_name TEXT,
        amount REAL DEFAULT 0.0,
        notes TEXT,
        status TEXT DEFAULT 'Pending',
        receipt_path TEXT,
        approved_by TEXT,
        approved_at TEXT
    )''')
    conn.commit()

# --- AUTH DECORATORS ---
def login_required(roles=None):
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            user_id = session.get('user_id')
            user_role = session.get('user_role') or session.get('role') or ''

            if not user_id:
                if request.path.startswith('/api/'):
                    return jsonify({"status": "error", "message": "Please log in"}), 401
                return redirect(url_for('login'))

            if roles:
                safe_roles = [r.lower() for r in roles]
                safe_user_role = user_role.lower()

                if safe_user_role not in safe_roles:
                    if request.path.startswith('/api/'):
                        return jsonify({"status": "error", "message": "Permission Denied"}), 403
                    flash(f"Access Denied: {user_role.title()}s cannot access this page.", "danger")
                    return redirect(url_for('index'))

            return f(*args, **kwargs)
        return decorated_function
    return decorator

# --- LOGGING & BACKUP HELPERS ---
def add_log(barcode, product_name, action_type, details, quantity=0, user_name=None, user_id=None, conn=None):
    db_conn = conn if conn is not None else get_db_connection()
    try:
        final_user_name = user_name
        if not final_user_name and user_id:
            user_row = db_conn.execute(
                'SELECT name FROM users WHERE CAST(id AS TEXT) = CAST(? AS TEXT) OR CAST(card_id AS TEXT) = CAST(? AS TEXT)', 
                (user_id, user_id)
            ).fetchone()
            if user_row:
                final_user_name = user_row['name']
            else:
                final_user_name = f"User #{user_id}"

        p_name = product_name if product_name else f"Item {barcode}"
        
        sql = '''INSERT INTO logs 
                (barcode, product_name, action_type, details, quantity, user_name, user_id, timestamp) 
                VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now', 'localtime'))'''
        
        params = (barcode, p_name, action_type, details, quantity, final_user_name, user_id)
        db_conn.execute(sql, params)
        
        if conn is None:
            db_conn.commit()
            
    except Exception as e:
        print(f"!!! DATABASE LOG ERROR !!!: {str(e)}")
    finally:
        if conn is None:
            db_conn.close()

PET_LOG_FILE = os.path.join(BASE_DIR, 'pet_voting_audit.csv')

def write_pet_vote_log(user_name, pet_name, vote_type, points_used=0):
    """
    将投票记录写入独立的 CSV 文件。
    vote_type: 'Free' 或 'VIP Points'
    """
    file_exists = os.path.isfile(PET_LOG_FILE)
    
    try:
        # 使用 'a' (append) 模式打开，确保不会覆盖旧记录
        with open(PET_LOG_FILE, mode='a', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            
            # 如果文件是新建的，先写入表头
            if not file_exists:
                writer.writerow(['Timestamp', 'Username', 'Pet Name', 'Vote Type', 'Points Used'])
            
            # 写入当前投票详情
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            writer.writerow([timestamp, user_name, pet_name, vote_type, points_used])
    except Exception as e:
        print(f"Error writing to pet log file: {e}")

def create_flexible_report(start_dt, end_dt, label):
    if not os.path.exists(REPORT_DIR): os.makedirs(REPORT_DIR)
    report_filename = f"Activity_Report_{label}.csv"
    report_path = os.path.join(REPORT_DIR, report_filename)
    conn = get_db_connection()
    logs = conn.execute('''
        SELECT l.*, p.price as cost, p.selling_price as sell, u.name as user_name
        FROM logs l
        LEFT JOIN products p ON l.barcode = p.barcode
        LEFT JOIN users u ON l.user_id = u.id
        WHERE datetime(l.timestamp, 'localtime') >= ? AND datetime(l.timestamp, 'localtime') <= ?
        ORDER BY l.timestamp ASC
    ''', (start_dt.strftime("%Y-%m-%d %H:%M:%S"), end_dt.strftime("%Y-%m-%d %H:%M:%S"))).fetchall()
    
    if not logs: 
        conn.close(); return False, "No data"
    
    rev = sum((l['sell'] or 0) * l['quantity'] for l in logs if l['action_type'] == 'Sale')
    cost = sum((l['cost'] or 0) * l['quantity'] for l in logs if l['action_type'] == 'Sale')
    
    with open(report_path, 'w', newline='', encoding='utf-8') as f:
        w = csv.writer(f)
        w.writerow(['--- SST REPORT: ' + label + ' (NZDT) ---'])
        w.writerow(['Generated At', datetime.now().strftime("%Y-%m-%d %H:%M")])
        w.writerow(['Revenue', f"${rev:.2f}"]); w.writerow(['Profit', f"${(rev-cost):.2f}"])
        w.writerow([]); w.writerow(['Time (Local)', 'User', 'Product', 'Action', 'Qty', 'Details'])
        for l in logs: w.writerow([l['timestamp'], l['user_name'], l['product_name'], l['action_type'], l['quantity'], l['details']])
    conn.close(); return True, report_filename

def get_products():
    conn = get_db_connection()
    data = conn.execute("SELECT barcode, name, shelf_qty, storage_qty FROM products").fetchall()
    products = [{'barcode': row['barcode'], 'name': row['name'], 'shelf_qty': row['shelf_qty'], 'storage_qty': row['storage_qty']} for row in data]
    conn.close()
    return products

def is_master():
    return session.get('user_role') == 'Master'

def migrate_walking_privacy():
    conn = get_db_connection()
    try:
        cursor = conn.execute("PRAGMA table_info(walking_challenge)")
        columns = [row[1] for row in cursor.fetchall()]
        if 'is_private' not in columns:
            conn.execute("ALTER TABLE walking_challenge ADD COLUMN is_private INTEGER DEFAULT 0")
            conn.commit()
    finally:
        conn.close()

# --- APP NAVIGATION & DASHBOARDS ---

@app.route('/')
@app.route('/index') 
@login_required()
def index():
    raw_role = session.get('role') or session.get('user_role') or 'Customer'
    safe_role = str(raw_role).strip().lower() 
    user_id = session.get('user_id')
    
    user_agent = request.user_agent.string.lower()
    is_mobile = any(keyword in user_agent for keyword in ['mobile', 'android', 'iphone', 'ipad'])
    
    # 1. 获取当前时间用于促销过滤
    now_promo = datetime.now().strftime("%Y-%m-%dT%H:%M")
    
    conn = get_db_connection()
    try:
        user = conn.execute('SELECT * FROM users WHERE id = ?', (user_id,)).fetchone()
        products = conn.execute('SELECT * FROM products ORDER BY name ASC').fetchall()
        personal_logs = conn.execute('''
            SELECT * FROM logs WHERE user_id = ? ORDER BY timestamp DESC LIMIT 8
        ''', (user_id,)).fetchall()
        
        # 2. 查询当前正在进行中的有效促销活动
        active_promotions = conn.execute('''
            SELECT name, promo_type, discount_ratio, special_price, 
                   bogo_buy, bogo_free, threshold_amount, discount_amount, required_payment_method
            FROM promotions 
            WHERE is_active = 1 
            AND start_date <= ? AND end_date >= ?
        ''', (now_promo, now_promo)).fetchall()
        
    finally:
        conn.close()

    if is_mobile:
        if safe_role in ['master', 'runner', 'accountant']:
            # 将促销数据传递给 mobile_index.html
            return render_template('mobile_index.html', user=user, promotions=active_promotions)
        else:
            return redirect(url_for('mobile_shopping'))

    if safe_role in ['master', 'accountant', 'runner']:
        # 将促销数据传递给 index.html
        return render_template('index.html', products=products, user=user, personal_logs=personal_logs, promotions=active_promotions)
    
    return redirect(url_for('shopping'))

# ==============================================================
# MOBILE TOP-UP ROUTES
# ==============================================================

@app.route('/mobile_topup', methods=['GET'])
@login_required(roles=['Master', 'Runner'])
def mobile_topup():
    conn = get_db_connection()
    try:
        products = conn.execute("SELECT barcode, name, storage_qty, shelf_qty, shelf_capacity FROM products").fetchall()
    finally:
        conn.close()
    return render_template('mobile_topup.html', products=products)

@app.route('/process_mobile_topup', methods=['POST'])
@login_required(roles=['Master', 'Runner'])
def process_mobile_topup():
    barcode = request.form.get('barcode')
    try:
        qty = int(request.form.get('quantity', 1))
    except ValueError:
        qty = 1

    if barcode and qty > 0:
        conn = get_db_connection()
        try:
            p = conn.execute('SELECT * FROM products WHERE barcode = ?', (barcode,)).fetchone()
            if p:
                actual_move_qty = min(qty, p['storage_qty'])
                if actual_move_qty > 0:
                    conn.execute('''UPDATE products 
                                    SET storage_qty = storage_qty - ?, 
                                        shelf_qty = shelf_qty + ? 
                                    WHERE barcode = ?''', (actual_move_qty, actual_move_qty, barcode))
                    add_log(
                        barcode=barcode, product_name=p['name'], action_type="Restock",
                        details=f"Mobile Refill: {actual_move_qty} units", quantity=actual_move_qty,
                        user_name=session.get('user_name'), user_id=session.get('user_id'), conn=conn
                    )
                    conn.commit()
                    flash(f"Moved {actual_move_qty} x {p['name']} to shelf.", "success")
                else:
                    flash(f"Not enough stock in storage to refill {p['name']}.", "error")
            else:
                flash("Product not found.", "error")
        finally:
            conn.close()
    else:
        flash("Invalid request.", "error")
    return redirect(url_for('mobile_topup'))

@app.route('/mobile_delivery', methods=['GET'])
@login_required(roles=['Master', 'Runner'])
def mobile_delivery():
    conn = get_db_connection()
    try:
        products = conn.execute('SELECT * FROM products ORDER BY name ASC').fetchall()
    finally:
        conn.close()
    return render_template('mobile_delivery.html', products=products)

@app.route('/process_mobile_delivery', methods=['POST'])
@login_required(roles=['Master', 'Runner'])
def process_mobile_delivery():
    barcode = request.form.get('barcode')
    try:
        qty = int(request.form.get('quantity', 1))
        cost_price = float(request.form.get('price', 0.0)) 
        selling_price = float(request.form.get('selling_price', 0.0))
        capacity = int(request.form.get('shelf_capacity', 0))
        min_threshold = int(request.form.get('min_threshold', 0))
    except (ValueError, TypeError):
        flash("Invalid number format entered.", "error")
        return redirect(url_for('mobile_delivery'))

    current_staff = session.get('user_name', 'Mobile Runner')
    user_id = session.get('user_id')

    if barcode and qty > 0:
        conn = get_db_connection()
        try:
            p = conn.execute('SELECT name FROM products WHERE barcode = ?', (barcode,)).fetchone()
            if p:
                conn.execute('''UPDATE products 
                                SET storage_qty = storage_qty + ?, price = ?, selling_price = ?, 
                                    shelf_capacity = ?, min_threshold = ?
                                WHERE barcode = ?''', 
                             (qty, cost_price, selling_price, capacity, min_threshold, barcode))
                
                add_log(
                    barcode=barcode, product_name=p['name'], action_type="Delivery", 
                    details=f"Rcvd: {qty} units. Cost updated to ${cost_price:.2f}",
                    quantity=qty, user_name=current_staff, user_id=user_id, conn=conn
                )
                conn.commit()
                flash(f"Successfully received {qty} x {p['name']} and updated details.", "success")
            else:
                flash("Error: Product not found in database.", "error")
        except Exception as e:
            flash(f"DB Error: {e}", "error")
        finally:
            conn.close()
    else:
        flash("Invalid barcode or quantity.", "error")
    return redirect(url_for('mobile_delivery'))

@app.route('/mobile_shopping')
@login_required(roles=['Customer', 'VIP', 'Master', 'Runner', 'accountant'])
def mobile_shopping():
    conn = get_db_connection()
    products = conn.execute('''SELECT barcode, name, selling_price, shelf_qty FROM products WHERE shelf_qty > 0''').fetchall()
    fast_lane = conn.execute('''SELECT barcode, name, selling_price FROM products WHERE is_quick_access = 1 AND shelf_qty > 0''').fetchall()
    
    user_id = session.get('user_id')
    user = conn.execute('SELECT * FROM users WHERE id = ?', (user_id,)).fetchone() if user_id else None
    conn.close()
    
    return render_template('mobile_shopping.html', products=products, fast_lane=fast_lane, user=user)

@app.route('/mobile_users', methods=['GET'])
@login_required()
def mobile_users():
    user_role = session.get('user_role', session.get('role', '')).lower()
    if user_role not in ['master', 'accountant']:
        flash("Unauthorized access. Master or Accountant role required.", "error")
        return redirect(url_for('index'))

    u_id = request.args.get('u_id')
    conn = get_db_connection()
    try:
        all_users = conn.execute("SELECT id, name, card_id, role, balance, is_vip, points FROM users").fetchall()
        target_user = conn.execute('SELECT * FROM users WHERE id = ?', (u_id,)).fetchone() if u_id else None
    finally:
        conn.close()

    return render_template('mobile_user.html', all_users=all_users, target_user=target_user)

@app.route('/mobile_user_update', methods=['POST'])
@login_required()
def mobile_user_update():
    user_role = session.get('user_role', session.get('role', '')).lower()
    if user_role not in ['master', 'accountant']:
        flash("Unauthorized access.", "error")
        return redirect(url_for('index'))

    user_id = request.form.get('user_id')
    name = request.form.get('name', '').strip()
    email = request.form.get('email', '').strip()
    card_id = request.form.get('card_id', '').strip() 
    role = request.form.get('role', 'Customer')
    
    try:
        new_balance = float(request.form.get('balance', 0.0))
        points = int(request.form.get('points', 0))
    except (ValueError, TypeError):
        flash("Invalid numbers entered for balance or points.", "error")
        return redirect(url_for('mobile_users', u_id=user_id))
        
    is_vip = 1 if request.form.get('is_vip') else 0

    conn = get_db_connection()
    try:
        old_user = conn.execute('SELECT balance FROM users WHERE id = ?', (user_id,)).fetchone()
        old_balance = old_user['balance'] if old_user else 0.0
        diff = new_balance - old_balance

        conn.execute('''UPDATE users SET name = ?, email = ?, card_id = ?, role = ?, balance = ?, points = ?, is_vip = ? WHERE id = ?''', 
                     (name, email, card_id, role, new_balance, points, is_vip, user_id))
        
        if diff != 0:
            change_str = f"+${abs(diff):.2f}" if diff > 0 else f"-${abs(diff):.2f}"
            add_log(barcode="MOBILE", product_name="Mobile Credit Change", action_type='Financial',
                    details=f"Mobile Adjustment: {change_str}", quantity=diff, user_id=user_id, conn=conn)

        conn.commit()
        flash(f"Successfully updated {name}'s profile and history.", "success")
    except sqlite3.IntegrityError:
        conn.rollback()
        flash(f"Error: That Card ID or Email is already taken.", "error")
    except Exception as e:
        conn.rollback()
        flash(f"Error updating user: {e}", "error")
    finally:
        conn.close()

    return redirect(url_for('mobile_users', u_id=user_id))

@app.route('/mobile_new')
@login_required(roles=['master', 'runner'])
def mobile_new():
    conn = get_db_connection()
    products = conn.execute('SELECT * FROM products ORDER BY name ASC').fetchall()
    conn.close()
    return render_template('mobile_new.html', products=products)

@app.route('/mobile_catalog')
@login_required(roles=['master', 'accountant'])
def mobile_catalog():
    conn = get_db_connection()
    products = conn.execute('SELECT * FROM products ORDER BY name ASC').fetchall()
    conn.close()
    return render_template('mobile_catalog_list.html', products=products)

@app.route('/mobile/restock')
def mobile_restock():
    if session.get('user_role') not in ['Master', 'Runner']:
        flash("Unauthorized Access", "danger")
        return redirect(url_for('mobile_index'))
    return render_template('mobile_restock.html')

@app.route('/admin/dashboard')
def admin_dashboard():
    if session.get('user_role') != 'Master':
        flash("Unauthorized access!", "danger")
        return redirect(url_for('login'))

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users WHERE id = ?", (session.get('user_id'),))
    user_data = cursor.fetchone()
    conn.close()
    return render_template('admin.html', user=user_data)

@app.route('/admin/backup')
def backup_db():
    if not is_master(): return redirect(url_for('index'))
    try:
        if not os.path.exists(BACKUP_DIR):
            os.makedirs(BACKUP_DIR)
        
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_filename = f"backup_{timestamp}.db"
        dest = os.path.join(BACKUP_DIR, backup_filename)
        
        # Uses the unified DB path to ensure we backup the correct database
        shutil.copy2(DB_NAME, dest)
        return send_file(dest, as_attachment=True)
    except Exception as e:
        flash(f"Backup failed: {str(e)}", "danger")
        return redirect(url_for('admin_dashboard'))

@app.route('/admin/reset', methods=['POST'])
def reset_db():
    if session.get('user_role') != 'Master':
        flash("Unauthorized access!", "danger")
        return redirect(url_for('index'))

    entered_id = request.form.get('confirm_id')
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute("SELECT card_id FROM users WHERE id = ?", (session.get('user_id'),))
    master_data = cursor.fetchone()

    if not master_data or str(entered_id) != str(master_data['card_id']):
        conn.close()
        flash("Incorrect Master ID. Reset aborted.", "danger")
        return redirect(url_for('admin_dashboard'))

    try:
        cursor.execute("DELETE FROM products")
        cursor.execute("DELETE FROM logs")
        cursor.execute("DELETE FROM users WHERE role != 'Master'")
        cursor.execute("UPDATE users SET balance = 0, points = 0 WHERE role = 'Master'")
        conn.commit()
        flash("System reset successful. All data wiped, Master account preserved.", "success")
    except Exception as e:
        conn.rollback()
        flash(f"Reset failed: {str(e)}", "danger")
    finally:
        conn.close()

    return redirect(url_for('admin_dashboard'))

@app.route('/admin/promotion', methods=['GET', 'POST'])
@login_required(roles=['Master'])
def manage_promotion():
    conn = get_db_connection()
    
    if request.method == 'POST':
        # 1. 获取表单数据
        promo_id = request.form.get('promo_id') 
        name = request.form.get('name')
        start_date = request.form.get('start_date')
        end_date = request.form.get('end_date')
        details = request.form.get('details') # 获取促销详情描述
        
        # 处理条码选择逻辑
        barcodes = request.form.getlist('barcode')
        if not barcodes or 'ALL' in barcodes:
            final_barcode = 'ALL'
        else:
            final_barcode = ','.join(barcodes)
            
        promo_type = request.form.get('promo_type', 'discount')
        required_payment_method = request.form.get('required_payment_method', 'ALL') 
        
        # 处理数值转换（带默认值防止报错）
        discount_ratio = float(request.form.get('discount_ratio') or 1.0)
        special_price_raw = request.form.get('special_price', '').strip()
        special_price = float(special_price_raw) if special_price_raw else None

        if promo_type == 'combo':
            if final_barcode == 'ALL' or len(barcodes) < 2:
                flash("Combination special must target at least 2 specific products.", "danger")
                conn.close()
                return redirect(url_for('manage_promotion'))
            if special_price is None or special_price <= 0:
                flash("Combination special requires a valid fixed combo price.", "danger")
                conn.close()
                return redirect(url_for('manage_promotion'))
        
        bogo_buy = int(request.form.get('bogo_buy') or 1)
        bogo_free = int(request.form.get('bogo_free') or 1)
        
        threshold_amount = float(request.form.get('threshold_amount') or 0)
        discount_amount = float(request.form.get('discount_amount') or 0)
        
        vip_point_multiplier = float(request.form.get('vip_point_multiplier') or 1.0)
        extra_vip_points = int(request.form.get('extra_vip_points') or 0)
        
        try:
            if promo_id:
                # 【编辑模式】：更新已存在的促销活动
                # 注意：SQL 语句中加入了 details=?
                conn.execute('''
                    UPDATE promotions 
                    SET name=?, start_date=?, end_date=?, details=?, barcode=?, promo_type=?, 
                        required_payment_method=?, discount_ratio=?, special_price=?, 
                        bogo_buy=?, bogo_free=?, threshold_amount=?, discount_amount=?, 
                        vip_point_multiplier=?, extra_vip_points=?
                    WHERE id=?
                ''', (name, start_date, end_date, details, final_barcode, promo_type, 
                      required_payment_method, discount_ratio, special_price, 
                      bogo_buy, bogo_free, threshold_amount, discount_amount, 
                      vip_point_multiplier, extra_vip_points, promo_id))
                flash("Promotion updated successfully!", "success")
            else:
                # 【新建模式】：插入新的促销活动
                # 注意：SQL 语句中加入了 details 字段
                conn.execute('''
                    INSERT INTO promotions 
                    (name, start_date, end_date, details, barcode, promo_type, required_payment_method, 
                     discount_ratio, special_price, bogo_buy, bogo_free, threshold_amount, 
                     discount_amount, vip_point_multiplier, extra_vip_points, is_active)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
                ''', (name, start_date, end_date, details, final_barcode, promo_type, required_payment_method, 
                      discount_ratio, special_price, bogo_buy, bogo_free, threshold_amount, 
                      discount_amount, vip_point_multiplier, extra_vip_points))
                flash("Promotion created successfully!", "success")
                
            conn.commit()
        except Exception as e:
            conn.rollback()
            flash(f"Database Error: {str(e)}", "danger")
            
        return redirect(url_for('manage_promotion'))

    # GET 请求：查询现有活动以列表展示
    promotions = conn.execute('SELECT * FROM promotions ORDER BY start_date DESC').fetchall()
    # 提取商品供前端复选框使用
    products = conn.execute('SELECT barcode, name FROM products ORDER BY name ASC').fetchall()
    conn.close()
    
    return render_template('promotion.html', promotions=promotions, products=products)

@app.route('/admin/promotion/delete/<int:promo_id>', methods=['POST'])
@login_required(roles=['Master'])
def delete_promotion(promo_id):
    conn = get_db_connection()
    conn.execute('DELETE FROM promotions WHERE id = ?', (promo_id,))
    conn.commit()
    conn.close()
    flash("Promotion has been successfully removed。", "success")
    return redirect(url_for('manage_promotion'))

@app.route('/staff')
@login_required(roles=['Runner', 'Stock Taker'])
def staff_dashboard():
    conn = get_db_connection()
    products = conn.execute('SELECT * FROM products ORDER BY name ASC').fetchall()
    user = conn.execute('SELECT * FROM users WHERE id = ?', (session.get('user_id'),)).fetchone()
    staff_logs = conn.execute('''
        SELECT * FROM logs 
        WHERE user_id = ? AND action_type = "Restock" 
        ORDER BY timestamp DESC LIMIT 50
    ''', (session.get('user_id'),)).fetchall()
    conn.close()
    return render_template('staff_dashboard.html', products=products, user=user, staff_logs=staff_logs)

@app.route('/shopping')
@login_required()
def shopping():
    conn = get_db_connection()
    all_products = conn.execute('SELECT * FROM products WHERE shelf_qty > 0').fetchall()
    fast_lane = conn.execute('SELECT * FROM products WHERE is_quick_access = 1 AND shelf_qty > 0 ORDER BY name ASC').fetchall()
    
    user_id = session.get('user_id')
    if user_id == 9999:
        user = {'name': 'Guest User', 'balance': 0.0}
    else:
        user = conn.execute('SELECT * FROM users WHERE id = ?', (user_id,)).fetchone()
    
    conn.close()
    return render_template('shopping.html', products=all_products, fast_lane=fast_lane, user=user)

# --- OPERATIONAL ROUTES ---

@app.route('/restock_shelf', methods=['POST'])
@login_required(roles=['Master', 'Runner'])
def restock_shelf():
    barcode = request.form.get('barcode')
    try:
        move = int(request.form.get('move_qty', 0))
    except:
        move = 0
        
    uid = session.get('user_id')
    uname = session.get('user_name')
    
    conn = get_db_connection()
    try:
        p = conn.execute('SELECT * FROM products WHERE barcode = ?', (barcode,)).fetchone()
        
        if p and move > 0:
            qty = min(move, p['storage_qty'], p['shelf_capacity'] - p['shelf_qty'])
            
            if qty > 0:
                conn.execute('''UPDATE products SET 
                                storage_qty = storage_qty - ?, 
                                shelf_qty = shelf_qty + ? 
                                WHERE barcode = ?''', (qty, qty, barcode))
                
                add_log(
                    barcode=barcode, product_name=p['name'], action_type="Restock",
                    details=f"Manual Refill: {qty} units", quantity=qty, user_id=uid, user_name=uname, conn=conn
                )
                
                conn.commit()
                flash(f"Moved {qty} units of {p['name']}", "success")
            else:
                flash("Cannot move: Check capacity.", "warning")
    finally:
        conn.close()
        
    return redirect(url_for('index'))

@app.route('/sell_item', methods=['POST'])
@login_required()
def sell_item():
    barcodes_raw = request.form.get('barcodes', '')
    pay_method = request.form.get('payment_method')
    user_id = session.get('user_id')
    
    if not barcodes_raw:
        flash("Cart was empty.", "warning")
        return redirect(url_for('shopping'))

    barcodes = [b.strip() for b in barcodes_raw.split(',') if b.strip()]
    conn = get_db_connection()
    total = 0
    points_earned = 0
    points_deducted = 0
    redeemed_happened = False 
    
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    now_promo = datetime.now().strftime("%Y-%m-%dT%H:%M")
    
    successful_items = {}
    product_name_map = {row['barcode']: row['name'] for row in conn.execute('SELECT barcode, name FROM products').fetchall()}

    try:
        user = None

        # 0. 先尝试匹配组合特价规则
        barcode_counts = {}
        for bc in barcodes:
            barcode_counts[bc] = barcode_counts.get(bc, 0) + 1

        combo_promos = conn.execute('''
            SELECT * FROM promotions
            WHERE is_active = 1
            AND start_date <= ? AND end_date >= ?
            AND promo_type = 'combo'
            AND (required_payment_method = 'ALL' OR required_payment_method = ?)
            ORDER BY id DESC
        ''', (now_promo, now_promo, pay_method)).fetchall()

        for promo in combo_promos:
            if not promo['barcode'] or promo['barcode'] == 'ALL':
                continue

            required_items = [code.strip() for code in promo['barcode'].split(',') if code.strip()]
            if len(required_items) < 2 or promo['special_price'] is None:
                continue

            if all(barcode_counts.get(code, 0) >= 1 for code in required_items):
                times = min(barcode_counts.get(code, 0) for code in required_items)
                if times <= 0:
                    continue

                combo_key = f"COMBO_{promo['id']}"
                combo_name = "Combo: " + " + ".join(product_name_map.get(code, code) for code in required_items)

                successful_items[combo_key] = {
                    'name': combo_name,
                    'total_cost': promo['special_price'] * times,
                    'qty': times,
                    'combo_items': required_items
                }

                for code in required_items:
                    barcode_counts[code] -= times

        # 将未参与组合特价的商品剩余数量继续按单品逐个处理
        remaining_barcodes = []
        for bc, count in barcode_counts.items():
            remaining_barcodes.extend([bc] * count)

        if not remaining_barcodes and not successful_items:
            flash("No valid items found in cart.", "warning")
            return redirect(url_for('shopping'))
        if user_id and int(user_id) != 9999:
            user = conn.execute('SELECT * FROM users WHERE id = ?', (user_id,)).fetchone()

        # 1. 遍历商品，计算单品级别的促销 (Discount 和 BOGO)
        for bc in remaining_barcodes:
            p = conn.execute('SELECT * FROM products WHERE barcode = ?', (bc,)).fetchone()
            if p:
                # ==========================================
                # 【核心修改区】：支持多选商品的精确核销匹配
                # ==========================================
                search_bc = f"%,{bc},%" 

                promo = conn.execute('''
                    SELECT * FROM promotions 
                    WHERE is_active = 1 
                    AND start_date <= ? AND end_date >= ?
                    AND (barcode = 'ALL' OR ',' || barcode || ',' LIKE ?)
                    AND promo_type IN ('discount', 'bogo')
                    AND (required_payment_method = 'ALL' OR required_payment_method = ?)
                    ORDER BY id DESC LIMIT 1
                ''', (now_promo, now_promo, search_bc, pay_method)).fetchone()
                # ==========================================

                final_price = p['selling_price']
                item_pts_multiplier = 1.0
                item_extra_pts = 0

                if promo:
                    item_pts_multiplier = promo['vip_point_multiplier']
                    item_extra_pts = promo['extra_vip_points']
                    
                    if promo['promo_type'] == 'discount':
                        if promo['special_price'] is not None:
                            final_price = promo['special_price']
                        else:
                            final_price = final_price * promo['discount_ratio']
                            
                    elif promo['promo_type'] == 'bogo':
                        # 动态计算买赠：获取当前是该商品的第几个
                        seen_qty = 1 if bc not in successful_items else successful_items[bc]['qty'] + 1
                        buy_q = promo['bogo_buy']
                        free_q = promo['bogo_free']
                        cycle = buy_q + free_q
                        pos = seen_qty % cycle
                        if pos == 0 or pos > buy_q:
                            final_price = 0.0

                total += final_price
                
                if user and user['is_vip'] == 1:
                    points_earned += int(final_price * item_pts_multiplier) + item_extra_pts

                conn.execute('UPDATE products SET shelf_qty = shelf_qty - 1 WHERE barcode = ?', (bc,))
                
                if bc in successful_items:
                    successful_items[bc]['qty'] += 1
                    successful_items[bc]['total_cost'] += final_price 
                else:
                    successful_items[bc] = {
                        'name': p['name'],
                        'total_cost': final_price, 
                        'qty': 1
                    }
        
        if not successful_items:
            flash("No valid items found in cart.", "warning")
            return redirect(url_for('shopping'))

        display_parts = []
        for bc, data in successful_items.items():
            if data['qty'] > 1:
                display_parts.append(f"{data['name']} * {data['qty']}")
            else:
                display_parts.append(data['name'])
        
        item_list_str = ", ".join(display_parts)

        # 2. 计算全局级别的满减 (Threshold)
        threshold_promo = conn.execute('''
            SELECT * FROM promotions 
            WHERE is_active = 1 
            AND start_date <= ? AND end_date >= ?
            AND promo_type = 'threshold'
            AND (required_payment_method = 'ALL' OR required_payment_method = ?)
            ORDER BY threshold_amount DESC LIMIT 1
        ''', (now_promo, now_promo, pay_method)).fetchone()

        # 【重点】记录满减前的总价，用于后续均摊计算
        pre_threshold_total = total

        if threshold_promo and total >= threshold_promo['threshold_amount']:
            total -= threshold_promo['discount_amount']
            total = max(0.0, total) 
            item_list_str += f" [Promo: Spend ${threshold_promo['threshold_amount']} get ${threshold_promo['discount_amount']} OFF]"

        # ==========================================
        # 【核心修复区】：按比例均摊满减优惠与积分
        # ==========================================
        if pre_threshold_total > 0 and total < pre_threshold_total:
            # 计算折算比例 (例如: 原价100, 满减后90, 比例就是0.9)
            discount_ratio = total / pre_threshold_total
            # 1. 积分也按折算比例缩减，防止漏洞
            points_earned = int(points_earned * discount_ratio)
            # 2. 将优惠均摊到每一个单品明细中
            for bc in successful_items:
                successful_items[bc]['total_cost'] *= discount_ratio

        # 3. 用户结账及日志记录
        if user:
            if pay_method == 'account':
                projected_balance = user['balance'] - total
                if projected_balance < -10.00:
                    conn.rollback() 
                    flash(f"Payment Denied: Balance would exceed -$10.00 limit", "danger")
                    return redirect(url_for('shopping'))

            pts_string = f" (+{points_earned} pts earned)" if points_earned > 0 else ""
            log_detail = f"Items: {item_list_str} | Total: ${total:.2f}{pts_string}"
    
            conn.execute('''INSERT INTO ledger (timestamp, user_name, user_card, action_type, details, amount) 
                            VALUES (?, ?, ?, ?, ?, ?)''', (now, user['name'], user['card_id'], 'Sale', log_detail, total))

            is_first_item = True
            for bc, data in successful_items.items():
                item_total = data['total_cost'] 
                qty_suffix = f" * {data['qty']}" if data['qty'] > 1 else ""
                
                if is_first_item and points_earned > 0:
                    item_log_detail = f"Items: {data['name']}{qty_suffix} | Total: ${item_total:.2f} (+{points_earned} pts earned)"
                    is_first_item = False
                else:
                    item_log_detail = f"Items: {data['name']}{qty_suffix} | Total: ${item_total:.2f}"
                    
                add_log(barcode=bc, product_name=data['name'], action_type='Sale', 
                        details=item_log_detail, quantity=-data['qty'], user_id=user_id, conn=conn)      

            if pay_method == 'account':
                conn.execute('UPDATE users SET balance = balance - ?, points = points + ? WHERE id = ?', (total, points_earned, user_id))
            else:
                conn.execute('UPDATE users SET points = points + ? WHERE id = ?', (points_earned, user_id))

            # VIP Auto-Redeem Check
            temp_user = conn.execute('SELECT points, name, card_id, is_vip FROM users WHERE id = ?', (user_id,)).fetchone()
            if temp_user['is_vip'] == 1 and temp_user['points'] >= 100:
                units = temp_user['points'] // 100
                points_deducted = units * 100
                credit_gained = units * 5.0
                redeemed_happened = True
                conn.execute('UPDATE users SET balance = balance + ?, points = points - ? WHERE id = ?', (credit_gained, points_deducted, user_id))
                redeem_detail = f"Auto-Redeem: {points_deducted} VIP points for ${credit_gained:.2f} Credit"
                conn.execute('''INSERT INTO ledger (timestamp, user_name, user_card, action_type, details, amount) 
                                VALUES (?, ?, ?, ?, ?, ?)''', (now, temp_user['name'], temp_user['card_id'], 'Financial', redeem_detail, credit_gained))
                add_log(barcode="SYSTEM", product_name='SYSTEM', action_type='Financial', 
                        details=f"Auto-Redeem: {points_deducted} pts for {temp_user['name']}", quantity=0, user_id=user_id, conn=conn)
        else:
            # Guest Sale Logic
            log_detail = f"Guest Sale: {item_list_str} | Total: ${total:.2f} via {pay_method.upper()}"
            for bc, data in successful_items.items():
                item_total = data['total_cost'] 
                qty_suffix = f" * {data['qty']}" if data['qty'] > 1 else ""
                item_log_detail = f"Guest Sale: {data['name']}{qty_suffix} | Total: ${item_total:.2f}"
                add_log(barcode=bc, product_name=data['name'], action_type='Sale', 
                        details=item_log_detail, quantity=-data['qty'], user_id=9999, conn=conn)

        conn.commit()

        updated_user = conn.execute('SELECT * FROM users WHERE id = ?', (user_id,)).fetchone() if user_id else None
        all_products = conn.execute('SELECT * FROM products').fetchall()
        fast_lane = conn.execute('SELECT * FROM products WHERE is_quick_access = 1').fetchall()
        
        receipt = {
            "total": total, "method": pay_method, "points_earned": points_earned,
            "new_points": updated_user['points'] if updated_user and updated_user['is_vip'] else 0,
            "new_balance": updated_user['balance'] if updated_user else 0,
            "auto_redeem": points_deducted, "redeemed": redeemed_happened
        }

        return render_template('shopping.html', products=all_products, fast_lane=fast_lane, user=updated_user, receipt=receipt)

    except Exception as e:
        if conn: conn.rollback()
        flash(f"System Error: {str(e)}", "danger")
        return redirect(url_for('shopping'))
    finally:
        if conn: conn.close()

@app.route('/pet_voting')
def pet_voting():
    # Allow guests and registered users to view
    user_id = session.get('user_id', 9999)

    conn = get_db_connection()
    # Fetch all pets ordered by display_number, then by id
    pets = conn.execute('SELECT id, pet_name, owner_name, photo_filename, display_number FROM pets ORDER BY display_number ASC, id ASC').fetchall()
    awards = conn.execute('SELECT * FROM awards ORDER BY id').fetchall()
    if not awards:
        awards = [{'id': 1, 'award_name': 'Pet of the Year', 'description': 'Classic award for top voted pet.'}]

    # Check if user is VIP and get their current points
    user = conn.execute('SELECT * FROM users WHERE id = ?', (user_id,)).fetchone() if user_id != 9999 else None

    used_free_award_ids = set()
    if user_id != 9999:
        free_votes = conn.execute('SELECT award_id FROM pet_votes WHERE user_id = ? AND is_free_vote = 1', (user_id,)).fetchall()
        used_free_award_ids = {row['award_id'] for row in free_votes if row['award_id']}
    else:
        used_free_award_ids = set(session.get('guest_free_awards', []))

    conn.close()
    return render_template('pet_voting.html', pets=pets, user=user, awards=awards,
                           used_free_award_ids=list(used_free_award_ids))

@app.route('/pet_voting/vote', methods=['POST'])
def submit_pet_vote():
    user_id = session.get('user_id', 9999)
    pet_id = request.form.get('pet_id')
    vote_type = request.form.get('vote_type') # 'free' or 'points'
    try:
        award_id = int(request.form.get('award_id', 1))
    except (TypeError, ValueError):
        award_id = 1

    conn = get_db_connection()
    try:
        # 1. 获取用户信息和宠物信息
        user = conn.execute('SELECT * FROM users WHERE id = ?', (user_id,)).fetchone() if user_id != 9999 else None
        pet = conn.execute('SELECT pet_name FROM pets WHERE id = ?', (pet_id,)).fetchone()
        award = conn.execute('SELECT award_name FROM awards WHERE id = ?', (award_id,)).fetchone()

        pet_name = pet['pet_name'] if pet else f"Unknown (ID:{pet_id})"
        award_name = award['award_name'] if award else 'Pet of the Year'
        user_display_name = user['name'] if user else "Guest"

        # --- 处理免费票逻辑 ---
        if vote_type == 'free':
            if user_id == 9999:
                guest_awards = set(session.get('guest_free_awards', []))
                if award_id in guest_awards:
                    flash(f"You have already used your free vote for {award_name}!", "danger")
                    return redirect(url_for('pet_voting'))
                guest_awards.add(award_id)
                session['guest_free_awards'] = list(guest_awards)
            else:
                existing_vote = conn.execute('SELECT id FROM pet_votes WHERE user_id = ? AND award_id = ? AND is_free_vote = 1',
                                             (user_id, award_id)).fetchone()
                if existing_vote:
                    flash(f"You have already used your free vote for {award_name}!", "danger")
                    return redirect(url_for('pet_voting'))

                conn.execute('INSERT INTO pet_votes (user_id, pet_id, award_id, is_free_vote) VALUES (?, ?, ?, 1)',
                             (user_id, pet_id, award_id))

            # 更新宠物总票数
            conn.execute('UPDATE pets SET total_votes = total_votes + 1 WHERE id = ?', (pet_id,))

            # 写入 Web 日志 (logs.html)
            add_log(barcode="VOTE", product_name="Pet Voting", action_type="Vote",
                    details=f"Free vote cast for {pet_name} in {award_name}", quantity=1, user_id=user_id, conn=conn)

            # 提交数据库变更
            conn.commit()

            # 写入 CSV 审计日志
            write_pet_vote_log(user_display_name, f"{pet_name} ({award_name})", 'Free', 0)
            flash(f"Free vote cast successfully for {award_name}!", "success")

        # --- 处理 VIP 积分票逻辑 ---
        elif vote_type == 'points':
            if not user or user['is_vip'] != 1:
                flash("Only VIP members can vote using points.", "danger")
                return redirect(url_for('pet_voting'))

            try:
                points_to_use = int(request.form.get('points_to_use', 0))
            except ValueError:
                points_to_use = 0

            if points_to_use <= 0 or points_to_use > user['points']:
                flash("Invalid points amount or insufficient VIP points.", "danger")
                return redirect(url_for('pet_voting'))

            conn.execute('UPDATE users SET points = points - ? WHERE id = ?', (points_to_use, user_id))
            conn.execute('UPDATE pets SET total_votes = total_votes + ? WHERE id = ?', (points_to_use, pet_id))
            conn.execute('INSERT INTO pet_votes (user_id, pet_id, award_id, points_used) VALUES (?, ?, ?, ?)',
                         (user_id, pet_id, award_id, points_to_use))

            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            conn.execute('''INSERT INTO ledger (timestamp, user_name, user_card, action_type, details, amount)
                            VALUES (?, ?, ?, ?, ?, ?)''',
                         (now, user['name'], user['card_id'], 'Vote', f"Used {points_to_use} points for {pet_name} in {award_name}", 0))

            add_log(barcode="VOTE", product_name="Pet Voting", action_type="Vote",
                    details=f"Used {points_to_use} points for {pet_name} in {award_name}", quantity=0, user_id=user_id, conn=conn)

            conn.commit()
            write_pet_vote_log(user_display_name, f"{pet_name} ({award_name})", 'VIP Points', points_to_use)
            flash(f"Successfully used {points_to_use} VIP points for {award_name}!", "success")

    except Exception as e:
        if conn:
            conn.rollback()
        flash(f"An error occurred: {str(e)}", "danger")
    finally:
        if conn:
            conn.close()

    return redirect(url_for('pet_voting'))

@app.route('/pet_voting/results')
@login_required(roles=['Master', 'Accountant'])
def pet_voting_results():
    conn = get_db_connection()
    awards = conn.execute('SELECT * FROM awards ORDER BY id').fetchall()
    award_results = []

    for award in awards:
        results = conn.execute('''
            SELECT p.id, p.pet_name, p.owner_name,
                   COALESCE(SUM(CASE WHEN pv.is_free_vote = 1 THEN 1 ELSE 0 END), 0) AS free_votes,
                   COALESCE(SUM(pv.points_used), 0) AS points_votes,
                   COALESCE(SUM(CASE WHEN pv.is_free_vote = 1 THEN 1 ELSE 0 END), 0) + COALESCE(SUM(pv.points_used), 0) AS total_votes
            FROM pets p
            LEFT JOIN pet_votes pv ON pv.pet_id = p.id AND pv.award_id = ?
            GROUP BY p.id
            ORDER BY total_votes DESC
        ''', (award['id'],)).fetchall()
        award_results.append({'award': award, 'results': results})

    conn.close()
    return render_template('pet_results.html', award_results=award_results)

@app.route('/admin/add_pet', methods=['GET', 'POST'])
@login_required(roles=['Master']) # 确保只有 Master 权限可以访问
def admin_add_pet():
    conn = get_db_connection()
    try:
        if request.method == 'POST':
            action = request.form.get('action', 'add_pet')
            if action == 'add_award':
                award_name = request.form.get('award_name', '').strip()
                award_description = request.form.get('award_description', '').strip()
                if not award_name:
                    flash("Award name is required.", "danger")
                    return redirect(url_for('admin_add_pet'))
                try:
                    conn.execute('INSERT INTO awards (award_name, description) VALUES (?, ?)',
                                 (award_name, award_description))
                    conn.commit()
                    flash(f"Successfully added award: {award_name}!", "success")
                    return redirect(url_for('admin_add_pet'))
                except Exception as e:
                    conn.rollback()
                    flash(f"Database error: {str(e)}", "danger")
                    return redirect(url_for('admin_add_pet'))

            if action == 'delete_award':
                try:
                    award_id = int(request.form.get('award_id', 0))
                except ValueError:
                    award_id = 0
                if award_id <= 0:
                    flash("Invalid award specified.", "danger")
                    return redirect(url_for('admin_add_pet'))

                # Get all votes for this award
                votes = conn.execute('SELECT * FROM pet_votes WHERE award_id = ?', (award_id,)).fetchall()
                
                # Refund VIP points and delete all votes
                for vote in votes:
                    if vote['points_used'] > 0:
                        # Refund points to user
                        conn.execute('UPDATE users SET points = points + ? WHERE id = ?', (vote['points_used'], vote['user_id']))
                    # Delete the vote record
                    conn.execute('DELETE FROM pet_votes WHERE id = ?', (vote['id'],))
                
                # Delete the award
                try:
                    conn.execute('DELETE FROM awards WHERE id = ?', (award_id,))
                    conn.commit()
                    flash("Award deleted successfully. All votes refunded and removed.", "success")
                    return redirect(url_for('admin_add_pet'))
                except Exception as e:
                    conn.rollback()
                    flash(f"Database error: {str(e)}", "danger")
                    return redirect(url_for('admin_add_pet'))

            # Common form fields used by add/edit
            pet_name = request.form.get('pet_name', '').strip()
            owner_name = request.form.get('owner_name', '').strip()
            display_number = request.form.get('display_number', '').strip()
            photo = request.files.get('photo')

            # Helper: parse display number
            try:
                display_number_val = int(display_number) if display_number else 0
            except ValueError:
                display_number_val = 0

            # --- Add new pet ---
            if action == 'add_pet' or action == 'add_pet':
                if not pet_name:
                    flash("Pet Name is required!", "danger")
                    return redirect(url_for('admin_add_pet'))

                if not photo or not photo.filename:
                    flash("Photo is required!", "danger")
                    return redirect(url_for('admin_add_pet'))

                # Check file type
                allowed_extensions = {'png', 'jpg', 'jpeg', 'gif'}
                if '.' not in photo.filename or photo.filename.rsplit('.', 1)[1].lower() not in allowed_extensions:
                    flash("Invalid file type. Only PNG, JPG, JPEG, GIF allowed.", "danger")
                    return redirect(url_for('admin_add_pet'))

                # Generate unique filename and save
                ext = photo.filename.rsplit('.', 1)[1].lower()
                filename = f"{uuid.uuid4()}.{ext}"
                photo_path = os.path.join(BASE_DIR, 'static', 'pets', filename)
                os.makedirs(os.path.dirname(photo_path), exist_ok=True)
                try:
                    photo.save(photo_path)
                    conn.execute('''INSERT INTO pets (pet_name, owner_name, photo_filename, display_number) 
                                    VALUES (?, ?, ?, ?)''', (pet_name, owner_name, filename, display_number_val))
                    conn.commit()
                    flash(f"Successfully added pet: {pet_name}!", "success")
                    return redirect(url_for('pet_voting'))
                except Exception as e:
                    conn.rollback()
                    # remove file if saved
                    try:
                        if os.path.exists(photo_path): os.remove(photo_path)
                    except Exception:
                        pass
                    flash(f"Database error: {str(e)}", "danger")
                    return redirect(url_for('admin_add_pet'))

            # --- Edit existing pet ---
            if action == 'edit_pet':
                try:
                    pet_id = int(request.form.get('pet_id', 0))
                except (ValueError, TypeError):
                    pet_id = 0
                if pet_id <= 0:
                    flash("Invalid pet specified.", "danger")
                    return redirect(url_for('admin_add_pet'))

                if not pet_name:
                    flash("Pet Name is required!", "danger")
                    return redirect(url_for('admin_add_pet'))

                existing = conn.execute('SELECT * FROM pets WHERE id = ?', (pet_id,)).fetchone()
                if not existing:
                    flash("Pet not found.", "danger")
                    return redirect(url_for('admin_add_pet'))

                new_filename = existing['photo_filename']
                # If new photo uploaded, validate and replace
                if photo and photo.filename:
                    allowed_extensions = {'png', 'jpg', 'jpeg', 'gif'}
                    if '.' not in photo.filename or photo.filename.rsplit('.', 1)[1].lower() not in allowed_extensions:
                        flash("Invalid file type for replacement photo.", "danger")
                        return redirect(url_for('admin_add_pet'))
                    ext = photo.filename.rsplit('.', 1)[1].lower()
                    new_filename = f"{uuid.uuid4()}.{ext}"
                    photo_path = os.path.join(BASE_DIR, 'static', 'pets', new_filename)
                    os.makedirs(os.path.dirname(photo_path), exist_ok=True)
                    try:
                        photo.save(photo_path)
                        # remove old file
                        try:
                            old_path = os.path.join(BASE_DIR, 'static', 'pets', existing['photo_filename'])
                            if existing['photo_filename'] and os.path.exists(old_path):
                                os.remove(old_path)
                        except Exception:
                            pass
                    except Exception as e:
                        flash(f"Failed saving replacement photo: {e}", "danger")
                        return redirect(url_for('admin_add_pet'))

                try:
                    conn.execute('UPDATE pets SET pet_name = ?, owner_name = ?, display_number = ?, photo_filename = ? WHERE id = ?',
                                 (pet_name, owner_name, display_number_val, new_filename, pet_id))
                    conn.commit()
                    flash("Pet updated successfully.", "success")
                    return redirect(url_for('admin_add_pet'))
                except Exception as e:
                    conn.rollback()
                    flash(f"Database error: {e}", "danger")
                    return redirect(url_for('admin_add_pet'))

            # --- Delete pet ---
            if action == 'delete_pet':
                try:
                    pet_id = int(request.form.get('pet_id', 0))
                except (ValueError, TypeError):
                    pet_id = 0
                if pet_id <= 0:
                    flash("Invalid pet specified.", "danger")
                    return redirect(url_for('admin_add_pet'))

                existing = conn.execute('SELECT * FROM pets WHERE id = ?', (pet_id,)).fetchone()
                if not existing:
                    flash("Pet not found.", "danger")
                    return redirect(url_for('admin_add_pet'))

                # Refund any VIP points used on votes for this pet
                votes = conn.execute('SELECT * FROM pet_votes WHERE pet_id = ?', (pet_id,)).fetchall()
                for vote in votes:
                    if vote['points_used'] and vote['points_used'] > 0:
                        try:
                            conn.execute('UPDATE users SET points = points + ? WHERE id = ?', (vote['points_used'], vote['user_id']))
                        except Exception:
                            pass
                    conn.execute('DELETE FROM pet_votes WHERE id = ?', (vote['id'],))

                try:
                    conn.execute('DELETE FROM pets WHERE id = ?', (pet_id,))
                    conn.commit()
                    # remove photo file
                    try:
                        photo_path = os.path.join(BASE_DIR, 'static', 'pets', existing['photo_filename'])
                        if existing['photo_filename'] and os.path.exists(photo_path):
                            os.remove(photo_path)
                    except Exception:
                        pass
                    flash('Pet deleted successfully.', 'success')
                    return redirect(url_for('admin_add_pet'))
                except Exception as e:
                    conn.rollback()
                    flash(f"Database error: {str(e)}", "danger")
                    return redirect(url_for('admin_add_pet'))

        awards = conn.execute('SELECT * FROM awards ORDER BY id').fetchall()
        pet_rows = conn.execute('SELECT * FROM pets ORDER BY COALESCE(display_number, 0) ASC, id ASC').fetchall()
        pets = [dict(r) for r in pet_rows]
        return render_template('admin_add_pet.html', awards=awards, pets=pets)
    finally:
        conn.close()

# --- ADMINISTRATIVE ROUTES ---

@app.route('/admin/users', methods=['GET', 'POST'])
@login_required(roles=['Master', 'Accountant'])
def manage_users():
    conn = get_db_connection()
    if request.method == 'POST':
        if session.get('user_role') != 'Master':
            flash("Permission Denied: Only a Master can modify full user profiles.", "danger")
        else:
            u_id = request.form.get('user_id')
            n_email = request.form.get('email', '').strip()
            n_role = request.form.get('role')
            n_card = request.form.get('card_id', '').strip()
            try:
                n_balance = float(request.form.get('balance', 0))
                n_points = int(request.form.get('points', 0))
            except (ValueError, TypeError):
                flash("Error: Invalid format for Balance or Points.", "danger")
                return redirect(url_for('manage_users'))

            try:
                old_data = conn.execute('SELECT name, balance FROM users WHERE id = ?', (u_id,)).fetchone()
                if old_data:
                    old_bal = old_data['balance'] or 0.0
                    diff = n_balance - old_bal
                    if diff != 0:
                        change_str = f"+${abs(diff):.2f}" if diff > 0 else f"-${abs(diff):.2f}"
                        add_log(barcode="EDIT", product_name="Profile Credit Change", action_type='Financial',
                                details=f"Admin modified balance in user list: {change_str}", quantity=diff, user_id=u_id, conn=conn)

                conn.execute('''UPDATE users SET email = ?, role = ?, card_id = ?, balance = ?, points = ? WHERE id = ?''', 
                             (n_email, n_role, n_card, n_balance, n_points, u_id))
                conn.commit()
                flash("User profile and financial data updated successfully.", "success")
            except sqlite3.IntegrityError:
                flash("Error: That IC Card ID or Email is already assigned to another user.", "danger")
                
    users = conn.execute('''
        SELECT u.*, MAX(datetime(l.timestamp, 'localtime')) as last_active, (u.balance <= -50.0) as needs_billing
        FROM users u LEFT JOIN logs l ON u.id = l.user_id GROUP BY u.id ORDER BY u.balance ASC
    ''').fetchall()
    conn.close()
    return render_template('user_management.html', users=users)

@app.route('/admin/user/<int:user_id>', methods=['GET', 'POST'])
@login_required(roles=['Master', 'Accountant'])
def user_detail(user_id):
    conn = get_db_connection()
    if request.method == 'POST':
        is_vip = 1 if request.form.get('is_vip') else 0
        name = request.form.get('name')
        email = request.form.get('email')
        card_id = request.form.get('card_id')
        role = request.form.get('role')

        try:
            if session.get('user_role') == 'Master':
                balance = float(request.form.get('balance', 0))
                points = int(request.form.get('points', 0))
                
                old_bal_row = conn.execute('SELECT balance FROM users WHERE id=?', (user_id,)).fetchone()
                old_bal = old_bal_row['balance'] if old_bal_row else 0.0
                diff = balance - old_bal
                
                if diff != 0:
                    change_str = f"+${abs(diff):.2f}" if diff > 0 else f"-${abs(diff):.2f}"
                    add_log(barcode="DETAIL", product_name="Detail Credit Change", action_type='Financial',
                            details=f"Modified via Detail Page: {change_str}", quantity=diff, user_id=user_id, conn=conn)

                conn.execute('''UPDATE users SET name=?, email=?, card_id=?, role=?, balance=?, points=?, is_vip=? WHERE id=?''',
                             (name, email, card_id, role, balance, points, is_vip, user_id))
            else:
                # Accountant logic: Includes name, email, card_id, role, and now is_vip
                conn.execute('''UPDATE users SET name=?, email=?, card_id=?, role=?, is_vip=? WHERE id=?''',
                             (name, email, card_id, role, is_vip, user_id))
            
            conn.commit()
            flash(f"Profile for {name} updated successfully!", "success")
        except Exception as e:
            conn.rollback()
            flash(f"Database Error: {str(e)}", "danger")
        finally:
            conn.close()
        return redirect(url_for('manage_users'))

    user = conn.execute('SELECT * FROM users WHERE id = ?', (user_id,)).fetchone()
    conn.close()
    return render_template('user_detail.html', user=user)

@app.route('/admin/adjust_points', methods=['POST'])
@login_required(roles=['Master'])
def adjust_points():
    target_card_id = request.form.get('target_card_id', '').strip()
    points_change = int(request.form.get('points', 0))
    
    conn = get_db_connection()
    user = conn.execute('SELECT id, name FROM users WHERE card_id = ?', (target_card_id,)).fetchone()
    
    if not user:
        flash("User not found.", "danger")
    else:
        conn.execute('UPDATE users SET points = points + ? WHERE card_id = ?', (points_change, target_card_id))
        add_log(user['name'], 'Financial', f"Points Adjusted: {points_change}", 0, user['id'], conn=conn)
        conn.commit()
        flash(f"Updated {user['name']}'s points.", "success")
    
    conn.close()
    return redirect(url_for('manage_users'))

@app.route('/admin/finance')
@login_required(roles=['Master', 'Accountant'])
def finance_ledger():
    conn = get_db_connection()
    ledger = conn.execute("SELECT * FROM ledger WHERE action_type = 'Sale' ORDER BY timestamp DESC").fetchall()
    conn.close()
    return render_template('finance.html', ledger=ledger)

@app.route('/admin/reimbursements', methods=['GET', 'POST'])
@login_required(roles=['Master', 'Accountant', 'Runner'])
def reimbursements():
    conn = get_db_connection()
    ensure_reimbursements_table(conn)
    edit_request = None
    current_role = (session.get('user_role') or session.get('role') or '').lower()
    can_approve = current_role in ['master', 'accountant']
    can_manage = session.get('user_role') == 'Master'

    if request.method == 'POST':
        action = request.form.get('action', 'submit_request')

        if action == 'submit_request':
            reimbursement_type = request.form.get('reimbursement_type', 'Shopping').strip()
            event_name = request.form.get('event_name', '').strip()
            notes = request.form.get('notes', '').strip()
            try:
                amount = float(request.form.get('amount', 0))
            except (TypeError, ValueError):
                amount = 0.0

            receipt_path = None
            receipt = request.files.get('receipt')
            if receipt and receipt.filename:
                allowed_extensions = {'pdf', 'png', 'jpg', 'jpeg'}
                if '.' in receipt.filename and receipt.filename.rsplit('.', 1)[1].lower() in allowed_extensions:
                    save_dir = os.path.join(BASE_DIR, 'static', 'uploads', 'reimbursements')
                    os.makedirs(save_dir, exist_ok=True)
                    ext = receipt.filename.rsplit('.', 1)[1].lower()
                    receipt_filename = f"{uuid.uuid4()}.{ext}"
                    receipt.save(os.path.join(save_dir, receipt_filename))
                    receipt_path = f"uploads/reimbursements/{receipt_filename}"
                else:
                    flash('Unsupported receipt file type. Use PDF, PNG, JPG, or JPEG.', 'danger')
                    return redirect(url_for('reimbursements'))

            if amount <= 0:
                flash('Amount must be greater than zero.', 'danger')
                return redirect(url_for('reimbursements'))

            conn.execute('''INSERT INTO reimbursements
                            (timestamp, requested_by, requested_by_role, reimbursement_type, event_name, amount, notes, status, receipt_path)
                            VALUES (?, ?, ?, ?, ?, ?, ?, 'Pending', ?)''',
                         (datetime.now().strftime('%Y-%m-%d %H:%M:%S'), session.get('user_name', 'System'), session.get('user_role'), reimbursement_type, event_name, amount, notes, receipt_path))
            conn.commit()
            flash('Reimbursement request submitted successfully.', 'success')
            return redirect(url_for('reimbursements'))

        if action in ['approve', 'reject']:
            if not can_approve:
                flash('Only Master or Accountant can authorize approvals.', 'danger')
                return redirect(url_for('reimbursements'))

            req_id = request.form.get('request_id')
            request_row = conn.execute('SELECT * FROM reimbursements WHERE id = ?', (req_id,)).fetchone()
            if not request_row:
                flash('Reimbursement request not found.', 'danger')
                return redirect(url_for('reimbursements'))

            if request_row['requested_by'] == session.get('user_name'):
                flash('You cannot authorize your own reimbursement request.', 'danger')
                return redirect(url_for('reimbursements'))

            status = 'Approved' if action == 'approve' else 'Rejected'
            approver = session.get('user_name', 'System')
            approved_at = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            conn.execute('UPDATE reimbursements SET status = ?, approved_by = ?, approved_at = ? WHERE id = ?',
                         (status, approver, approved_at, req_id))
            conn.commit()
            flash(f'Reimbursement request {status.lower()} successfully.', 'success')
            return redirect(url_for('reimbursements'))

        if action == 'edit_request':
            if not can_manage:
                flash('Only Master can edit reimbursement requests.', 'danger')
                return redirect(url_for('reimbursements'))

            req_id = request.form.get('request_id')
            reimbursement_type = request.form.get('reimbursement_type', 'Shopping').strip()
            event_name = request.form.get('event_name', '').strip()
            notes = request.form.get('notes', '').strip()
            try:
                amount = float(request.form.get('amount', 0))
            except (TypeError, ValueError):
                amount = 0.0

            if amount <= 0:
                flash('Amount must be greater than zero.', 'danger')
                return redirect(url_for('reimbursements', edit_id=req_id))

            receipt = request.files.get('receipt')
            if receipt and receipt.filename:
                allowed_extensions = {'pdf', 'png', 'jpg', 'jpeg'}
                if '.' in receipt.filename and receipt.filename.rsplit('.', 1)[1].lower() in allowed_extensions:
                    save_dir = os.path.join(BASE_DIR, 'static', 'uploads', 'reimbursements')
                    os.makedirs(save_dir, exist_ok=True)
                    ext = receipt.filename.rsplit('.', 1)[1].lower()
                    receipt_filename = f"{uuid.uuid4()}.{ext}"
                    receipt.save(os.path.join(save_dir, receipt_filename))
                    receipt_path = f"uploads/reimbursements/{receipt_filename}"
                    conn.execute('''UPDATE reimbursements SET reimbursement_type = ?, event_name = ?, amount = ?, notes = ?, receipt_path = ? WHERE id = ?''',
                                 (reimbursement_type, event_name, amount, notes, receipt_path, req_id))
                else:
                    flash('Unsupported receipt file type. Use PDF, PNG, JPG, or JPEG.', 'danger')
                    return redirect(url_for('reimbursements', edit_id=req_id))
            else:
                conn.execute('''UPDATE reimbursements SET reimbursement_type = ?, event_name = ?, amount = ?, notes = ? WHERE id = ?''',
                             (reimbursement_type, event_name, amount, notes, req_id))

            conn.commit()
            flash('Reimbursement request updated successfully.', 'success')
            return redirect(url_for('reimbursements'))

        if action == 'delete_request':
            if not can_manage:
                flash('Only Master can delete reimbursement requests.', 'danger')
                return redirect(url_for('reimbursements'))

            req_id = request.form.get('request_id')
            row = conn.execute('SELECT receipt_path FROM reimbursements WHERE id = ?', (req_id,)).fetchone()
            if row and row['receipt_path']:
                receipt_full = os.path.join(BASE_DIR, 'static', row['receipt_path'])
                if os.path.exists(receipt_full):
                    try:
                        os.remove(receipt_full)
                    except Exception:
                        pass
            conn.execute('DELETE FROM reimbursements WHERE id = ?', (req_id,))
            conn.commit()
            flash('Reimbursement request deleted.', 'success')
            return redirect(url_for('reimbursements'))

    if request.method == 'GET' and request.args.get('edit_id') and can_manage:
        edit_id = request.args.get('edit_id')
        edit_request = conn.execute('SELECT * FROM reimbursements WHERE id = ?', (edit_id,)).fetchone()

    reimbursements = conn.execute('SELECT * FROM reimbursements ORDER BY timestamp DESC').fetchall()
    summary_rows = conn.execute('SELECT status, COUNT(*) as count, COALESCE(SUM(amount), 0) as total FROM reimbursements GROUP BY status').fetchall()
    conn.close()

    summary = {'Total': 0, 'Pending': 0, 'Approved': 0, 'Rejected': 0}
    for row in summary_rows:
        summary[row['status'].title()] = row['count']
        summary['Total'] += row['count']

    return render_template('reimbursement.html', reimbursements=reimbursements, summary=summary,
                           edit_request=edit_request, can_approve=can_approve, can_manage=can_manage,
                           current_user=session.get('user_name'), current_role=current_role)

@app.route('/admin/reimbursements/export')
@login_required(roles=['Master', 'Accountant', 'Runner'])
def export_reimbursements():
    conn = get_db_connection()
    ensure_reimbursements_table(conn)
    start_date = request.args.get('start_date', '')
    end_date = request.args.get('end_date', '')
    query = 'SELECT * FROM reimbursements'
    params = []

    if start_date and end_date:
        query += ' WHERE date(timestamp) BETWEEN ? AND ?'
        params.extend([start_date, end_date])
    query += ' ORDER BY timestamp DESC'

    rows = conn.execute(query, params).fetchall()
    conn.close()

    output = []
    header = ['ID', 'Timestamp', 'Requested By', 'Role', 'Type', 'Event / Purpose', 'Amount', 'Status', 'Approved By', 'Approved At', 'Notes', 'Receipt Path']
    output.append(header)
    for row in rows:
        output.append([
            row['id'], row['timestamp'], row['requested_by'], row['requested_by_role'],
            row['reimbursement_type'], row['event_name'], f"{row['amount']:.2f}", row['status'],
            row['approved_by'] or '', row['approved_at'] or '', row['notes'] or '', row['receipt_path'] or ''
        ])

    def generate_csv():
        writer = csv.writer((line for line in output))
        for line in output:
            yield ','.join('"{}"'.format(str(item).replace('"', '""')) for item in line) + '\n'

    csv_data = '\n'.join(','.join('"{}"'.format(str(item).replace('"', '""')) for item in line) for line in output)
    filename = f"reimbursements_{start_date or 'all'}_{end_date or 'all'}.csv"
    response = Response(csv_data, mimetype='text/csv')
    response.headers['Content-Disposition'] = f'attachment; filename={filename}'
    return response

@app.route('/admin/adjust_credit', methods=['POST'])
@login_required(roles=['Master', 'Accountant'])
def adjust_credit():
    target_card_id = request.form.get('target_card_id', '').strip()
    try:
        amount = float(request.form.get('amount', 0))
    except (ValueError, TypeError):
        flash("Invalid amount entered. Please enter a number.", "danger")
        return redirect(url_for('manage_users'))
    
    conn = get_db_connection()
    user = conn.execute('SELECT id, name, balance FROM users WHERE card_id = ?', (target_card_id,)).fetchone()
    
    if not user:
        flash(f"User not found.", "danger")
    else:
        new_balance = (user['balance'] or 0.0) + amount
        conn.execute('UPDATE users SET balance = ? WHERE card_id = ?', (new_balance, target_card_id))
        
        action_label = "Credit Addition" if amount > 0 else "Credit Deduction"
        details = f"{action_label} for {user['name']}"
        
        add_log(barcode="FINANCIAL", product_name="Balance Adj", action_type='Financial', 
                details=details, quantity=amount, user_id=user['id'], conn=conn)
        
        conn.commit()
        flash(f"Successfully updated {user['name']}.", "success")
        
    conn.close()
    return redirect(url_for('manage_users'))

@app.route('/api/update_credit', methods=['POST'])
@login_required(roles=['Accountant', 'Master'])
def update_credit():
    data = request.json
    if not data:
        return jsonify({"status": "error", "message": "No JSON data provided"}), 400

    target_card_id = data.get('target_user')
    try:
        amount = float(data.get('amount', 0))
    except (TypeError, ValueError):
        return jsonify({"status": "error", "message": "Invalid amount format"}), 400

    conn = get_db_connection()
    try:
        user = conn.execute('SELECT id, name, balance FROM users WHERE card_id = ?', (target_card_id,)).fetchone()
        if not user:
            return jsonify({"status": "error", "message": "Target user does not exist"}), 404

        current_balance = user['balance'] if user['balance'] is not None else 0.0
        new_balance = current_balance + amount

        conn.execute('UPDATE users SET balance = ? WHERE card_id = ?', (new_balance, target_card_id))
        
        action_label = "Credit Added" if amount > 0 else "Credit Deducted"
        details = f"{action_label}: ${abs(amount):.2f} via API. New Balance: ${new_balance:.2f}"
        add_log(user['name'], 'Financial', details, 0, user['id'], conn=conn)

        conn.commit()
        return jsonify({"status": "success", "message": f"Updated {user['name']}", "new_balance": new_balance})

    except Exception as e:
        conn.rollback()
        return jsonify({"status": "error", "message": str(e)}), 500
    finally:
        conn.close()

@app.route('/admin/reset_balance', methods=['POST'])
@login_required(roles=['Master', 'Accountant'])
def reset_balance():
    u_id = request.form.get('user_id')
    conn = get_db_connection()
    user = conn.execute('SELECT name, balance FROM users WHERE id = ?', (u_id,)).fetchone()
    
    if user:
        old_bal = user['balance'] or 0.0
        conn.execute('UPDATE users SET balance = 0.0 WHERE id = ?', (u_id,))
        
        add_log(barcode="RESET", product_name="Balance Reset", action_type='Financial',
                details=f"Manually cleared balance of ${old_bal:.2f} to zero", quantity=-old_bal, user_id=u_id, conn=conn)
        conn.commit()
        flash(f"Balance for {user['name']} has been cleared to $0.00.", "success")
    
    conn.close()
    return redirect(url_for('manage_users'))

# --- FINANCIAL & ACCOUNTING DASHBOARD ---

@app.route('/admin/accounts')
@login_required(roles=['Master', 'Accountant'])
def account_ledger():
    conn = get_db_connection()
    period = request.args.get('period', 'all')
    search_q = request.args.get('search', '').lower()
    start_date = request.args.get('start_date', '')
    end_date = request.args.get('end_date', '')
    
    t_clause = ""
    if start_date and end_date:
        t_clause = f"AND date(l.timestamp) BETWEEN '{start_date}' AND '{end_date}'"
    elif period == 'last_week':
        t_clause = "AND datetime(l.timestamp, 'localtime') >= datetime('now', 'localtime', '-7 days')"
    elif period == 'last_month':
        t_clause = "AND datetime(l.timestamp, 'localtime') >= datetime('now', 'localtime', '-30 days')"

    d_query = conn.execute("SELECT SUM(balance) FROM users WHERE balance < 0").fetchone()
    total_debt_val = abs(d_query[0]) if d_query[0] else 0
    debt_list = conn.execute('SELECT name, card_id, balance FROM users WHERE balance < 0 ORDER BY balance ASC').fetchall()

    l_data = conn.execute(f'''
        SELECT l.*, u.name as u_name, u.card_id as u_card, p.selling_price
        FROM logs l
        LEFT JOIN users u ON CAST(l.user_id AS TEXT) = CAST(u.id AS TEXT)
        LEFT JOIN products p ON l.barcode = p.barcode
        WHERE l.action_type IN ('Sale', 'Financial', 'Credit', 'Adjustment', 'Settlement')
        {t_clause}
        ORDER BY l.timestamp DESC
    ''').fetchall()

    ledger = []
    for row in l_data:
        item = dict(row)
        details = str(item.get('details', ''))
        
        if item['action_type'] == 'Sale':
            qty = float(item.get('quantity', 0) or 0)
            price = float(item.get('selling_price', 0) or 0)
            item['processed_amount'] = qty * price
        else:
            q_val = float(item.get('quantity', 0) or 0)
            if q_val != 0:
                item['processed_amount'] = q_val
            else:
                amt_match = re.search(r'([+-])?\$(\d+\.?\d*)', details)
                if amt_match:
                    sign, val = amt_match.group(1), float(amt_match.group(2))
                    item['processed_amount'] = -val if sign == '-' else val
                else:
                    item['processed_amount'] = 0.0

        if search_q:
            blob = f"{item['u_name']} {item['u_card']} {details}".lower()
            if search_q in blob: ledger.append(item)
        else:
            ledger.append(item)

    conn.close()
    return render_template('account.html', ledger=ledger, debt_customers=debt_list, total_debt=total_debt_val, 
                           current_period=period, start_date=start_date, end_date=end_date)

@app.route('/admin/download_ledger')
@login_required(roles=['Master', 'Accountant'])
def download_ledger():
    if not os.path.exists(REPORT_DIR): 
        os.makedirs(REPORT_DIR)
    
    f_name = f"Financial_Ledger_{datetime.now().strftime('%Y%m%d_%H%M')}.csv"
    f_path = os.path.join(REPORT_DIR, f_name)
    
    conn = get_db_connection()
    l_rows = conn.execute('''
        SELECT l.timestamp, u.name, u.email, u.card_id, l.action_type, l.product_name, l.quantity, l.details
        FROM logs l
        LEFT JOIN users u ON l.user_id = u.id
        WHERE l.action_type IN ('Sale', 'Financial')
        ORDER BY l.timestamp DESC
    ''').fetchall()
    
    with open(f_path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerow(['SST Financial Ledger Export - ' + datetime.now().strftime('%Y-%m-%d %H:%M')])
        writer.writerow(['Date (Local)', 'User', 'Email', 'Card ID', 'Type', 'Item/Details', 'Qty', 'Notes'])
        for r in l_rows:
            writer.writerow([r[0], r[1], r[2], r[3], r[4], r[5], r[6], r[7]])
    
    conn.close()
    return send_file(f_path, as_attachment=True)

@app.route('/admin/download_pet_log')
@login_required(roles=['Master', 'Accountant'])
def download_pet_log():
    """允许管理员下载宠物投票的 CSV 审计日志"""
    # 使用你在之前步骤中定义的路径
    file_path = os.path.join(BASE_DIR, 'pet_voting_audit.csv')
    
    # 检查文件是否存在，避免系统崩溃
    if os.path.exists(file_path):
        try:
            return send_file(file_path, as_attachment=True)
        except Exception as e:
            flash(f"Error during file download: {str(e)}", "danger")
            return redirect(url_for('pet_voting_results'))
    else:
        # 如果目前还没有产生任何投票日志
        flash("The pet voting log file has not been generated yet (no votes recorded).", "warning")
        return redirect(url_for('pet_voting_results'))

@app.route('/register_product', methods=['GET', 'POST'])
@login_required(roles=['master', 'runner']) 
def register_product():
    if request.method == 'POST':
        barcode = request.form.get('barcode', '').strip()
        name = request.form.get('name', '').strip()
        
        try:
            price = float(request.form.get('price') or 0)
            selling_price = float(request.form.get('selling_price') or 0)
            capacity = int(request.form.get('shelf_capacity') or 20)
            min_t = int(request.form.get('min_threshold') or 5)
        except ValueError:
            flash("Invalid numeric input. Please check prices and quantities.", "danger")
            return redirect(url_for('register_product'))
        
        quick_access = 1 if request.form.get('fast_lane') else 0
        conn = get_db_connection()
        try:
            conn.execute('''INSERT INTO products 
                (barcode, name, price, selling_price, shelf_capacity, min_threshold, is_quick_access)
                VALUES (?, ?, ?, ?, ?, ?, ?)''', 
                (barcode, name, price, selling_price, capacity, min_t, quick_access))
            
            add_log(barcode, "Registration", f"Registered {name} (Fast Lane: {quick_access})", 0, conn=conn)
            conn.commit()
            flash(f"Product {name} registered successfully!", "success")
        except sqlite3.IntegrityError:
            flash("Error: Barcode already exists.", "danger")
        except Exception as e:
            flash(f"System Error: {e}", "danger")
        finally:
            conn.close()
            
        return redirect(url_for('index'))
        
    user_agent = request.user_agent.string.lower()
    is_mobile = any(keyword in user_agent for keyword in ['mobile', 'android', 'iphone', 'ipad'])
    
    if is_mobile:
        return render_template('mobile_new.html')
        
    return render_template('register_product.html')

@app.route('/add_stock', methods=['GET', 'POST'])
@login_required(roles=['Master', 'Runner'])
def add_stock():
    conn = get_db_connection()
    if request.method == 'POST':
        barcode = request.form.get('barcode')
        n_qty = int(request.form.get('quantity', 0))
        n_cost = float(request.form.get('price', 0))
        user_id = session.get('user_id')

        p = conn.execute('SELECT * FROM products WHERE barcode = ?', (barcode,)).fetchone()
        
        if p:
            cur_inventory = p['storage_qty'] + p['shelf_qty']
            total_qty = cur_inventory + n_qty
            
            if total_qty > 0:
                wac = ((p['price'] * cur_inventory) + (n_cost * n_qty)) / total_qty
            else:
                wac = n_cost
                
            conn.execute('''UPDATE products 
                            SET storage_qty = storage_qty + ?, price = ? 
                            WHERE barcode = ?''', (n_qty, round(wac, 2), barcode))
            
            add_log(product_name=p['name'], action_type="Delivery", 
                    details=f"Inbound shipment: {n_qty} units @ ${n_cost:.2f} each.", 
                    quantity=n_qty, user_id=user_id, barcode=barcode, conn=conn)
            
            conn.commit()
            flash(f"Successfully added {n_qty} units of {p['name']}.", "success")
        else:
            flash("Product barcode not found in system.", "danger")

        conn.close()
        return redirect(url_for('index'))

    products = conn.execute('SELECT * FROM products ORDER BY name ASC').fetchall()
    conn.close()
    return render_template('add_stock.html', products=products)

@app.route('/bulk_stock', methods=['GET', 'POST'])
@login_required(roles=['Master', 'Runner'])
def bulk_stock():
    conn = get_db_connection()
    
    # --- 1. HANDLE SAVING (POST REQUEST) ---
    if request.method == 'POST':
        barcodes = request.form.getlist('barcode')
        new_quantities = request.form.getlist('shelf_qty')
        old_quantities = request.form.getlist('old_shelf_qty') # From the hidden field in bulk_stock_2.html[cite: 21]
        
        # Capture current staff info for the logs
        current_user_id = session.get('user_id')
        current_user_name = session.get('user_name')

        for bc, new_q_str, old_q_str in zip(barcodes, new_quantities, old_quantities):
            # Only process fields where a new number was actually entered
            if new_q_str.strip() != "":
                try:
                    new_q = int(new_q_str)
                    old_q = int(old_q_str)
                    diff = new_q - old_q
                    
                    p = conn.execute('SELECT name, selling_price FROM products WHERE barcode = ?', (bc,)).fetchone()
                    
                    if p:
                        # AUDIT LOGIC: If shelf count is LOWER, record as an unrecorded sale[cite: 19]
                        if diff < 0:
                            missing_qty = abs(diff)
                            revenue = missing_qty * p['selling_price']
                            
                            # A. Record in logs.html (Inventory History)
                            # We pass user_id/name so the log shows WHO did the audit
                            add_log(
                                barcode=bc, 
                                product_name=p['name'], 
                                action_type="Sale", 
                                details=f"Stocktake Audit: {missing_qty} unrecorded units found missing", 
                                quantity=-missing_qty, 
                                user_id=current_user_id,
                                user_name=current_user_name,
                                conn=conn
                            )
                            
                            # B. Record in ledger (Financial History)
                            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                            conn.execute('''INSERT INTO ledger (timestamp, user_name, action_type, details, amount) 
                                            VALUES (?, ?, ?, ?, ?)''', 
                                         (now, f"AUDIT ({current_user_name})", "Sale", f"Found missing: {p['name']} x{missing_qty}", revenue))
                        
                        # C. Record in logs for an UPWARD adjustment (e.g. found extra stock)[cite: 18]
                        elif diff > 0:
                            add_log(bc, p['name'], "Adjustment", f"Stocktake: Found {diff} extra units on shelf", diff, user_id=current_user_id, conn=conn)

                        # Finally, update the database with the new counted value[cite: 19]
                        conn.execute('UPDATE products SET shelf_qty = ? WHERE barcode = ?', (new_q, bc))
                
                except ValueError:
                    continue # Skip if non-numeric data was somehow sent
        
        conn.commit()
        conn.close()
        flash("Stocktake Complete. Unrecorded sales have been logged automatically.", "success")
        return redirect(url_for('index'))

    # --- 2. HANDLE DISPLAYING THE PAGE (GET REQUEST) ---
    try:
        products = conn.execute('SELECT * FROM products ORDER BY name ASC').fetchall()
    finally:
        conn.close()
        
    return render_template('bulk_stock.html', products=products)

@app.route('/product/<barcode>', methods=['GET', 'POST'])
@login_required(roles=['master', 'runner', 'accountant'])
def product_detail(barcode):
    conn = get_db_connection()
    
    if request.method == 'POST':
        try:
            name = request.form.get('name', '').strip()
            storage_qty = int(request.form.get('storage_qty') or 0)
            shelf_qty = int(request.form.get('shelf_qty') or 0)
            min_t = int(request.form.get('min_threshold') or 0)
            sell_price = float(request.form.get('selling_price') or 0)
            # This variable was already captured, but not saved to the DB
            cost_price = float(request.form.get('price') or 0)
            capacity = int(request.form.get('shelf_capacity') or 0)
            quick_access = 1 if request.form.get('is_quick_access') else 0
            
            # UPDATED QUERY: Added 'price=?' and included 'cost_price' in the parameters
            conn.execute('''UPDATE products SET 
                            name=?, storage_qty=?, shelf_qty=?, 
                            min_threshold=?, selling_price=?, 
                            shelf_capacity=?, is_quick_access=?,
                            price=? 
                            WHERE barcode=?''', 
                         (name, storage_qty, shelf_qty, min_t, sell_price, capacity, quick_access, cost_price, barcode))
            
            add_log(barcode, "Update", f"Manual detail adjustment for {name}. WAC updated to ${cost_price:.2f}", 0, conn=conn)
            conn.commit()
            flash("Product configuration and cost updated successfully!", "success")
            return redirect(url_for('index'))

        except ValueError:
            flash("Error: Please enter valid numbers for quantities and prices.", "danger")
            return redirect(url_for('product_detail', barcode=barcode))
        finally:
            conn.close()

    product = conn.execute('SELECT * FROM products WHERE barcode = ?', (barcode,)).fetchone()
    
    if not product:
        conn.close()
        flash("Product not found.", "danger")
        return redirect(url_for('index'))

    item_logs = conn.execute('''
        SELECT l.*, u.name as user_name 
        FROM logs l 
        LEFT JOIN users u ON l.user_id = u.id 
        WHERE l.barcode = ? 
        ORDER BY l.timestamp DESC 
        LIMIT 50
    ''', (barcode,)).fetchall()
    
    conn.close()
        
    user_agent = request.user_agent.string.lower()
    is_mobile = any(keyword in user_agent for keyword in ['mobile', 'android', 'iphone', 'ipad'])

    if is_mobile:
        return render_template('mobile_item.html', product=product, item_logs=item_logs)
    return render_template('product_detail.html', product=product, item_logs=item_logs)

@app.route('/delete_product/<barcode>')
@login_required(roles=['Master'])
def delete_product(barcode):
    conn = get_db_connection()
    p = conn.execute('SELECT name FROM products WHERE barcode = ?', (barcode,)).fetchone()
    if p:
        conn.execute('DELETE FROM products WHERE barcode = ?', (barcode,))
        add_log(barcode, "Deletion", f"Product '{p['name']}' removed from system.", 0, conn=conn)
        conn.commit()
        flash(f"Product '{p['name']}' has been permanently deleted.", "warning")
    else:
        flash("Product not found.", "danger")
    
    conn.close()
    return redirect(url_for('index'))

@app.route('/update_price', methods=['POST'])
@login_required(roles=['Master'])
def update_price():
    barcode = request.form.get('barcode')
    new_p = float(request.form.get('selling_price', 0))
    conn = get_db_connection()
    conn.execute('UPDATE products SET selling_price = ? WHERE barcode = ?', (new_p, barcode))
    conn.commit()
    conn.close()
    return redirect(url_for('index'))

@app.route('/rollback/<int:log_id>', methods=['POST'])
@login_required()
def rollback_action(log_id):
    if session.get('user_id') == 9999:
        flash("Guest mode cannot rollback actions.", "warning")
        return redirect(url_for('index'))

    conn = get_db_connection()
    try:
        log = conn.execute('SELECT * FROM logs WHERE id = ?', (log_id,)).fetchone()
        
        if not log:
            flash("Log entry not found.", "danger")
            return redirect(url_for('index'))

        barcode = log['barcode']
        qty = log['quantity'] 
        action = log['action_type']

        if action == 'Sale':
            conn.execute('UPDATE products SET shelf_qty = shelf_qty - ? WHERE barcode = ?', (qty, barcode))
            details = f"Undo Sale: Put {abs(qty)} items back on shelf"
        elif action == 'Restock':
            conn.execute('UPDATE products SET shelf_qty = shelf_qty - ?, storage_qty = storage_qty + ? WHERE barcode = ?', 
                         (qty, qty, barcode))
            details = f"Undo Restock: Moved {qty} items back to storage"
        elif action == 'Delivery':
            conn.execute('UPDATE products SET storage_qty = storage_qty - ? WHERE barcode = ?', (qty, barcode))
            details = f"Undo Delivery: Removed {qty} items from storage"

        add_log(barcode=barcode, product_name=log['product_name'], action_type='Rollback',
                details=details, quantity=-qty, user_id=session.get('user_id'), conn=conn)

        conn.commit()
        flash(f"Successfully rolled back: {log['product_name']}", "success")

    except Exception as e:
        conn.rollback()
        print(f"Rollback Error: {e}")
        flash("System error during rollback.", "danger")
    finally:
        conn.close()

    return redirect(url_for('index'))

# --- LOGS & REPORTS ---

@app.route('/logs')
@login_required(roles=['Master', 'Accountant', 'Runner'])
def show_logs():
    conn = get_db_connection()
    today = datetime.now()
    
    start_of_month = today.replace(day=1, hour=0, minute=0, second=0).strftime("%Y-%m-%d %H:%M:%S")
    month_count = conn.execute(
        "SELECT COUNT(*) FROM logs WHERE datetime(timestamp, 'localtime') >= ?", 
        (start_of_month,)
    ).fetchone()[0]

    query = '''
        SELECT 
            l.*, 
            u.name as user_name 
        FROM logs l
        LEFT JOIN users u ON (
            CAST(l.user_id AS TEXT) = CAST(u.id AS TEXT) OR 
            CAST(l.user_id AS TEXT) = CAST(u.card_id AS TEXT)
        )
        ORDER BY l.timestamp DESC 
        LIMIT 200
    '''
    logs = conn.execute(query).fetchall()
    
    reports = []
    if os.path.exists(REPORT_DIR):
        reports = sorted([f for f in os.listdir(REPORT_DIR) if f.endswith('.csv')], reverse=True)

    conn.close()

    # --- ADDED: Mobile Detection ---
    user_agent = request.user_agent.string.lower()
    is_mobile = any(keyword in user_agent for keyword in ['mobile', 'android', 'iphone', 'ipad'])

    if is_mobile:
        return render_template('mobile_logs.html', logs=logs)
        
    return render_template('logs.html', logs=logs, reports=reports, month_count=month_count, month_name=today.strftime('%B'))

@app.route('/generate_report_manual')
@login_required(roles=['Master'])
def generate_report_manual():
    today = datetime.now(); start = today.replace(day=1, hour=0, minute=0, second=0)
    success, message = create_flexible_report(start, today, today.strftime("%Y-%m"))
    if success: flash(f"Report for {today.strftime('%B')} generated!", "success")
    else: flash("No activity found for this month.", "warning")
    return redirect(url_for('view_logs'))

@app.route('/download_report/<filename>')
@login_required(roles=['Master'])
def download_report(filename):
    return send_from_directory(REPORT_DIR, filename)

# --- LOGIN / REGISTRATION ---

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        c_id = request.form.get('card_id', '').strip()
        conn = get_db_connection()
        user = conn.execute('SELECT * FROM users WHERE card_id = ?', (c_id,)).fetchone()
        conn.close()
        
        if user:
            session.update({'user_id': user['id'], 'user_name': user['name'], 'user_role': user['role']})
            return redirect(url_for('index'))
            
        flash(f"Card ID {c_id} is not registered.", "error")
        return redirect(url_for('login'))
        
    user_agent = request.user_agent.string.lower()
    is_mobile = any(keyword in user_agent for keyword in ['mobile', 'android', 'iphone', 'ipad'])
    
    # ==========================================
    # 【新增逻辑】：查询当前有效的促销活动 (仅在页面加载时运行)
    # ==========================================
    now_promo = datetime.now().strftime("%Y-%m-%dT%H:%M")
    conn = get_db_connection()
    try:
        active_promotions = conn.execute('''
            SELECT name, promo_type, discount_ratio, special_price, 
                   bogo_buy, bogo_free, threshold_amount, discount_amount, required_payment_method,
                   vip_point_multiplier, extra_vip_points, details
            FROM promotions 
            WHERE is_active = 1 
            AND start_date <= ? AND end_date >= ?
        ''', (now_promo, now_promo)).fetchall()
    except Exception as e:
        active_promotions = []
    finally:
        conn.close()
    # ==========================================
    
    if is_mobile:
        # 将促销信息也传递给移动端登录页（以备后续扩展使用）
        return render_template('mobile.html', promotions=active_promotions)
        
    # 将促销信息传递给 kiosk 大屏登录页
    return render_template('login.html', promotions=active_promotions)

@app.route('/login_guest')
def login_guest():
    session.clear()
    session.update({'user_id': 9999, 'user_name': 'Guest User', 'user_role': 'Customer'})
    
    user_agent = request.user_agent.string.lower()
    is_mobile = any(keyword in user_agent for keyword in ['mobile', 'android', 'iphone', 'ipad'])
    
    if is_mobile:
        return redirect(url_for('mobile_shopping'))
        
    return redirect(url_for('shopping'))

@app.route('/register_user', methods=['GET', 'POST'])
def register_user():
    if request.method == 'POST':
        card_id = request.form.get('card_id', '').strip()
        name = request.form.get('name', '').strip()
        email = request.form.get('email', '').strip()
        
        conn = get_db_connection()
        existing_user = conn.execute('''
            SELECT * FROM users 
            WHERE card_id = ? OR email = ? OR name = ?
        ''', (card_id, email, name)).fetchone()
        
        if existing_user:
            if existing_user['card_id'] == card_id:
                flash("This Card ID is already registered to another user.", "danger")
            elif existing_user['email'] == email:
                flash("This Email Address is already in use.", "danger")
            else:
                flash("A user with this exact name already exists.", "warning")
                
            conn.close()
            return redirect(url_for('register_user', prefill_card=card_id))

        try:
            conn.execute('INSERT INTO users (card_id, name, email) VALUES (?, ?, ?)', (card_id, name, email))
            conn.commit()
            flash("Registration Successful! You can now login.", "success")
            return redirect(url_for('login'))
        except Exception as e:
            flash(f"Error: {str(e)}", "danger")
            return redirect(url_for('register_user'))
        finally:
            conn.close()

    prefill = request.args.get('prefill_card', '')
    user_agent = request.user_agent.string.lower()
    is_mobile = any(keyword in user_agent for keyword in ['mobile', 'android', 'iphone', 'ipad'])
    
    if is_mobile:
        return render_template('mobile_register.html', prefill_card=prefill)
        
    return render_template('register_user.html', prefill_card=prefill)

@app.route('/user_delete/<int:u_id>', methods=['POST'])
@login_required(roles=['master'])
def user_delete(u_id):
    if u_id == session.get('user_id'):
        flash("Security Error: You cannot delete your own Master account.", "error")
        return redirect(url_for('mobile_users', u_id=u_id))

    conn = get_db_connection()
    try:
        user = conn.execute('SELECT name FROM users WHERE id = ?', (u_id,)).fetchone()
        if not user:
            flash("User not found.", "error")
            return redirect(url_for('mobile_users'))

        conn.execute('DELETE FROM users WHERE id = ?', (u_id,))
        conn.commit()
        flash(f"User '{user['name']}' has been permanently removed.", "success")
    except Exception as e:
        flash(f"Database Error: {e}", "error")
    finally:
        conn.close()

    return redirect(url_for('manage_users'))

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

@app.route('/picking_list')
def picking_list():
    conn = get_db_connection()
    cursor = conn.cursor()
    query = "SELECT barcode, name, shelf_qty, storage_qty, min_threshold, shelf_capacity FROM products"
    cursor.execute(query)
    all_items = cursor.fetchall()

    low_stock = []
    for row in all_items:
        if row['shelf_qty'] <= row['min_threshold']:
            low_stock.append({
                'barcode': row['barcode'],
                'name': row['name'],
                'shelf_qty': row['shelf_qty'],
                'storage_qty': row['storage_qty'],
                'shelf_capacity': row['shelf_capacity'], 
                'needed': row['min_threshold'] - row['shelf_qty']
            })
    
    conn.close()
    return render_template('picking_list.html', low_stock=low_stock, all_products=all_items)

@app.route('/mobile_picking', methods=['GET', 'POST'])
@login_required(roles=['Master', 'Runner'])
def mobile_picking():
    conn = get_db_connection()
    conn.row_factory = sqlite3.Row
    
    if request.method == 'POST':
        try:
            barcode = request.form.get('barcode')
            qty = int(request.form.get('qty', 0))
            current_staff = session.get('user_name', 'Mobile Runner')

            p = conn.execute('SELECT * FROM products WHERE barcode = ?', (barcode,)).fetchone()
            
            if p and qty > 0:
                if p['storage_qty'] >= qty:
                    conn.execute('''UPDATE products SET 
                                    storage_qty = storage_qty - ?, 
                                    shelf_qty = shelf_qty + ? 
                                    WHERE barcode = ?''', (qty, qty, barcode))
                    
                    add_log(barcode, p['name'], "Restock", f"Mobile Sync: Moved {qty} to shelf.", 
                            qty, user_name=current_staff, conn=conn)
                    conn.commit()
                    return jsonify({"status": "success", "message": f"Updated {p['name']}"})
                else:
                    return jsonify({"status": "error", "message": "Insufficient Storage"})
            
            return jsonify({"status": "error", "message": "Product not found"})

        except Exception as e:
            print(f"Sync Error: {e}")
            return jsonify({"status": "error", "message": "Database Busy or Connection Lost"}), 500
        finally:
            conn.close()

    try:
        raw_data = request.args.get('items') or request.args.get('data', '')
        parsed_items = []

        if raw_data:
            if ':' in raw_data:
                for pair in raw_data.split(','):
                    if ':' in pair:
                        bc, target_qty = pair.split(':', 1)
                        p = conn.execute('SELECT name, shelf_qty, storage_qty FROM products WHERE barcode = ?', (bc,)).fetchone()
                        if p:
                            parsed_items.append({
                                'name': p['name'], 'barcode': bc, 'qty': target_qty,
                                'shelf_qty': p['shelf_qty'], 'storage_qty': p['storage_qty']
                            })
            
            elif '|' in raw_data: 
                for entry in raw_data.split('|'):
                    if ' [' in entry:
                        name_part, rest = entry.rsplit(' [', 1)
                        bc = rest.split(']')[0]
                        target_qty = rest.split('x')[-1]
                        p = conn.execute('SELECT name, shelf_qty, storage_qty FROM products WHERE barcode = ?', (bc,)).fetchone()
                        parsed_items.append({
                            'name': p['name'] if p else name_part, 'barcode': bc, 'qty': target_qty,
                            'shelf_qty': p['shelf_qty'] if p else 0, 'storage_qty': p['storage_qty'] if p else 0
                        })
    finally:
        conn.close()

    return render_template('mobile_picker.html', items=parsed_items)

@app.route('/api/scan_inventory', methods=['POST'])
def scan_inventory():
    data = request.json
    barcode = data.get('barcode')
    qty = data.get('qty', 1)
    
    conn = get_db_connection()
    p = conn.execute("SELECT name FROM products WHERE barcode = ?", (barcode,)).fetchone()
    conn.close()
    
    if p:
        product_name = p['name']
        return jsonify({"status": "success", "message": f" {qty} of {product_name} in stock"})
    return jsonify({"status": "error", "message": "barcode not registered"}), 404

@app.route('/restock_report', methods=['GET'])
def restock_report_page():
    if session.get('user_role') not in ['Master', 'Runner', 'Accountant']:
        flash('Unauthorized access.', 'danger')
        return redirect(url_for('index'))
    return render_template('restock_report.html')

@app.route('/api/restock_report')
@login_required()
def restock_report_api():
    conn = get_db_connection()
    
    # CORRECTED: Uses 'logs' table (NZDT) and 'price' (cost) column
    # Uses ABS(l.quantity) because sales are stored as negative numbers in logs
    query = '''
        SELECT 
            p.barcode, p.name, p.shelf_qty, p.storage_qty, p.price,
            COALESCE(SUM(ABS(l.quantity)), 0) as total_sold_7d
        FROM products p
        LEFT JOIN logs l ON p.barcode = l.barcode 
            AND l.action_type = 'Sale'
            AND l.timestamp >= date('now', '-7 days', 'localtime')
        GROUP BY p.barcode
    '''
    try:
        products = conn.execute(query).fetchall()
    except Exception as e:
        print(f"Database Query Error: {e}") # Check terminal for this if error persists
        return jsonify({"error": "Database query failed"}), 500
    finally:
        conn.close()

    report = []
    for p in products:
        # p['price'] is the cost price in your schema
        cost_price = p['price'] if p['price'] is not None else 0
        total_stock = p['shelf_qty'] + p['storage_qty']
        
        # Calculate daily velocity over last 7 days
        avg_daily = round(p['total_sold_7d'] / 7, 2)
        
        # Trigger: If overall stock < 7 days of sales, or storage is empty
        trigger_qty = max(5, ceil(avg_daily * 7)) 
        
        if total_stock <= trigger_qty or p['storage_qty'] == 0:
            # Buffer: Aim for a 14-day supply (min 10 units)[cite: 17]
            target_stock = max(10, ceil(avg_daily * 14))
            suggested_buy = target_stock - total_stock
            
            # Ensure we suggest a restock if storage is dry even if shelf has some items[cite: 17]
            if suggested_buy > 0 or p['storage_qty'] == 0:
                final_buy = max(suggested_buy, 5 if p['storage_qty'] == 0 else 0)
                
                report.append({
                    "name": p['name'],
                    "barcode": p['barcode'],
                    "shelf_qty": p['shelf_qty'],
                    "storage_qty": p['storage_qty'],
                    "total_sold_7d": p['total_sold_7d'],
                    "avg_daily_sales": avg_daily,
                    "suggested_buy": final_buy,
                    "estimated_cost": final_buy * cost_price,
                    "priority": "CRITICAL" if p['storage_qty'] == 0 else "LOW STOCK"
                })

    return jsonify(report)

_WINDOWS_DRIVE_TYPES = {
    0: 'Unknown',
    1: 'No Root',
    2: 'Removable',
    3: 'Fixed',
    4: 'Network',
    5: 'CD-ROM',
    6: 'RAM Disk',
}


def _get_windows_volume_label(root):
    if platform.system() != 'Windows':
        return ''
    try:
        import ctypes
        buf = ctypes.create_unicode_buffer(261)
        ctypes.windll.kernel32.GetVolumeInformationW(
            root, buf, ctypes.sizeof(buf), None, None, None, None, 0
        )
        return buf.value.strip()
    except Exception:
        return ''


def list_windows_drives():
    """Enumerate local drive letters (works in Task Scheduler / kiosk sessions)."""
    if platform.system() != 'Windows':
        return []
    import ctypes
    drives = []
    bitmask = ctypes.windll.kernel32.GetLogicalDrives()
    for i, letter in enumerate(string.ascii_uppercase):
        if not (bitmask & (1 << i)):
            continue
        root = f'{letter}:\\'
        if not os.path.exists(root):
            continue
        drive_type = ctypes.windll.kernel32.GetDriveTypeW(root)
        label = _get_windows_volume_label(root)
        drives.append({
            'letter': letter,
            'path': root,
            'type': drive_type,
            'type_name': _WINDOWS_DRIVE_TYPES.get(drive_type, 'Unknown'),
            'is_removable': drive_type == 2,
            'label': label,
        })
    drives.sort(key=lambda d: (not d['is_removable'], d['letter']))
    return drives


def resolve_safe_drive_export_path(drive_path, filename):
    """Write only to the root of an existing drive (prevents path traversal)."""
    if not filename or not filename.lower().endswith('.xlsx'):
        raise ValueError('Only .xlsx exports are allowed')
    safe_name = os.path.basename(filename.replace('\\', '/'))
    if safe_name != filename.replace('\\', '/') or '..' in safe_name:
        raise ValueError('Invalid filename')

    match = re.match(r'^([A-Za-z]):\\?$', drive_path.strip())
    if not match:
        raise ValueError('Invalid drive path')
    root = f'{match.group(1).upper()}:\\'
    if not os.path.exists(root):
        raise ValueError('Drive is not available')
    full_path = os.path.normpath(os.path.join(root, safe_name))
    if not full_path.lower().startswith(root.lower()):
        raise ValueError('Invalid export path')
    return full_path


@app.route('/api/walking/export/drives')
@login_required(roles=['Master'])
def walking_export_drives():
    return jsonify({'status': 'ok', 'drives': list_windows_drives()})


@app.route('/api/walking/master_export/save', methods=['POST'])
@login_required(roles=['Master'])
def walking_master_export_save():
    drive = (request.form.get('drive') or '').strip()
    upload = request.files.get('file')
    if not upload:
        return jsonify({'status': 'error', 'message': 'No file received'}), 400
    try:
        save_path = resolve_safe_drive_export_path(drive, upload.filename or 'export.xlsx')
        upload.save(save_path)
        return jsonify({'status': 'ok', 'path': save_path})
    except ValueError as exc:
        return jsonify({'status': 'error', 'message': str(exc)}), 400
    except OSError as exc:
        return jsonify({'status': 'error', 'message': f'Could not save file: {exc}'}), 500


@app.route('/walking')
@login_required()
def walking_page():
    user_id = session.get('user_id')
    lb_filter = request.args.get('filter', 'overall')
    focus_date_str = request.args.get('date', datetime.now().strftime('%Y-%m-%d'))
    
    # Block guests from manual URL access
    if user_id == 9999:
        flash("The Walking Challenge is for registered members only.", "warning")
        return redirect(url_for('shopping'))
    
    # ... rest of your walking logic ...
    
    conn = get_db_connection()
    user = conn.execute('SELECT * FROM users WHERE id = ?', (user_id,)).fetchone()

    # 1. Fetch steps for the logged-in user dashboard
    rows = conn.execute('SELECT log_date, steps, is_private FROM walking_challenge WHERE user_id = ?', (user_id,)).fetchall()
    user_daily_steps = {row['log_date']: {'steps': row['steps'], 'private': row['is_private']} for row in rows}

    # 2. Master Export: Collect data for ALL users who have logged steps
    master_export_data = []
    if user['role'] == 'Master':
        all_data = conn.execute('''
            SELECT users.name, walking_challenge.log_date, walking_challenge.steps 
            FROM walking_challenge 
            JOIN users ON walking_challenge.user_id = users.id
            ORDER BY users.name, walking_challenge.log_date DESC
        ''').fetchall()
        master_export_data = [dict(r) for r in all_data]
    
    # 3. Leaderboard query logic (standard)
    # ... your existing query here ...

    conn.close()
    return render_template('Walking.html', 
                           user=user, 
                           user_daily_steps=user_daily_steps,
                           master_export_data=master_export_data,
                           focus_date=focus_date_str)

@app.route('/update_walking_config', methods=['POST'])
@login_required()
def update_walking_config():
    """Saves height and targets to the database[cite: 13]"""
    user_id = session.get('user_id')
    h = request.form.get('height', type=int)
    m = request.form.get('target_may', type=int)
    j = request.form.get('target_july', type=int)
    s = request.form.get('target_sep', type=int)

    conn = get_db_connection()
    try:
        conn.execute('''
            UPDATE users SET height = ?, target_may = ?, target_july = ?, target_sep = ? 
            WHERE id = ?''', (h, m, j, s, user_id))
        conn.commit()
    finally:
        conn.close()
    return redirect(url_for('walking_page'))

@app.route('/upload_steps', methods=['POST'])
@login_required()
def upload_steps():
    user_id = session.get('user_id')
    steps = request.form.get('steps', type=int)
    log_date = request.form.get('log_date')
    # Capture the privacy toggle (1 for private, 0 for public)
    is_private = request.form.get('is_private', 0, type=int)
    
    if not log_date:
        log_date = datetime.now().strftime('%Y-%m-%d')

    if steps is not None and steps >= 0:
        conn = get_db_connection()
        try:
            existing = conn.execute(
                'SELECT id FROM walking_challenge WHERE user_id = ? AND log_date = ?', 
                (user_id, log_date)
            ).fetchone()

            if existing:
                conn.execute(
                    'UPDATE walking_challenge SET steps = ?, is_private = ? WHERE id = ?', 
                    (steps, is_private, existing['id'])
                )
                flash(f"Steps updated for {log_date}!", "success")
            else:
                conn.execute(
                    'INSERT INTO walking_challenge (user_id, steps, log_date, is_private) VALUES (?, ?, ?, ?)', 
                    (user_id, steps, log_date, is_private)
                )
                flash(f"Steps saved for {log_date}!", "success")
            
            conn.commit()
        except Exception as e:
            conn.rollback()
            flash(f"Database Error: {str(e)}", "danger")
        finally:
            conn.close()
    else:
        flash("Please enter a valid number of steps.", "warning")
        
    return redirect(url_for('walking_page'))

@app.route('/admin/reset_walking_challenge', methods=['POST'])
@login_required()
def reset_walking_challenge():
    # Final safety check in the backend
    if session.get('user_role') != 'Master':
        flash("Unauthorized: Only Master Admins can reset the challenge.", "danger")
        return redirect(url_for('walking_page'))
        
    conn = get_db_connection()
    try:
        conn.execute('DELETE FROM walking_challenge')
        conn.commit()
        flash("The Walking Challenge has been completely reset.", "success")
    except Exception as e:
        conn.rollback()
        flash(f"Error: {str(e)}", "danger")
    finally:
        conn.close()
        
    return redirect(url_for('walking_page'))

# Ensure the database schema exists for both direct execution and imported app instances.
init_db()
migrate_walking_privacy()

if __name__ == '__main__':
    # MUST be 0.0.0.0 to allow cellphone access
    app.run(host='0.0.0.0', port=5000, debug=True)