import sys
import os
import re
import datetime as dt

# Garante que o módulo dataweb_client.py (na raiz do projeto) seja importável
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import plotly.colors as pcolors

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

# Tradução dos rótulos de medida que vêm da API (em inglês) para exibição
TABLE_LABEL_PT = {
    "Customs Value": "Valor (USD)",
    "First Unit of Quantity": "Quantidade",
}

def label_pt(label_original: str) -> str:
    return TABLE_LABEL_PT.get(label_original, label_original)

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
    "Agregar todos os HTS numa única linha",
    value=False,
    key="aggregate_commodities_checkbox",
    disabled=st.session_state.get("metrica_quantidade_checkbox", False),
    help=(
        "Desativado quando 'Quantidade' está marcada: HTS diferentes podem "
        "ter unidades de medida diferentes (kg, toneladas etc.), e somar "
        "isso na origem geraria um número sem sentido."
    ),
)

st.sidebar.markdown("**Métrica(s)**")
metrica_valor = st.sidebar.checkbox("Valor (USD)", value=True, key="metrica_valor_checkbox")
metrica_quantidade = st.sidebar.checkbox("Quantidade", value=False, key="metrica_quantidade_checkbox")

if metrica_quantidade:
    aggregate_commodities = False

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

buscar = st.sidebar.button(
    "Buscar dados",
    disabled=not (metrica_valor or metrica_quantidade),
)

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
                    # Valores ausentes (None) representam ausência de
                    # comércio registrado no período -- equivalem a 0.
                    df_i[col] = pd.to_numeric(df_i[col], errors="coerce").fillna(0)

                if monthly:
                    df_i = reshape_monthly_timeline(df_i, year_col="Year")
                    # Após "achatar" para linha do tempo, garante que
                    # nenhuma coluna de período tenha ficado com NaN
                    # (pode acontecer se algum mês/ano não tinha linha
                    # correspondente para o grupo).
                    periodo_cols_flat = [
                        c for c in df_i.columns
                        if re.match(r"^[A-Za-zçã]{3}/\d{4}$", str(c))
                    ]
                    df_i[periodo_cols_flat] = df_i[periodo_cols_flat].fillna(0)

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

    def preparar_df_exibicao(df, medida_label):
        """Aplica a mesma limpeza usada na exibição (remover/renomear
        Quantity Description, adicionar coluna Total) -- reutilizada tanto
        pela tabela em tela quanto pelos exports (CSV/Excel)."""
        periodo_cols = periodo_cols_de(df)
        eh_medida_valor = "Quantity" not in medida_label

        df_exibicao = df.copy()

        if "Quantity Description" in df_exibicao.columns:
            if eh_medida_valor:
                df_exibicao = df_exibicao.drop(columns=["Quantity Description"])
            else:
                df_exibicao["Quantity Description"] = (
                    df_exibicao["Quantity Description"]
                    .astype(str)
                    .str.replace("Value for: ", "", regex=False)
                )
                df_exibicao = df_exibicao.rename(
                    columns={"Quantity Description": "Unidade de Medida"}
                )

        if periodo_cols:
            df_exibicao["Total"] = df_exibicao[periodo_cols].sum(axis=1, skipna=True)

        return df_exibicao, periodo_cols

    def renderizar_medida(df, medida_label, tab_key, df_exibicao, periodo_cols, excel_buffer=None):
        st.success(f"{len(df_exibicao)} linha(s) retornada(s).")
        st.dataframe(
            df_exibicao,
            use_container_width=True,
            column_config={
                col: st.column_config.NumberColumn(format="localized")
                for col in periodo_cols + (["Total"] if periodo_cols else [])
            },
        )

        # Download individual (CSV) sempre disponível, mesmo com as duas
        # métricas selecionadas -- útil se o usuário só quiser uma aba.
        csv = df_exibicao.to_csv(index=False).encode("utf-8")
        st.download_button(
            f"⬇️ Baixar CSV — {label_pt(medida_label)}", csv, f"comex_eua_{tab_key}.csv", "text/csv",
            key=f"{tab_key}_download",
        )

        # Excel combinado (Valor + Quantidade, abas separadas) -- logo
        # abaixo do CSV, disponível em qualquer uma das abas.
        if excel_buffer is not None:
            st.download_button(
                "⬇️ Baixar Excel (Valor + Quantidade, abas separadas)",
                excel_buffer,
                "comex_eua_valor_quantidade.xlsx",
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                key=f"{tab_key}_excel_download",
            )

        if not periodo_cols:
            return

    if len(dfs_por_medida) > 1:
        # Excel com uma aba por medida -- é o formato que suporta múltiplas
        # abas de fato (CSV não suporta). Gerado uma vez, reutilizado nos
        # botões de download de cada aba (logo abaixo do CSV).
        import io
        buffer = io.BytesIO()
        with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
            for label, df in dfs_por_medida.items():
                df_exibicao_tmp, _ = preparar_df_exibicao(df, label)
                sheet_name = label_pt(label)[:31]  # limite do Excel
                df_exibicao_tmp.to_excel(writer, sheet_name=sheet_name, index=False)
        excel_bytes = buffer.getvalue()

        tabs = st.tabs([label_pt(label) for label in dfs_por_medida.keys()])
        for tab, (label, df) in zip(tabs, dfs_por_medida.items()):
            with tab:
                df_exibicao, periodo_cols = preparar_df_exibicao(df, label)
                renderizar_medida(
                    df, label, tab_key=re.sub(r"\W+", "_", label.lower()),
                    df_exibicao=df_exibicao, periodo_cols=periodo_cols,
                    excel_buffer=excel_bytes,
                )
    else:
        label, df = next(iter(dfs_por_medida.items()))
        df_exibicao, periodo_cols = preparar_df_exibicao(df, label)
        renderizar_medida(
            df, label, tab_key=re.sub(r"\W+", "_", label.lower()),
            df_exibicao=df_exibicao, periodo_cols=periodo_cols,
        )

    # ----------------------------------------------------------------
    # Gráficos -- um por via de entrada (default: top 5), com slicer de
    # período e botão para trocar entre Valor e Quantidade.
    # ----------------------------------------------------------------
    st.divider()

    tem_valor = "Customs Value" in dfs_por_medida
    tem_qtd = "First Unit of Quantity" in dfs_por_medida

    if tem_valor and tem_qtd:
        metrica_grafico = st.radio(
            "Métrica dos gráficos", ["Valor (USD)", "Quantidade"],
            horizontal=True, key="metrica_grafico_toggle",
        )
    elif tem_valor:
        metrica_grafico = "Valor (USD)"
    else:
        metrica_grafico = "Quantidade"

    chave_medida = "Customs Value" if metrica_grafico == "Valor (USD)" else "First Unit of Quantity"
    df_fonte = dfs_por_medida.get(chave_medida)

    if df_fonte is not None:
        periodo_cols = periodo_cols_de(df_fonte)
        label_cols = [c for c in df_fonte.columns if c not in periodo_cols]

        # Detecta a coluna de HTS (comparando com os códigos que o
        # usuário informou nos filtros). Feito logo após o toggle de
        # métrica, antes de qualquer gráfico -- deixa claro que a escolha
        # vale para todos os gráficos abaixo.
        hts_col = None
        for c in label_cols:
            valores = set(str(v) for v in df_fonte[c].dropna().unique())
            if valores and valores.issubset(set(hts_codes)):
                hts_col = c
                break

        hts_presentes = sorted(df_fonte[hts_col].dropna().unique()) if hts_col else []

        # Rótulo amigável (código + descrição) por HTS, reaproveitado tanto
        # no seletor quanto na legenda mostrada embaixo dos títulos.
        hts_labels = {}
        if hts_col:
            if "Description" in df_fonte.columns:
                desc_col = "Description"
            else:
                candidatos = [
                    c for c in label_cols
                    if c not in (hts_col, "Quantity Description")
                ]
                candidatos = [
                    c for c in candidatos
                    if df_fonte[c].dropna().astype(str).isin(COUNTRY_CODES.keys()).mean() < 0.5
                    and df_fonte[c].dropna().astype(str).isin(DISTRICT_CODES.keys()).mean() < 0.5
                ]
                desc_col = candidatos[0] if candidatos else None

            for h in hts_presentes:
                if desc_col:
                    desc_vals = df_fonte.loc[df_fonte[hts_col] == h, desc_col].dropna().unique()
                    desc = desc_vals[0] if len(desc_vals) else ""
                    hts_labels[h] = f"{h} — {desc}"[:80]
                else:
                    hts_labels[h] = h

        # Seleção de HTS -- disponível para as duas métricas quando há mais
        # de um HTS na consulta. Em Valor, inclui a opção "Total" (soma de
        # todos). Em Quantidade, não -- HTS diferentes podem ter unidades
        # de medida diferentes, então é preciso escolher um por vez.
        TOTAL_LABEL = "Total (soma de todos os HTS)"
        if hts_col and len(hts_presentes) > 1:
            opcoes_hts = {hts_labels[h]: h for h in hts_presentes}
            if metrica_grafico == "Valor (USD)":
                opcoes_ordenadas = [TOTAL_LABEL] + list(opcoes_hts.keys())
            else:
                opcoes_ordenadas = list(opcoes_hts.keys())

            escolha_label = st.selectbox(
                "HTS exibido nos gráficos",
                options=opcoes_ordenadas,
                help=(
                    "Em Quantidade não é possível somar HTS diferentes -- "
                    "as unidades de medida podem não ser as mesmas."
                ),
            )
            if escolha_label == TOTAL_LABEL:
                hts_escolhido = None
                df_fonte_grafico = df_fonte
            else:
                hts_escolhido = opcoes_hts[escolha_label]
                df_fonte_grafico = df_fonte[df_fonte[hts_col] == hts_escolhido]
        else:
            hts_escolhido = None
            df_fonte_grafico = df_fonte

        def legenda_unidade_hts():
            """Mostra, embaixo do título de cada gráfico, a unidade de
            medida (quando Quantidade) e qual HTS está sendo exibido
            (quando um HTS específico foi escolhido, não o Total)."""
            linhas = []
            if metrica_grafico == "Quantidade" and "Quantity Description" in df_fonte_grafico.columns:
                unidades = (
                    df_fonte_grafico["Quantity Description"]
                    .dropna().astype(str)
                    .str.replace("Value for: ", "", regex=False)
                    .unique()
                )
                unidades_txt = ", ".join(sorted(u for u in unidades if u and u != "nan"))
                if unidades_txt:
                    linhas.append(f"Unidade de medida: {unidades_txt}")
            if hts_escolhido is not None:
                linhas.append(f"HTS exibido: {hts_labels.get(hts_escolhido, hts_escolhido)}")
            if linhas:
                st.markdown(
                    f"<p style='text-align:center; font-size:0.85rem; color:#666;'>"
                    f"{' · '.join(linhas)}</p>",
                    unsafe_allow_html=True,
                )

        st.markdown(
            f"""
            <h2 style='text-align:center; color:#042373; font-family:Arial; font-weight:bold;'>
                Volume Importado em {metrica_grafico}: Realizado vs Projetado
            </h2>
            """,
            unsafe_allow_html=True,
        )
        legenda_unidade_hts()

        if not periodo_cols:
            st.info("Sem colunas de período disponíveis para exibir gráficos.")
        else:
            # Slicer -- limita o período exibido nos gráficos, dentro do
            # intervalo já consultado.
            if len(periodo_cols) > 1:
                periodo_inicio, periodo_fim = st.select_slider(
                    "Período exibido nos gráficos",
                    options=periodo_cols,
                    value=(periodo_cols[0], periodo_cols[-1]),
                )
                idx_ini = periodo_cols.index(periodo_inicio)
                idx_fim = periodo_cols.index(periodo_fim)
                periodo_visivel = periodo_cols[idx_ini: idx_fim + 1]
            else:
                periodo_visivel = periodo_cols

            # Detecta a coluna de via/distrito para quebrar os gráficos
            via_col = None
            for c in label_cols:
                valores = df_fonte_grafico[c].dropna().astype(str)
                if len(valores) == 0:
                    continue
                if valores.isin(DISTRICT_CODES.keys()).mean() >= 0.5:
                    via_col = c
                    break

            if via_col:
                df_via = (
                    df_fonte_grafico.groupby(via_col, as_index=False)[periodo_cols]
                    .sum(min_count=1)
                )
                df_via["_total"] = df_via[periodo_cols].sum(axis=1, skipna=True)
                df_via = df_via.sort_values("_total", ascending=False)
                todas_vias = df_via[via_col].tolist()
                top5_default = sorted(todas_vias[:5])
                todas_vias_alfa = sorted(todas_vias)

                combo_id = re.sub(r"\W+", "_", f"{metrica_grafico}_{hts_escolhido or 'total'}".lower())
                ms_key = f"vias_grafico_multiselect_{combo_id}"
                reset_flag_key = f"vias_grafico_reset_flag_{combo_id}"
                if st.session_state.get(reset_flag_key):
                    st.session_state[ms_key] = top5_default
                    st.session_state[reset_flag_key] = False

                col_label, col_btn = st.columns([5, 1])
                with col_label:
                    st.markdown("**Vias exibidas**")
                with col_btn:
                    if st.button("🔝 Restaurar Top 5", key=f"vias_grafico_reset_btn_{combo_id}", use_container_width=True):
                        st.session_state[reset_flag_key] = True
                        st.rerun()

                vias_selecionadas = st.multiselect(
                    "Vias exibidas",
                    options=todas_vias_alfa,
                    default=top5_default,
                    key=ms_key,
                    label_visibility="collapsed",
                )

                if not vias_selecionadas:
                    st.info("Selecione ao menos uma via para exibir os gráficos.")
                else:
                    cols_por_linha = 2
                    for i in range(0, len(vias_selecionadas), cols_por_linha):
                        cols = st.columns(cols_por_linha)
                        for j, via in enumerate(vias_selecionadas[i:i + cols_por_linha]):
                            with cols[j]:
                                row = df_via[df_via[via_col] == via].iloc[0]
                                valores = [row[c] for c in periodo_visivel]

                                fig = go.Figure()
                                if monthly:
                                    fig.add_trace(
                                        go.Scatter(
                                            x=periodo_visivel, y=valores,
                                            mode="lines+markers", name="Realizado",
                                            line=dict(color="blue"),
                                            hovertemplate="%{x}<br>%{y:,.0f}<extra></extra>",
                                        )
                                    )
                                else:
                                    fig.add_trace(
                                        go.Bar(
                                            x=periodo_visivel, y=valores,
                                            name="Realizado", marker_color="blue",
                                            hovertemplate="Ano: %{x}<br>%{y:,.0f}<extra></extra>",
                                        )
                                    )
                                fig.update_layout(
                                    title=str(via),
                                    height=320,
                                    plot_bgcolor="#DBF7FF",
                                    paper_bgcolor="white",
                                    margin=dict(t=50, b=40, l=40, r=20),
                                    showlegend=False,
                                )
                                fig.update_xaxes(showline=True, linewidth=2, linecolor="#042373", mirror=True)
                                fig.update_yaxes(showline=True, linewidth=2, linecolor="#042373", mirror=True)
                                chart_key = "via_chart_" + re.sub(r"\W+", "_", str(via).lower())
                                st.plotly_chart(fig, use_container_width=True, key=chart_key)

                # ------------------------------------------------------
                # Gráfico 2 -- todas as vias combinadas num único gráfico
                # (padrão do dash Brasil: "Volume total transacionado por URF")
                # ------------------------------------------------------
                st.divider()
                st.markdown(
                    f"""
                    <h2 style='text-align:center; color:#042373; font-family:Arial; font-weight:bold;'>
                        Volume total transacionado em {metrica_grafico} (Realizado) por Via de Entrada
                    </h2>
                    """,
                    unsafe_allow_html=True,
                )
                legenda_unidade_hts()

                if len(periodo_cols) > 1:
                    periodo2_inicio, periodo2_fim = st.select_slider(
                        "Período exibido neste gráfico",
                        options=periodo_cols,
                        value=(periodo_cols[0], periodo_cols[-1]),
                        key=f"periodo_slicer_grafico2_{combo_id}",
                    )
                    idx2_ini = periodo_cols.index(periodo2_inicio)
                    idx2_fim = periodo_cols.index(periodo2_fim)
                    periodo_visivel2 = periodo_cols[idx2_ini: idx2_fim + 1]
                else:
                    periodo_visivel2 = periodo_cols

                ms_key2 = f"vias_grafico2_multiselect_{combo_id}"
                reset_flag_key2 = f"vias_grafico2_reset_flag_{combo_id}"
                if st.session_state.get(reset_flag_key2):
                    st.session_state[ms_key2] = top5_default
                    st.session_state[reset_flag_key2] = False

                MAX_VIAS_GRAFICO2 = 12

                col_label2, col_btn2 = st.columns([5, 1])
                with col_label2:
                    st.markdown("**Vias exibidas**")
                with col_btn2:
                    if st.button("🔝 Restaurar Top 5", key=f"vias_grafico2_reset_btn_{combo_id}", use_container_width=True):
                        st.session_state[reset_flag_key2] = True
                        st.rerun()

                vias_selecionadas2 = st.multiselect(
                    "Vias exibidas neste gráfico",
                    options=todas_vias_alfa,
                    default=top5_default,
                    key=ms_key2,
                    label_visibility="collapsed",
                    max_selections=MAX_VIAS_GRAFICO2,
                )
                st.caption(
                    f"Máximo de {MAX_VIAS_GRAFICO2} vias por vez neste gráfico, "
                    "para manter as cores e a leitura claras."
                )

                if not vias_selecionadas2:
                    st.info("Selecione ao menos uma via para exibir o gráfico.")
                else:
                    paleta = pcolors.qualitative.Alphabet
                    fig2 = go.Figure()
                    for i, via in enumerate(vias_selecionadas2):
                        row = df_via[df_via[via_col] == via].iloc[0]
                        valores = [row[c] for c in periodo_visivel2]
                        fig2.add_trace(
                            go.Bar(
                                x=periodo_visivel2,
                                y=valores,
                                name=str(via),
                                marker_color=paleta[i % len(paleta)],
                                hovertemplate="%{x}<br>%{y:,.0f}<extra></extra>",
                            )
                        )
                    fig2.update_layout(
                        barmode="group",
                        xaxis_title="Período",
                        yaxis_title=metrica_grafico,
                        plot_bgcolor="#DBF7FF",
                        paper_bgcolor="white",
                        height=600,
                        legend_title="Via",
                        legend=dict(
                            orientation="h", yanchor="top", y=-0.2,
                            xanchor="center", x=0.5,
                            bgcolor="white", bordercolor="#042373", borderwidth=1,
                        ),
                        margin=dict(t=60, b=140, l=50, r=50),
                    )
                    fig2.update_xaxes(showline=True, linewidth=2, linecolor="#042373", mirror=True)
                    fig2.update_yaxes(showline=True, linewidth=2, linecolor="#042373", mirror=True)
                    st.plotly_chart(fig2, use_container_width=True, key=f"grafico2_combinado_{combo_id}")
            else:
                st.info(
                    "Nenhuma quebra por via de entrada detectada nos dados atuais "
                    "-- marque 'Via de entrada' nos filtros e desmarque 'Agregar "
                    "todas as vias' para ver os gráficos por via."
                )

            # ------------------------------------------------------------
            # Gráfico 3 -- porcentagem por país (top 5 + "Outros"), em barras
            # (equivalente ao gráfico de pizza/rosca do dash Brasil).
            # ------------------------------------------------------------
            st.divider()
            st.markdown(
                f"""
                <h2 style='text-align:center; color:#042373; font-family:Arial; font-weight:bold;'>
                    Porcentagem por País de {metrica_grafico} (Realizado) Importado
                </h2>
                """,
                unsafe_allow_html=True,
            )
            legenda_unidade_hts()

            country_col = None
            for c in label_cols:
                valores = df_fonte_grafico[c].dropna().astype(str)
                if len(valores) == 0:
                    continue
                if valores.isin(COUNTRY_CODES.keys()).mean() >= 0.5:
                    country_col = c
                    break

            if country_col:
                if len(periodo_cols) > 1:
                    periodo3_inicio, periodo3_fim = st.select_slider(
                        "Período considerado neste gráfico",
                        options=periodo_cols,
                        value=(periodo_cols[0], periodo_cols[-1]),
                        key=f"periodo_slicer_grafico3_{combo_id}",
                    )
                    idx3_ini = periodo_cols.index(periodo3_inicio)
                    idx3_fim = periodo_cols.index(periodo3_fim)
                    periodo_visivel3 = periodo_cols[idx3_ini: idx3_fim + 1]
                else:
                    periodo_visivel3 = periodo_cols

                df_pais = (
                    df_fonte_grafico.groupby(country_col, as_index=False)[periodo_visivel3]
                    .sum(min_count=1)
                )
                df_pais["_valor"] = df_pais[periodo_visivel3].sum(axis=1, skipna=True)
                df_pais = df_pais.sort_values("_valor", ascending=False).reset_index(drop=True)

                total_geral = df_pais["_valor"].sum()

                if total_geral and total_geral > 0:
                    todos_paises = df_pais[country_col].tolist()
                    top5_default3 = sorted(todos_paises[:5])
                    todos_paises_alfa = sorted(todos_paises)

                    ms_key3 = f"paises_grafico3_multiselect_{combo_id}"
                    reset_flag_key3 = f"paises_grafico3_reset_flag_{combo_id}"
                    if st.session_state.get(reset_flag_key3):
                        st.session_state[ms_key3] = top5_default3
                        st.session_state[reset_flag_key3] = False

                    col_label3, col_btn3 = st.columns([5, 1])
                    with col_label3:
                        st.markdown("**Países exibidos**")
                    with col_btn3:
                        if st.button("🔝 Restaurar Top 5", key=f"paises_grafico3_reset_btn_{combo_id}", use_container_width=True):
                            st.session_state[reset_flag_key3] = True
                            st.rerun()

                    paises_selecionados = st.multiselect(
                        "Países exibidos neste gráfico",
                        options=todos_paises_alfa,
                        default=top5_default3,
                        key=ms_key3,
                        label_visibility="collapsed",
                        max_selections=12,
                    )
                    st.caption(
                        "O restante dos países não selecionados aqui entra "
                        "somado na barra \"Outros\". Máximo de 12 países por vez."
                    )

                    if not paises_selecionados:
                        st.info("Selecione ao menos um país para exibir o gráfico.")
                    else:
                        df_selecionados = df_pais[df_pais[country_col].isin(paises_selecionados)]
                        df_selecionados = df_selecionados.sort_values("_valor", ascending=False)

                        labels = df_selecionados[country_col].tolist()
                        valores_abs = df_selecionados["_valor"].tolist()

                        resto_valor = total_geral - sum(valores_abs)
                        if resto_valor > 0:
                            labels.append("Outros")
                            valores_abs.append(resto_valor)

                        percentuais = [v / total_geral * 100 for v in valores_abs]

                        paleta = pcolors.qualitative.Alphabet
                        cores = [paleta[i % len(paleta)] for i in range(len(labels))]
                        if "Outros" in labels:
                            cores[labels.index("Outros")] = "#E4572E"  # destaca "Outros"

                        fig3 = go.Figure(
                            go.Bar(
                                x=labels,
                                y=percentuais,
                                marker_color=cores,
                                text=[f"{p:.1f}%" for p in percentuais],
                                textposition="outside",
                                hovertemplate="%{x}<br>%{y:.2f}%<extra></extra>",
                            )
                        )
                        fig3.update_layout(
                            xaxis_title="País",
                            yaxis_title="% do total importado",
                            xaxis=dict(categoryorder="array", categoryarray=labels),
                            plot_bgcolor="#DBF7FF",
                            paper_bgcolor="white",
                            height=500,
                            showlegend=False,
                            margin=dict(t=50, b=50, l=50, r=50),
                        )
                        fig3.update_xaxes(showline=True, linewidth=2, linecolor="#042373", mirror=True)
                        fig3.update_yaxes(showline=True, linewidth=2, linecolor="#042373", mirror=True)
                        st.plotly_chart(fig3, use_container_width=True, key=f"grafico3_pct_pais_{combo_id}")
                else:
                    st.info("Sem valores no período selecionado para calcular percentuais.")
            else:
                st.info(
                    "Nenhuma quebra por país detectada nos dados atuais -- marque "
                    "'Países de origem' nos filtros e desmarque 'Agregar todos os "
                    "países' para ver este gráfico."
                )
