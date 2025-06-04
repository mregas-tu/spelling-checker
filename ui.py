import streamlit as st
import requests
import figma_to_firebase
from collections import defaultdict
import pandas as pd
import json
import re

# --- Config ---
GITHUB_JSON_URL = "https://raw.githubusercontent.com/mregas-tu/spelling-checker/main/strings.json"
st.set_page_config(page_title="Figma → Firebase", page_icon="🚀", layout="wide")
st.title("🚀 Figma → Firebase Uploader")
st.markdown("### 📄 Cambios detectados entre Figma y GitHub")

# --- Obtener datos de Figma ---
diff = []
invalid_keys = set()
try:
    status = st.empty()
    status.info("Obteniendo datos...")
    raw_entries = figma_to_firebase.get_figma_strings_raw()
    figma_data = defaultdict(list)
    for k, v in raw_entries:
        figma_data[k].append(v)
        if not re.match(r'^[a-z0-9_]+$', k):
            diff.append({"key": k, "estado": "Key inválida", "Figma": v, "GitHub": "nombre inválido"})
            invalid_keys.add(k)
    status.empty()
except Exception as e:
    st.error(f"❌ Error al obtener los textos desde Figma:\n\n{str(e)}")
    figma_data = {}

# --- Obtener datos de GitHub ---
try:
    response = requests.get(GITHUB_JSON_URL)
    response.raise_for_status()
    github_data = response.json()
    for k, v in github_data.items():
        if isinstance(v, list):
            github_data[k] = v[0]
except Exception as e:
    st.error(f"❌ Error al cargar el archivo desde GitHub:\n\n{str(e)}")
    github_data = {}

# --- Comparar diferencias ---
if figma_data and github_data:
    for key, values in figma_data.items():
        if key in invalid_keys:
            continue

        figma_values = set(values)
        github_val = str(github_data.get(key, '-'))

        if len(figma_values) > 1:
            for val in figma_values:
                diff.append({"key": key, "estado": "Keys idénticas", "Figma": val, "GitHub": github_val})
        elif key in github_data and github_data[key] != values[0]:
            diff.append({"key": key, "estado": "Cambio", "Figma": values[0], "GitHub": str(github_data[key])})
        elif key not in github_data:
            diff.append({"key": key, "estado": "Nuevo", "Figma": values[0], "GitHub": "-"})

    for key, value in github_data.items():
        if key not in figma_data:
            diff.append({"key": key, "estado": "Eliminado", "Figma": "-", "GitHub": str(value)})

    if not diff:
        st.info("✅ No hay diferencias entre Figma y GitHub.")
        st.stop()

    df = pd.DataFrame(diff)
    df = df[["key", "Figma", "GitHub", "estado"]]
    df.sort_values(by="estado", key=lambda col: col.map({"Eliminado": 0, "Key inválida": 1, "Keys idénticas": 2, "Conflicto": 3, "Cambio": 4, "Nuevo": 5}), inplace=True)

    has_conflicts = any(df["estado"].isin(["Conflicto", "Key inválida", "Keys idénticas"]))
    has_deleted = any(df["estado"] == "Eliminado")
    rows_to_display = df[df["estado"].isin(["Conflicto", "Key inválida", "Keys idénticas"])] if has_conflicts else df[df["estado"] != "Conflicto"]

    def color_estado(val):
        return {
            "Key inválida": "color: #FF8C00",
            "Keys idénticas": "color: #DA70D6",
            "Conflicto": "color: #DAA520",
            "Cambio": "color: #4682B4",
            "Nuevo": "color: #228B22",
            "Eliminado": "color: #B22222"
        }.get(val, "")

    def highlight_row(val, estado):
        if estado in ["Conflicto", "Key inválida", "Keys idénticas"]:
            return "background-color: #FFFACD"
        if estado == "Eliminado":
            return "background-color: #ffe5e5"
        return ""

    if not rows_to_display.empty:
        if has_conflicts:
            st.warning("🚨 SE DETECTARON CONFLICTOS EN LOS SIGUIENTES NODOS")
        else:
            st.success(f"🔍 Se encontraron {len(rows_to_display)} diferencias totales:")

        styled_df = rows_to_display.style\
            .applymap(color_estado, subset=["estado"])\
            .apply(lambda row: [highlight_row(v, row.estado) for v in row], axis=1)\
            .hide(axis='index')
        st.dataframe(styled_df, hide_index=True, use_container_width=True)

        if has_conflicts:
            st.stop()

