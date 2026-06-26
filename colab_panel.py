# -*- coding: utf-8 -*-
import os
import sys
import time
import json
import subprocess
import threading
import re
import requests
import psutil
import shutil
import zipfile
from bs4 import BeautifulSoup
from flask import Flask, jsonify, request, send_from_directory, render_template_string

app = Flask(__name__)

# --- Paths & Configs ---
# Support both Google Colab Linux path and test path
if os.path.exists('/content/drive'):
    DRIVE_PATH = '/content/drive/MyDrive/minecraft'
else:
    # Local fallback for testing in scratch
    DRIVE_PATH = r'C:\Users\arnie\.gemini\antigravity-ide\scratch\minecraft'
    if not os.path.exists(DRIVE_PATH):
        os.makedirs(DRIVE_PATH, exist_ok=True)

SERVERCONFIG = os.path.join(DRIVE_PATH, 'server_list.txt')
LOGS_DIR = os.path.join(DRIVE_PATH, 'logs')

# Global process holders
mc_process = None
tunnel_process = None
server_status = "offline"  # offline, starting, online, stopping, updating
active_server = ""
session_logs = []  # Single unified log cache for the current session (replaces system_logs + latest.log reading)
log_thread = None

# Create logs dir if not exists
os.makedirs(LOGS_DIR, exist_ok=True)

def add_system_log(message):
    timestamp = time.strftime("[%H:%M:%S]")
    log_line = f"{timestamp} [SISTEMA] {message}"
    session_logs.append(log_line)
    print(log_line)

def load_historical_logs(server_name):
    global session_logs
    if not server_name:
        return
    log_file_path = os.path.join(DRIVE_PATH, server_name, 'logs', 'latest.log')
    if os.path.exists(log_file_path):
        try:
            # Load last 150 lines for instant console history
            with open(log_file_path, 'r', encoding='utf-8', errors='ignore') as f:
                lines = f.readlines()
                last_lines = lines[-150:]
                ansi_escape = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
                session_logs = [ansi_escape.sub('', l.strip()) for l in last_lines]
                add_system_log(f"Historial de consola cargado ({len(session_logs)} líneas).")
        except Exception as e:
            add_system_log(f"No se pudo cargar el historial de logs: {str(e)}")

