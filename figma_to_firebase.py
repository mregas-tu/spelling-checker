# -*- coding: utf-8 -*-
import requests
import json
import smtplib
from email.message import EmailMessage
from google.oauth2 import service_account
from google.auth.transport.requests import Request
import google.generativeai as genai
import base64
import os
import time
from dotenv import load_dotenv

# === CARGAR VARIABLES DE ENTORNO ===
load_dotenv()

# === CONFIGURACIÓN ===
FIGMA_TOKEN = os.environ.get("FIGMA_TOKEN")
FILE_KEY = os.environ.get("FIGMA_FILE_KEY")
FIREBASE_CREDENTIALS_PATH = os.environ.get("FIREBASE_CREDENTIALS_PATH", "firebase_credentials.json")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN")

headers = {
    "X-Figma-Token": FIGMA_TOKEN
}

# === EXTRAER TEXTOS DE FIGMA ===
def extract_text_nodes(node, result):
    if node.get("type") == "TEXT":
        name = node.get("name")
        characters = node.get("characters")
        if name and characters:
            if name not in result:
                result[name] = []
            result[name].append(characters)
    for child in node.get("children", []):
        extract_text_nodes(child, result)

def get_figma_strings():
    print("🔄 Fetching Figma file...")
    url = f"https://api.figma.com/v1/files/{FILE_KEY}?depth=100"
    res = requests.get(url, headers=headers)
    res.raise_for_status()
    data = res.json()
    result = {}

    for page in data["document"]["children"]:
        extract_text_nodes(page, result)

    print(f"✅ Extraídos {sum(len(v) for v in result.values())} textos desde Figma.")
    return result

def get_figma_strings_raw():
    print("🔄 Fetching Figma file (raw entries)...")
    url = f"https://api.figma.com/v1/files/{FILE_KEY}?depth=100"
    res = requests.get(url, headers=headers)
    res.raise_for_status()
    data = res.json()

    result = []

    def collect(node):
        if node.get("type") == "TEXT":
            name = node.get("name")
            characters = node.get("characters")
            if name and characters:
                result.append((name, characters))
        for child in node.get("children", []):
            collect(child)

    for page in data["document"]["children"]:
        collect(page)

    print(f"✅ Extraídos {len(result)} textos desde Figma (sin agrupar).")
    return result

# === VERIFICAR CONFLICTOS (solo warning, no detiene ejecución) ===
def warn_if_same_key_has_multiple_values(strings_dict):
    conflicted = {k: list(set(v)) for k, v in strings_dict.items() if len(set(v)) > 1}

    if conflicted:
        print("\n⚠️  WARNING: SE ENCONTRARON CLAVES DUPLICADAS CON TEXTOS DIFERENTES:")
        with open("string_conflicts.log", "w", encoding="utf-8") as log:
            for k, values in conflicted.items():
                print(f"🔁 {k}:")
                for v in values:
                    print(f'    - "{v}"')
        print("\n")
    else:
        print("✅ No hay conflictos de nombre con valores diferentes.")

# === CORREGIR CON GEMINI ===
def correct_spelling_with_gemini(texts_dict):
    print("🧠 Corrigiendo ortografía con Gemini...")

    genai.configure(api_key=GEMINI_API_KEY)
    model = genai.GenerativeModel("models/gemini-1.5-flash")

    corrected = {}
    for key, values in texts_dict.items():
        value = values[0]
        prompt = f"Dame solo el texto corregido (en español), sin explicaciones ni comillas. Si el texto está bien, devolvelo igual. Texto: {value}"
        try:
            response = model.generate_content(prompt)
            result = response.text.strip().strip('"')

            if result != value:
                print(f"\n📝 Sugerencia para '{key}':")
                print(f"    Original:  {value}")
                print(f"    Corregido: {result}")
                confirm = input("¿Aceptar corrección? (s/n): ").strip().lower()
                corrected[key] = result if confirm == "s" else value
            else:
                corrected[key] = value
        except Exception as e:
            print(f"⚠️ Error al corregir '{key}': {e}")
            corrected[key] = value

    print(f"\n✅ Correcciones completadas ({len(corrected)} textos).")
    return corrected

