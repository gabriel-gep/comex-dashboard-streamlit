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
    get_table_label,
    num_tables,
    reshape_monthly_timeline,
    COUNTRY_CODES,
    DISTRICT_CODES,
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
2. Escolha a(s) métrica(s): **Valor** (USD) e/ou **Quantidade** (unidade do produto).
3. Escolha o período: **Anual** ou **Mensal** (linha do tempo contínua).
4. Opcionalmente, filtre por país(es) de origem e/ou via de entrada (porto/distrito aduaneiro).
5. Clique em **Buscar dados** SEMPRE que quiser carregar ou atualizar as visualizações.
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
_raw_lines = [c.strip() for c in hts_input.splitlines() if c.strip()]
hts_codes = [re.sub(r"[^0-9]", "", line) for line in _raw_lines]
hts_codes = [c for c in hts_codes if c]

aggregate_commodities = st.sidebar.checkbox(
    "Agregar todos os HTS numa única linha", value=False
)

st.sidebar.markdown("**Métrica(s)**")
metrica_valor = st.sidebar.checkbox("Valor (USD)", value=True)
metrica_quantidade = st.sidebar.checkbox("Quantidade", value=False)

periodo_tipo = st.sidebar.radio("Período", ["Anual", "Mensal"], horizontal=True)

ano_atual = dt.datetime.now().year
year_start, year_end = st.sidebar.slider(
    "Intervalo de anos",
    min_value=2010,
    max_value=ano_atual - 1,
    value=(2020, ano_atual - 1),
)
years = [str(y) for y in range(year_start, year_end + 1)]

countries = st.sidebar.multiselect(
    "Países de origem (opcional — vazio = todos)",
    options=sorted(COUNTRY_CODES.keys()),
    default=[],
)
aggregate_countries = st.sidebar.checkbox("Agregar todos os países", value=True)

districts = st.sidebar.multiselect(
    "Via de entrada / distrito aduaneiro (opcional — vazio = todos)",
    options=sorted(DISTRICT_CODES.keys()),
    default=[],
)
aggregate_districts = st.sidebar.checkbox("Agregar todas as vias", value=True)

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

    measures = []
    if metrica_valor:
        measures.append("CONS_CUSTOMS_VALUE")
    if metrica_quantidade:
        measures.append("CONS_FIR_UNIT_QUANT")
    if not measures:
        st.warning("Selecione ao menos uma métrica (Valor e/ou Quantidade).")
        st.stop()

    monthly = periodo_tipo == "Mensal"

    with st.spinner("Consultando USITC DataWeb..."):
        query = build_import_query(
            hts_codes=hts_codes,
            years=years,
            countries=countries,
            aggregate_commodities=aggregate_commodities,
            aggregate_countries=aggregate_countries,
            measures=measures,
            monthly=monthly,
            districts=districts,
            aggregate_districts=aggregate_districts,
        )
        try:
            response = run_report(query, TOKEN)
        except Exception as e:
            st.error(f"Erro ao consultar a API DataWeb: {e}")
            st.stop()

        dfs_por_medida = {}
        try:
            for i in range(num_tables(response)):
                label = get_table_label(response, measure_num=i)
                df_i = parse_report(response, measure_num=i)

                # Colunas de dado (ano ou mês) vêm como texto -- converte
                # para numérico antes de qualquer outra coisa.
                data_cols = [c for c in years if c in df_i.columns] if not monthly else \
                            [m for m in ["January","February","March","April","May","June",
                                         "July","August","September","October","November","December"]
                             if m in df_i.columns]
                for col in data_cols:
                    df_i[col] = (
                        df_i[col]
                        .astype(str)
                        .str.replace(",", "", regex=False)
                        .str.strip()
                        .replace({"": None, "nan": None})
                    )
                    df_i[col] = pd.to_numeric(df_i[col], errors="coerce")

                if monthly:
                    df_i = reshape_monthly_timeline(df_i, year_col="Year")

                dfs_por_medida[label] = df_i
        except Exception as e:
            st.error(f"Erro ao processar a resposta da API: {e}")
            st.stop()

    st.session_state["df_eua_multi"] = dfs_por_medida
    st.session_state["df_eua_monthly"] = monthly
    st.session_state["df_eua_years"] = years

