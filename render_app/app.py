import os
import json
import urllib.parse
import requests
from flask import Flask, render_template, request, jsonify, redirect, url_for, session

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "cloudcraft-render-secret-2026-key")

# OAuth 2.0 Configuration for Google
GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "")
REDIRECT_URI_ENV = os.environ.get("REDIRECT_URI", "")

# Google OAuth Endpoints
GOOGLE_DISCOVERY_URL = "https://accounts.google.com/.well-known/openid-configuration"
SCOPES = [
    "openid",
    "https://www.googleapis.com/auth/userinfo.profile",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/drive.file",
    "https://www.googleapis.com/auth/drive.readonly"
]

DEFAULT_COLAB_URL = os.environ.get("COLAB_TUNNEL_URL", "")
DEFAULT_API_KEY = os.environ.get("COLAB_API_KEY", "cloudcraft-secret-key-2026")

def get_google_provider_cfg():
    try:
        return requests.get(GOOGLE_DISCOVERY_URL, timeout=25).json()
    except Exception:
        return {
            "authorization_endpoint": "https://accounts.google.com/o/oauth2/v2/auth",
            "token_endpoint": "https://oauth2.googleapis.com/token",
            "userinfo_endpoint": "https://openidconnect.googleapis.com/v1/userinfo"
        }

@app.route('/')
def index():
    user = session.get("user")
    drive_data = session.get("drive_data", {})
    return render_template(
        'index.html',
        user=user,
        drive_data=drive_data,
        default_url=drive_data.get("colab_url") or DEFAULT_COLAB_URL,
        default_key=drive_data.get("api_key") or DEFAULT_API_KEY,
        google_client_id=GOOGLE_CLIENT_ID
    )

@app.route('/login')
def login():
    client_id = request.args.get("client_id") or GOOGLE_CLIENT_ID
    if not client_id:
        return redirect(url_for('index') + "?error=missing_client_id")

    provider_cfg = get_google_provider_cfg()
    auth_endpoint = provider_cfg["authorization_endpoint"]

    redirect_uri = REDIRECT_URI_ENV or url_for("callback", _external=True)

    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "access_type": "offline",
        "prompt": "consent"
    }

    session["oauth_client_id"] = client_id
    auth_url = f"{auth_endpoint}?{urllib.parse.urlencode(params)}"
    return redirect(auth_url)

@app.route('/callback')
def callback():
    code = request.args.get("code")
    if not code:
        return redirect(url_for('index') + "?error=no_code")

    client_id = session.get("oauth_client_id") or GOOGLE_CLIENT_ID
    client_secret = os.environ.get("GOOGLE_CLIENT_SECRET", "")

    provider_cfg = get_google_provider_cfg()
    token_endpoint = provider_cfg["token_endpoint"]
    redirect_uri = REDIRECT_URI_ENV or url_for("callback", _external=True)

    token_data = {
        "code": code,
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": redirect_uri,
        "grant_type": "authorization_code"
    }

    try:
        token_res = requests.post(token_endpoint, data=token_data, timeout=25)
        tokens = token_res.json()
        access_token = tokens.get("access_token")

        if not access_token:
            return redirect(url_for('index') + "?error=token_failed")

        # Get User Info
        userinfo_endpoint = provider_cfg["userinfo_endpoint"]
        user_res = requests.get(
            userinfo_endpoint,
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=25
        )
        user_info = user_res.json()

        session["user"] = {
            "name": user_info.get("name", "Usuario"),
            "email": user_info.get("email", ""),
            "picture": user_info.get("picture", "")
        }
        session["access_token"] = access_token

        # Try searching for minecraft/server_list.txt in Google Drive
        drive_config = fetch_drive_config(access_token)
        session["drive_data"] = drive_config

    except Exception as e:
        return redirect(url_for('index') + f"?error={urllib.parse.quote(str(e))}")

    return redirect(url_for('index'))

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('index'))

