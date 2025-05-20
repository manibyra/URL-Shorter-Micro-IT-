from flask import Flask, render_template, request, redirect, url_for, flash, session, abort
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash
import mysql.connector
import os
import string, random
import qrcode
from flask import abort
import re

app = Flask(__name__)
app.secret_key = 'your_secret_key'

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

QR_FOLDER = os.path.join('static/qrcodes')
os.makedirs(QR_FOLDER, exist_ok=True)

DB_CONFIG = {
    'host': 'localhost',
    'user': 'root',
    'password': '',
    'database': 'url_shorter'
}

# --- DB Utility ---
def get_db_connection():
    return mysql.connector.connect(**DB_CONFIG)

# --- User Class ---
class User(UserMixin):
    def __init__(self, id_, username):
        self.id = id_
        self.username = username

    @staticmethod
    def get(user_id):
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT id, username FROM users WHERE id = %s", (user_id,))
        row = cursor.fetchone()
        conn.close()
        if row:
            return User(row[0], row[1])
        return None

@login_manager.user_loader
def load_user(user_id):
    return User.get(user_id)

# --- Routes ---
@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        confirm_password = request.form['confirm_password']
        
        if password != confirm_password:
            flash('Passwords do not match. Please try again.', 'error')
            return render_template('register.html')
        
        hashed_password = generate_password_hash(password)
        
        conn = get_db_connection()
        cursor = conn.cursor()
        try:
            cursor.execute("INSERT INTO users (username, password) VALUES (%s, %s)", (username, hashed_password))
            conn.commit()
            flash('Registration successful. Please login.')

        except mysql.connector.IntegrityError:
            flash('Username already exists.', 'error')
        finally:
            conn.close()
    
    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password_input = request.form['password']
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT id, username, password FROM users WHERE username = %s", (username,))
        user = cursor.fetchone()
        conn.close()

        if user and check_password_hash(user[2], password_input):
            user_obj = User(user[0], user[1])
            login_user(user_obj)
            return redirect(url_for('dashboard'))
        else:
            flash('Invalid credentials')
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))

@app.route('/dashboard')
@login_required
def dashboard():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM urls WHERE user_id = %s ORDER BY id ASC", (current_user.id,))
    urls = cursor.fetchall()
    conn.close()

    return render_template('dashboard.html', urls=urls)

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/delete/<int:url_id>', methods=['POST'])
@login_required
def delete_url(url_id):
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    # Get QR code path before deleting the row
    cursor.execute("SELECT qr_code_path FROM urls WHERE id = %s AND user_id = %s", (url_id, current_user.id))
    result = cursor.fetchone()

    if result:
        qr_code_path = result['qr_code_path']

        # Delete the URL record
        cursor.execute("DELETE FROM urls WHERE id = %s AND user_id = %s", (url_id, current_user.id))
        conn.commit()

        # Delete the QR code file if it exists
        if qr_code_path and os.path.exists(qr_code_path):
            os.remove(qr_code_path)

        flash('URL and QR code deleted successfully.')
    else:
        flash('URL not found or not authorized.')

    conn.close()
    return redirect(url_for('dashboard'))



@app.route('/shorten', methods=['POST'])
@login_required
def shorten():
    original_url = request.form['original_url'].strip()
    custom_alias = request.form['custom_alias'].strip()
    expires_at_input = request.form.get('expires_at')
    
    expires_at = None
    if expires_at_input:
        try:
            expires_at = datetime.strptime(expires_at_input, '%Y-%m-%dT%H:%M')
        except ValueError:
            flash("Invalid expiration date format.", "error")
            return redirect(url_for('dashboard'))

    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT short_url FROM urls WHERE original_url = %s AND user_id = %s", (original_url, current_user.id))
    existing = cursor.fetchone()
    if existing:
        flash(f"This URL has already been shortened: http://localhost:5000/{existing[0]}", "info")
        conn.close()
        return redirect(url_for('dashboard'))

    if custom_alias:
        cursor.execute("SELECT * FROM urls WHERE short_url = %s", (custom_alias,))
        if cursor.fetchone():
            flash("Custom alias already taken by someone. Please try another one.", "error")
            conn.close()
            return redirect(url_for('dashboard'))
        short_url = custom_alias
    else:
        short_url = generate_short_url()
        while True:
            cursor.execute("SELECT * FROM urls WHERE short_url = %s", (short_url,))
            if cursor.fetchone():
                short_url = generate_short_url()
            else:
                break

    qr_code_path = create_qr_code(short_url)

    cursor.execute(
        "INSERT INTO urls (original_url, short_url, user_id, qr_code_path, expires_at) VALUES (%s, %s, %s, %s, %s)",
        (original_url, short_url, current_user.id, qr_code_path, expires_at)
    )
    conn.commit()
    conn.close()

    return redirect(url_for('dashboard'))