# --- Java Installation Helpers ---
def get_installed_java_version():
    try:
        # Run java -version. Note that java outputs version info to stderr
        result = subprocess.run(["java", "-version"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=5)
        output = result.stderr or result.stdout
        match = re.search(r'version "(\d+)\.', output)
        if match:
            return int(match.group(1))
        match = re.search(r'version "1\.(\d+)\.', output)
        if match:
            return int(match.group(1))
    except Exception:
        pass
    return None

def determine_required_java_version(version, server_type):
    # Normalize version string
    version = str(version).strip()
    server_type = str(server_type).lower()
    
    if server_type == "velocity":
        return 17
        
    try:
        parts = [int(x) for x in re.findall(r'\d+', version)]
        if not parts:
            return 21
        major = parts[0]
        minor = parts[1] if len(parts) > 1 else 0
        patch = parts[2] if len(parts) > 2 else 0
    except Exception:
        return 21
        
    # Case 1: Minecraft Version (e.g. 1.21.1, 1.12.2)
    if major == 1:
        if minor >= 21 or (minor == 20 and patch >= 5):
            return 21
        elif minor >= 17:
            return 17
        elif minor >= 13:
            return 11
        else:
            return 8
            
    # Case 2: NeoForge Version
    if server_type == "neoforge":
        if major >= 21:
            return 21
        elif major == 20:
            if minor >= 5:
                return 21
            return 17
        else:
            return 17
            
    # Case 3: Forge Version
    if server_type == "forge":
        if major >= 51:
            return 21
        elif major >= 37:
            return 17
        elif major >= 26:
            return 11
        else:
            return 8
            
    # Case 4: Mohist
    if server_type == "mohist":
        if major >= 37:
            return 17
        elif major >= 26:
            return 11
        else:
            return 8
            
    # Fallback
    if major >= 51:
        return 21
    elif major >= 37:
        return 17
    elif major >= 26:
        return 11
    else:
        return 8

def repair_java_security_if_needed(required_ver):
    if sys.platform == 'win32':
        return
        
    java_path = f"/usr/lib/jvm/java-{required_ver}-openjdk-amd64"
    conf_sec_dir = f"{java_path}/conf/security"
    conf_sec_file = f"{conf_sec_dir}/java.security"
    
    if not os.path.exists(conf_sec_file):
        add_system_log(f"Falta archivo java.security en {conf_sec_file}. Intentando reparar...")
        subprocess.run(f"sudo mkdir -p {conf_sec_dir}", shell=True)
        etc_path = f"/etc/java-{required_ver}-openjdk/security/java.security"
        if os.path.exists(etc_path):
            subprocess.run(f"sudo ln -sf {etc_path} {conf_sec_file}", shell=True)
            add_system_log("Reparado mediante enlace simbólico a /etc.")
        else:
            fallback_found = False
            for alt_ver in [21, 17, 11, 8]:
                alt_path = f"/usr/lib/jvm/java-{alt_ver}-openjdk-amd64/conf/security/java.security"
                if os.path.exists(alt_path):
                    subprocess.run(f"sudo cp {alt_path} {conf_sec_file}", shell=True)
                    add_system_log(f"Reparado mediante copia desde Java {alt_ver}.")
                    fallback_found = True
                    break
                alt_path_old = f"/usr/lib/jvm/java-{alt_ver}-openjdk-amd64/jre/lib/security/java.security"
                if os.path.exists(alt_path_old):
                    subprocess.run(f"sudo cp {alt_path_old} {conf_sec_file}", shell=True)
                    add_system_log(f"Reparado mediante copia desde Java {alt_ver} (ruta antigua).")
                    fallback_found = True
                    break
            if not fallback_found:
                add_system_log("Advertencia: No se encontró ningún archivo java.security de respaldo para copiar.")

def install_java_if_needed(version, server_type):
    if sys.platform == 'win32':
        add_system_log("Entorno local Windows detectado. Saltando instalación de Java.")
        return True
        
    required_ver = determine_required_java_version(version, server_type)
    
    # Check if custom Java is enabled in colabconfig
    try:
        colabconfig = load_colab_config(active_server)
        java_config = colabconfig.get("java", {})
        cust_enabled = str(java_config.get("CustomEnabled", "False")).lower() == "true"
        if cust_enabled:
            cust_ver_str = java_config.get("version", java_config.get("version:", ""))
            cust_ver_match = re.search(r'\d+', str(cust_ver_str))
            if cust_ver_match:
                required_ver = int(cust_ver_match.group(0))
                add_system_log(f"Java personalizado habilitado en colabconfig.txt. Versión requerida: {required_ver}")
    except Exception as e:
        add_system_log(f"No se pudo leer la configuración de Java personalizada: {str(e)}")
        
    installed_ver = get_installed_java_version()
    
    if installed_ver == required_ver:
        add_system_log(f"Java {required_ver} ya está instalado y seleccionado como predeterminado.")
        repair_java_security_if_needed(required_ver)
        return True
        
    return install_java_by_number(required_ver)

def install_java_by_number(required_ver):
    if sys.platform == 'win32':
        return True
        
    add_system_log(f"Instalando Java {required_ver} (OpenJDK)... Esto tardará aproximadamente un minuto.")
    
    # 1. Wait and release apt locks
    add_system_log("Liberando bloqueos del gestor de paquetes (apt)...")
    subprocess.run("sudo rm -f /var/lib/dpkg/lock-frontend /var/lib/dpkg/lock /var/lib/apt/lists/lock /var/cache/apt/archives/lock > /dev/null 2>&1", shell=True)
    subprocess.run("sudo dpkg --configure -a > /dev/null 2>&1", shell=True)
    
    # 2. Try standard openjdk-jdk first
    pkg_name = f"openjdk-{required_ver}-jdk"
    add_system_log(f"Ejecutando apt-get install para {pkg_name}...")
    
    subprocess.run("sudo apt-get update -y > /dev/null 2>&1", shell=True)
    result = subprocess.run(f"sudo apt-get install -y {pkg_name}", shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    
    # 3. If failed, add OpenJDK PPA and retry
    if result.returncode != 0:
        add_system_log(f"Fallo inicial al instalar {pkg_name} (Código: {result.returncode}). Añadiendo PPA de OpenJDK...")
        subprocess.run("sudo add-apt-repository -y ppa:openjdk-r/ppa > /dev/null 2>&1", shell=True)
        subprocess.run("sudo apt-get update -y > /dev/null 2>&1", shell=True)
        result = subprocess.run(f"sudo apt-get install -y {pkg_name}", shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        
    # 4. If still failed, try JRE headless package as fallback
    if result.returncode != 0:
        add_system_log("Fallo al instalar JDK. Intentando instalar versión JRE Headless de respaldo...")
        jre_pkg = f"openjdk-{required_ver}-jre-headless"
        result = subprocess.run(f"sudo apt-get install -y {jre_pkg}", shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        
    # 5. If completely failed, print stderr details
    if result.returncode != 0:
        add_system_log(f"Error crítico instalando Java {required_ver}:")
        add_system_log(f"Detalles del error: {result.stderr.strip() if result.stderr else 'Desconocido'}")
        return False
        
    # 6. Locate installed Java path dynamically from /usr/lib/jvm
    jvm_dir = "/usr/lib/jvm"
    java_path = None
    if os.path.exists(jvm_dir):
        for folder in os.listdir(jvm_dir):
            if folder.startswith(f"java-{required_ver}-openjdk") and os.path.exists(os.path.join(jvm_dir, folder, "bin", "java")):
                java_path = os.path.join(jvm_dir, folder)
                break
                
    if not java_path:
        java_path = f"/usr/lib/jvm/java-{required_ver}-openjdk-amd64"
        
    add_system_log(f"Java {required_ver} detectado en la ruta: {java_path}")
    
    # 7. Configure alternatives
    add_system_log("Registrando alternativas de Java...")
    subprocess.run(f"sudo update-alternatives --install /usr/bin/java java {java_path}/bin/java 1 > /dev/null 2>&1", shell=True)
    subprocess.run(f"sudo update-alternatives --install /usr/bin/javac javac {java_path}/bin/javac 1 > /dev/null 2>&1", shell=True)
    
    os.environ["JAVA_HOME"] = java_path
    
    subprocess.run(f"sudo update-alternatives --set java {java_path}/bin/java > /dev/null 2>&1", shell=True)
    subprocess.run(f"sudo update-alternatives --set javac {java_path}/bin/javac > /dev/null 2>&1", shell=True)
    
    # Double check
    new_ver = get_installed_java_version()
    if new_ver == required_ver:
        add_system_log(f"¡Java {required_ver} instalado y configurado como predeterminado exitosamente!")
        repair_java_security_if_needed(required_ver)
        return True
    else:
        add_system_log(f"Advertencia: Se completó la instalación, pero java -version reporta Java {new_ver} (se esperaba {required_ver}).")
        repair_java_security_if_needed(required_ver)
        return True


def install_playit_if_needed():
    if sys.platform == 'win32':
        return True
        
    if not os.path.exists('/usr/local/bin/playit'):
        add_system_log("El cliente de Playit.gg no se encuentra en /usr/local/bin/playit.")
        add_system_log("Descargando el binario standalone de Playit.gg...")
        try:
            os.makedirs('/usr/local/bin', exist_ok=True)
            subprocess.run("wget -q -O /usr/local/bin/playit https://github.com/playit-cloud/playit-agent/releases/latest/download/playit-linux-amd64", shell=True)
            subprocess.run("chmod +x /usr/local/bin/playit", shell=True)
            if os.path.exists('/usr/local/bin/playit'):
                add_system_log("Playit.gg se descargó e instaló correctamente.")
                return True
            else:
                add_system_log("No se pudo descargar el binario de Playit.gg.")
                return False
        except Exception as e:
            add_system_log(f"Error descargando Playit.gg: {str(e)}")
            return False
    return True


# --- Helper Functions ---
def load_server_config():
    if not os.path.exists(SERVERCONFIG):
        default_config = {
            "server_list": [],
            "server_in_use": "",
            "ngrok_proxy": {"authtoken": "", "region": "us"},
            "zrok_proxy": {"authtoken": ""},
            "playit_proxy": {"secretkey": ""},
            "localtonet_proxy": {"authtoken": ""}
        }
        with open(SERVERCONFIG, 'w') as f:
            json.dump(default_config, f, indent=4)
        return default_config
    try:
        with open(SERVERCONFIG, 'r') as f:
            return json.load(f)
    except Exception as e:
        add_system_log(f"Error cargando server_list.txt: {str(e)}")
        return {}

def save_server_config(config):
    try:
        with open(SERVERCONFIG, 'w') as f:
            json.dump(config, f, indent=4)
    except Exception as e:
        add_system_log(f"Error guardando server_list.txt: {str(e)}")

def get_colab_config_path(server_name):
    return os.path.join(DRIVE_PATH, server_name, 'colabconfig.txt')

def load_colab_config(server_name):
    path = get_colab_config_path(server_name)
    if os.path.exists(path):
        try:
            with open(path, 'r') as f:
                return json.load(f)
        except Exception as e:
            add_system_log(f"Error cargando colabconfig.txt: {str(e)}")
    return {"server_type": "paper", "server_version": "1.21.1", "tunnel_service": "playit"}

def get_server_properties_path(server_name):
    return os.path.join(DRIVE_PATH, server_name, 'server.properties')

def free_minecraft_ports():
    ports = list(range(25565, 25576)) + list(range(19132, 19143))
    cleaned = False
    for proc in psutil.process_iter(['pid', 'name', 'connections']):
        try:
            for conn in proc.info.get('connections', []) or []:
                if conn.laddr.port in ports:
                    proc.kill()
                    cleaned = True
                    break
        except Exception:
            pass
    if cleaned:
        add_system_log("Puertos de Minecraft liberados (procesos anteriores finalizados).")

# --- Tunnel Starters ---
# --- Tunnel Starters ---
def start_playit_tunnel(config):
    global tunnel_process
    
    # Download Playit binary if needed
    install_playit_if_needed()
    
    secret_key = config.get("playit_proxy", {}).get("secretkey", "").strip()
    if not secret_key:
        add_system_log("Iniciando túnel Playit.gg fresco (sin clave secreta). Se generará un enlace de vinculación...")
        for path in ['/root/.config/playit_gg/playit.toml', '/etc/playit/playit.toml']:
            if os.path.exists(path):
                try:
                    os.remove(path)
                except Exception:
                    pass
    else:
        add_system_log("Iniciando túnel Playit.gg con clave secreta...")
        # Save playit config
        os.makedirs('/root/.config/playit_gg', exist_ok=True)
        os.makedirs('/etc/playit', exist_ok=True)
        playit_toml = f'secret_key = "{secret_key}"\n'
        try:
            with open('/root/.config/playit_gg/playit.toml', 'w') as f:
                f.write(playit_toml)
            with open('/etc/playit/playit.toml', 'w') as f:
                f.write(playit_toml)
        except Exception as e:
            add_system_log(f"No se pudieron crear archivos de configuración de playit (seguramente ejecutando en Windows de prueba): {str(e)}")
    
    playit_log = os.path.join(LOGS_DIR, 'playit.txt')
    
    # For Windows testing, use mock or local path if playit executable is not available
    cmd = 'playit'
    if sys.platform == 'win32':
        # On Windows, just create a mock process or try running playit.exe if in path
        cmd = 'playit.exe' if os.path.exists('playit.exe') else 'cmd.exe /c echo Tunnel Playit Mock'
    
    try:
        with open(playit_log, 'w') as log_f:
            tunnel_process = subprocess.Popen(
                [cmd, '--secret-path', '/root/.config/playit_gg/playit.toml'],
                stdout=log_f, stderr=log_f, text=True
            )
        add_system_log("Proceso del túnel Playit iniciado en segundo plano.")
    except Exception as e:
        add_system_log(f"Error al iniciar Playit: {str(e)}")

def start_ngrok_tunnel(config, server_type):
    add_system_log("Iniciando túnel Ngrok...")
    ngrok_config = config.get("ngrok_proxy", {})
    authtoken = ngrok_config.get("authtoken", "")
    region = ngrok_config.get("region", "us")
    
    if not authtoken:
        add_system_log("Error: Authtoken de Ngrok no configurado en los Ajustes de Red.")
        return
        
    try:
        # Install pyngrok if not present
        try:
            import pyngrok
        except ImportError:
            add_system_log("Instalando dependencia 'pyngrok'...")
            subprocess.run("pip install -q pyngrok", shell=True)
            
        from pyngrok import conf, ngrok
        ngrok.set_auth_token(authtoken)
        conf.get_default().region = region
        
        tunnel_port = 19132 if server_type == "bedrock" else 25565
        proto = "udp" if server_type == "bedrock" else "tcp"
        
        add_system_log(f"Conectando túnel Ngrok {proto} en puerto {tunnel_port} (región: {region})...")
        tunnel_url = ngrok.connect(tunnel_port, proto)
        public_ip = str(tunnel_url.public_url).replace("tcp://", "")
        add_system_log(f"¡Túnel Ngrok activo! Dirección para conectar: {public_ip}")
        
        # Save to file
        with open(os.path.join(LOGS_DIR, 'ngrok_ip.txt'), 'w') as f:
            f.write(public_ip)
    except Exception as e:
        add_system_log(f"Error iniciando túnel Ngrok: {str(e)}")

def start_zrok_tunnel(config, server_type):
    global tunnel_process, active_server
    add_system_log("Iniciando túnel Zrok...")
    zrok_config = config.get("zrok_proxy", {})
    authtoken = zrok_config.get("authtoken", "")
    if not authtoken:
        add_system_log("Error: Authtoken de Zrok no configurado en los Ajustes de Red.")
        return
        
    if sys.platform == 'win32':
        add_system_log("Entorno local Windows detectado. Saltando inicio de Zrok.")
        return
        
    try:
        # Check/install zrok
        zrok_dir = os.path.join(DRIVE_PATH, active_server, "tunnel", "zrok")
        zrok_bin = os.path.join(zrok_dir, "zrok")
        os.makedirs(zrok_dir, exist_ok=True)
        
        if not os.path.exists(zrok_bin):
            add_system_log("Descargando binario de Zrok...")
            download_url = None
            try:
                assets = requests.get("https://api.github.com/repos/openziti/zrok/releases/latest").json().get("assets", [])
                for asset in assets:
                    if "linux_amd64" in asset["browser_download_url"]:
                        download_url = asset["browser_download_url"]
                        break
            except Exception:
                pass
                
            if not download_url:
                download_url = "https://github.com/openziti/zrok/releases/download/v0.4.32/zrok_0.4.32_linux_amd64.tar.gz"
                
            tar_path = os.path.join(zrok_dir, "zrok.tar.gz")
            r = requests.get(download_url)
            with open(tar_path, 'wb') as f:
                f.write(r.content)
            subprocess.run(f"tar -xf {tar_path} -C {zrok_dir}", shell=True)
            subprocess.run(f"chmod +x {zrok_bin}", shell=True)
            
        # Enable zrok environment if needed
        status_result = subprocess.run([zrok_bin, "status"], capture_output=True, text=True)
        if "unable to load environment" in status_result.stderr or "unable to load environment" in status_result.stdout:
            add_system_log("Habilitando entorno Zrok con token...")
            subprocess.run(f"{zrok_bin} enable {authtoken} --headless -d colab@colab", shell=True)
            
        # Start share
        backend_mode = "udpTunnel" if server_type == "bedrock" else "tcpTunnel"
        port = "19132" if server_type == "bedrock" else "25565"
        
        zrok_log = os.path.join(LOGS_DIR, 'zrok.txt')
        with open(zrok_log, 'w') as log_f:
            tunnel_process = subprocess.Popen(
                [zrok_bin, "share", "private", "--backend-mode", backend_mode, f"127.0.0.1:{port}", "--headless"],
                stdout=log_f, stderr=log_f, text=True
            )
        add_system_log(f"Túnel Zrok ({backend_mode}) iniciado en segundo plano.")
    except Exception as e:
        add_system_log(f"Error iniciando túnel Zrok: {str(e)}")

def start_localtonet_tunnel(config):
    global tunnel_process, active_server
    add_system_log("Iniciando túnel LocalToNet...")
    localtonet_config = config.get("localtonet_proxy", {})
    authtoken = localtonet_config.get("authtoken", "")
    if not authtoken:
        add_system_log("Error: Authtoken de LocalToNet no configurado en los Ajustes de Red.")
        return
        
    if sys.platform == 'win32':
        add_system_log("Entorno local Windows detectado. Saltando inicio de LocalToNet.")
        return
        
    try:
        localtonet_dir = os.path.join(DRIVE_PATH, active_server, "tunnel", "localtonet")
        localtonet_bin = os.path.join(localtonet_dir, "localtonet")
        os.makedirs(localtonet_dir, exist_ok=True)
        
        if not os.path.exists(localtonet_bin):
            add_system_log("Descargando LocalToNet...")
            zip_path = os.path.join(localtonet_dir, "localtonet.zip")
            r = requests.get("https://localtonet.com/download/localtonet-linux-x64.zip")
            with open(zip_path, 'wb') as f:
                f.write(r.content)
            subprocess.run(f"unzip -o {zip_path} -d {localtonet_dir}", shell=True)
            subprocess.run(f"chmod +x {localtonet_bin}", shell=True)
            
        localtonet_log = os.path.join(LOGS_DIR, 'localtonet.txt')
        with open(localtonet_log, 'w') as log_f:
            tunnel_process = subprocess.Popen(
                [localtonet_bin, "authtoken", authtoken],
                stdout=log_f, stderr=log_f, text=True
            )
        add_system_log("Túnel LocalToNet iniciado en segundo plano. Recuerda iniciar la conexión TCP/UDP desde el panel de LocalToNet.")
    except Exception as e:
        add_system_log(f"Error iniciando túnel LocalToNet: {str(e)}")

def start_network_tunnel(config, server_type):
    active_server = config.get("server_in_use", "")
    tunnel_service = "playit"
    if active_server:
        colabconfig = load_colab_config(active_server)
        tunnel_service = colabconfig.get("tunnel_service", "playit")
        
    add_system_log(f"Iniciando túnel de red ({tunnel_service})...")
    if tunnel_service == "ngrok":
        start_ngrok_tunnel(config, server_type)
    elif tunnel_service == "zrok":
        start_zrok_tunnel(config, server_type)
    elif tunnel_service == "localtonet":
        start_localtonet_tunnel(config)
    else:
        # Default to playit
        start_playit_tunnel(config)


def stop_tunnels():
    global tunnel_process
    if tunnel_process:
        try:
            tunnel_process.terminate()
            tunnel_process.wait(timeout=3)
            add_system_log("Túnel de red finalizado correctamente.")
        except Exception:
            try:
                tunnel_process.kill()
            except:
                pass
        tunnel_process = None
        
    try:
        from pyngrok import ngrok
        ngrok.disconnect_all()
        ngrok.kill()
        add_system_log("Túneles de Ngrok desconectados y cerrados.")
    except Exception:
        pass
        
    # Delete temporary ngrok IP file
    ngrok_ip_file = os.path.join(LOGS_DIR, 'ngrok_ip.txt')
    if os.path.exists(ngrok_ip_file):
        try:
            os.remove(ngrok_ip_file)
        except Exception:
            pass
            
    # Force kill any playit/ngrok/zrok/localtonet instances
    if sys.platform != 'win32':
        os.system('pkill playit')
        os.system('pkill ngrok')
        os.system('pkill zrok')
        os.system('pkill localtonet')


def get_tunnel_ip():
    config = load_server_config()
    active_server = config.get("server_in_use", "")
    tunnel_service = "playit"
    if active_server:
        colabconfig = load_colab_config(active_server)
        tunnel_service = colabconfig.get("tunnel_service", "playit")
        
    if tunnel_service == "ngrok":
        ngrok_ip_file = os.path.join(LOGS_DIR, 'ngrok_ip.txt')
        if os.path.exists(ngrok_ip_file):
            try:
                with open(ngrok_ip_file, 'r') as f:
                    return f.read().strip()
            except Exception:
                pass
        return "ngrok (Ver logs/ngrok_ip.txt)"
    elif tunnel_service == "zrok":
        return "zrok (Ver logs/zrok.txt / Consola)"
    elif tunnel_service == "localtonet":
        return "localtonet.com (Ver su Panel)"
        
    playit_log = os.path.join(LOGS_DIR, 'playit.txt')
    if os.path.exists(playit_log):
        try:
            with open(playit_log, 'r') as f:
                content = f.read()
                
                # Check for claim link
                claim_match = re.search(r'https://playit\.gg/claim/[\w\-]+', content)
                if claim_match:
                    return f"VINCULAR:{claim_match.group(0)}"
                
                # Search for mapping, playit logs usually show "assigned address: xxxx.playit.gg"
                match = re.search(r'assigned address\s+([\w\-\.:]+)', content, re.IGNORECASE)
                if match:
                    return match.group(1)
                match = re.search(r'([\w\-\.]+:\d+)\s+<-->', content)
                if match:
                    return match.group(1)
        except Exception:
            pass
    return "playit.gg (Ver logs/playit.txt)"


# --- Minecraft Process Runner ---
def monitor_mc_output():
    global mc_process, server_status, active_server
    if not mc_process:
        return
    
    add_system_log("Hilo de monitoreo de consola iniciado.")
    
    unsupported_class_version_detected = False
    required_class_version = None
    
    while True:
        try:
            if not mc_process:
                break
            line = mc_process.stdout.readline()
            if not line:
                break
            
            # Print to python console for debugging
            print(line.strip())
            
            # Clean ANSI color codes
            ansi_escape = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
            clean_line = ansi_escape.sub('', line.strip())
            
            # Add to session_logs directly
            if clean_line:
                session_logs.append(clean_line)
                
            # Detect UnsupportedClassVersionError
            if "UnsupportedClassVersionError" in clean_line:
                unsupported_class_version_detected = True
                
            if unsupported_class_version_detected:
                match = re.search(r'class file version (\d+)\.', clean_line)
                if match:
                    required_class_version = int(match.group(1))
            
            # Simple status check
            if "Done (" in line or "Server started." in line:
                server_status = "online"
                add_system_log("¡El servidor de Minecraft está ONLINE!")
        except Exception:
            break
    
    # Process ended
    exit_code = mc_process.poll() if mc_process else 0
    
    # Self-healing logic for UnsupportedClassVersionError
    if unsupported_class_version_detected and required_class_version:
        java_map = {
            69: 25,
            68: 24,
            67: 23,
            66: 22,
            65: 21,
            61: 17,
            55: 11,
            52: 8
        }
        target_java = java_map.get(required_class_version)
        if not target_java:
            target_java = required_class_version - 44
            
        add_system_log(f"¡Se detectó un error de versión de Java! Se requiere Java {target_java} (class version {required_class_version}).")
        
        # Save custom Java version to colabconfig.txt so it persists across restarts
        try:
            colabconfig = load_colab_config(active_server)
            colabconfig["java"] = {
                "CustomEnabled": "True",
                "version": str(target_java),
                "build": "OpenJDK"
            }
            path = get_colab_config_path(active_server)
            with open(path, 'w') as f:
                json.dump(colabconfig, f, indent=4)
            add_system_log(f"Configuración de Java {target_java} guardada en colabconfig.txt para futuros arranques.")
        except Exception as e:
            add_system_log(f"No se pudo guardar la configuración de Java en colabconfig.txt: {str(e)}")
            
        def self_heal_helper():
            global server_status
            server_status = "updating"
            if install_java_by_number(target_java):
                add_system_log(f"Auto-corrección completada. Reiniciando el servidor de Minecraft con Java {target_java}...")
                start_mc_internal_run()
            else:
                add_system_log("No se pudo auto-corregir la versión de Java.")
                server_status = "offline"
                
        threading.Thread(target=self_heal_helper, daemon=True).start()
        return
        
    add_system_log(f"El servidor de Minecraft se detuvo con código de salida: {exit_code}")
    server_status = "offline"
    mc_process = None
    stop_tunnels()

def start_mc_internal_run():
    try:
        start_mc_process_internal()
    except Exception as e:
        add_system_log(f"Fallo al reiniciar el servidor en auto-corrección: {str(e)}")

# --- API Routes ---

@app.route('/')
def index():
    # Read dashboard.html from scratch directory
    dashboard_path = os.path.join(os.path.dirname(__file__), 'dashboard.html')
    if not os.path.exists(dashboard_path):
        # Fallback if executing from a different cwd
        dashboard_path = r'C:\Users\arnie\.gemini\antigravity-ide\brain\ccecd530-23c0-4479-a187-164a80a19c55\scratch\dashboard.html'
    
    if os.path.exists(dashboard_path):
        with open(dashboard_path, 'r', encoding='utf-8') as f:
            return render_template_string(f.read())
    return "Error: dashboard.html no encontrado."

@app.route('/api/status', methods=['GET'])
def get_status():
    global server_status, active_server
    
    # Load active server if not set
    config = load_server_config()
    active_server = config.get("server_in_use", "")
    
    # Query system stats
    cpu = psutil.cpu_percent()
    ram = psutil.virtual_memory()
    ram_used = round(ram.used / (1024**3), 1)
    ram_total = round(ram.total / (1024**3), 1)
    
    # Server queries (players count) using mcstatus if server is online
    players_online = 0
    players_max = 0
    if server_status == "online":
        # Check if local server responds
        try:
            from mcstatus import JavaServer
            server = JavaServer.lookup("127.0.0.1:25565")
            query = server.status()
            players_online = query.players.online
            players_max = query.players.max
        except Exception:
            # Fallback if mcstatus fails or bedrock port is used
            pass
            
    # Check if process is dead but status is still online/starting
    global mc_process
    if mc_process and mc_process.poll() is not None:
        server_status = "offline"
        mc_process = None
        stop_tunnels()

    # Get public tunnel URL if any
    tunnel_ip = "Esperando..."
    playit_claim_url = ""
    if server_status == "online":
        raw_ip = get_tunnel_ip()
        if raw_ip.startswith("VINCULAR:"):
            playit_claim_url = raw_ip.split(":", 1)[1]
            tunnel_ip = "Vincular Cuenta Playit"
        else:
            tunnel_ip = raw_ip
            
            # If server is established, verify if a generated playit key was claimed.
            # If so, save it to server_list.txt for future runs.
            secret_key = config.get("playit_proxy", {}).get("secretkey", "").strip()
            if not secret_key:
                toml_path = '/root/.config/playit_gg/playit.toml'
                if os.path.exists(toml_path):
                    try:
                        with open(toml_path, 'r') as f:
                            toml_content = f.read()
                        key_match = re.search(r'secret_key\s*=\s*["\']([\w\-]+)["\']', toml_content)
                        if key_match:
                            new_key = key_match.group(1).strip()
                            if new_key:
                                config["playit_proxy"]["secretkey"] = new_key
                                save_server_config(config)
                                add_system_log("¡Clave secreta de Playit.gg autoguardada en Drive tras vinculación exitosa!")
                    except Exception:
                        pass
        
    active_server_type = ""
    active_server_version = ""
    if active_server:
        try:
            colabconfig = load_colab_config(active_server)
            active_server_type    = colabconfig.get("server_type",    "")
            active_server_version = colabconfig.get("server_version", "")
        except:
            pass
        
    return jsonify({
        "status": server_status,
        "active_server": active_server,
        "active_server_type": active_server_type,
        "active_server_version": active_server_version,
        "cpu": cpu,
        "ram_used": ram_used,
        "ram_total": ram_total,
        "players_online": players_online,
        "players_max": players_max,
        "tunnel_ip": tunnel_ip,
        "playit_claim_url": playit_claim_url,
        "panel_url": request.host_url
    })

@app.route('/api/logs', methods=['GET'])
def get_logs():
    cursor = int(request.args.get('cursor', 0))
    
    # If the cursor is larger than the current log count, reset it (client page reloads or panel restarted)
    if cursor > len(session_logs):
        cursor = 0
        
    lines = session_logs[cursor:]
    return jsonify({
        "lines": lines,
        "cursor": cursor + len(lines)
    })

def start_mc_process_internal():
    global mc_process, server_status, active_server, log_thread, session_logs
    
    config = load_server_config()
    active_server = config.get("server_in_use", "")
    if not active_server:
        add_system_log("Error: No hay servidor seleccionado.")
        server_status = "offline"
        return False
        
    server_status = "starting"
    
    # 1. Free ports
    free_minecraft_ports()
    
    # 2. Get server specifications
    colabconfig = load_colab_config(active_server)
    server_type = colabconfig.get("server_type", "paper")
    version = colabconfig.get("server_version", "1.21.1")
    
    server_dir = os.path.join(DRIVE_PATH, active_server)
    
    # Accept eula.txt automatically
    eula_path = os.path.join(server_dir, 'eula.txt')
    try:
        with open(eula_path, 'w') as f:
            f.write('eula=true')
    except Exception:
        pass

    # Java jar selection
    jar_name = 'server.jar'
    if server_type == 'forge':
        # Search jar
        files = os.listdir(server_dir)
        for f in files:
            if f.startswith("forge") and f.endswith(".jar") and 'installer' not in f:
                jar_name = f
                break
    elif server_type == 'bedrock':
        jar_name = 'bedrock_server'
    
    # Setup tunnel in background
    start_network_tunnel(config, server_type)
    
    # Determine the java binary to execute (use absolute path of the selected Java version if possible)
    java_bin = "java"
    required_ver = 17
    if sys.platform != 'win32':
        required_ver = determine_required_java_version(version, server_type)
        try:
            java_config = colabconfig.get("java", {})
            cust_enabled = str(java_config.get("CustomEnabled", "False")).lower() == "true"
            if cust_enabled:
                cust_ver_str = java_config.get("version", java_config.get("version:", ""))
                cust_ver_match = re.search(r'\d+', str(cust_ver_str))
                if cust_ver_match:
                    required_ver = int(cust_ver_match.group(0))
        except Exception:
            pass
            
        candidate_bin = None
        jvm_dir = "/usr/lib/jvm"
        if os.path.exists(jvm_dir):
            for folder in os.listdir(jvm_dir):
                if folder.startswith(f"java-{required_ver}-openjdk") and os.path.exists(os.path.join(jvm_dir, folder, "bin", "java")):
                    candidate_bin = os.path.join(jvm_dir, folder, "bin", "java")
                    break
        if not candidate_bin:
            candidate_bin = f"/usr/lib/jvm/java-{required_ver}-openjdk-amd64/bin/java"
            
        if os.path.exists(candidate_bin):
            java_bin = candidate_bin
            add_system_log(f"Usando ruta absoluta de Java: {java_bin}")
    
    # 3. Start subprocess
    cmd = ""
    run_sh_path = os.path.join(server_dir, 'run.sh')
    if os.path.exists(run_sh_path) and server_type != 'arclight' and server_type != 'bedrock':
        try:
            with open(run_sh_path, 'r', encoding='utf-8', errors='ignore') as f:
                run_content = f.read()
            if 'java' in run_content:
                # Find the line that executes java
                exec_line = ""
                for line in run_content.splitlines():
                    line_s = line.strip()
                    if line_s and not line_s.startswith('#') and 'java' in line_s:
                        exec_line = line_s
                        break
                if exec_line:
                    match = re.match(r'^("?[^"\s]*java"?)', exec_line)
                    if match:
                        java_cmd = match.group(1)
                        cmd_extracted = exec_line.replace(java_cmd, java_bin, 1)
                    else:
                        java_idx = exec_line.find('java')
                        cmd_extracted = exec_line[java_idx:].strip()
                        cmd_extracted = cmd_extracted.replace('java', java_bin, 1)
                    
                    jvm_args = " -Xms8G -Xmx10G -XX:ConcGCThreads=2 -XX:ParallelGCThreads=4"
                    if server_type in ["paper", "purpur", "arclight"]:
                        jvm_args += ' -XX:+UseG1GC -XX:+ParallelRefProcEnabled -XX:MaxGCPauseMillis=200 -XX:+UnlockExperimentalVMOptions -XX:+DisableExplicitGC -XX:+AlwaysPreTouch -XX:G1NewSizePercent=30 -XX:G1MaxNewSizePercent=40 -XX:G1HeapRegionSize=8M -XX:G1ReservePercent=20 -XX:G1HeapWastePercent=5 -XX:G1MixedGCCountTarget=4 -XX:InitiatingHeapOccupancyPercent=15 -XX:G1MixedGCLiveThresholdPercent=90 -XX:G1RSetUpdatingPauseTimePercent=5 -XX:SurvivorRatio=32 -XX:+PerfDisableSharedMem -XX:MaxTenuringThreshold=1 -XX:ConcGCThreads=2 -XX:ParallelGCThreads=4 -Dusing.aikars.flags=https://mcflags.emc.gs -Daikars.new.flags=true'
                    elif server_type == "velocity":
                        jvm_args += ' -XX:+UseG1GC -XX:G1HeapRegionSize=4M -XX:+UnlockExperimentalVMOptions -XX:+ParallelRefProcEnabled -XX:+AlwaysPreTouch -XX:MaxInlineLevel=15'
                    
                    cmd = cmd_extracted.replace('@user_jvm_args.txt', jvm_args).replace('"$@"', 'nogui "$@"')
                    if 'nogui' not in cmd:
                        cmd += ' nogui'
                    cmd = " ".join(cmd.split())
                    add_system_log("Se detectó run.sh para iniciar el servidor.")
        except Exception as e:
            add_system_log(f"No se pudo procesar run.sh: {str(e)}")

    if not cmd:
        if server_type == "bedrock":
            if sys.platform != 'win32':
                os.system(f'chmod +x "{server_dir}/bedrock_server"')
                cmd = f"./{jar_name}"
            else:
                cmd = f"{jar_name}.exe" if os.path.exists(os.path.join(server_dir, f"{jar_name}.exe")) else "cmd.exe /c echo Bedrock Mock Server Started && pause"
        else:
            jvm_args = " -Xms8G -Xmx10G -XX:ConcGCThreads=2 -XX:ParallelGCThreads=4"
            if required_ver >= 9:
                jvm_args = " -Xlog:os+container=off" + jvm_args
                
            if server_type in ["paper", "purpur", "arclight"]:
                jvm_args += ' -XX:+UseG1GC -XX:+ParallelRefProcEnabled -XX:MaxGCPauseMillis=200 -XX:+UnlockExperimentalVMOptions -XX:+DisableExplicitGC -XX:+AlwaysPreTouch -XX:G1NewSizePercent=30 -XX:G1MaxNewSizePercent=40 -XX:G1HeapRegionSize=8M -XX:G1ReservePercent=20 -XX:G1HeapWastePercent=5 -XX:G1MixedGCCountTarget=4 -XX:InitiatingHeapOccupancyPercent=15 -XX:G1MixedGCLiveThresholdPercent=90 -XX:G1RSetUpdatingPauseTimePercent=5 -XX:SurvivorRatio=32 -XX:+PerfDisableSharedMem -XX:MaxTenuringThreshold=1 -XX:ConcGCThreads=2 -XX:ParallelGCThreads=4 -Dusing.aikars.flags=https://mcflags.emc.gs -Daikars.new.flags=true'
            elif server_type == "velocity":
                jvm_args += ' -XX:+UseG1GC -XX:G1HeapRegionSize=4M -XX:+UnlockExperimentalVMOptions -XX:+ParallelRefProcEnabled -XX:+AlwaysPreTouch -XX:MaxInlineLevel=15'
            
            cmd = f"{java_bin} -server {jvm_args} -jar {jar_name} nogui"

    add_system_log(f"Comando de ejecución: {cmd}")
    
    try:
        mc_process = subprocess.Popen(
            cmd,
            shell=True,
            cwd=server_dir,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1
        )
        
        log_thread = threading.Thread(target=monitor_mc_output, daemon=True)
        log_thread.start()
        return True
    except Exception as e:
        server_status = "offline"
        add_system_log(f"Error crítico al arrancar Minecraft: {str(e)}")
        stop_tunnels()
        return False

@app.route('/api/start', methods=['POST'])
def start_mc():
    global mc_process, server_status, active_server, log_thread, session_logs
    if mc_process and mc_process.poll() is None:
        return jsonify({"status": "error", "message": "El servidor ya está en ejecución."})
        
    config = load_server_config()
    active_server = config.get("server_in_use", "")
    if not active_server:
        return jsonify({"status": "error", "message": "No hay ningún servidor seleccionado."})
        
    colabconfig = load_colab_config(active_server)
    server_type = colabconfig.get("server_type", "paper")
    version = colabconfig.get("server_version", "1.21.1")
    
    # Reset logs for the active launch session
    session_logs = []
    add_system_log(f"Iniciando el servidor de Minecraft '{active_server}'...")
    
    # 1. Verify/Install Java required version before launch
    try:
        install_java_if_needed(version, server_type)
    except Exception as e:
        add_system_log(f"Advertencia durante verificación de Java: {str(e)}")
        
    success = start_mc_process_internal()
    if success:
        return jsonify({"status": "ok"})
    else:
        return jsonify({"status": "error", "message": "Fallo al ejecutar el servidor."})

@app.route('/api/stop', methods=['POST'])
def stop_mc():
    global mc_process, server_status
    if not mc_process or mc_process.poll() is not None:
        return jsonify({"status": "error", "message": "El servidor ya está apagado."})
        
    server_status = "stopping"
    add_system_log("Enviando comando de parada /stop al servidor de Minecraft...")
    
    try:
        # Send /stop command
        mc_process.stdin.write("stop\n")
        mc_process.stdin.flush()
        
        # Start helper thread to force kill if it hangs
        def force_kill_helper():
            global mc_process, server_status
            time.sleep(20)
            if mc_process and mc_process.poll() is None:
                add_system_log("El servidor tardó demasiado en cerrarse. Forzando detención (kill)...")
                try:
                    mc_process.kill()
                except:
                    pass
                mc_process = None
                server_status = "offline"
                stop_tunnels()
                
        threading.Thread(target=force_kill_helper, daemon=True).start()
        return jsonify({"status": "ok"})
    except Exception as e:
        add_system_log(f"Error enviando comando de parada: {str(e)}")
        # Force terminate
        try:
            mc_process.terminate()
        except:
            pass
        mc_process = None
        server_status = "offline"
        stop_tunnels()
        return jsonify({"status": "ok", "message": "Forzado cierre por error."})

@app.route('/api/command', methods=['POST'])
def send_command():
    global mc_process
    if not mc_process or mc_process.poll() is not None:
        return jsonify({"status": "error", "message": "El servidor no está encendido."})
        
    data = request.json
    command = data.get("command", "").strip()
    if not command:
        return jsonify({"status": "error", "message": "Comando vacío."})
        
    # Remove leading slash if any (Minecraft console doesn't strictly need slash, but handles it)
    if command.startswith("/"):
        command = command[1:]
        
    try:
        add_system_log(f"Enviando comando a consola: {command}")
        mc_process.stdin.write(f"{command}\n")
        mc_process.stdin.flush()
        return jsonify({"status": "ok"})
    except Exception as e:
        return jsonify({"status": "error", "message": f"Error al escribir en consola: {str(e)}"})

@app.route('/api/properties', methods=['GET', 'POST'])
def handle_properties():
    config = load_server_config()
    server_name = config.get("server_in_use", "")
    if not server_name:
        return jsonify({"status": "error", "message": "No hay servidor seleccionado."})
        
    path = get_server_properties_path(server_name)
    
    if request.method == 'GET':
        if not os.path.exists(path):
            return jsonify({})
            
        properties = {}
        try:
            with open(path, 'r', encoding='utf-8', errors='ignore') as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith('#') and '=' in line:
                        parts = line.split('=', 1)
                        properties[parts[0].strip()] = parts[1].strip()
            return jsonify(properties)
        except Exception as e:
            return jsonify({"status": "error", "message": f"Error leyendo propiedades: {str(e)}"})
            
    # POST - Save properties
    else:
        new_props = request.json
        if not os.path.exists(path):
            # Create file
            with open(path, 'w') as f:
                f.write("# Minecraft server properties\n")
                
        try:
            # Read existing lines
            lines = []
            existing_keys = set()
            with open(path, 'r', encoding='utf-8', errors='ignore') as f:
                for line in f:
                    if line.strip() and not line.strip().startswith('#') and '=' in line:
                        key = line.split('=', 1)[0].strip()
                        if key in new_props:
                            lines.append(f"{key}={new_props[key]}\n")
                            existing_keys.add(key)
                            continue
                    lines.append(line)
            
            # Add missing keys
            with open(path, 'w', encoding='utf-8') as f:
                for line in lines:
                    f.write(line)
                for key, val in new_props.items():
                    if key not in existing_keys:
                        f.write(f"{key}={val}\n")
            
            add_system_log("Propiedades de server.properties actualizadas con éxito.")
            return jsonify({"status": "ok"})
        except Exception as e:
            return jsonify({"status": "error", "message": f"Error guardando propiedades: {str(e)}"})

@app.route('/api/servers', methods=['GET'])
def get_servers():
    config = load_server_config()
    server_list = config.get("server_list", [])
    active = config.get("server_in_use", "")
    
    # Scan filesystem directories to make sure list is accurate
    scanned_servers = []
    if os.path.exists(DRIVE_PATH):
        for entry in os.listdir(DRIVE_PATH):
            full_path = os.path.join(DRIVE_PATH, entry)
            if os.path.isdir(full_path) and entry != 'logs' and not entry.startswith('.'):
                scanned_servers.append(entry)
                
    # Merge scanned into config server list if missing
    updated = False
    for s in scanned_servers:
        if s not in server_list:
            server_list.append(s)
            updated = True
            
    if updated:
        config["server_list"] = server_list
        save_server_config(config)
        
    return jsonify({
        "servers": server_list,
        "active": active
    })

@app.route('/api/network-config', methods=['GET', 'POST'])
def handle_network_config():
    config = load_server_config()
    
    if request.method == 'GET':
        active_server = config.get("server_in_use", "")
        tunnel_service = "playit"
        if active_server:
            colabconfig = load_colab_config(active_server)
            tunnel_service = colabconfig.get("tunnel_service", "playit")
            
        return jsonify({
            "tunnel_service": tunnel_service,
            "playit_secret": config.get("playit_proxy", {}).get("secretkey", ""),
            "ngrok_token": config.get("ngrok_proxy", {}).get("authtoken", ""),
            "ngrok_region": config.get("ngrok_proxy", {}).get("region", "us"),
            "zrok_token": config.get("zrok_proxy", {}).get("authtoken", ""),
            "localtonet_token": config.get("localtonet_proxy", {}).get("authtoken", "")
        })
        
    else:
        # POST - Save network settings
        data = request.json
        
        if "playit_proxy" not in config: config["playit_proxy"] = {}
        if "ngrok_proxy" not in config: config["ngrok_proxy"] = {}
        if "zrok_proxy" not in config: config["zrok_proxy"] = {}
        if "localtonet_proxy" not in config: config["localtonet_proxy"] = {}
        
        config["playit_proxy"]["secretkey"] = data.get("playit_secret", "").strip()
        config["ngrok_proxy"]["authtoken"] = data.get("ngrok_token", "").strip()
        config["ngrok_proxy"]["region"] = data.get("ngrok_region", "us").strip()
        config["zrok_proxy"]["authtoken"] = data.get("zrok_token", "").strip()
        config["localtonet_proxy"]["authtoken"] = data.get("localtonet_token", "").strip()
        save_server_config(config)
        
        # Save tunnel selection in colabconfig.txt of the active server
        active_server = config.get("server_in_use", "")
        if active_server:
            try:
                colabconfig = load_colab_config(active_server)
                colabconfig["tunnel_service"] = data.get("tunnel_service", "playit")
                path = get_colab_config_path(active_server)
                with open(path, 'w') as f:
                    json.dump(colabconfig, f, indent=4)
            except Exception as e:
                return jsonify({"status": "error", "message": f"Error al guardar colabconfig.txt: {str(e)}"})
                
        add_system_log("Configuración de red y túneles guardada exitosamente.")
        return jsonify({"status": "ok"})

def SERVERSJAR(command, server_type=None, version=None):
    # Get the download URL (jar) AND return the detailed versions for each software (all)
    if command == "GetVersions":
        if server_type is None:
            return []
        Server_Jars_All = {
            'paper': 'https://api.papermc.io/v2/projects/paper',
            'velocity': 'https://api.papermc.io/v2/projects/velocity',
            'purpur': 'https://api.purpurmc.org/v2/purpur',
            'mohist': 'https://api.mohistmc.com/project/mohist/versions',
            'banner': 'https://api.mohistmc.com/project/banner/versions',
            'folia': 'https://api.papermc.io/v2/projects/folia'
        }
        try:
            server_type = server_type.lower()
            if server_type in ['vanilla', 'snapshot']:
                rJSON = requests.get('https://launchermeta.mojang.com/mc/game/version_manifest.json').json()
                t = 'release' if server_type == 'vanilla' else 'snapshot'
                server_version = [hit["id"] for hit in rJSON["versions"] if hit["type"] == t]
                return server_version
            elif server_type in ['paper','velocity','purpur','folia']:
                rJSON = requests.get(Server_Jars_All[server_type]).json()
                server_version = [hit for hit in rJSON["versions"]]
                server_version.reverse()
                return server_version
            elif server_type in ['mohist', 'banner']:
                rJSON = requests.get(Server_Jars_All[server_type]).json()
                server_version = [v["name"] for v in rJSON]
                server_version.reverse()
                return server_version
            elif server_type == 'fabric':
                rJSON = requests.get('https://meta.fabricmc.net/v2/versions/game').json()
                server_version = [hit['version'] for hit in rJSON if hit.get('stable') == True]
                return server_version
            elif server_type == "neoforge":
                rJSON = requests.get("https://maven.neoforged.net/api/maven/versions/releases/net/neoforged/neoforge").json()
                server_version = [hit for hit in rJSON["versions"]]
                server_version.reverse()
                return server_version
            elif server_type == 'forge':
                rJSON = requests.get('https://files.minecraftforge.net/net/minecraftforge/forge/index.html')
                soup = BeautifulSoup(rJSON.content, "html.parser")
                server_version = [tag.text.strip() for tag in soup.find_all('a') if '.' in tag.text and '\n' not in tag.text]
                valid_versions = []
                for v in server_version:
                    if re.match(r'^\d+\.\d+(\.\d+)?$', v) or '-' in v:
                        valid_versions.append(v)
                seen = set()
                uniq_versions = []
                for v in valid_versions:
                    if v not in seen:
                        seen.add(v)
                        uniq_versions.append(v)
                return uniq_versions
            elif server_type == "bedrock":
                DOWNLOAD_LINKS_URL = "https://net-secondary.web.minecraft-services.net/api/v1.0/download/links"
                BACKUP_URL = "https://raw.githubusercontent.com/ghwns9652/Minecraft-Bedrock-Server-Updater/main/backup_download_link.txt"
                HEADERS = {
                    "User-Agent": "Mozilla/5.0 (X11; CrOS x86_64 12871.102.0) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/81.0.4044.141 Safari/537.36"
                }
                try:
                    response = requests.get(DOWNLOAD_LINKS_URL, headers=HEADERS, timeout=5)
                    response.raise_for_status()
                    all_links = response.json()['result']['links']
                    download_link = next(
                        (link['downloadUrl'] for link in all_links if link['downloadType'] == 'serverBedrockLinux'),
                        None
                    )
                except Exception:
                    try:
                        response = requests.get(BACKUP_URL, headers=HEADERS, timeout=5)
                        response.raise_for_status()
                        download_link = response.text.strip()
                    except Exception:
                        download_link = None
                if download_link:
                    try:
                        ver = download_link.split('bedrock-server-')[1].split(".zip")[0]
                        return [ver]
                    except Exception:
                        return ["latest"]
                return ["latest"]
            elif server_type == "arclight":
                rJSON = requests.get('https://files.hypoglycemia.icu/v1/files/arclight/minecraft').json()['files']
                return [hit['name'] for hit in rJSON]
            elif server_type == "crucible":
                return ["1.7.10"]
            elif server_type == "magma":
                return ["1.12.2", "1.18.2", "1.19.3", "1.20.1"]
            elif server_type == "ketting":
                return ["1.20"]
            elif server_type == "cardboard":
                return ["1.16.5", "1.17.1"]
        except Exception as e:
            print(f"Error getting versions: {str(e)}")
        return []

    elif command == "GetDownloadUrl":
        if not version or not server_type:
            return None
        server_type = server_type.lower()
        try:
            if server_type in ['vanilla', 'snapshot']:
                rJSON = requests.get('https://launchermeta.mojang.com/mc/game/version_manifest.json').json()
                t = 'release' if server_type == 'vanilla' else 'snapshot'
                for hit in rJSON["versions"]:
                    if hit["type"] == t and hit['id'] == version:
                        return requests.get(hit['url']).json()["downloads"]['server']['url']
            elif server_type in ['paper','velocity','folia']:
                build = requests.get(f'https://api.papermc.io/v2/projects/{server_type}/versions/{version}').json()["builds"][-1]
                jar_name = requests.get(f'https://api.papermc.io/v2/projects/{server_type}/versions/{version}/builds/{build}').json()["downloads"]["application"]["name"]
                return f'https://api.papermc.io/v2/projects/{server_type}/versions/{version}/builds/{build}/downloads/{jar_name}'
            elif server_type == 'purpur':
                build = requests.get(f'https://api.purpurmc.org/v2/purpur/{version}').json()["builds"]["latest"]
                return f'https://api.purpurmc.org/v2/purpur/{version}/{build}/download'
            elif server_type in ['mohist', 'banner']:
                builds_resp = requests.get(f'https://api.mohistmc.com/project/{server_type}/{version}/builds').json()
                if builds_resp:
                    last_build_id = builds_resp[-1]["id"]
                    return f'https://api.mohistmc.com/project/{server_type}/{version}/builds/{last_build_id}/download'
            elif server_type == 'fabric':
                installerVersion = requests.get('https://meta.fabricmc.net/v2/versions/installer').json()[0]["version"]
                fabricVersion = requests.get(f'https://meta.fabricmc.net/v2/versions/loader/{version}').json()[0]["loader"]["version"]
                return "https://meta.fabricmc.net/v2/versions/loader/" + version + "/" + fabricVersion + "/" + installerVersion + "/server/jar"
            elif server_type == 'forge':
                rJSON = requests.get(f'https://files.minecraftforge.net/net/minecraftforge/forge/index_{version}.html')
                soup = BeautifulSoup(rJSON.content, "html.parser")
                tag = soup.find('a', title="Installer")
                if tag:
                    href = tag.get('href', '')
                    if 'url=' in href:
                        return href.split('url=', 1)[1]
                    return href
            elif server_type == "neoforge":
                return f"https://maven.neoforged.net/releases/net/neoforged/neoforge/{version}/neoforge-{version}-installer.jar"
            elif server_type == "bedrock":
                DOWNLOAD_LINKS_URL = "https://net-secondary.web.minecraft-services.net/api/v1.0/download/links"
                BACKUP_URL = "https://raw.githubusercontent.com/ghwns9652/Minecraft-Bedrock-Server-Updater/main/backup_download_link.txt"
                HEADERS = {
                    "User-Agent": "Mozilla/5.0 (X11; CrOS x86_64 12871.102.0) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/81.0.4044.141 Safari/537.36"
                }
                try:
                    response = requests.get(DOWNLOAD_LINKS_URL, headers=HEADERS, timeout=5)
                    response.raise_for_status()
                    all_links = response.json()['result']['links']
                    download_link = next(
                        (link['downloadUrl'] for link in all_links if link['downloadType'] == 'serverBedrockLinux'),
                        None
                    )
                except Exception:
                    try:
                        response = requests.get(BACKUP_URL, headers=HEADERS, timeout=5)
                        response.raise_for_status()
                        download_link = response.text.strip()
                    except Exception:
                        download_link = None
                return download_link
            elif server_type == "arclight":
                return f"https://files.hypoglycemia.icu/v1/files/arclight/minecraft/{version}/loaders/latest/download"
            elif server_type == "crucible":
                return "https://github.com/CrucibleMC/Crucible/releases/download/1.7.10-5.4/Crucible-1.7.10-5.4.jar"
            elif server_type == "magma":
                return f"https://releases.magmamc.io/api/v1/magma/{version}/latest/download"
            elif server_type == "ketting":
                return "https://github.com/KettingMC/Ketting-Launcher/releases/download/v1.5.1/kettinglauncher-1.5.1-sources.jar"
        except Exception as e:
            print(f"Error getting download URL: {str(e)}")
        return None

creation_in_progress = False

def create_server_thread_func(server_name, server_type, version):
    global creation_in_progress, session_logs, active_server
    creation_in_progress = True
    
    add_system_log(f"Iniciando descarga e instalación del servidor '{server_name}' ({server_type} - {version})...")
    
    server_dir = os.path.join(DRIVE_PATH, server_name)
    os.makedirs(server_dir, exist_ok=True)
    os.makedirs(os.path.join(server_dir, 'tunnel'), exist_ok=True)
    
    # Save colabconfig
    colabconfig = {
        "server_type": server_type,
        "server_version": version.split("-")[0].strip(),
        "tunnel_service": "playit"
    }
    with open(get_colab_config_path(server_name), 'w') as f:
        json.dump(colabconfig, f, indent=4)
        
    # Download EULA
    eula_path = os.path.join(server_dir, 'eula.txt')
    with open(eula_path, 'w') as f:
        f.write('eula=true')
        
    # Get download URL
    url = SERVERSJAR("GetDownloadUrl", server_type, version)
    if not url:
        add_system_log(f"Error: No se pudo obtener la URL de descarga para {server_type} {version}.")
        creation_in_progress = False
        return
        
    # Determine jar name
    jar_name = "server.jar"
    if server_type == "forge":
        jar_name = "forge-installer.jar"
    elif server_type == "neoforge":
        jar_name = "neoforge-installer.jar"
    elif server_type == "bedrock":
        jar_name = "bedrock-server.zip"
        
    add_system_log(f"Descargando archivo desde: {url}...")
    try:
        r = requests.get(url, stream=True)
        r.raise_for_status()
        total_length = r.headers.get('content-length')
        download_path = os.path.join(server_dir, jar_name)
        
        with open(download_path, 'wb') as f:
            if total_length is None:
                f.write(r.content)
            else:
                dl = 0
                total_length = int(total_length)
                last_percent = -1
                for chunk in r.iter_content(chunk_size=1024*1024):
                    if chunk:
                        f.write(chunk)
                        dl += len(chunk)
                        percent = int(100 * dl / total_length)
                        if percent % 10 == 0 and percent != last_percent:
                            add_system_log(f"Descargando: {percent}% completado ({round(dl / (1024*1024), 1)} MB / {round(total_length / (1024*1024), 1)} MB)...")
                            last_percent = percent
                            
        add_system_log("Descarga completada con éxito.")
        
        # Bedrock Unzip
        if server_type == "bedrock":
            add_system_log("Descomprimiendo archivos de Bedrock...")
            with zipfile.ZipFile(download_path, 'r') as zip_ref:
                zip_ref.extractall(server_dir)
            try:
                os.remove(download_path)
            except:
                pass
            add_system_log("Bedrock configurado exitosamente.")
            
        # Forge Installer Run
        elif server_type in ["forge", "neoforge"]:
            add_system_log(f"Ejecutando instalador de {server_type}... Esto puede tardar varios minutos.")
            proc_cmd = ["java", "-jar", jar_name, "--installServer"]
            inst_proc = subprocess.Popen(
                proc_cmd,
                cwd=server_dir,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True
            )
            while inst_proc.poll() is None:
                line = inst_proc.stdout.readline()
                if line:
                    clean_line = line.strip()
                    if clean_line:
                        if "Progress" in clean_line or "Downloading" in clean_line or "extracting" in clean_line:
                            print(clean_line)
                        else:
                            add_system_log(f"[INSTALADOR] {clean_line}")
            exit_code = inst_proc.poll()
            add_system_log(f"Proceso del instalador finalizado con código: {exit_code}")
            try:
                os.remove(download_path)
            except:
                pass
                
        # Register server globally
        config = load_server_config()
        if server_name not in config["server_list"]:
            config["server_list"].append(server_name)
        config["server_in_use"] = server_name
        save_server_config(config)
        active_server = server_name
        
        add_system_log(f"¡Servidor '{server_name}' creado e instalado con éxito! Ya puedes iniciar el servidor.")
    except Exception as e:
        add_system_log(f"Error durante la creación del servidor: {str(e)}")
        
    creation_in_progress = False

@app.route('/api/server-types', methods=['GET'])
def get_server_types():
    types = ['Vanilla', 'Snapshot', 'Paper', 'Purpur', 'Mohist', 'Arclight', 'Velocity', 'Banner', 'Fabric', 'Folia', 'Forge', 'Neoforge', 'Bedrock', 'Crucible', 'Magma', 'Ketting', 'Cardboard', 'Custom']
    return jsonify(types)

@app.route('/api/versions', methods=['GET'])
def get_versions():
    server_type = request.args.get('server_type', '').strip()
    if not server_type:
        return jsonify([])
    versions = SERVERSJAR("GetVersions", server_type=server_type)
    return jsonify(versions)

@app.route('/api/create-server', methods=['POST'])
def create_server_endpoint():
    global creation_in_progress
    if creation_in_progress:
        return jsonify({"status": "error", "message": "Ya hay una creación o instalación de servidor en curso."})
        
    data = request.json
    server_name = data.get("server_name", "").strip().replace(" ", "_")
    server_type = data.get("server_type", "").strip().lower()
    server_version = data.get("server_version", "").strip()
    
    if not server_name or not server_type or not server_version:
        return jsonify({"status": "error", "message": "Faltan parámetros requeridos (nombre, tipo o versión)."})
        
    # Check special chars
    if not re.match(r'^[\w\-_]+$', server_name):
        return jsonify({"status": "error", "message": "El nombre del servidor no puede contener caracteres especiales."})
        
    # Check if already exists
    server_dir = os.path.join(DRIVE_PATH, server_name)
    if os.path.exists(server_dir) and os.listdir(server_dir):
        return jsonify({"status": "error", "message": f"El servidor '{server_name}' ya existe y no está vacío."})
        
    # Start thread
    threading.Thread(
        target=create_server_thread_func,
        args=(server_name, server_type, server_version),
        daemon=True
    ).start()
    
    return jsonify({"status": "ok", "message": "Instalación del servidor iniciada en segundo plano. Observa la consola."})

@app.route('/api/delete-server', methods=['POST'])
def delete_server_endpoint():
    global mc_process
    if mc_process and mc_process.poll() is None:
        return jsonify({"status": "error", "message": "No se puede eliminar un servidor mientras esté encendido."})
        
    data = request.json
    server_name = data.get("server_name", "").strip()
    if not server_name:
        return jsonify({"status": "error", "message": "Nombre de servidor inválido."})
        
    server_dir = os.path.join(DRIVE_PATH, server_name)
    if not os.path.exists(server_dir):
        return jsonify({"status": "error", "message": "El servidor no existe."})
        
    add_system_log(f"Eliminando el servidor '{server_name}' de forma permanente...")
    
    try:
        shutil.rmtree(server_dir)
        # Update server config
        config = load_server_config()
        if server_name in config["server_list"]:
            config["server_list"].remove(server_name)
        if config["server_in_use"] == server_name:
            config["server_in_use"] = config["server_list"][0] if config["server_list"] else ""
        save_server_config(config)
        
        add_system_log(f"Servidor '{server_name}' eliminado de Drive con éxito.")
        return jsonify({"status": "ok"})
    except Exception as e:
        return jsonify({"status": "error", "message": f"Error al eliminar: {str(e)}"})

@app.route('/api/timezone', methods=['POST'])
def change_timezone():
    data = request.json
    area = data.get("area", "").strip()
    zone = data.get("zone", "").strip()
    if not area or not zone:
        return jsonify({"status": "error", "message": "Área y zona horaria requeridos."})
        
    if sys.platform == 'win32':
        return jsonify({"status": "ok", "new_time": "Thu Jun 25 18:52:10 UTC 2026"})
        
    try:
        subprocess.run("sudo rm -f /etc/localtime", shell=True)
        subprocess.run(f"sudo ln -s /usr/share/zoneinfo/{area}/{zone} /etc/localtime", shell=True)
        
        date_res = subprocess.run("date", capture_output=True, text=True)
        new_time = date_res.stdout.strip()
        
        add_system_log(f"Zona horaria de la VM cambiada a {area}/{zone}. Nueva fecha: {new_time}")
        return jsonify({"status": "ok", "new_time": new_time})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)})

@app.route('/api/backup-world', methods=['POST'])
def backup_world():
    config = load_server_config()
    server_name = config.get("server_in_use", "")
    if not server_name:
        return jsonify({"status": "error", "message": "No hay servidor seleccionado."})
        
    server_path = os.path.join(DRIVE_PATH, server_name)
    backup_world_dir = os.path.join(DRIVE_PATH, "backup", "world")
    os.makedirs(backup_world_dir, exist_ok=True)
    
    available_worlds = []
    for w in ["world", "world_nether", "world_the_end"]:
        if os.path.exists(os.path.join(server_path, w)):
            available_worlds.append(w)
            
    if not available_worlds:
        return jsonify({"status": "error", "message": "No se encontraron mundos ('world') en este servidor."})
        
    timestamp = time.strftime("%Y-%m-%dT%H%M%S")
    backup_name = f"{server_name}_worlds_{timestamp}"
    backup_path = os.path.join(backup_world_dir, backup_name)
    
    try:
        os.makedirs(backup_path, exist_ok=True)
        for w in available_worlds:
            add_system_log(f"Copiando mundo '{w}' al backup...")
            shutil.copytree(os.path.join(server_path, w), os.path.join(backup_path, w))
            
        add_system_log(f"Backup de mundos completado: backup/world/{backup_name}")
        return jsonify({"status": "ok", "backup_path": f"backup/world/{backup_name}"})
    except Exception as e:
        return jsonify({"status": "error", "message": f"Error al respaldar mundos: {str(e)}"})

@app.route('/api/backup-server', methods=['POST'])
def backup_server():
    config = load_server_config()
    server_name = config.get("server_in_use", "")
    if not server_name:
        return jsonify({"status": "error", "message": "No hay servidor seleccionado."})
        
    server_path = os.path.join(DRIVE_PATH, server_name)
    backup_dir = os.path.join(DRIVE_PATH, "backup")
    os.makedirs(backup_dir, exist_ok=True)
    
    timestamp = time.strftime("%Y-%m-%dT%H%M%S")
    backup_name = f"{server_name}-{timestamp}"
    backup_zip_path = os.path.join(backup_dir, backup_name)
    
    try:
        add_system_log(f"Creando archivo ZIP de todo el servidor '{server_name}'...")
        shutil.make_archive(
            base_name=backup_zip_path,
            format='zip',
            root_dir=server_path,
            base_dir='.'
        )
        add_system_log(f"Copia de seguridad del servidor guardada en: backup/{backup_name}.zip")
        return jsonify({"status": "ok", "backup_path": f"backup/{backup_name}.zip"})
    except Exception as e:
        return jsonify({"status": "error", "message": f"Error al zipear el servidor: {str(e)}"})

@app.route('/api/emergency-cleanup', methods=['POST'])
def emergency_cleanup():
    global mc_process
    add_system_log("Iniciando Limpieza de Emergencia...")
    free_minecraft_ports()
    
    config = load_server_config()
    server_name = config.get("server_in_use", "")
    cleaned_lock = False
    
    if server_name:
        lock_file = os.path.join(DRIVE_PATH, server_name, 'world', 'session.lock')
        if os.path.exists(lock_file):
            try:
                os.remove(lock_file)
                cleaned_lock = True
                add_system_log(f"Archivo lock eliminado: {lock_file}")
            except Exception as e:
                add_system_log(f"No se pudo eliminar lock: {str(e)}")
                
    add_system_log("Limpieza de emergencia completada.")
    return jsonify({"status": "ok", "cleaned_lock": cleaned_lock})

@app.route('/api/bedrock/players', methods=['GET'])
def get_bedrock_players():
    config = load_server_config()
    server_name = config.get("server_in_use", "")
    if not server_name:
        return jsonify({"players": [], "ops": []})
        
    server_path = os.path.join(DRIVE_PATH, server_name)
    players_file = os.path.join(server_path, 'bedrock_players.json')
    permissions_file = os.path.join(server_path, 'permissions.json')
    
    players = []
    ops = []
    
    if os.path.exists(players_file):
        try:
            with open(players_file, 'r') as f:
                players = json.load(f)
        except:
            pass
            
    if os.path.exists(permissions_file):
        try:
            with open(permissions_file, 'r') as f:
                ops = json.load(f)
        except:
            pass
            
    return jsonify({
        "players": players,
        "ops": ops
    })

@app.route('/api/bedrock/search-player', methods=['POST'])
def search_bedrock_player():
    config = load_server_config()
    server_name = config.get("server_in_use", "")
    if not server_name:
        return jsonify({"status": "error", "message": "No hay servidor seleccionado."})
        
    data = request.json
    gamertag = data.get("gamertag", "").strip()
    if not gamertag:
        return jsonify({"status": "error", "message": "Gamertag vacío."})
        
    server_path = os.path.join(DRIVE_PATH, server_name)
    players_file = os.path.join(server_path, 'bedrock_players.json')
    
    url = f"https://mcprofile.io/api/v1/bedrock/gamertag/{gamertag}"
    try:
        add_system_log(f"Buscando XUID para Bedrock gamertag '{gamertag}'...")
        res = requests.get(url, timeout=5)
        res_data = res.json()
        if "xuid" in res_data:
            name = res_data["gamertag"]
            xuid = res_data["xuid"]
            
            players = []
            if os.path.exists(players_file):
                try:
                    with open(players_file, 'r') as f:
                        players = json.load(f)
                except:
                    pass
            if not any(p["xuid"] == xuid for p in players):
                players.append({"name": name, "xuid": xuid})
                with open(players_file, 'w') as f:
                    json.dump(players, f, indent=2)
                    
            add_system_log(f"Jugador '{name}' guardado exitosamente con XUID: {xuid}.")
            return jsonify({"status": "ok", "name": name, "xuid": xuid})
        else:
            return jsonify({"status": "error", "message": "No se encontró el XUID de ese jugador."})
    except Exception as e:
        return jsonify({"status": "error", "message": f"Error de API: {str(e)}"})

@app.route('/api/bedrock/op', methods=['POST'])
def manage_bedrock_op():
    config = load_server_config()
    server_name = config.get("server_in_use", "")
    if not server_name:
        return jsonify({"status": "error", "message": "No hay servidor seleccionado."})
        
    data = request.json
    xuid = data.get("xuid", "").strip()
    action = data.get("action", "").strip()
    if not xuid or not action:
        return jsonify({"status": "error", "message": "XUID y acción requeridos."})
        
    server_path = os.path.join(DRIVE_PATH, server_name)
    permissions_file = os.path.join(server_path, 'permissions.json')
    
    permissions = []
    if os.path.exists(permissions_file):
        try:
            with open(permissions_file, 'r') as f:
                permissions = json.load(f)
        except:
            pass
            
    if action == "give":
        if not any(op["xuid"] == xuid for op in permissions):
            permissions.append({"permission": "operator", "xuid": xuid})
            add_system_log(f"Otorgado OP a XUID: {xuid}")
    elif action == "remove":
        permissions = [op for op in permissions if op["xuid"] != xuid]
        add_system_log(f"Retirado OP a XUID: {xuid}")
        
    try:
        with open(permissions_file, 'w') as f:
            json.dump(permissions, f, indent=2)
        return jsonify({"status": "ok"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)})

