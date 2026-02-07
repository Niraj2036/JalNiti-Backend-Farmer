from flask import Flask, request, jsonify
from flask_cors import CORS
from app.levels import levels_bp
from app.balance import balance_bp
from app.crop import crop_bp
from app.sowing import sowing_bp
from app.crop_ai import crop_ai_bp
app = Flask(__name__)
CORS(app)   # allows frontend to call backend

# -----------------------------
# Health check
# -----------------------------
@app.route("/", methods=["GET"])
def home():
    return jsonify({
        "status": "running",
        "message": "Flask backend is up 🚀"
    })

# -----------------------------
# Sample GET API
# -----------------------------
@app.route("/api/hello", methods=["GET"])
def hello():
    name = request.args.get("name", "World")
    return jsonify({
        "greeting": f"Hello {name}"
    })

# -----------------------------
# Sample POST API
# -----------------------------
@app.route("/api/data", methods=["POST"])
def receive_data():
    data = request.get_json()

    return jsonify({
        "status": "success",
        "received_data": data
    }), 200

# -----------------------------
# Error handling
# -----------------------------
@app.errorhandler(404)
def not_found(e):
    return jsonify({"error": "Route not found"}), 404

@app.errorhandler(500)
def server_error(e):
    return jsonify({"error": "Internal server error"}), 500

app.register_blueprint(levels_bp, url_prefix="/api/levels")
app.register_blueprint(balance_bp, url_prefix="/api/balance")
app.register_blueprint(crop_bp, url_prefix="/api/crop")
app.register_blueprint(sowing_bp, url_prefix="/api/sowing")
app.register_blueprint(crop_ai_bp, url_prefix="/api/crop-ai")
# -----------------------------
# Run server
# -----------------------------
if __name__ == "__main__":
    app.run(
        debug=True,
        host="0.0.0.0",
        port=5000
    )