# === SUBIR A FIREBASE ===
def upload_to_firebase(string_data):
    print("☁️ Subiendo a Firebase Remote Config vía REST...")

    credentials_json = os.environ["FIREBASE_CREDENTIALS_JSON"]
    credentials_dict = json.loads(credentials_json)

    credentials_obj = service_account.Credentials.from_service_account_info(
        credentials_dict,
        scopes=["https://www.googleapis.com/auth/firebase.remoteconfig"]
    )
    credentials_obj.refresh(Request())

    access_token = credentials_obj.token
    project_id = credentials_obj.project_id

    url = f"https://firebaseremoteconfig.googleapis.com/v1/projects/{project_id}/remoteConfig"

    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json; UTF-8",
        "If-Match": "*"
    }

    payload = {
        "parameters": {
            "strings": {
                "defaultValue": {
                    "value": json.dumps(string_data, ensure_ascii=False)
                }
            }
        }
    }

    response = requests.put(url, headers=headers, data=json.dumps(payload))

    if response.status_code == 200:
        print("🚀 Remote Config actualizado con éxito")
    else:
        print(f"❌ Error {response.status_code}: {response.text}")

# === GENERAR ARCHIVOS ===
def generate_localizable_and_constants(strings):
    with open("Localizable.strings", "w", encoding="utf-8") as f:
        for key, value in strings.items():
            f.write(f'"{key}" = "{value}";\n')

    with open("Strings.swift", "w", encoding="utf-8") as f:
        f.write("enum Strings {\n")
        for key in strings:
            f.write(f'    static let {key} = NSLocalizedString("{key}", comment: "")\n')
        f.write("}\n")

# === SUBIR A GITHUB ===
def upload_file_to_github(file_path, repo, path_in_repo, branch="main"):
    if not GITHUB_TOKEN:
        print("❌ GITHUB_TOKEN no está seteado en las variables de entorno.")
        return

    with open(file_path, "rb") as f:
        content = f.read()

    encoded_content = base64.b64encode(content).decode("utf-8")
    api_url = f"https://api.github.com/repos/{repo}/contents/{path_in_repo}"

    headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json"
    }

    get_resp = requests.get(
        api_url,
        headers={**headers, "Cache-Control": "no-cache"},
        params={"ref": branch}
    )   
    sha = get_resp.json().get("sha") if get_resp.status_code == 200 else None

    payload = {
        "message": "chore: update strings.json",
        "content": encoded_content,
        "branch": branch,
    }
    if sha:
        payload["sha"] = sha

    put_resp = requests.put(api_url, headers=headers, data=json.dumps(payload))
    if put_resp.status_code in [200, 201]:
        print("📤 strings.json subido a GitHub correctamente.")
    else:
        print(f"❌ Error al subir: {put_resp.status_code} - {put_resp.text}")

# === OBTENER CONTENIDO RAW DE GITHUB ===
def download_raw_file_from_github(owner, repo, branch, path_in_repo):
    url = f"https://api.github.com/repos/{owner}/{repo}/contents/{path_in_repo}"
    headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json",
        "Cache-Control": "no-cache"
    }
    params = {"ref": branch}
    response = requests.get(url, headers=headers, params=params)
    response.raise_for_status()
    content = response.json()["content"]
    return base64.b64decode(content).decode("utf-8")

# === SUGERENCIA DE CORRECCIONES ===
def get_spelling_suggestions(texts_dict):
    print("🧠 Obteniendo sugerencias ortográficas con Gemini...")

    genai.configure(api_key=GEMINI_API_KEY)
    model = genai.GenerativeModel("models/gemini-1.5-flash")

    suggestions = {}
    for key, values in texts_dict.items():
        value = values[0]
        prompt = f"Dame solo el texto corregido (en español), sin explicaciones ni comillas. Si el texto está bien, devolvelo igual. Texto: {value}"
        try:
            response = model.generate_content(prompt)
            result = response.text.strip().strip('"')
            if result != value:
                suggestions[key] = {
                    "original": value,
                    "sugerido": result
                }
        except Exception as e:
            print(f"⚠️ Error al procesar '{key}': {e}")
    return suggestions

# === MAIN ===
def main():
    raw_strings = get_figma_strings()
    warn_if_same_key_has_multiple_values(raw_strings)

    flat_strings = {k: v[0] for k, v in raw_strings.items()}

    with open("strings-original.json", "w", encoding="utf-8") as f:
        json.dump(flat_strings, f, ensure_ascii=False, indent=2)

    strings_corrected = correct_spelling_with_gemini(raw_strings)

    with open("strings.json", "w", encoding="utf-8") as f:
        json.dump(strings_corrected, f, ensure_ascii=False, indent=2)
        print("📜 strings.json (corregido) generado.")

    upload_to_firebase(strings_corrected)
    generate_localizable_and_constants(strings_corrected)

    upload_file_to_github(
        file_path="strings.json",
        repo="mregas-tu/spelling-checker",
        path_in_repo="strings.json",
        branch="main"
    )

if __name__ == "__main__":
    main()