@app.route('/api/change-server', methods=['POST'])
def change_server():
    global mc_process, session_logs
    if mc_process and mc_process.poll() is None:
        return jsonify({"status": "error", "message": "No se puede cambiar de servidor mientras el servidor actual esté encendido."})
        
    data = request.json
    server_name = data.get("server_name", "").strip()
    
    if not server_name:
        return jsonify({"status": "error", "message": "Nombre de servidor inválido."})
        
    server_dir = os.path.join(DRIVE_PATH, server_name)
    if not os.path.exists(server_dir):
        return jsonify({"status": "error", "message": f"La carpeta del servidor '{server_name}' no existe en Drive."})
        
    config = load_server_config()
    config["server_in_use"] = server_name
    if server_name not in config["server_list"]:
        config["server_list"].append(server_name)
    save_server_config(config)
    
    # Load logs of new server
    session_logs = []
    load_historical_logs(server_name)
    
    add_system_log(f"Servidor activo cambiado a: {server_name}")
    return jsonify({"status": "ok"})

@app.route('/api/restart', methods=['POST'])
def restart_mc():
    global mc_process, server_status
    if not mc_process or mc_process.poll() is not None:
        return jsonify({"status": "error", "message": "El servidor ya está apagado."})
    
    def restart_task():
        global mc_process, server_status
        # Step 1: send /stop
        server_status = "stopping"
        try:
            mc_process.stdin.write("stop\n")
            mc_process.stdin.flush()
        except Exception:
            pass
        # Step 2: Wait up to 30 s
        for _ in range(30):
            if not mc_process or mc_process.poll() is not None:
                break
            time.sleep(1)
        # Step 3: Force kill if still alive
        if mc_process and mc_process.poll() is None:
            try:
                mc_process.kill()
                mc_process.wait(timeout=5)
            except Exception:
                pass
        mc_process = None
        stop_tunnels()
        time.sleep(2)
        add_system_log("Reiniciando el servidor de Minecraft...")
        start_mc_process_internal()
        
    threading.Thread(target=restart_task, daemon=True).start()
    return jsonify({"status": "ok"})

