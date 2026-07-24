# 🚀 CloudCraft — Panel de Control Web & Control desde Render.com (Estilo Aternos)

<div align="center">

[![Descargar Cuaderno (Recomendado)](https://img.shields.io/badge/Descargar-CloudCraft.ipynb-brightgreen?style=for-the-badge&logo=jupyter)](https://github.com/zZKingWolfZz/CLOUDcraft/releases/latest/download/CloudCraft.ipynb)
&nbsp;&nbsp;
[![Desplegar en Render (Gratis)](https://img.shields.io/badge/Desplegar-Render.com-purple?style=for-the-badge&logo=render)](https://render.com)

</div>

CloudCraft es una solución interactiva y moderna para alojar, gestionar e iniciar servidores de Minecraft (Java y Bedrock) en **Google Colab**, con almacenamiento persistente en **Google Drive** y un panel web de control independiente listo para desplegar en **Render.com** (estilo Aternos).

---

## ✨ Características Principales

* 🔄 **Control Remoto desde Render.com (Estilo Aternos)**: Accede a tu panel desde cualquier celular o PC y presiona el botón gigante **REINICIAR SERVIDOR** en tiempo real.
* 🌐 **Túneles Públicos Automáticos**: Generación automática de túneles HTTPS seguros con Cloudflare, Ngrok, Zrok, LocalToNet y Playit.gg.
* 🖥️ **Consola Interactiva**: Envía comandos al servidor en tiempo real (`/op`, `/tp`, `/gamemode`, `/stop`).
* 📦 **Creador de Servidores Web**: Crea e instala nuevos servidores seleccionando el tipo de software (Paper, Purpur, Forge, Fabric, Vanilla, Bedrock, Geyser) y la versión dinámicamente.
* ⚡ **Control Total del Servidor**: Botones rápidos para Iniciar, Detener y Reiniciar el servidor de forma segura.
* 📂 **Explorador de Archivos Web**: Lee, edita código en línea, sube y descarga archivos directamente desde el navegador.
* 🗺️ **Gestión de Mundos**: Descarga el mapa activo comprimido en `.zip`, sube mundos locales, o reinicia el mapa para generar uno nuevo.
* 👥 **Administrador de Jugadores**: Gestiona lista blanca (whitelist), operadores (OP) y baneos visualmente.
* 🛠️ **Verificaciones de Java Robustas**: Auto-libera bloqueos de `apt` en Colab e instala la versión de Java adecuada (8, 11, 17, 21) según la versión de Minecraft.

---

## 🚀 Cómo empezar en Google Colab

1. Abre el cuaderno `CloudCraft.ipynb` en **Google Colaboratory**.
2. Ejecuta la celda **`[⚙] Configuración Inicial (Set up)`**:
   * Autoriza el montaje de Google Drive.
   * Se creará la carpeta `Drive/MyDrive/minecraft`.
3. Ejecuta la celda **`[⚡] Iniciar Panel de Control Web`**:
   * Iniciará el panel backend y mostrará tu **🌐 URL del Túnel Público de Cloudflare**.
   * Copia esa URL para usarla en tu aplicación de Render.com.

---

## 🌐 Cómo Desplegar en Render.com (100% Gratis - Sin Tarjeta)

1. Inicia sesión en [Render.com](https://render.com/) y haz clic en **New +** ➔ **Web Service**.
2. Conecta tu repositorio de GitHub: `zZKingWolfZz/CLOUDcraft`.
3. En la configuración del servicio:
   * **Name:** `cloudcraft-render`
   * **Root Directory:** `render_app` *(⚠️ ¡Poner exactamente render_app!)*
   * **Environment:** `Python 3`
   * **Build Command:** `pip install -r requirements.txt`
   * **Start Command:** `gunicorn app:app`
   * **Instance Type:** `Free` ($0/month)
4. Haz clic en **Create Web Service**.
5. Abre la URL asignada por Render (ejemplo: `https://cloudcraft-render.onrender.com`), pega tu **URL del Túnel de Colab** y tendrás tu botón **REINICIAR SERVIDOR** funcionando desde cualquier lugar.

---

## 📂 Contenido del Repositorio

* `CloudCraft.ipynb`: Cuaderno Jupyter optimizado para ejecutarse en Colab.
* `colab_panel.py`: Servidor backend en Python (Flask) para gestión del servidor de Minecraft.
* `dashboard.html`: Interfaz de usuario responsiva (HTML/CSS/JS).
* `render_app/`: Aplicación en Flask lista para desplegar en Render.com.

---

## 🛡️ Notas de Seguridad

* **Nunca compartas tu archivo `server_list.txt`**: Contiene claves privadas de tus túneles.
* Desarrollado con ❤️ para la comunidad de Minecraft.
