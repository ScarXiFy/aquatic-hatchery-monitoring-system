from flask import Blueprint, render_template

main_bp = Blueprint("main", __name__)


@main_bp.route("/")
def index():
    return render_template("home.html")


@main_bp.route("/graph")
def graph():
    return render_template("view_graph.html")