@app.route('/api/files/list', methods=['GET'])
def list_files():
    config = load_server_config()
    server_name = config.get("server_in_use", "")
    if not server_name:
        return jsonify({"status": "error", "message": "No hay servidor seleccionado."})
        
    rel_path = request.args.get("path", "").strip().strip("/")
    server_root = os.path.join(DRIVE_PATH, server_name)
    target_dir = os.path.abspath(os.path.join(server_root, rel_path))
    
    # Secure against path traversal
    if not target_dir.startswith(os.path.abspath(server_root)):
        return jsonify({"status": "error", "message": "Acceso denegado."})
        
    if not os.path.exists(target_dir):
        return jsonify({"status": "error", "message": "Directorio no existe."})
        
    try:
        items = []
        for entry in os.scandir(target_dir):
            is_dir = entry.is_dir()
            stat = entry.stat()
            items.append({
                "name": entry.name,
                "is_dir": is_dir,
                "size": stat.st_size if not is_dir else 0,
                "mtime": stat.st_mtime
            })
        # Sort directories first, then files alphabetically
        items.sort(key=lambda x: (not x["is_dir"], x["name"].lower()))
        return jsonify({"status": "ok", "items": items})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)})

@app.route('/api/files/read', methods=['GET'])
def read_file_content():
    config = load_server_config()
    server_name = config.get("server_in_use", "")
    if not server_name:
        return jsonify({"status": "error", "message": "No hay servidor seleccionado."})
        
    rel_path = request.args.get("path", "").strip().strip("/")
    server_root = os.path.join(DRIVE_PATH, server_name)
    target_file = os.path.abspath(os.path.join(server_root, rel_path))
    
    if not target_file.startswith(os.path.abspath(server_root)):
        return jsonify({"status": "error", "message": "Acceso denegado."})
        
    if not os.path.exists(target_file) or os.path.isdir(target_file):
        return jsonify({"status": "error", "message": "Archivo no encontrado."})
        
    # Check file size limit (2MB)
    if os.path.getsize(target_file) > 2 * 1024 * 1024:
        return jsonify({"status": "error", "message": "El archivo es demasiado grande para ser editado desde la web."})
        
    try:
        with open(target_file, 'r', encoding='utf-8', errors='ignore') as f:
            content = f.read()
        return jsonify({"status": "ok", "content": content})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)})