# --------------------------------------------------------------------
# Exibição
# --------------------------------------------------------------------
if "df_eua_multi" in st.session_state:
    dfs_por_medida = st.session_state["df_eua_multi"]
    monthly = st.session_state.get("df_eua_monthly", False)
    years = st.session_state.get("df_eua_years", [])

    def periodo_cols_de(df):
        if monthly:
            return [c for c in df.columns if re.match(r"^[A-Za-zçã]{3}/\d{4}$", str(c))]
        return [c for c in years if c in df.columns]

    def detectar_coluna_quebra(df, label_cols):
        """Detecta se alguma coluna representa país OU via/distrito,
        comparando os valores com as listas conhecidas."""
        melhor = (None, None, 0.0)  # (coluna, tipo, proporção)
        for c in label_cols:
            valores = df[c].dropna().astype(str)
            if len(valores) == 0:
                continue
            prop_pais = valores.isin(COUNTRY_CODES.keys()).mean()
            prop_via = valores.isin(DISTRICT_CODES.keys()).mean()
            if prop_pais > melhor[2] and prop_pais >= 0.5:
                melhor = (c, "País", prop_pais)
            if prop_via > melhor[2] and prop_via >= 0.5:
                melhor = (c, "Via", prop_via)
        return melhor[0], melhor[1]

    def montar_rotulo(row, cols):
        partes = [
            str(row[c]) for c in cols
            if str(row[c]).strip() not in ("", "nan", "None")
        ]
        return " – ".join(partes) if partes else "Total"

    def desenhar_grafico(df_plot, periodo_cols, titulo, key_prefix, legenda_longa, legend_title, unidade):
        todos_rotulos = df_plot.sort_values("_valor_ranking", ascending=False)["_rotulo"].tolist()
        top5_default = todos_rotulos[:5]

        ms_key = f"{key_prefix}_multiselect"
        reset_flag_key = f"{key_prefix}_reset_flag"

        if st.session_state.get(reset_flag_key):
            st.session_state[ms_key] = top5_default
            st.session_state[reset_flag_key] = False

        col_label, col_btn = st.columns([5, 1])
        with col_label:
            st.markdown(f"**Linhas exibidas — {titulo}**")
        with col_btn:
            if st.button("🔝 Restaurar Top 5", key=f"{key_prefix}_reset_btn", use_container_width=True):
                st.session_state[reset_flag_key] = True
                st.rerun()

        rotulos_selecionados = st.multiselect(
            "Linhas exibidas",
            options=todos_rotulos,
            default=top5_default,
            key=ms_key,
            label_visibility="collapsed",
        )

        if not rotulos_selecionados:
            st.info("Selecione ao menos uma linha para exibir o gráfico.")
            return

        df_sel = df_plot[df_plot["_rotulo"].isin(rotulos_selecionados)]

        fig = go.Figure()
        modo = "lines+markers" if monthly else None
        for _, row in df_sel.iterrows():
            valores = [row[c] for c in periodo_cols]
            if monthly:
                fig.add_trace(
                    go.Scatter(
                        x=periodo_cols,
                        y=valores,
                        mode="lines+markers",
                        name=row["_rotulo"],
                        hovertemplate="%{x}<br>Valor: %{y:,.0f}<extra></extra>",
                    )
                )
            else:
                fig.add_trace(
                    go.Bar(
                        x=periodo_cols,
                        y=valores,
                        name=row["_rotulo"],
                        hovertemplate="Ano: %{x}<br>Valor: %{y:,.0f}<extra></extra>",
                    )
                )

        if legenda_longa:
            legend_config = dict(
                orientation="h", yanchor="top", y=-0.25, xanchor="center", x=0.5,
                font=dict(size=10),
            )
            margin_config = dict(t=40, b=160, l=50, r=50)
        else:
            legend_config = dict()
            margin_config = dict(t=40, b=50, l=50, r=50)

        fig.update_layout(
            barmode="group" if not monthly else None,
            xaxis_title="Período" if monthly else "Ano",
            yaxis_title=unidade,
            plot_bgcolor="#DBF7FF",
            paper_bgcolor="white",
            height=450 if not legenda_longa else 550,
            margin=margin_config,
            legend_title=legend_title,
            legend=legend_config,
        )
        fig.update_xaxes(showline=True, linewidth=2, linecolor="#042373", mirror=True)
        fig.update_yaxes(showline=True, linewidth=2, linecolor="#042373", mirror=True)

        st.plotly_chart(fig, use_container_width=True, key=f"{key_prefix}_chart")

    def renderizar_medida(df, medida_label, tab_key):
        periodo_cols = periodo_cols_de(df)

        df_exibicao = df.copy()
        if periodo_cols:
            df_exibicao["Total"] = df_exibicao[periodo_cols].sum(axis=1, skipna=True)

        st.success(f"{len(df_exibicao)} linha(s) retornada(s).")
        st.dataframe(
            df_exibicao,
            use_container_width=True,
            column_config={
                col: st.column_config.NumberColumn(format="localized")
                for col in periodo_cols + (["Total"] if periodo_cols else [])
            },
        )

        csv = df_exibicao.to_csv(index=False).encode("utf-8")
        st.download_button(
            "⬇️ Baixar CSV", csv, f"comex_eua_{tab_key}.csv", "text/csv",
            key=f"{tab_key}_download",
        )

        if not periodo_cols:
            return

        st.markdown(
            f"""
            <h2 style='text-align:center; color:#042373; font-family:Arial; font-weight:bold;'>
                {medida_label} — {'Linha do Tempo' if monthly else 'Por Ano'}
            </h2>
            """,
            unsafe_allow_html=True,
        )

        label_cols = [c for c in df.columns if c not in periodo_cols]
        # "Quantity Description" é metadado (unidade de medida), não uma
        # dimensão de quebra útil para agrupar/legendar.
        label_cols_chart = [c for c in label_cols if c != "Quantity Description"]

        quebra_col, quebra_tipo = detectar_coluna_quebra(df, label_cols_chart)

        df_grafico = df.copy()
        df_grafico["_valor_ranking"] = df_grafico[periodo_cols].sum(axis=1, skipna=True)

        unidade_eixo = "Valor (Customs Value, USD)" if "Quantity" not in medida_label else "Quantidade"

        if quebra_col:
            group_cols = [c for c in label_cols_chart if c != quebra_col]
            df_grafico["_rotulo"] = df_grafico[quebra_col].astype(str)

            if group_cols:
                df_grafico["_grupo"] = df_grafico.apply(lambda r: montar_rotulo(r, group_cols), axis=1)
                grupos = df_grafico.drop_duplicates("_grupo").sort_values(
                    "_valor_ranking", ascending=False
                )["_grupo"].tolist()

                for i, grupo in enumerate(grupos):
                    st.markdown(f"**{grupo}**")
                    df_plot = df_grafico[df_grafico["_grupo"] == grupo]
                    desenhar_grafico(
                        df_plot, periodo_cols, grupo, key_prefix=f"{tab_key}_grafico_{i}",
                        legenda_longa=False, legend_title=quebra_tipo, unidade=unidade_eixo,
                    )
            else:
                desenhar_grafico(
                    df_grafico, periodo_cols, "Todos os HTS", key_prefix=f"{tab_key}_grafico_unico",
                    legenda_longa=False, legend_title=quebra_tipo, unidade=unidade_eixo,
                )
        else:
            df_grafico["_rotulo"] = df_grafico.apply(lambda r: montar_rotulo(r, label_cols_chart), axis=1)
            desenhar_grafico(
                df_grafico, periodo_cols, "Todas as linhas", key_prefix=f"{tab_key}_grafico_unico",
                legenda_longa=True, legend_title="Linha", unidade=unidade_eixo,
            )

    if len(dfs_por_medida) > 1:
        tabs = st.tabs(list(dfs_por_medida.keys()))
        for tab, (label, df) in zip(tabs, dfs_por_medida.items()):
            with tab:
                renderizar_medida(df, label, tab_key=re.sub(r"\W+", "_", label.lower()))
    else:
        label, df = next(iter(dfs_por_medida.items()))
        renderizar_medida(df, label, tab_key=re.sub(r"\W+", "_", label.lower()))
