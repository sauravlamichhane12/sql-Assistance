from flask import Flask, render_template, request, jsonify, session, redirect, url_for
from vanna_config import MyVanna, DATABASES
import pymysql
import os
from pymysql import MySQLError
from functools import wraps
from datetime import datetime
from typing import Tuple
import logging
from dotenv import load_dotenv

import json

app = Flask(__name__)
app.secret_key = os.urandom(24)
app.config['TEMPLATES_AUTO_RELOAD'] = True

# Configure logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv()

# Database configurations - move to environment variables
DATABASES = {
    "chinook": {
        "host": os.getenv("DB_HOST", "localhost"),
        "user": os.getenv("DB_USER", "root"),
        "password": os.getenv("DB_PASSWORD", "root1234"),
        "database": "chinook",
        "port": int(os.getenv("DB_PORT", 3306))
    },
    # ... other databases
}

def db_connection_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'selected_db' not in session or not session['selected_db']:
            return jsonify({"error": "No database selected"}), 400
        return f(*args, **kwargs)
    return decorated_function

def test_mysql_connection(db_config: dict) -> Tuple[bool, str]:
    """
    Test MySQL database connection with comprehensive error handling
    """
    # Create a copy to avoid modifying the original
    config = db_config.copy()
    
    # Remove auth_plugin if present as it's not supported by PyMySQL connect
    config.pop('auth_plugin', None)
    
    # Set connection parameters
    connection_params = {
        'host': config.get('server') or config.get('host', 'localhost'),
        'user': config.get('user'),
        'password': config.get('password'),
        'database': config.get('database'),
        'port': config.get('port', 3306),
        'connect_timeout': config.get('connect_timeout', 5),
        'cursorclass': pymysql.cursors.DictCursor
    }

    # Validate required parameters
    missing_params = []
    for param in ['user', 'password', 'database']:
        if not connection_params.get(param):
            missing_params.append(param)
    
    if missing_params:
        error_msg = f"Missing required connection parameters: {', '.join(missing_params)}"
        app.logger.error(error_msg)
        return False, error_msg

    try:
        connection = pymysql.connect(**connection_params)
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")
                if cursor.fetchone():
                    return True, f"Successfully connected to {connection_params['database']}"
                raise Exception("Connection test failed")
        finally:
            connection.close()
    except Exception as e:
        error_msg = f"Connection error: {str(e)}"
        app.logger.error(error_msg)
        return False, error_msg

def get_vanna_instance(db_name):
    if 'vanna_instances' not in session:
        session['vanna_instances'] = {}
    
    if db_name not in session['vanna_instances']:
        try:
            # Get database config and ensure database name is set
            db_config = DATABASES[db_name].copy()
            db_config['database'] = db_name  # Explicitly set database name
            
            app.logger.debug(f"Creating Vanna instance for {db_name}")
            app.logger.debug(f"Database config: {db_config}")
            
            # Test connection before creating Vanna instance
            conn_success, conn_msg = test_mysql_connection(db_config)
            if not conn_success:
                raise Exception(conn_msg)
            
            vn = MyVanna(db_name)
            try:
                vn.store_database_schema()
            except Exception as schema_error:
                app.logger.error(f"Schema storage error: {str(schema_error)}")
                raise Exception(f"Schema storage failed: {str(schema_error)}")
            
            session['vanna_instances'][db_name] = {
                'initialized': True,
                'last_used': datetime.now().isoformat()
            }
            session.modified = True
            return vn
        except Exception as e:
            app.logger.error(f"Vanna initialization error for {db_name}: {str(e)}")
            raise Exception(str(e))
    return MyVanna(db_name)

@app.route('/', methods=['GET', 'POST'])
def index():
    session.setdefault('selected_db', None)
    session.setdefault('show_sql', True)
    session.setdefault('chat_history', {})
    session.setdefault('connection_status', {})
    session.setdefault('error', None)

    if request.method == 'POST':
        selected_db = request.form.get('database')
        if selected_db and selected_db in DATABASES:
            # Create a complete config dictionary with the database name
            db_config = DATABASES[selected_db].copy()
            db_config.update({
                'database': selected_db,  # Explicitly set database name
                'host': 'localhost',
                'user': 'root',
                'password': 'root1234',
                'port': 3306
            })
            
            app.logger.debug(f"Testing connection for database: {selected_db}")
            app.logger.debug(f"Complete config: {db_config}")
            
            success, message = test_mysql_connection(db_config)
            
            if success:
                session['selected_db'] = selected_db
                session['chat_history'].setdefault(selected_db, [])
                session['connection_status'] = {selected_db: True}
                session['error'] = None
            else:
                app.logger.error(f"Connection failed for {selected_db}: {message}")
                return render_template(
                    'index1.html',
                    databases=DATABASES.keys(),
                    error=message,
                    selected_db=None,
                    connection_status=False
                )

    return render_template(
        'index.html',
        databases=DATABASES.keys(),
        selected_db=session.get('selected_db'),
        chat_history=session.get('chat_history', {}).get(session.get('selected_db'), []),
        connection_status=session.get('connection_status', {}).get(session.get('selected_db'), False),
        sql_visible=session.get('show_sql', True),
        error=session.get('error')
    )