@app.route('/api/files/write', methods=['POST'])
def write_file_content():
    config = load_server_config()
    server_name = config.get("server_in_use", "")
    if not server_name:
        return jsonify({"status": "error", "message": "No hay servidor seleccionado."})
        
    data = request.json
    rel_path = data.get("path", "").strip().strip("/")
    content = data.get("content", "")
    
    server_root = os.path.join(DRIVE_PATH, server_name)
    target_file = os.path.abspath(os.path.join(server_root, rel_path))
    
    if not target_file.startswith(os.path.abspath(server_root)):
        return jsonify({"status": "error", "message": "Acceso denegado."})
        
    try:
        os.makedirs(os.path.dirname(target_file), exist_ok=True)
        with open(target_file, 'w', encoding='utf-8') as f:
            f.write(content)
        add_system_log(f"Archivo editado y guardado desde el Explorador Web: {rel_path}")
        return jsonify({"status": "ok"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)})

@app.route('/api/files/delete', methods=['POST'])
def delete_file_item():
    config = load_server_config()
    server_name = config.get("server_in_use", "")
    if not server_name:
        return jsonify({"status": "error", "message": "No hay servidor seleccionado."})
        
    data = request.json
    rel_path = data.get("path", "").strip().strip("/")
    
    server_root = os.path.join(DRIVE_PATH, server_name)
    target_item = os.path.abspath(os.path.join(server_root, rel_path))
    
    if not target_item.startswith(os.path.abspath(server_root)) or target_item == os.path.abspath(server_root):
        return jsonify({"status": "error", "message": "Acceso denegado."})
        
    try:
        if os.path.isdir(target_item):
            shutil.rmtree(target_item)
            add_system_log(f"Directorio eliminado desde el Explorador Web: {rel_path}")
        else:
            os.remove(target_item)
            add_system_log(f"Archivo eliminado desde el Explorador Web: {rel_path}")
        return jsonify({"status": "ok"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)})