# --- Revisión Ortográfica ---
if has_conflicts:
    st.stop()

st.markdown("---")
st.markdown("### 💜 Revisión ortográfica con Gemini")

if "ortografia" not in st.session_state:
    st.session_state.ortografia = False
if "seleccionadas" not in st.session_state:
    st.session_state.seleccionadas = set()
if "sugerencias" not in st.session_state:
    st.session_state.sugerencias = {}
if "eliminado_confirmado" not in st.session_state:
    st.session_state.eliminado_confirmado = False

if has_deleted and not st.session_state.eliminado_confirmado:
    st.markdown("#### ⚠️ Se detectó que un nodo fue eliminado o renombrado. ¿Estás seguro de que querés continuar?")
    confirm = st.radio("", ["No", "Sí"], horizontal=True, index=0)
    if confirm == "Sí":
        st.session_state.eliminado_confirmado = True
        st.rerun()
    else:
        st.stop()

# --- Filtrar solo los textos con estado Cambio o Nuevo ---
figma_strings_full = figma_to_firebase.get_figma_strings()
strings_para_analizar = {row["key"]: figma_strings_full[row["key"]] for row in diff if row["estado"] in ["Cambio", "Nuevo"] and row["key"] in figma_strings_full}

if not st.session_state.ortografia:
    if st.button("Analizar ortografía"):
        with st.spinner("Analizando con Gemini..."):
            try:
                sugerencias = figma_to_firebase.get_spelling_suggestions(strings_para_analizar)
                if sugerencias:
                    st.session_state.sugerencias = sugerencias
                    st.session_state.ortografia = True
                    st.rerun()
                else:
                    st.info("✅ No se encontraron sugerencias ortográficas.")
                    if st.button("Actualizar strings en GitHub y RemoteConfig"):
                        try:
                            with open("strings.json", "w", encoding="utf-8") as f:
                                flat_figma_data = {k: (v[0] if isinstance(v, list) else v) for k, v in figma_data.items() if v}
                                json.dump(flat_figma_data, f, ensure_ascii=False, indent=2)
                            figma_to_firebase.upload_to_firebase(flat_figma_data)
                            figma_to_firebase.upload_file_to_github(
                                file_path="strings.json",
                                repo="mregas-tu/spelling-checker",
                                path_in_repo="strings.json",
                                branch="main"
                            )
                            st.success("✅ Strings actualizados correctamente en GitHub y Firebase")
                        except Exception as e:
                            st.error(f"❌ No se pudo subir el JSON: {str(e)}")
            except Exception as e:
                st.error(f"❌ Error al analizar ortografía: {str(e)}")
else:
    st.markdown("#### Sugerencias detectadas")
    for key, pair in st.session_state.sugerencias.items():
        checked = st.checkbox(f"{key}: '{pair['original']}' → '{pair['sugerido']}'", key=key)
        if checked:
            st.session_state.seleccionadas.add(key)
        else:
            st.session_state.seleccionadas.discard(key)

    if st.button("Aplicar correcciones seleccionadas"):
        for key in st.session_state.seleccionadas:
            figma_data[key] = st.session_state.sugerencias[key]['sugerido']
        try:
            with open("strings.json", "w", encoding="utf-8") as f:
                flat_figma_data = {k: (v[0] if isinstance(v, list) else v) for k, v in figma_data.items() if v}
                json.dump(flat_figma_data, f, ensure_ascii=False, indent=2)
            figma_to_firebase.upload_to_firebase(flat_figma_data)
            figma_to_firebase.upload_file_to_github(
                file_path="strings.json",
                repo="mregas-tu/spelling-checker",
                path_in_repo="strings.json",
                branch="main"
            )
            st.success("✅ Correcciones aplicadas y subidas a GitHub y Firebase")
        except Exception as e:
            st.error(f"❌ No se pudo subir el JSON: {str(e)}")

st.markdown("---")