def fetch_drive_config(access_token):
    headers = {"Authorization": f"Bearer {access_token}"}
    try:
        # Search for server_list.txt in Drive
        q = "name = 'server_list.txt' and trashed = false"
        res = requests.get(
            f"https://www.googleapis.com/drive/v3/files?q={urllib.parse.quote(q)}",
            headers=headers,
            timeout=25
        )
        files = res.json().get("files", [])
        if files:
            file_id = files[0]["id"]
            content_res = requests.get(
                f"https://www.googleapis.com/drive/v3/files/{file_id}?alt=media",
                headers=headers,
                timeout=25
            )
            data = content_res.json()
            return {
                "active_server": data.get("server_in_use", ""),
                "api_key": data.get("api_key", "cloudcraft-secret-key-2026"),
                "colab_url": data.get("tunnel_url", ""),
                "drive_connected": True
            }
    except Exception:
        pass
    return {"drive_connected": True}

@app.route('/api/proxy/status', methods=['POST'])
def proxy_status():
    data = request.json or {}
    colab_url = data.get('colab_url', '').rstrip('/')
    api_key = data.get('api_key', '')

    if not colab_url:
        return jsonify({"status": "error", "message": "Ingresa la URL de tu túnel de Colab o conéctate con Google Drive."}), 400

    endpoint = f"{colab_url}/api/remote/status?key={api_key}"
    try:
        res = requests.get(endpoint, timeout=25)
        return jsonify(res.json()), res.status_code
    except Exception as e:
        return jsonify({
            "status": "error",
            "message": f"No se pudo conectar con Colab: {str(e)}. Asegúrate de tener el cuaderno ejecutándose."
        }), 502

@app.route('/api/proxy/restart', methods=['POST'])
def proxy_restart():
    data = request.json or {}
    colab_url = data.get('colab_url', '').rstrip('/')
    api_key = data.get('api_key', '')

    if not colab_url:
        return jsonify({"status": "error", "message": "URL de túnel no especificada."}), 400

    endpoint = f"{colab_url}/api/remote/restart?key={api_key}"
    try:
        res = requests.post(endpoint, timeout=25)
        return jsonify(res.json()), res.status_code
    except Exception as e:
        return jsonify({
            "status": "error",
            "message": f"Error enviando orden de reinicio: {str(e)}"
        }), 502

@app.route('/api/proxy/start', methods=['POST'])
def proxy_start():
    data = request.json or {}
    colab_url = data.get('colab_url', '').rstrip('/')
    api_key = data.get('api_key', '')

    if not colab_url:
        return jsonify({"status": "error", "message": "URL de túnel no especificada."}), 400

    endpoint = f"{colab_url}/api/remote/start?key={api_key}"
    try:
        res = requests.post(endpoint, timeout=25)
        return jsonify(res.json()), res.status_code
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 502

@app.route('/api/proxy/stop', methods=['POST'])
def proxy_stop():
    data = request.json or {}
    colab_url = data.get('colab_url', '').rstrip('/')
    api_key = data.get('api_key', '')

    if not colab_url:
        return jsonify({"status": "error", "message": "URL de túnel no especificada."}), 400

    endpoint = f"{colab_url}/api/remote/stop?key={api_key}"
    try:
        res = requests.post(endpoint, timeout=25)
        return jsonify(res.json()), res.status_code
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 502

@app.route('/api/proxy/command', methods=['POST'])
def proxy_command():
    data = request.json or {}
    colab_url = data.get('colab_url', '').rstrip('/')
    api_key = data.get('api_key', '')
    cmd = data.get('command', '')

    if not colab_url:
        return jsonify({"status": "error", "message": "URL de túnel no especificada."}), 400

    endpoint = f"{colab_url}/api/remote/command?key={api_key}"
    try:
        res = requests.post(endpoint, json={"command": cmd}, timeout=25)
        return jsonify(res.json()), res.status_code
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 502

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