@app.route('/api/files/create-folder', methods=['POST'])
def create_folder():
    config = load_server_config()
    server_name = config.get("server_in_use", "")
    if not server_name:
        return jsonify({"status": "error", "message": "No hay servidor seleccionado."})
        
    data = request.json
    rel_path = data.get("path", "").strip().strip("/")
    folder_name = data.get("folder_name", "").strip()
    
    if not folder_name or '/' in folder_name or '\\' in folder_name:
        return jsonify({"status": "error", "message": "Nombre de carpeta inválido."})
        
    server_root = os.path.join(DRIVE_PATH, server_name)
    target_dir = os.path.abspath(os.path.join(server_root, rel_path, folder_name))
    
    if not target_dir.startswith(os.path.abspath(server_root)):
        return jsonify({"status": "error", "message": "Acceso denegado."})
        
    try:
        os.makedirs(target_dir, exist_ok=True)
        add_system_log(f"Carpeta creada desde el Explorador Web: {os.path.join(rel_path, folder_name)}")
        return jsonify({"status": "ok"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)})

@app.route('/api/players/lists', methods=['GET'])
def get_player_lists():
    config = load_server_config()
    server_name = config.get("server_in_use", "")
    if not server_name:
        return jsonify({"ops": [], "whitelist": [], "banned": []})
        
    server_path = os.path.join(DRIVE_PATH, server_name)
    
    def read_json_file(filename):
        path = os.path.join(server_path, filename)
        if os.path.exists(path):
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except:
                pass
        return []
        
    ops = read_json_file("ops.json")
    whitelist = read_json_file("whitelist.json")
    banned = read_json_file("banned-players.json")
    
    # Bedrock fallback compatibility
    if not ops and os.path.exists(os.path.join(server_path, "permissions.json")):
        ops_bedrock = read_json_file("permissions.json")
        players = read_json_file("bedrock_players.json")
        for ob in ops_bedrock:
            if ob.get("permission") == "operator":
                name = next((p["name"] for p in players if p["xuid"] == ob.get("xuid")), "Desconocido")
                ops.append({"name": name, "uuid": ob.get("xuid"), "level": "operator"})
                
    if not whitelist and os.path.exists(os.path.join(server_path, "whitelist.json")):
        wl_bedrock = read_json_file("whitelist.json")
        if wl_bedrock and len(wl_bedrock) > 0 and "xuid" in wl_bedrock[0]:
            whitelist = [{"name": item.get("name"), "uuid": item.get("xuid")} for item in wl_bedrock]
            
    return jsonify({
        "ops": ops,
        "whitelist": whitelist,
        "banned": banned
    })

