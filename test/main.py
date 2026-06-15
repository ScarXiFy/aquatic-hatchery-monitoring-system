from flask import Flask, render_template
from piUtils import sourceValveState, drainValveState
#import RPI.GPIO as GPIO

app = Flask(__name__)

@app.route("/")
def index():
    return render_template('index.html')

@app.route("/valve/<string:name>/<int:state>", methods=["POST"])
def controlValve(name, state):

    isOpen = (state == 1)

    if name == "source":
        sourceValveState(isOpen)
    elif name == "drain":
        drainValveState(isOpen)
    else:
        return ("Unknown valve", 400)
    return ("", 204)

if __name__ == '__main__':
    app.run(host="0.0.0.0", debug=True)