
from pathlib import Path
from flask import Flask, jsonify, send_from_directory

app = Flask(__name__)

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"


@app.route("/")
def home():
    return send_from_directory(FRONTEND_DIR, "index.html")


@app.route("/style.css")
def stylesheet():
    return send_from_directory(FRONTEND_DIR, "style.css")


@app.route("/script.js")
def javascript():
    return send_from_directory(FRONTEND_DIR, "script.js")


@app.route("/api/health")
def health():
    return jsonify({
        "status": "healthy",
        "project": "RepoBridge AI"
    })


if __name__ == "__main__":
    app.run(debug=True)
