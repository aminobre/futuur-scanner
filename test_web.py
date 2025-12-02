from flask import Flask

app = Flask(__name__)

@app.route("/")
def index():
    return "Flask is working"

if __name__ == "__main__":
    app.run()  # default: http://127.0.0.1:5000
