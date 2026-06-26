# 🚀 CloudCraft — Panel de Control Web para Google Colab

<div align="center">

[![Descargar Cuaderno (Recomendado)](https://img.shields.io/badge/Descargar-CloudCraft.ipynb-brightgreen?style=for-the-badge&logo=jupyter)](https://github.com/zZKingWolfZz/CLOUDcraft/releases/latest/download/CloudCraft.ipynb)
&nbsp;&nbsp;
[![Descargar Alternativo (Raw)](https://img.shields.io/badge/Descargar-Fallback-blue?style=for-the-badge&logo=github)](https://github.com/zZKingWolfZz/CLOUDcraft/raw/main/CloudCraft.ipynb)

</div>

CloudCraft es una solución interactiva y moderna para alojar, gestionar e iniciar servidores de Minecraft (tanto Java como Bedrock) utilizando la infraestructura en la nube de **Google Colab**, con almacenamiento persistente en **Google Drive** y una interfaz gráfica premium inspirada en paneles líderes de hosting.

---

## ✨ Características Principales

* 🖥️ **Consola Interactiva**: Envía comandos al servidor de Minecraft en tiempo real y lee la salida directamente desde el navegador.
* 📦 **Creador de Servidores Web**: Crea e instala nuevos servidores seleccionando el tipo de software (Paper, Forge, Bedrock, Fabric, etc.) y la versión de Minecraft dinámicamente desde el panel.
* ⚡ **Control Total del Servidor**: Botones rápidos para Iniciar, Detener y Reiniciar el servidor de Minecraft de forma segura.
* 📂 **Explorador de Archivos Web**: Lee, edita, sube y descarga archivos directamente del servidor desde el panel.
* 🗺️ **Gestión de Mundos**: Descarga el mapa activo comprimido en `.zip`, sube mundos locales, o reinicia el mapa para generar uno nuevo.
* 👥 **Administrador de Jugadores**: Gestiona la lista de operadores (OP), la lista blanca (whitelist) y jugadores baneados de forma visual.
* 🔗 **Conectividad Playit.gg Segura y Automatizada**:
  * **Sin claves hardcodeadas**: Si ejecutas el túnel sin clave secreta, CloudCraft generará un enlace de vinculación (claim link) visible en el panel.
  * **Auto-guardado**: Al hacer clic en el enlace y vincular el agente a tu cuenta de Playit, la clave de acceso se guardará de forma persistente en tu Google Drive para futuros arranques.
* 🛠️ **Verificaciones de Java Robustas**: Auto-libera bloqueos de `apt` en Colab e instala automáticamente la versión de Java adecuada (8, 11, 17, 21) requerida según la versión de Minecraft.

---

## 🚀 Cómo empezar en Google Colab

1. Sube el archivo `CloudCraft.ipynb` a tu cuenta de **Google Drive** o ábrelo directamente en **Google Colaboratory**.
2. Ejecuta la celda **`[⚙] Configuración Inicial (Set up)`**:
   * Esto autorizará el montaje de Google Drive (donde se guardarán todos tus servidores y progresos).
   * Creará automáticamente la carpeta de Minecraft en tu Drive (`Drive/MyDrive/minecraft`).
3. Ejecuta la celda **`[⚡] Iniciar Panel de Control Web`**:
   * Esta celda iniciará el servidor backend de CloudCraft y generará un enlace seguro de acceso privado (`eval_js`).
   * Haz clic en el botón verde **"Abrir Panel de Control"** para ingresar al panel en tu navegador.
4. **Vincula tu Túnel**:
   * Si es la primera vez que inicias el servidor, verás un aviso naranja en el panel con el enlace de vinculación a Playit.gg. Haz clic en él para activar el agente.
   * Una vez activado, el panel guardará la configuración y te proporcionará la IP del servidor de Minecraft para conectar con tus amigos.

---

## 📂 Contenido del Repositorio

* `CloudCraft.ipynb`: Cuaderno Jupyter optimizado listo para ser ejecutado en Colab.
* `colab_panel.py`: Servidor backend escrito en Python (Flask) que orquesta la ejecución del servidor de Minecraft y los túneles.
* `dashboard.html`: Interfaz de usuario interactiva y responsiva (HTML/CSS/JS).

---

## 🛡️ Contribuciones y Seguridad

* **Nunca compartas tu archivo `server_list.txt`**: Este archivo en tu Google Drive contiene las claves privadas de tus túneles de red (Ngrok, Playit, Zrok).
* Desarrollado con ❤️ para la comunidad de Minecraft en Colab.
