import os
import time
import uuid
import random
from flask import Flask, request, redirect, url_for, render_template, make_response, g

app = Flask(__name__)

SESSION_TTL_SECONDS = int(os.environ.get("SESSION_TTL_SECONDS", "300"))

MEMBERS = {
    "12345": {"name": "Alicia Morgan", "savings_balance": 4210.55, "checking_balance": 812.10},
    "20001": {"name": "David Chen", "savings_balance": 150.00, "checking_balance": 3300.42},
    "40404": {"name": "Restricted Record", "savings_balance": None, "checking_balance": None},  
    # 99999 intentionally absent -> "member not found" business outcome
}

SUB_ACCOUNTS = {}  

SESSIONS = {}  


def _now():
    return time.time()


def _get_session_id():
    return request.cookies.get("cu_session")


def _session_valid(session_id):
    if not session_id or session_id not in SESSIONS:
        return False
    sess = SESSIONS[session_id]
    if _now() - sess["last_seen"] > SESSION_TTL_SECONDS:
        del SESSIONS[session_id]
        return False
    sess["last_seen"] = _now()
    return True


@app.before_request
def _load_session():
    g.session_id = _get_session_id()
    g.authenticated = _session_valid(g.session_id)


def _maybe_slow():
    """Simulate transient slowness when ?slow=1 is present, or randomly ~1 in 6 requests."""
    if request.args.get("slow") == "1" or random.random() < 0.08:
        time.sleep(random.uniform(1.8, 3.2))


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        # Any non-empty username/password is accepted -- this is a proxy target.
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "").strip()
        if not username or not password:
            return render_template("login.html", error="Username and password are required.", expired=False)
        session_id = str(uuid.uuid4())
        SESSIONS[session_id] = {"created_at": _now(), "last_seen": _now()}
        resp = make_response(redirect(url_for("search")))
        resp.set_cookie("cu_session", session_id, httponly=True)
        return resp
    expired = request.args.get("expired") == "1"
    return render_template("login.html", error=None, expired=expired)


@app.route("/logout")
def logout():
    if g.session_id in SESSIONS:
        del SESSIONS[g.session_id]
    resp = make_response(redirect(url_for("login")))
    resp.delete_cookie("cu_session")
    return resp


def _require_session():
    if not g.authenticated:
        return redirect(url_for("login", expired=1))
    return None

@app.route("/search", methods=["GET", "POST"])
def search():
    redirect_resp = _require_session()
    if redirect_resp:
        return redirect_resp
    _maybe_slow()

    result_member = None
    not_found = False
    queried_id = None

    if request.method == "POST":
        queried_id = request.form.get("member_id", "").strip()
        result_member = MEMBERS.get(queried_id)
        not_found = result_member is None

    return render_template(
        "search.html",
        result_member=result_member,
        not_found=not_found,
        queried_id=queried_id,
    )

@app.route("/member/<member_id>")
def member_detail(member_id):
    redirect_resp = _require_session()
    if redirect_resp:
        return redirect_resp
    _maybe_slow()

    member = MEMBERS.get(member_id)
    if not member:
        return render_template("error_not_found.html", member_id=member_id), 404

    return render_template("member_detail.html", member=member, member_id=member_id)


@app.route("/member/<member_id>/balance")
def member_balance_iframe(member_id):
    """Rendered inside an <iframe> on the detail page -- deliberately a separate document."""
    redirect_resp = _require_session()
    if redirect_resp:
        return redirect_resp

    member = MEMBERS.get(member_id)
    if not member:
        return render_template("error_not_found.html", member_id=member_id), 404
    if member_id == "40404":
        return render_template("error_permission.html", member_id=member_id), 403

    return render_template("balance_iframe.html", member=member, member_id=member_id)


@app.route("/member/<member_id>/sub-account/new", methods=["GET", "POST"])
def sub_account_new(member_id):
    redirect_resp = _require_session()
    if redirect_resp:
        return redirect_resp

    member = MEMBERS.get(member_id)
    if not member:
        return render_template("error_not_found.html", member_id=member_id), 404

    if request.method == "POST":
        account_type = request.form.get("account_type", "")
        try:
            deposit = float(request.form.get("initial_deposit", "0"))
        except ValueError:
            deposit = -1

        if deposit < 50:
            return render_template(
                "sub_account_new.html",
                member=member,
                member_id=member_id,
                error="Initial deposit must be at least $50.00.",
                account_type=account_type,
                initial_deposit=request.form.get("initial_deposit", ""),
            )

        resp = make_response(
            redirect(url_for("sub_account_review", member_id=member_id, account_type=account_type, deposit=deposit))
        )
        return resp

    return render_template("sub_account_new.html", member=member, member_id=member_id, error=None,
                            account_type="", initial_deposit="")


@app.route("/member/<member_id>/sub-account/review", methods=["GET", "POST"])
def sub_account_review(member_id):
    redirect_resp = _require_session()
    if redirect_resp:
        return redirect_resp

    member = MEMBERS.get(member_id)
    if not member:
        return render_template("error_not_found.html", member_id=member_id), 404

    if request.method == "POST":
        account_type = request.form.get("account_type")
        deposit = float(request.form.get("deposit"))
        account_number = f"SUB-{member_id}-{random.randint(1000, 9999)}"
        SUB_ACCOUNTS.setdefault(member_id, []).append(
            {"type": account_type, "deposit": deposit, "account_number": account_number}
        )
        return redirect(url_for("sub_account_confirm", member_id=member_id, account_number=account_number))

    account_type = request.args.get("account_type")
    deposit = request.args.get("deposit")
    return render_template(
        "sub_account_review.html",
        member=member,
        member_id=member_id,
        account_type=account_type,
        deposit=deposit,
    )


@app.route("/member/<member_id>/sub-account/confirm")
def sub_account_confirm(member_id):
    redirect_resp = _require_session()
    if redirect_resp:
        return redirect_resp
    account_number = request.args.get("account_number", "UNKNOWN")
    member = MEMBERS.get(member_id, {"name": "Unknown"})
    return render_template(
        "sub_account_confirm.html", member=member, member_id=member_id, account_number=account_number
    )


@app.route("/")
def index():
    return redirect(url_for("search") if g.authenticated else url_for("login"))


if __name__ == "__main__":
    port = int(os.environ.get("TARGET_APP_PORT", "5001"))
    app.run(host="127.0.0.1", port=port, debug=False)
