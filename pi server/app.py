from flask import Flask, render_template
from database import init_db
from routes.telemetry import telemetry_bp
from routes.config import config_bp

app = Flask(__name__)

# Register Blueprints
app.register_blueprint(telemetry_bp)
app.register_blueprint(config_bp)

@app.route('/', methods=['GET'])
def dashboard():
    return render_template('index.html')

if __name__ == '__main__':
    init_db()
    app.run(host='0.0.0.0', port=7000, threaded=True)
