from flask import Flask

app = Flask(__name__)

@app.route("/")
def hello():
    return "hello Prodpai Cloud Test Deploy"

@app.route('/health')
def health_check():
    return {"status": "healthy"}, 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080) # nosemgrep