@app.route('/verify_db', methods=['POST'])
def verify_db():
    db_name = request.json.get('db_name')
    if not db_name or db_name not in DATABASES:
        return jsonify({"valid": False, "message": "Database not configured"})
    
    # Create complete config with database name
    db_config = DATABASES[db_name].copy()
    db_config.update({
        'database': db_name,  # Explicitly set database name
        'host': 'localhost',
        'user': 'root',
        'password': 'root1234',
        'port': 3306
    })
    
    valid, message = test_mysql_connection(db_config)
    return jsonify({"valid": valid, "message": message})

@app.route('/ask', methods=['POST'])
@db_connection_required
def ask():
    if not request.is_json:
        return jsonify({"error": "Request must be JSON"}), 400
        
    data = request.get_json()
    question = data.get('prompt', '').strip()
    
    if not question:
        return jsonify({"error": "Question cannot be empty"}), 400

    try:
        vn = get_vanna_instance(session['selected_db'])
        sql_query = vn.generate_sql_query(question)
        
        chat_entry = {
            "question": question,
            "sql": sql_query,
            "timestamp": datetime.now().isoformat()
        }
        session['chat_history'][session['selected_db']].append(chat_entry)
        session.modified = True
        
        return jsonify(chat_entry)
    except Exception as e:
        logger.error(f"Query error: {str(e)}")
        return jsonify({"error": str(e)}), 500

@app.route('/set_sql_visibility', methods=['POST'])
def set_sql_visibility():
    try:
        session['show_sql'] = request.form.get('show_sql', 'false').lower() == 'true'
        session.modified = True
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 400

@app.route('/train', methods=['POST'])
@db_connection_required
def train():
    try:
        vn = get_vanna_instance(session['selected_db'])
        vn.train_vanna()
        return jsonify({"message": "Training completed successfully!"})
    except Exception as e:
        logger.error(f"Training error: {str(e)}")
        return jsonify({"error": str(e)}), 500

@app.route('/reset', methods=['POST'])
def reset():
    try:
        session.clear()
        return jsonify({"success": True, "message": "Session reset successfully"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/add_training', methods=['POST'])
def add_training_data():
    if 'selected_db' not in session:
        return jsonify({"error": "No database selected"}), 400
        
    try:
        data = request.get_json()
        question = data.get('question')
        sql = data.get('sql')
        ddl = data.get('ddl')
        documentation = data.get('documentation')

        # Validate required fields
        if not question or not sql:
            return jsonify({"error": "Question and SQL are required"}), 400

        vn = get_vanna_instance(session['selected_db'])
        training_id = vn.train(
            question=question,
            sql=sql,
            ddl=ddl,
            documentation=documentation
        )

        app.logger.info(f"Training data added successfully. ID: {training_id}")
        return jsonify({"id": training_id, "message": "Training data added successfully"})
    except Exception as e:
        app.logger.error(f"Training error: {str(e)}")
        return jsonify({"error": str(e)}), 500

@app.route('/upload-json', methods=['POST'])
def upload_json():
    if 'jsonFile' not in request.files:
        return jsonify({'error': 'No file part'}), 400
    
    file = request.files['jsonFile']
    if file.filename == '':
        return jsonify({'error': 'No selected file'}), 400
    
    # Read and parse the JSON file
    try:
        training_data = json.load(file)
    except Exception as e:
        return jsonify({'error': f'Invalid JSON file: {str(e)}'}), 400
    
    # Process the training data (e.g., train the model)
    try:
        for item in training_data:
            question = item.get('question')
            sql = item.get('sql')
            ddl = item.get('ddl', '')
            documentation = item.get('documentation', '')
            
            # Example: Add training data to the model
            add_training_data(question, sql, ddl, documentation)
        
        return jsonify({'message': 'Training completed successfully'}), 200
    except Exception as e:
        return jsonify({'error': f'Training failed: {str(e)}'}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
