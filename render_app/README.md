# 🚀 Guía de Despliegue en Render.com (Estilo Aternos con Google Login y Drive API)

Con esta aplicación desplegada en **Render.com**, los usuarios pueden **Iniciar Sesión con su Cuenta de Google** (igual que en Aternos), conectar con su **Google Drive** y presionar **REINICIAR SERVIDOR** para controlar su servidor de Minecraft alojado en Google Colab.

---

## 🔑 Paso 1: Crear Credenciales de Google OAuth 2.0 (Gratis)

1. Ve a [Google Cloud Console](https://console.cloud.google.com/).
2. Crea un proyecto (ejemplo: `CloudCraft Aternos`).
3. Ve a **APIs y Servicios** -> **Pantalla de Consentimiento OAuth**:
   - Tipo de usuario: **Externo**.
   - Nombre de la app: `CloudCraft Render Control`.
   - Añade tu correo electrónico y guarda.
4. Ve a **Credenciales** -> **Crear Credenciales** -> **ID de Cliente de OAuth**:
   - Tipo de aplicación: **Aplicación Web**.
   - Nombre: `Render Control App`.
   - **URI de redirección autorizados:**
     - `https://tu-app-en-render.onrender.com/callback`
     - `http://localhost:5000/callback` (para pruebas locales).
5. Guarda y obtendrás:
   - **Client ID** (ejemplo: `123456789-xxx.apps.googleusercontent.com`)
   - **Client Secret** (ejemplo: `GOCSPX-xxxx...`)

---

## 📦 Paso 2: Desplegar en Render.com

1. Sube el contenido de esta carpeta (`render_app`) a un repositorio en **GitHub**.
2. Ve a [Render.com](https://render.com) y crea un nuevo **Web Service**.
3. Selecciona tu repositorio de GitHub.
4. En **Environment Variables**, añade:
   - `GOOGLE_CLIENT_ID`: Tu Client ID de Google.
   - `GOOGLE_CLIENT_SECRET`: Tu Client Secret de Google.
   - `SECRET_KEY`: Una clave aleatoria para la sesión.
5. Haz clic en **Deploy Web Service**.

---

## 🎮 Cómo Funciona (Estilo Aternos)

1. Abre tu enlace de Render (ejemplo: `https://tu-app.onrender.com`).
2. Haz clic en **Google Login** para iniciar sesión con tu cuenta de Google.
3. Se solicitará acceso a **Google Drive** para leer tu carpeta `minecraft/`.
4. La página mostrará tu foto de perfil, correo de Google e indicador verde **Google Drive Conectado**.
5. Presiona el botón gigante **REINICIAR SERVIDOR** para reiniciar tu servidor de Minecraft en Colab en tiempo real.