@app.route('/api/players/add', methods=['POST'])
def add_player_to_list():
    config = load_server_config()
    server_name = config.get("server_in_use", "")
    if not server_name:
        return jsonify({"status": "error", "message": "No hay servidor seleccionado."})
        
    data = request.json
    list_name = data.get("list_name", "").strip().lower()
    player_name = data.get("player_name", "").strip()
    
    if not player_name or not list_name:
        return jsonify({"status": "error", "message": "Faltan parámetros."})
        
    server_path = os.path.join(DRIVE_PATH, server_name)
    colabconfig = load_colab_config(server_name)
    is_bedrock = colabconfig.get("server_type", "") == "bedrock"
    
    global mc_process
    if mc_process and mc_process.poll() is None and not is_bedrock:
        cmd = ""
        if list_name == "ops": cmd = f"op {player_name}"
        elif list_name == "whitelist": cmd = f"whitelist add {player_name}"
        elif list_name == "banned": cmd = f"ban {player_name}"
        
        if cmd:
            try:
                mc_process.stdin.write(f"{cmd}\n")
                mc_process.stdin.flush()
                add_system_log(f"Comando de jugador enviado al servidor en ejecución: /{cmd}")
                time.sleep(0.5)
                return jsonify({"status": "ok", "message": f"Comando '{cmd}' enviado al servidor."})
            except Exception as e:
                pass
                
    uuid = ""
    resolved_name = player_name
    
    if is_bedrock:
        url = f"https://mcprofile.io/api/v1/bedrock/gamertag/{player_name}"
        try:
            res = requests.get(url, timeout=5).json()
            if "xuid" in res:
                uuid = res["xuid"]
                resolved_name = res["gamertag"]
                players_file = os.path.join(server_path, 'bedrock_players.json')
                players = []
                if os.path.exists(players_file):
                    try:
                        with open(players_file, 'r') as f: players = json.load(f)
                    except: pass
                if not any(p["xuid"] == uuid for p in players):
                    players.append({"name": resolved_name, "xuid": uuid})
                    with open(players_file, 'w') as f: json.dump(players, f, indent=2)
            else:
                return jsonify({"status": "error", "message": "No se encontró el XUID para ese Gamertag Bedrock."})
        except Exception as e:
            return jsonify({"status": "error", "message": f"Error buscando Gamertag Bedrock: {str(e)}"})
    else:
        url = f"https://api.mojang.com/users/profiles/minecraft/{player_name}"
        try:
            res = requests.get(url, timeout=5)
            if res.status_code == 200:
                res_data = res.json()
                uuid = res_data["id"]
                uuid = f"{uuid[:8]}-{uuid[8:12]}-{uuid[12:16]}-{uuid[16:20]}-{uuid[20:]}"
                resolved_name = res_data["name"]
            else:
                import uuid as uuid_lib
                uuid = str(uuid_lib.uuid3(uuid_lib.NAMESPACE_DNS, f"OfflinePlayer:{player_name}"))
        except:
            import uuid as uuid_lib
            uuid = str(uuid_lib.uuid3(uuid_lib.NAMESPACE_DNS, f"OfflinePlayer:{player_name}"))
            
    filename = ""
    if is_bedrock:
        if list_name == "ops": filename = "permissions.json"
        elif list_name == "whitelist": filename = "whitelist.json"
    else:
        if list_name == "ops": filename = "ops.json"
        elif list_name == "whitelist": filename = "whitelist.json"
        elif list_name == "banned": filename = "banned-players.json"
        
    if not filename:
        return jsonify({"status": "error", "message": "Lista no soportada."})
        
    file_path = os.path.join(server_path, filename)
    items = []
    if os.path.exists(file_path):
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                items = json.load(f)
        except:
            pass
            
    if is_bedrock:
        if list_name == "ops":
            if not any(i.get("xuid") == uuid for i in items):
                items.append({"permission": "operator", "xuid": uuid})
        elif list_name == "whitelist":
            if not any(i.get("xuid") == uuid for i in items):
                items.append({"ignoresPlayerLimit": False, "name": resolved_name, "xuid": uuid})
    else:
        if list_name == "ops":
            if not any(i.get("uuid") == uuid for i in items):
                items.append({"uuid": uuid, "name": resolved_name, "level": 4, "bypassesPlayerLimit": False})
        elif list_name == "whitelist":
            if not any(i.get("uuid") == uuid for i in items):
                items.append({"uuid": uuid, "name": resolved_name})
        elif list_name == "banned":
            if not any(i.get("uuid") == uuid for i in items):
                items.append({
                    "uuid": uuid,
                    "name": resolved_name,
                    "created": time.strftime("%Y-%m-%d %H:%M:%S %z"),
                    "source": "Console",
                    "expires": "forever",
                    "reason": "Baneado desde el Panel Web"
                })
                
    try:
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(items, f, indent=2)
        add_system_log(f"Jugador '{resolved_name}' agregado a {filename} (offline edit).")
        return jsonify({"status": "ok"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)})

