import sys
import os
import re
import datetime as dt

# Garante que o módulo dataweb_client.py (na raiz do projeto) seja importável
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import streamlit as st
import pandas as pd
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

    # Colunas de ano vêm como texto (ex: "925,827") -- converter para
    # numérico permite ordenação correta na tabela e uso direto no gráfico.
    for col in years:
        if col in df.columns:
            df[col] = (
                df[col]
                .astype(str)
                .str.replace(",", "", regex=False)
                .str.strip()
                .replace({"": None, "nan": None})
            )
            df[col] = pd.to_numeric(df[col], errors="coerce")

    st.session_state["df_eua"] = df
    st.session_state["df_eua_years"] = years

if "df_eua" in st.session_state:
    df = st.session_state["df_eua"]
    years_cols = [c for c in df.columns if c in st.session_state.get("df_eua_years", [])]

    st.success(f"{len(df)} linha(s) retornada(s).")
    st.dataframe(
        df,
        use_container_width=True,
        column_config={
            col: st.column_config.NumberColumn(format="localized")
            for col in years_cols
        },
    )

    csv = df.to_csv(index=False).encode("utf-8")
    st.download_button("⬇️ Baixar CSV", csv, "comex_eua_import.csv", "text/csv")

    # --- Gráfico: valor por ano, com rótulo legível e filtro Top N ---
    if years_cols:
        st.markdown(
            """
            <h2 style='text-align:center; color:#042373; font-family:Arial; font-weight:bold;'>
                Valor de Importação por Ano
            </h2>
            """,
            unsafe_allow_html=True,
        )

        # Colunas que não são de ano identificam a linha (país, descrição
        # do HTS, etc.) -- usamos elas para montar o rótulo do gráfico,
        # em vez de "Linha N".
        label_cols = [c for c in df.columns if c not in years_cols]

        def montar_rotulo(row):
            partes = [
                str(row[c]) for c in label_cols
                if str(row[c]).strip() not in ("", "nan", "None")
            ]
            return " – ".join(partes) if partes else "Total"

        df_grafico = df.copy()
        df_grafico["_rotulo"] = df_grafico.apply(montar_rotulo, axis=1)
        df_grafico["_valor_ranking"] = df_grafico[years_cols].sum(axis=1, skipna=True)
        df_grafico = df_grafico.sort_values("_valor_ranking", ascending=False)

        todos_rotulos = df_grafico["_rotulo"].tolist()
        top5_default = todos_rotulos[:5]

        rotulos_selecionados = st.multiselect(
            "Linhas exibidas no gráfico (por padrão, top 5 por valor total no período)",
            options=todos_rotulos,
            default=top5_default,
        )

        if rotulos_selecionados:
            df_plot = df_grafico[df_grafico["_rotulo"].isin(rotulos_selecionados)]

            fig = go.Figure()
            for _, row in df_plot.iterrows():
                valores = [row[c] for c in years_cols]
                fig.add_trace(
                    go.Bar(
                        x=years_cols,
                        y=valores,
                        name=row["_rotulo"],
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
                legend_title="Linha",
            )
            fig.update_xaxes(showline=True, linewidth=2, linecolor="#042373", mirror=True)
            fig.update_yaxes(showline=True, linewidth=2, linecolor="#042373", mirror=True)

            st.plotly_chart(fig, use_container_width=True)
        else:
            st.info("Selecione ao menos uma linha para exibir o gráfico.")
