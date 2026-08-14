import sys
import os
import re
import datetime as dt

# Garante que o módulo dataweb_client.py (na raiz do projeto) seja importável
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import streamlit as st
import plotly.graph_objects as go

from dataweb_client import (
    build_import_query,
    run_report,
    parse_report,
    COUNTRY_CODES,
)

st.set_page_config(page_title="Comex EUA", page_icon="🌍", layout="wide")

st.markdown(
    """
    <h1 style='text-align:center; color:#042373; font-family:Arial; font-weight:bold;'>
        Dashboard COMEX Importações — EUA
    </h1>
    """,
    unsafe_allow_html=True,
)

st.warning(
    "**Fonte:** USITC DataWeb (dados oficiais de comércio exterior dos EUA). "
    "Consulta atual cobre apenas **Importações** (Import For Consumption), por código HTS."
)

st.markdown("""
### Utilização do Aplicativo

1. Informe um ou mais códigos **HTS** (Harmonized Tariff Schedule).
2. Selecione o intervalo de anos.
3. Opcionalmente, filtre por país(es) de origem — deixe vazio para trazer todos.
4. Clique em **Buscar dados** SEMPRE que quiser carregar ou atualizar as visualizações.
""")

# --------------------------------------------------------------------
# Sidebar - filtros
# --------------------------------------------------------------------
st.sidebar.header("🔍 Filtros")

hts_input = st.sidebar.text_area(
    "Códigos HTS (um por linha)",
    value="0306144030",
    help=(
        "Ex: 0306144030 ou 2505.10.10.00 — pontos são removidos "
        "automaticamente. Pode informar vários, um por linha."
    ),
)
# Remove qualquer caractere que não seja dígito (pontos, espaços etc.),
# já que a API espera o código HTS só com números.
_raw_lines = [c.strip() for c in hts_input.splitlines() if c.strip()]
hts_codes = [re.sub(r"[^0-9]", "", line) for line in _raw_lines]
hts_codes = [c for c in hts_codes if c]

ano_atual = dt.datetime.now().year
year_start, year_end = st.sidebar.slider(
    "Intervalo de anos",
    min_value=2010,
    max_value=ano_atual - 1,
    value=(2020, ano_atual - 1),
)
years = [str(y) for y in range(year_start, year_end + 1)]

aggregate_commodities = st.sidebar.checkbox(
    "Agregar todos os HTS numa única linha", value=False
)

countries = st.sidebar.multiselect(
    "Países de origem (opcional — vazio = todos)",
    options=sorted(COUNTRY_CODES.keys()),
    default=[],
)
aggregate_countries = st.sidebar.checkbox("Agregar todos os países", value=True)

buscar = st.sidebar.button("Buscar dados")

# --------------------------------------------------------------------
# Token vem dos secrets, sem alerta visual (monitoramento é feito
# separadamente via GitHub Actions + e-mail)
# --------------------------------------------------------------------
TOKEN = st.secrets.get("DATAWEB_TOKEN")

# --------------------------------------------------------------------
# Execução da consulta
# --------------------------------------------------------------------
if buscar:
    if not TOKEN:
        st.error(
            "Token da API DataWeb não configurado. Adicione `DATAWEB_TOKEN` "
            "em st.secrets para habilitar esta página."
        )
        st.stop()

    if not hts_codes:
        st.warning("Informe pelo menos um código HTS.")
        st.stop()

    with st.spinner("Consultando USITC DataWeb..."):
        query = build_import_query(
            hts_codes=hts_codes,
            years=years,
            countries=countries,
            aggregate_commodities=aggregate_commodities,
            aggregate_countries=aggregate_countries,
        )
        try:
            response = run_report(query, TOKEN)
            df = parse_report(response, measure_num=0)
        except Exception as e:
            st.error(f"Erro ao consultar a API DataWeb: {e}")
            st.stop()

    st.session_state["df_eua"] = df
    st.session_state["df_eua_years"] = years

if "df_eua" in st.session_state:
    df = st.session_state["df_eua"]

    st.success(f"{len(df)} linha(s) retornada(s).")
    st.dataframe(df, use_container_width=True)

    csv = df.to_csv(index=False).encode("utf-8")
    st.download_button("⬇️ Baixar CSV", csv, "comex_eua_import.csv", "text/csv")

    # --- Gráfico simples: valor por ano, mesmo estilo visual do painel BR ---
    years_cols = [c for c in df.columns if c in st.session_state.get("df_eua_years", [])]
    if years_cols:
        st.markdown(
            """
            <h2 style='text-align:center; color:#042373; font-family:Arial; font-weight:bold;'>
                Valor de Importação por Ano
            </h2>
            """,
            unsafe_allow_html=True,
        )

        fig = go.Figure()
        for idx, row in df.iterrows():
            valores = [
                float(str(row[c]).replace(",", "")) if str(row[c]).strip() not in ("", "nan") else None
                for c in years_cols
            ]
            fig.add_trace(
                go.Bar(
                    x=years_cols,
                    y=valores,
                    name=f"Linha {idx + 1}",
                    hovertemplate="Ano: %{x}<br>Valor: %{y:,.0f}<extra></extra>",
                )
            )

        fig.update_layout(
            barmode="group",
            xaxis_title="Ano",
            yaxis_title="Valor (Customs Value, USD)",
            plot_bgcolor="#DBF7FF",
            paper_bgcolor="white",
            width=1100,
            height=500,
            margin=dict(t=60, b=50, l=50, r=50),
        )
        fig.update_xaxes(showline=True, linewidth=2, linecolor="#042373", mirror=True)
        fig.update_yaxes(showline=True, linewidth=2, linecolor="#042373", mirror=True)

        st.plotly_chart(fig, use_container_width=True)