@app.route('/api/players/remove', methods=['POST'])
def remove_player_from_list():
    config = load_server_config()
    server_name = config.get("server_in_use", "")
    if not server_name:
        return jsonify({"status": "error", "message": "No hay servidor seleccionado."})
        
    data = request.json
    list_name = data.get("list_name", "").strip().lower()
    player_name = data.get("player_name", "").strip()
    uuid = data.get("uuid", "").strip()
    
    if not list_name or (not player_name and not uuid):
        return jsonify({"status": "error", "message": "Faltan parámetros."})
        
    server_path = os.path.join(DRIVE_PATH, server_name)
    colabconfig = load_colab_config(server_name)
    is_bedrock = colabconfig.get("server_type", "") == "bedrock"
    
    global mc_process
    if mc_process and mc_process.poll() is None and not is_bedrock and player_name:
        cmd = ""
        if list_name == "ops": cmd = f"deop {player_name}"
        elif list_name == "whitelist": cmd = f"whitelist remove {player_name}"
        elif list_name == "banned": cmd = f"pardon {player_name}"
        
        if cmd:
            try:
                mc_process.stdin.write(f"{cmd}\n")
                mc_process.stdin.flush()
                add_system_log(f"Comando enviado al servidor en ejecución: /{cmd}")
                time.sleep(0.5)
                return jsonify({"status": "ok"})
            except:
                pass
                
    filename = ""
    if is_bedrock:
        if list_name == "ops": filename = "permissions.json"
        elif list_name == "whitelist": filename = "whitelist.json"
    else:
        if list_name == "ops": filename = "ops.json"
        elif list_name == "whitelist": filename = "whitelist.json"
        elif list_name == "banned": filename = "banned-players.json"
        
    if not filename:
        return jsonify({"status": "error", "message": "Lista no soportada."})
        
    file_path = os.path.join(server_path, filename)
    if not os.path.exists(file_path):
        return jsonify({"status": "error", "message": "El archivo de la lista no existe."})
        
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            items = json.load(f)
            
        new_items = []
        for item in items:
            if is_bedrock:
                if list_name == "ops":
                    if item.get("xuid") == uuid or item.get("xuid") == player_name: continue
                else:
                    if item.get("xuid") == uuid or item.get("name", "").lower() == player_name.lower(): continue
            else:
                if item.get("uuid") == uuid or item.get("name", "").lower() == player_name.lower(): continue
            new_items.append(item)
            
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(new_items, f, indent=2)
            
        add_system_log(f"Jugador removido de {filename} (offline edit).")
        return jsonify({"status": "ok"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)})

# --- World Management Endpoints ---

@app.route('/api/worlds/reset', methods=['POST'])
def reset_world():
    global server_status, active_server
    if server_status != "offline":
        return jsonify({"status": "error", "message": "El servidor debe estar apagado para reiniciar el mundo."})
    if not active_server:
        return jsonify({"status": "error", "message": "No hay ningún servidor seleccionado."})
    
    server_dir = os.path.join(DRIVE_PATH, active_server)
    deleted = []
    for d in ['world', 'world_nether', 'world_the_end']:
        path = os.path.join(server_dir, d)
        if os.path.exists(path):
            try:
                shutil.rmtree(path)
                deleted.append(d)
            except Exception as e:
                return jsonify({"status": "error", "message": f"Error eliminando {d}: {str(e)}"})
    
    add_system_log(f"Mundos reiniciados (eliminados): {', '.join(deleted)}")
    return jsonify({"status": "ok", "message": f"Mundo(s) {', '.join(deleted)} eliminado(s) correctamente."})

@app.route('/api/worlds/download', methods=['GET'])
def download_world():
    global active_server
    if not active_server:
        return "Error: No hay ningún servidor seleccionado.", 404
    server_dir = os.path.join(DRIVE_PATH, active_server)
    world_dir = os.path.join(server_dir, 'world')
    if not os.path.exists(world_dir):
        return "Error: El mundo 'world' no existe en este servidor.", 404
        
    temp_zip = os.path.join(server_dir, 'world-download-temp.zip')
    if os.path.exists(temp_zip):
        try:
            os.remove(temp_zip)
        except:
            pass
            
    try:
        # Zip the world directory
        with zipfile.ZipFile(temp_zip, 'w', zipfile.ZIP_DEFLATED) as zipf:
            for root, dirs, files in os.walk(world_dir):
                for file in files:
                    file_path = os.path.join(root, file)
                    arcname = os.path.relpath(file_path, os.path.dirname(world_dir))
                    zipf.write(file_path, arcname)
        
        return send_from_directory(server_dir, 'world-download-temp.zip', as_attachment=True)
    except Exception as e:
        return f"Error al comprimir el mundo: {str(e)}", 500

@app.route('/api/worlds/upload', methods=['POST'])
def upload_world():
    global server_status, active_server
    if server_status != "offline":
        return jsonify({"status": "error", "message": "El servidor debe estar apagado para subir un mundo."})
    if not active_server:
        return jsonify({"status": "error", "message": "No hay ningún servidor seleccionado."})
        
    if 'file' not in request.files:
        return jsonify({"status": "error", "message": "No se subió ningún archivo."})
        
    file = request.files['file']
    if file.filename == '':
        return jsonify({"status": "error", "message": "Nombre de archivo vacío."})
        
    if not file.filename.endswith('.zip'):
        return jsonify({"status": "error", "message": "El archivo de mundo debe estar en formato .zip."})
        
    server_dir = os.path.join(DRIVE_PATH, active_server)
    temp_zip = os.path.join(server_dir, 'world-upload-temp.zip')
    
    try:
        file.save(temp_zip)
        
        # Remove existing world directories
        for d in ['world', 'world_nether', 'world_the_end']:
            path = os.path.join(server_dir, d)
            if os.path.exists(path):
                shutil.rmtree(path)
                
        # Extract zip
        world_dir = os.path.join(server_dir, 'world')
        with zipfile.ZipFile(temp_zip, 'r') as zip_ref:
            namelist = zip_ref.namelist()
            has_root_world = any(name.startswith('world/') or name.startswith('world\\') for name in namelist)
            
            if has_root_world:
                zip_ref.extractall(server_dir)
            else:
                os.makedirs(world_dir, exist_ok=True)
                zip_ref.extractall(world_dir)
                
        os.remove(temp_zip)
        add_system_log("Nuevo mundo subido y extraído exitosamente en 'world'.")
        return jsonify({"status": "ok", "message": "Mundo subido y extraído correctamente."})
    except Exception as e:
        if os.path.exists(temp_zip):
            try: os.remove(temp_zip)
            except: pass
        return jsonify({"status": "error", "message": f"Error al procesar y extraer el mundo: {str(e)}"})

# --- Log Management Endpoints ---

@app.route('/api/log/read', methods=['GET'])
def read_latest_log():
    global active_server
    if not active_server:
        return jsonify({"status": "error", "message": "No hay servidor seleccionado."})
    log_file_path = os.path.join(DRIVE_PATH, active_server, 'logs', 'latest.log')
    if os.path.exists(log_file_path):
        try:
            with open(log_file_path, 'r', encoding='utf-8', errors='ignore') as f:
                content = f.read()
            return jsonify({"status": "ok", "content": content})
        except Exception as e:
            return jsonify({"status": "error", "message": f"Error leyendo el archivo logs/latest.log: {str(e)}"})
    else:
        return jsonify({"status": "error", "message": "El archivo logs/latest.log no existe."})

@app.route('/api/log/download', methods=['GET'])
def download_latest_log():
    global active_server
    if not active_server:
        return "Error: No hay servidor seleccionado.", 404
    log_dir = os.path.join(DRIVE_PATH, active_server, 'logs')
    log_file_path = os.path.join(log_dir, 'latest.log')
    if os.path.exists(log_file_path):
        return send_from_directory(log_dir, 'latest.log', as_attachment=True)
    return "Error: El archivo logs/latest.log no existe.", 404

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 8000))
    
    # Load initial historical logs for the active server if exists
    config = load_server_config()
    active_server = config.get("server_in_use", "")
    if active_server:
        load_historical_logs(active_server)
    else:
        add_system_log("No hay servidor seleccionado por defecto.")
        
    add_system_log(f"Iniciando panel web en puerto {port}...")
    app.run(host='0.0.0.0', port=port, debug=False, threaded=True)