@app.route('/shorten_anon', methods=['POST'])
def shorten_anon():
    original_url = request.form['original_url'].strip()

    conn = get_db_connection()
    cursor = conn.cursor()

    # Check if this URL was already shortened anonymously
    cursor.execute("SELECT short_url FROM urls WHERE original_url = %s AND user_id IS NULL", (original_url,))
    existing = cursor.fetchone()
    if existing:
        short_url = existing[0]
    else:
        short_url = generate_short_url()
        while True:
            cursor.execute("SELECT * FROM urls WHERE short_url = %s", (short_url,))
            if cursor.fetchone():
                short_url = generate_short_url()
            else:
                break

        qr_code_path = create_qr_code(short_url)
        cursor.execute(
            "INSERT INTO urls (original_url, short_url, user_id, qr_code_path) VALUES (%s, %s, %s, %s)",
            (original_url, short_url, None, qr_code_path)
        )
        conn.commit()

    conn.close()

    # Pass short_url to homepage for display
    return render_template('index.html', short_url=f"http://localhost:5000/{short_url}")

@app.route('/<short>')
def redirect_short_url(short):
    # Reject invalid short URLs early
    if not re.fullmatch(r'[A-Za-z0-9]{4,10}', short):
        abort(404)

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    cursor.execute("SELECT id, original_url, expiration_date, qr_code_path FROM urls WHERE short_url = %s", (short,))
    row = cursor.fetchone()

    if row:
        # Check expiration
        if row['expiration_date'] and datetime.now() > row['expiration_date']:
            # Delete expired record
            cursor.execute("DELETE FROM urls WHERE id = %s", (row['id'],))
            conn.commit()

            # Delete QR code file
            if row['qr_code_path'] and os.path.exists(row['qr_code_path']):
                os.remove(row['qr_code_path'])

            conn.close()
            flash("This link has expired.", "error")
            return redirect(url_for('index'))

        conn.close()
        return redirect(row['original_url'])
    else:
        conn.close()
        flash("URL not found", "error")
        return redirect(url_for('index'))

# --- Helper Functions ---
def generate_short_url(length=6):
    return ''.join(random.choices(string.ascii_letters + string.digits, k=length))

def create_qr_code(url_code):
    qr = qrcode.make(f"http://localhost:5000/{url_code}")
    path = os.path.join(QR_FOLDER, f"{url_code}.png")
    qr.save(path)
    return path

@app.route('/go/<short>')
def go(short):
    if not re.fullmatch(r'[A-Za-z0-9]{4,10}', short):
        abort(404)

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    cursor.execute("SELECT id, original_url, expiration_date, qr_code_path FROM urls WHERE short_url = %s", (short,))
    row = cursor.fetchone()

    if row:
        if row['expiration_date'] and datetime.now() > row['expiration_date']:
            cursor.execute("DELETE FROM urls WHERE id = %s", (row['id'],))
            conn.commit()
            conn.close()

            if row['qr_code_path'] and os.path.exists(row['qr_code_path']):
                os.remove(row['qr_code_path'])

            flash("This link has expired.", "error")
            return redirect(url_for('index'))

        conn.close()
        return redirect(row['original_url'])
    else:
        conn.close()
        flash("URL not found", "error")
        return redirect(url_for('index'))

if __name__ == '__main__':
    app.run(debug=True)
