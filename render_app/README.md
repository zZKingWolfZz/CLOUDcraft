# 🚀 Guía de Despliegue en Render.com — CloudCraft Control

Con esta aplicación desplegada en **Render.com**, podrás controlar y **REINICIAR** tu servidor de Minecraft alojado en Google Colab desde cualquier dispositivo (celular o PC), 100% Gratis y **sin tarjeta de crédito**.

---

## 📋 Pasos para Desplegar en Render.com (Gratis)

1. Inicia sesión en [Render.com](https://render.com/).
2. Haz clic en **New +** ➔ **Web Service**.
3. Selecciona tu repositorio de GitHub: `zZKingWolfZz/CLOUDcraft`.
4. En los campos de configuración llena:
   - **Name:** `cloudcraft-servidor`
   - **Root Directory:** `render_app` *(⚠️ ¡Poner exactamente `render_app`!)*
   - **Environment:** `Python 3`
   - **Build Command:** `pip install -r requirements.txt`
   - **Start Command:** `gunicorn app:app`
   - **Instance Type:** `Free` (Gratis)
5. Haz clic abajo en **Create Web Service**.

---

## 🎮 Cómo Usar tu Panel en Render

1. Abre tu enlace público otorgado por Render (ejemplo: `https://cloudcraft-servidor.onrender.com`).
2. Pega la **URL del Túnel Público** que te genera Colab al ejecutar la celda `[⚡] Iniciar Panel de Control Web` (la dirección que termina en `.trycloudflare.com` o `.ngrok-free.app`).
3. ¡Listo! Tu navegador recordará la URL automáticamente y podrás presionar **REINICIAR SERVIDOR**, ver la IP, el consumo de RAM/CPU y enviar comandos a la consola.
