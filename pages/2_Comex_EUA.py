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

    # Adiciona coluna de Total (soma dos anos) ao final da tabela
    df_exibicao = df.copy()
    df_exibicao["Total"] = df_exibicao[years_cols].sum(axis=1, skipna=True)

    st.success(f"{len(df_exibicao)} linha(s) retornada(s).")
    st.dataframe(
        df_exibicao,
        use_container_width=True,
        column_config={
            col: st.column_config.NumberColumn(format="localized")
            for col in years_cols + ["Total"]
        },
    )

    csv = df_exibicao.to_csv(index=False).encode("utf-8")
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

        label_cols = [c for c in df.columns if c not in years_cols]

        # Detecta automaticamente qual coluna representa o país, comparando
        # os valores da coluna com a lista de países conhecida (COUNTRY_CODES).
        # Não exige 100% de correspondência: a API pode incluir linhas
        # especiais (ex: "Countries NEC", "Free Trade Zones") que não estão
        # na lista -- usamos a coluna com maior proporção de correspondência.
        country_col = None
        melhor_proporcao = 0.0
        for c in label_cols:
            valores = df[c].dropna().astype(str)
            if len(valores) == 0:
                continue
            proporcao = valores.isin(COUNTRY_CODES.keys()).mean()
            if proporcao > melhor_proporcao and proporcao >= 0.5:
                melhor_proporcao = proporcao
                country_col = c

        df_grafico = df.copy()
        df_grafico["_valor_ranking"] = df_grafico[years_cols].sum(axis=1, skipna=True)

        def montar_rotulo(row, cols):
            partes = [
                str(row[c]) for c in cols
                if str(row[c]).strip() not in ("", "nan", "None")
            ]
            return " – ".join(partes) if partes else "Total"

        def desenhar_grafico(df_plot, titulo, key_prefix):
            todos_rotulos = df_plot.sort_values("_valor_ranking", ascending=False)["_rotulo"].tolist()
            top5_default = todos_rotulos[:5]

            ms_key = f"{key_prefix}_multiselect"
            reset_flag_key = f"{key_prefix}_reset_flag"

            # Se o botão "Top 5" foi clicado no ciclo anterior, restaura a
            # seleção ANTES de instanciar o widget (não dá para alterar o
            # session_state de um widget depois que ele já foi criado).
            if st.session_state.get(reset_flag_key):
                st.session_state[ms_key] = top5_default
                st.session_state[reset_flag_key] = False

            col_ms, col_btn = st.columns([5, 1])
            with col_ms:
                rotulos_selecionados = st.multiselect(
                    f"Linhas exibidas — {titulo}",
                    options=todos_rotulos,
                    default=top5_default,
                    key=ms_key,
                )
            with col_btn:
                st.write("")
                if st.button("🔝 Top 5", key=f"{key_prefix}_reset_btn", use_container_width=True):
                    st.session_state[reset_flag_key] = True
                    st.rerun()

            if not rotulos_selecionados:
                st.info("Selecione ao menos uma linha para exibir o gráfico.")
                return

            df_sel = df_plot[df_plot["_rotulo"].isin(rotulos_selecionados)]

            fig = go.Figure()
            for _, row in df_sel.iterrows():
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
                height=450,
                margin=dict(t=40, b=50, l=50, r=50),
                legend_title="País" if country_col else "Linha",
            )
            fig.update_xaxes(showline=True, linewidth=2, linecolor="#042373", mirror=True)
            fig.update_yaxes(showline=True, linewidth=2, linecolor="#042373", mirror=True)

            st.plotly_chart(fig, use_container_width=True, key=f"{key_prefix}_chart")

        if country_col:
            # Um gráfico por combinação das demais colunas de identificação
            # (tipicamente, um gráfico por HTS selecionado), com legenda
            # mostrando apenas o nome do país.
            group_cols = [c for c in label_cols if c != country_col]
            df_grafico["_rotulo"] = df_grafico[country_col].astype(str)

            if group_cols:
                df_grafico["_grupo"] = df_grafico.apply(lambda r: montar_rotulo(r, group_cols), axis=1)
                grupos = df_grafico.drop_duplicates("_grupo").sort_values(
                    "_valor_ranking", ascending=False
                )["_grupo"].tolist()

                for i, grupo in enumerate(grupos):
                    st.markdown(f"**{grupo}**")
                    df_plot = df_grafico[df_grafico["_grupo"] == grupo]
                    desenhar_grafico(df_plot, grupo, key_prefix=f"grafico_{i}")
            else:
                desenhar_grafico(df_grafico, "Todos os HTS", key_prefix="grafico_unico")
        else:
            # Sem quebra por país -- rótulo usa todas as colunas de identificação
            df_grafico["_rotulo"] = df_grafico.apply(lambda r: montar_rotulo(r, label_cols), axis=1)
            desenhar_grafico(df_grafico, "Todas as linhas", key_prefix="grafico_unico")
