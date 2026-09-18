import sys
import os
import re
import datetime as dt

# Garante que o módulo dataweb_client.py (na raiz do projeto) seja importável
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
import plotly.colors as pcolors
from statsforecast import StatsForecast
from statsforecast.models import ETS

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
import census_trade_client

# Tradução dos rótulos de medida que vêm da API (em inglês) para exibição
TABLE_LABEL_PT = {
    "Customs Value": "Valor (USD)",
    "First Unit of Quantity": "Quantidade",
}

def label_pt(label_original: str) -> str:
    return TABLE_LABEL_PT.get(label_original, label_original)

# --------------------------------------------------------------------
# Projeção (ETS) -- portada da mesma lógica usada no dash Brasil.
# --------------------------------------------------------------------
MES_NUM_PT = {
    "Jan": 1, "Fev": 2, "Mar": 3, "Abr": 4, "Mai": 5, "Jun": 6,
    "Jul": 7, "Ago": 8, "Set": 9, "Out": 10, "Nov": 11, "Dez": 12,
}
MES_ABBR_PT_INV = {v: k for k, v in MES_NUM_PT.items()}

def periodo_label_para_data(label):
    """Converte 'Jan/2023' -> Timestamp(2023-01-01)."""
    mes_abbr, ano = label.split("/")
    return pd.Timestamp(year=int(ano), month=MES_NUM_PT[mes_abbr], day=1)

def data_para_periodo_label(data):
    """Converte Timestamp -> 'Jan/2023'."""
    return f"{MES_ABBR_PT_INV[data.month]}/{data.year}"

# Limiar de "série intermitente" -- se essa proporção (ou mais) dos meses
# no histórico usado pro treino for ~zero, a série não é projetada. O
# ETS multiplicativo suaviza picos esporádicos e "preenche" os meses
# futuros de forma artificial nesses casos (ver conversa/decisão).
LIMIAR_INTERMITENCIA = 0.5

def _proporcao_meses_zerados(valores):
    valores = list(valores)
    if not valores:
        return 1.0
    zerados = sum(1 for v in valores if abs(v) <= 1)
    return zerados / len(valores)

def forecast_ets_mnm_robust(df, date_col, h=6, constant=1e-6, max_retries=2):
    """
    Mesma lógica usada no dash Brasil: ETS (MNM, sazonalidade 12), com
    compressão suave para valores projetados que ultrapassam o máximo
    histórico da série. Roda uma série por coluna (exceto date_col).
    """
    ts_columns = [col for col in df.columns if col != date_col]
    all_forecasts = []

    for col in ts_columns:
        for attempt in range(max_retries + 1):
            try:
                temp_df = df[[date_col, col]].copy()
                temp_df = temp_df.dropna()

                if len(temp_df) < 2:
                    break

                temp_df = temp_df.rename(columns={date_col: "ds", col: "y"})
                temp_df["unique_id"] = col
                temp_df["ds"] = pd.to_datetime(temp_df["ds"])

                max_historico = temp_df["y"].max()
                temp_df["y"] = temp_df["y"] + constant

                last_date_series = temp_df["ds"].max()

                model = ETS(model="MNM", season_length=12)
                sf = StatsForecast(models=[model], freq="ME", n_jobs=1)
                forecast = sf.forecast(df=temp_df, h=h)

                forecast_values = forecast["ETS"].values - constant

                forecast_ajustado = []
                for valor in forecast_values:
                    if max_historico > 0 and valor > max_historico:
                        valor_ajustado = max_historico + (max_historico * 0.1) * (
                            1 - np.exp(-(valor - max_historico) / (max_historico * 0.2))
                        )
                        forecast_ajustado.append(valor_ajustado)
                    else:
                        forecast_ajustado.append(valor)

                forecast_dates = pd.date_range(
                    start=last_date_series + pd.DateOffset(months=1), periods=h, freq="ME"
                )

                forecast_series = pd.DataFrame({
                    "ds": forecast_dates, col: forecast_ajustado, "unique_id": col
                })
                all_forecasts.append(forecast_series)
                break
            except Exception:
                if attempt == max_retries:
                    try:
                        temp_df = df[[date_col, col]].copy().dropna()
                        if len(temp_df) > 0:
                            last_date_series = pd.to_datetime(temp_df[date_col].max())
                            forecast_dates = pd.date_range(
                                start=last_date_series + pd.DateOffset(months=1), periods=h, freq="ME"
                            )
                            forecast_series = pd.DataFrame({
                                "ds": forecast_dates, col: np.full(h, np.nan), "unique_id": col
                            })
                            all_forecasts.append(forecast_series)
                    except Exception:
                        pass
                continue

    if not all_forecasts:
        return pd.DataFrame()

    all_dates = set()
    for f in all_forecasts:
        all_dates.update(f["ds"].tolist())
    all_dates = sorted(list(all_dates))
    consolidated_df = pd.DataFrame({"ds": all_dates})

    for forecast_df in all_forecasts:
        col_name = forecast_df["unique_id"].iloc[0]
        series_forecast = forecast_df[["ds", col_name]].copy()
        consolidated_df = consolidated_df.merge(series_forecast, on="ds", how="left")

    consolidated_df = consolidated_df.set_index("ds")
    return consolidated_df


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
    "**Fontes:** USITC DataWeb e Census Bureau International Trade API "
    "(dados oficiais de comércio exterior dos EUA). "
    "Consulta atual cobre apenas **Importações** (Import For Consumption), por código HTS. "
    "O gráfico de Modal de Transporte usa a Census API; os demais usam o DataWeb."
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

aggregate_commodities = False  # sempre desagregado -- ver "Totais por HTS" na tabela

st.sidebar.markdown("**Métrica(s)**")
metrica_valor = st.sidebar.checkbox("Valor (USD)", value=True, key="metrica_valor_checkbox")
metrica_quantidade = st.sidebar.checkbox("Quantidade", value=False, key="metrica_quantidade_checkbox")

periodo_tipo = st.sidebar.radio("Período", ["Anual", "Mensal"], horizontal=True)

ano_atual = dt.datetime.now().year
year_start, year_end = st.sidebar.slider(
    "Intervalo de anos",
    min_value=2010,
    max_value=ano_atual,
    value=(2020, ano_atual),
)
years = [str(y) for y in range(year_start, year_end + 1)]

countries = st.sidebar.multiselect(
    "Países de origem (opcional — vazio = todos)",
    options=sorted(COUNTRY_CODES.keys()),
    default=[],
)
aggregate_countries = False  # sempre desagregado

districts = st.sidebar.multiselect(
    "Via de entrada / distrito aduaneiro (opcional — vazio = todos)",
    options=sorted(DISTRICT_CODES.keys()),
    default=[],
)
aggregate_districts = False  # sempre desagregado

buscar = st.sidebar.button(
    "Buscar dados",
    disabled=not (metrica_valor or metrica_quantidade),
)

# --------------------------------------------------------------------
# Tokens/chaves vêm dos secrets, sem alerta visual (monitoramento é feito
# separadamente via GitHub Actions + e-mail)
# --------------------------------------------------------------------
TOKEN = st.secrets.get("DATAWEB_TOKEN")
CENSUS_API_KEY = st.secrets.get("CENSUS_API_KEY")

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

    # No modo Mensal, sempre consulta pelo menos alguns anos extras pra
    # trás -- garante que a projeção (janela fixa de 60 meses) sempre
    # tenha histórico suficiente, mesmo que o usuário peça um período
    # curto pra VISUALIZAR. Esses meses extras não aparecem na tabela
    # nem nos outros gráficos -- só alimentam o modelo de projeção.
    ANOS_MINIMOS_PARA_QUERY = 6  # ~72 meses de folga (> 60 meses exigidos)
    if monthly:
        year_start_query = min(year_start, ano_atual - ANOS_MINIMOS_PARA_QUERY)
    else:
        year_start_query = year_start
    years_query = [str(y) for y in range(year_start_query, year_end + 1)]

    with st.spinner("Consultando USITC DataWeb..."):
        query = build_import_query(
            hts_codes=hts_codes,
            years=years_query,
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
        dfs_por_medida_full = {}  # versão com o histórico extra, só para a projeção
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

                    # Remove meses que ainda NÃO aconteceram (futuros em
                    # relação a hoje) -- sem isso, eles apareceriam como
                    # "0" indistinguível de "sem comércio", contaminando
                    # tanto a tabela quanto o modelo de projeção. Meses já
                    # ENCERRADOS continuam normalmente, mesmo que estejam
                    # no "ano corrente".
                    hoje = dt.date.today()
                    ultimo_mes_completo = hoje.replace(day=1) - dt.timedelta(days=1)
                    colunas_futuras = [
                        c for c in periodo_cols_flat
                        if periodo_label_para_data(c).date() > ultimo_mes_completo
                    ]
                    if colunas_futuras:
                        df_i = df_i.drop(columns=colunas_futuras)
                        periodo_cols_flat = [c for c in periodo_cols_flat if c not in colunas_futuras]

                    # Remove meses recém-encerrados que ainda NÃO têm dado
                    # publicado na fonte (comum: a USITC/Census têm um
                    # atraso de publicação de 1-3 meses). Detecta pela
                    # cauda: se os últimos meses (adjacentes ao mais
                    # recente) estão com total zerado logo depois de meses
                    # com dado real, trata como "não publicado ainda" (não
                    # "sem comércio real") e deixa a projeção cobrir esses
                    # meses também. Limitado a 3 meses de cauda por
                    # segurança (não mexe em zeros genuínos mais antigos).
                    periodo_cols_flat_ordenado = sorted(periodo_cols_flat, key=periodo_label_para_data)
                    MAX_MESES_NAO_PUBLICADOS = 3
                    removidos = 0
                    while (
                        len(periodo_cols_flat_ordenado) > 1
                        and removidos < MAX_MESES_NAO_PUBLICADOS
                    ):
                        ultima_col = periodo_cols_flat_ordenado[-1]
                        if df_i[ultima_col].sum() == 0:
                            df_i = df_i.drop(columns=[ultima_col])
                            periodo_cols_flat_ordenado.pop()
                            removidos += 1
                        else:
                            break

                    # Guarda a versão COMPLETA (com os anos extras de
                    # "colchão") só para a projeção -- não é isso que
                    # aparece na tabela nem nos outros gráficos.
                    dfs_por_medida_full[label] = df_i.copy()

                    # Recorta para o que o usuário efetivamente pediu no
                    # filtro -- é só isso que vira tabela/gráficos normais.
                    anos_pedidos = set(years)
                    colunas_fora_do_pedido = [
                        c for c in periodo_cols_flat_ordenado
                        if periodo_label_para_data(c).strftime("%Y") not in anos_pedidos
                    ]
                    if colunas_fora_do_pedido:
                        df_i = df_i.drop(columns=colunas_fora_do_pedido)
                else:
                    dfs_por_medida_full[label] = df_i  # anual não estende nada

                dfs_por_medida[label] = df_i
        except Exception as e:
            st.error(f"Erro ao processar a resposta da API: {e}")
            st.stop()

    st.session_state["df_eua_multi"] = dfs_por_medida
    st.session_state["df_eua_multi_full"] = dfs_por_medida_full
    st.session_state["df_eua_monthly"] = monthly
    st.session_state["df_eua_years"] = years

# --------------------------------------------------------------------
# Cache da consulta à Census API (modo de transporte) -- evita rebuscar
# a cada rerun do Streamlit; só refaz a chamada se HTS/anos/chave mudarem.
# --------------------------------------------------------------------
@st.cache_data(show_spinner=False, ttl=3600)
def _fetch_census_modal_cached(
    hts_tuple, year_start_str, year_end_str, api_key,
    district_codes_tuple=None, country_codes_tuple=None,
):
    return census_trade_client.fetch_mode_of_transport_multi_hts(
        list(hts_tuple), year_start_str, year_end_str, api_key,
        district_codes=list(district_codes_tuple) if district_codes_tuple else None,
        country_codes=list(country_codes_tuple) if country_codes_tuple else None,
    )


MES_ABBR_PT = {
    "01": "Jan", "02": "Fev", "03": "Mar", "04": "Abr", "05": "Mai", "06": "Jun",
    "07": "Jul", "08": "Ago", "09": "Set", "10": "Out", "11": "Nov", "12": "Dez",
}

# --------------------------------------------------------------------
# Cache da projeção (ETS) -- evita reprocessar o modelo a cada rerun do
# Streamlit; só recalcula se as vias selecionadas ou os dados mudarem.
# --------------------------------------------------------------------
@st.cache_data(show_spinner=False)
def _forecast_cached(df_wide_json, h=6):
    df_wide = pd.read_json(df_wide_json, orient="split")
    df_wide["data"] = pd.to_datetime(df_wide["data"])
    return forecast_ets_mnm_robust(df_wide, date_col="data", h=h)


# --------------------------------------------------------------------
# Exibição
# --------------------------------------------------------------------
if "df_eua_multi" in st.session_state:
    dfs_por_medida = st.session_state["df_eua_multi"]
    dfs_por_medida_full = st.session_state.get("df_eua_multi_full", {})
    monthly = st.session_state.get("df_eua_monthly", False)
    years = st.session_state.get("df_eua_years", [])

    def periodo_cols_de(df):
        if monthly:
            return [c for c in df.columns if re.match(r"^[A-Za-zçã]{3}/\d{4}$", str(c))]
        cols = [c for c in years if c in df.columns]
        # Ano corrente com total zerado (agregação anual ainda não
        # publicada pela fonte) não aparece em nenhuma tabela/gráfico --
        # centralizado aqui, vale tanto pra tabela quanto pros gráficos.
        if str(ano_atual) in cols and df[str(ano_atual)].sum() == 0:
            cols = [c for c in cols if c != str(ano_atual)]
        return cols

    def _periodo_para_data(label):
        if monthly:
            return periodo_label_para_data(label)
        return pd.Timestamp(year=int(label), month=1, day=1)

    def calcular_projecao_hts_via(medida_label):
        """Para o modo Mensal: projeta por combinação HTS+Via de Entrada
        (somando países), usando o histórico completo (dfs_por_medida_full)
        com a mesma janela fixa de 60 meses usada nos gráficos. Retorna
        DataFrame longo com Data, [coluna HTS], [coluna Via], Tipo=
        "Projetado" e a métrica (Valor (USD)/Volume). País não aparece
        nessas linhas -- a projeção não é feita nessa granularidade."""
        if not monthly:
            return pd.DataFrame()

        df_full = dfs_por_medida_full.get(medida_label)
        if df_full is None:
            return pd.DataFrame()

        periodo_cols_full = periodo_cols_de(df_full)
        if len(periodo_cols_full) < 2:
            return pd.DataFrame()

        label_cols = [c for c in df_full.columns if c not in periodo_cols_full]

        hts_col_local = None
        for c in label_cols:
            valores = set(str(v) for v in df_full[c].dropna().unique())
            if valores and valores.issubset(set(hts_codes)):
                hts_col_local = c
                break

        via_col_local = None
        for c in label_cols:
            valores = df_full[c].dropna().astype(str)
            if len(valores) and valores.isin(DISTRICT_CODES.keys()).mean() >= 0.5:
                via_col_local = c
                break

        if not hts_col_local or not via_col_local:
            return pd.DataFrame()

        eh_medida_valor = "Quantity" not in medida_label
        nome_valor = "Valor (USD)" if eh_medida_valor else "Volume"

        df_agrupado = (
            df_full.groupby([hts_col_local, via_col_local], as_index=False)[periodo_cols_full]
            .sum(min_count=1)
        )
        df_agrupado["_serie_id"] = df_agrupado[hts_col_local] + "||" + df_agrupado[via_col_local]

        JANELA_MESES_FORECAST = 60
        periodo_cols_janela = (
            periodo_cols_full[-JANELA_MESES_FORECAST:]
            if len(periodo_cols_full) > JANELA_MESES_FORECAST
            else periodo_cols_full
        )

        df_wide = pd.DataFrame({
            "data": [periodo_label_para_data(p) for p in periodo_cols_janela]
        })
        for _, r in df_agrupado.iterrows():
            valores_janela = [r[c] for c in periodo_cols_janela]
            if _proporcao_meses_zerados(valores_janela) >= LIMIAR_INTERMITENCIA:
                continue  # série intermitente -- não projeta (ver conversa)
            df_wide[r["_serie_id"]] = valores_janela

        try:
            forecast_df = _forecast_cached(
                df_wide.to_json(orient="split", date_format="iso"), h=6
            )
        except Exception:
            return pd.DataFrame()

        linhas = []
        for serie_id in df_agrupado["_serie_id"]:
            if serie_id not in forecast_df.columns:
                continue
            serie = forecast_df[serie_id].dropna()
            # Mesma checagem usada nos gráficos: ignora projeções
            # praticamente zeradas (sem valor real a mostrar).
            if serie.empty or serie.abs().max() <= 1:
                continue
            hts_val, via_val = serie_id.split("||", 1)
            for data, valor in serie.items():
                linhas.append({
                    # statsforecast gera datas de FIM de mês (freq="ME") --
                    # normaliza pro dia 1, igual às linhas Realizado.
                    "Data": data.replace(day=1),
                    hts_col_local: hts_val,
                    via_col_local: via_val,
                    nome_valor: valor,
                    "Tipo": "Projetado",
                })

        if not linhas:
            return pd.DataFrame()
        return pd.DataFrame(linhas)

    def preparar_df_exibicao(df, medida_label):
        """Formato longo (uma linha por período), granularidade HTS + Via
        de Entrada (sem quebra por país -- fica consistente com a
        granularidade das linhas Projetado). Colunas: Data, HTS, Via de
        Entrada, Descrição, [Unidade de Medida], Valor (USD)/Volume,
        [Tipo]. Reutilizada tanto pela tabela em tela quanto pelos
        exports (Excel)."""
        periodo_cols = periodo_cols_de(df)
        eh_medida_valor = "Quantity" not in medida_label
        nome_valor = "Valor (USD)" if eh_medida_valor else "Volume"

        if not periodo_cols:
            return df.copy(), periodo_cols

        # Remove colunas de período que ficaram de fora de periodo_cols
        # (ex: ano corrente ainda vazio) -- sem isso, elas "vazam" como
        # coluna solta ao entrar em id_vars do melt lá embaixo.
        colunas_periodo_completas = (
            [c for c in df.columns if re.match(r"^[A-Za-zçã]{3}/\d{4}$", str(c))]
            if monthly else [c for c in years if c in df.columns]
        )
        colunas_a_descartar = [c for c in colunas_periodo_completas if c not in periodo_cols]
        df_base = df.drop(columns=colunas_a_descartar, errors="ignore") if colunas_a_descartar else df

        label_cols = [c for c in df_base.columns if c not in periodo_cols]

        hts_col = None
        for c in label_cols:
            valores = set(str(v) for v in df_base[c].dropna().unique())
            if valores and valores.issubset(set(hts_codes)):
                hts_col = c
                break

        via_col_local = None
        for c in label_cols:
            valores = df_base[c].dropna().astype(str)
            if len(valores) and valores.isin(DISTRICT_CODES.keys()).mean() >= 0.5:
                via_col_local = c
                break

        desc_col = None
        if hts_col:
            if "Description" in df_base.columns:
                desc_col = "Description"
            else:
                candidatos = [c for c in label_cols if c not in (hts_col, "Quantity Description")]
                candidatos = [
                    c for c in candidatos
                    if df_base[c].dropna().astype(str).isin(COUNTRY_CODES.keys()).mean() < 0.5
                    and df_base[c].dropna().astype(str).isin(DISTRICT_CODES.keys()).mean() < 0.5
                ]
                desc_col = candidatos[0] if candidatos else None

        colunas_extra = [c for c in [desc_col] if c and c in df_base.columns]
        if not eh_medida_valor and "Quantity Description" in df_base.columns:
            colunas_extra.append("Quantity Description")

        group_cols = [c for c in [hts_col, via_col_local] if c]
        if group_cols:
            df_agg = (
                df_base.groupby(group_cols, as_index=False)
                .agg({**{c: "first" for c in colunas_extra}, **{c: "sum" for c in periodo_cols}})
            )
        else:
            soma = {c: df_base[c].sum(skipna=True) for c in periodo_cols}
            df_agg = pd.DataFrame([soma])
            for c in colunas_extra:
                df_agg[c] = None

        if "Quantity Description" in df_agg.columns:
            df_agg["Quantity Description"] = (
                df_agg["Quantity Description"].astype(str).str.replace("Value for: ", "", regex=False)
            )
            df_agg = df_agg.rename(columns={"Quantity Description": "Unidade de Medida"})

        id_vars = [c for c in df_agg.columns if c not in periodo_cols]
        df_longo = df_agg.melt(
            id_vars=id_vars, value_vars=periodo_cols,
            var_name="_Periodo", value_name=nome_valor,
        )
        df_longo["Data"] = df_longo["_Periodo"].map(_periodo_para_data)
        df_longo = df_longo.drop(columns=["_Periodo"])

        if monthly:
            df_longo["Tipo"] = "Realizado"
            df_proj = calcular_projecao_hts_via(medida_label)
            if not df_proj.empty:
                # Preenche a Descrição das linhas projetadas via mapa
                # HTS -> Descrição (constante por HTS, então dá pra
                # reaproveitar mesmo sem ter sido calculado por linha).
                if hts_col and desc_col and hts_col in df_proj.columns:
                    mapa_desc = (
                        df_agg.dropna(subset=[hts_col])
                        .drop_duplicates(hts_col)
                        .set_index(hts_col)[desc_col]
                        .to_dict()
                    )
                    df_proj[desc_col] = df_proj[hts_col].map(mapa_desc)
                df_longo = pd.concat([df_longo, df_proj], ignore_index=True, sort=False)

        ordem = ["Data"] + [c for c in df_longo.columns if c not in ("Data", nome_valor)] + [nome_valor]
        df_longo = df_longo[ordem].sort_values("Data").reset_index(drop=True)

        return df_longo, periodo_cols

    def preparar_df_totais(df, medida_label):
        """Tabela resumo por HTS (soma de país+via de entrada), em
        formato longo. Mantém a descrição do produto e, em Quantidade, a
        unidade de medida."""
        periodo_cols = periodo_cols_de(df)
        eh_medida_valor = "Quantity" not in medida_label
        nome_valor = "Valor (USD)" if eh_medida_valor else "Volume"

        if not periodo_cols:
            return pd.DataFrame()

        colunas_periodo_completas = (
            [c for c in df.columns if re.match(r"^[A-Za-zçã]{3}/\d{4}$", str(c))]
            if monthly else [c for c in years if c in df.columns]
        )
        colunas_a_descartar = [c for c in colunas_periodo_completas if c not in periodo_cols]
        df_base = df.drop(columns=colunas_a_descartar, errors="ignore") if colunas_a_descartar else df

        label_cols = [c for c in df_base.columns if c not in periodo_cols]

        hts_col = None
        for c in label_cols:
            valores = set(str(v) for v in df_base[c].dropna().unique())
            if valores and valores.issubset(set(hts_codes)):
                hts_col = c
                break

        # Coluna de descrição do produto (mesma lógica usada para os
        # rótulos de HTS no seletor dos gráficos).
        desc_col = None
        if hts_col:
            if "Description" in df_base.columns:
                desc_col = "Description"
            else:
                candidatos = [c for c in label_cols if c not in (hts_col, "Quantity Description")]
                candidatos = [
                    c for c in candidatos
                    if df_base[c].dropna().astype(str).isin(COUNTRY_CODES.keys()).mean() < 0.5
                    and df_base[c].dropna().astype(str).isin(DISTRICT_CODES.keys()).mean() < 0.5
                ]
                desc_col = candidatos[0] if candidatos else None

        if hts_col:
            colunas_extra = [c for c in [desc_col] if c and c in df_base.columns]
            if not eh_medida_valor and "Quantity Description" in df_base.columns:
                colunas_extra.append("Quantity Description")
            df_tot = (
                df_base.groupby(hts_col, as_index=False)
                .agg({
                    **{c: "first" for c in colunas_extra},
                    **{c: "sum" for c in periodo_cols},
                })
            )
            ordem_wide = [hts_col] + colunas_extra + periodo_cols
            df_tot = df_tot[ordem_wide]
        else:
            soma = {c: df_base[c].sum(skipna=True) for c in periodo_cols}
            df_tot = pd.DataFrame([soma])

        if "Quantity Description" in df_tot.columns:
            df_tot["Quantity Description"] = (
                df_tot["Quantity Description"]
                .astype(str)
                .str.replace("Value for: ", "", regex=False)
            )
            df_tot = df_tot.rename(columns={"Quantity Description": "Unidade de Medida"})

        # Melt pra formato longo.
        id_vars = [c for c in df_tot.columns if c not in periodo_cols]
        df_longo = df_tot.melt(
            id_vars=id_vars, value_vars=periodo_cols,
            var_name="_Periodo", value_name=nome_valor,
        )
        df_longo["Data"] = df_longo["_Periodo"].map(_periodo_para_data)
        df_longo = df_longo.drop(columns=["_Periodo"])
        if monthly:
            df_longo["Tipo"] = "Realizado"

            df_proj_hts_via = calcular_projecao_hts_via(medida_label)
            if not df_proj_hts_via.empty and hts_col:
                # Agrega a projeção HTS+Via por HTS (soma as vias) --
                # mesma granularidade desta tabela resumo.
                df_proj_hts = (
                    df_proj_hts_via.groupby(["Data", hts_col], as_index=False)[nome_valor]
                    .sum()
                )
                df_proj_hts["Tipo"] = "Projetado"
                if desc_col:
                    mapa_desc = (
                        df_tot.dropna(subset=[hts_col])
                        .drop_duplicates(hts_col)
                        .set_index(hts_col)[desc_col]
                        .to_dict()
                    )
                    df_proj_hts[desc_col] = df_proj_hts[hts_col].map(mapa_desc)
                df_longo = pd.concat([df_longo, df_proj_hts], ignore_index=True, sort=False)

        ordem = ["Data"] + [c for c in df_longo.columns if c not in ("Data", nome_valor)] + [nome_valor]
        df_longo = df_longo[ordem].sort_values("Data").reset_index(drop=True)

        return df_longo

    def renderizar_medida(df, medida_label, tab_key, df_exibicao, periodo_cols, df_totais=None, excel_buffer=None):
        eh_medida_valor = "Quantity" not in medida_label
        nome_valor = "Valor (USD)" if eh_medida_valor else "Volume"
        formato_data = "YYYY-MM-DD" if monthly else "YYYY"

        if df_totais is not None and not df_totais.empty:
            st.markdown("**Totais por HTS**")
            st.dataframe(
                df_totais,
                use_container_width=True,
                column_config={
                    nome_valor: st.column_config.NumberColumn(format="localized"),
                    "Data": st.column_config.DateColumn(format=formato_data),
                },
            )
            st.markdown("**Detalhe (por HTS e Via de Entrada)**")

        st.success(f"{len(df_exibicao)} linha(s) retornada(s).")
        st.dataframe(
            df_exibicao,
            use_container_width=True,
            column_config={
                nome_valor: st.column_config.NumberColumn(format="localized"),
                "Data": st.column_config.DateColumn(format=formato_data),
            },
        )

        # Excel (Totais + Detalhe, abas separadas) -- substitui o CSV, já
        # que agora sempre há pelo menos duas tabelas por medida.
        if excel_buffer is not None:
            st.download_button(
                "⬇️ Baixar Excel (Totais + Detalhe)",
                excel_buffer,
                "comex_eua_dados.xlsx",
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                key=f"{tab_key}_excel_download",
            )

        if not periodo_cols:
            return

    # Monta o Excel único (Totais + Detalhe por medida). Sempre em Excel,
    # nunca mais CSV -- com 1 medida selecionada, tem 2 abas; com as 2
    # medidas, tem 4 abas.
    import io
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        for label, df in dfs_por_medida.items():
            df_exibicao_tmp, _ = preparar_df_exibicao(df, label)
            df_totais_tmp = preparar_df_totais(df, label)
            nome_base = label_pt(label)
            if not df_totais_tmp.empty:
                df_totais_tmp.to_excel(writer, sheet_name=f"{nome_base} - Totais"[:31], index=False)
            df_exibicao_tmp.to_excel(writer, sheet_name=f"{nome_base} - Detalhe"[:31], index=False)
    excel_bytes = buffer.getvalue()

    if len(dfs_por_medida) > 1:
        tabs = st.tabs([label_pt(label) for label in dfs_por_medida.keys()])
        for tab, (label, df) in zip(tabs, dfs_por_medida.items()):
            with tab:
                df_exibicao, periodo_cols = preparar_df_exibicao(df, label)
                df_totais = preparar_df_totais(df, label)
                renderizar_medida(
                    df, label, tab_key=re.sub(r"\W+", "_", label.lower()),
                    df_exibicao=df_exibicao, periodo_cols=periodo_cols,
                    df_totais=df_totais, excel_buffer=excel_bytes,
                )
    else:
        label, df = next(iter(dfs_por_medida.items()))
        df_exibicao, periodo_cols = preparar_df_exibicao(df, label)
        df_totais = preparar_df_totais(df, label)
        renderizar_medida(
            df, label, tab_key=re.sub(r"\W+", "_", label.lower()),
            df_exibicao=df_exibicao, periodo_cols=periodo_cols,
            df_totais=df_totais, excel_buffer=excel_bytes,
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

        # combo_id identifica a combinação atual (métrica + HTS escolhido)
        # -- definido aqui, ANTES de qualquer "if via_col:"/"if country_col:",
        # para nunca dar erro quando um dos dois não existir nos dados.
        combo_id = re.sub(r"\W+", "_", f"{metrica_grafico}_{hts_escolhido or 'total'}".lower())

        def legenda_unidade_hts():
            """Mostra, embaixo do título de cada gráfico, qual HTS está
            sendo exibido (quando um HTS específico foi escolhido, não o
            Total) e, numa linha abaixo, a unidade de medida (quando
            Quantidade)."""
            linhas = []
            if hts_escolhido is not None:
                linhas.append(f"HTS exibido: {hts_labels.get(hts_escolhido, hts_escolhido)}")
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
            for linha in linhas:
                st.markdown(
                    f"<p style='text-align:center; font-size:0.85rem; color:#666; margin:0;'>"
                    f"{linha}</p>",
                    unsafe_allow_html=True,
                )

        titulo_grafico1 = (
            f"Volume Importado em {metrica_grafico}: Realizado vs Projetado"
            if monthly else
            f"Volume Importado em {metrica_grafico}: Realizado"
        )
        st.markdown(
            f"""
            <h2 style='text-align:center; color:#042373; font-family:Arial; font-weight:bold;'>
                {titulo_grafico1}
            </h2>
            """,
            unsafe_allow_html=True,
        )
        legenda_unidade_hts()
        if not monthly:
            texto_aviso_anual = (
                "Projeção disponível apenas no modo **Mensal** -- troque o "
                "período no filtro para ver a projeção (barras verdes) além "
                "do realizado."
            )
            if str(ano_atual) in years:
                texto_aviso_anual += (
                    f"  \n⚠️ O ano {ano_atual} está incluído no intervalo "
                    "selecionado e ainda não terminou -- como o total anual "
                    "completo ainda não está disponível na fonte, esse ano "
                    "não aparece nos gráficos abaixo até fechar."
                )
            st.info(texto_aviso_anual)

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

            # Detecta a coluna de via de entrada/distrito para quebrar os gráficos
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
                # Top 5 padrão só entre vias com dado de verdade no período
                # -- sem isso, uma via zerada podia "completar a lista" só
                # porque não havia 5 vias com movimento suficiente.
                vias_com_total_real = df_via[df_via["_total"] > 0][via_col].tolist()
                top5_default = sorted(vias_com_total_real[:5])
                todas_vias_alfa = sorted(todas_vias)

                ms_key = f"vias_grafico_multiselect_{combo_id}"
                reset_flag_key = f"vias_grafico_reset_flag_{combo_id}"
                if st.session_state.get(reset_flag_key):
                    st.session_state[ms_key] = top5_default
                    st.session_state[reset_flag_key] = False

                col_label, col_btn = st.columns([5, 1])
                with col_label:
                    st.markdown("**Vias de Entrada exibidas**")
                with col_btn:
                    if st.button("🔝 Restaurar Top 5", key=f"vias_grafico_reset_btn_{combo_id}", use_container_width=True):
                        st.session_state[reset_flag_key] = True
                        st.rerun()

                vias_selecionadas = st.multiselect(
                    "Vias de Entrada exibidas",
                    options=todas_vias_alfa,
                    default=top5_default,
                    key=ms_key,
                    label_visibility="collapsed",
                )

                if not vias_selecionadas:
                    st.info("Selecione ao menos uma via de entrada para exibir os gráficos.")
                else:
                    # Projeção só faz sentido no Mensal, e só quando a
                    # janela visível vai até o último período real
                    # (senão o usuário está olhando um recorte histórico,
                    # e mostrar "Projetado" ali confundiria mais que ajudaria).
                    mostrar_projecao = (
                        monthly
                        and periodo_visivel
                        and periodo_visivel[-1] == periodo_cols[-1]
                        and len(periodo_cols) >= 6  # mínimo de histórico para o modelo
                    )

                    forecast_por_via = {}
                    if mostrar_projecao:
                        try:
                            # Usa a versão COMPLETA (com o "colchão" de anos
                            # extras buscado na consulta) para montar a
                            # janela de treino -- garante ~72 meses
                            # disponíveis, sempre folgado acima dos 60
                            # exigidos, independente do que o usuário
                            # escolheu no filtro "Intervalo de anos" (que só
                            # controla o que é EXIBIDO).
                            df_fonte_full = dfs_por_medida_full.get(chave_medida)
                            df_fonte_grafico_full = df_fonte_full
                            if df_fonte_full is not None and hts_escolhido is not None and hts_col:
                                df_fonte_grafico_full = df_fonte_full[df_fonte_full[hts_col] == hts_escolhido]

                            JANELA_MESES_FORECAST = 60
                            if df_fonte_grafico_full is not None:
                                periodo_cols_full = periodo_cols_de(df_fonte_grafico_full)
                                df_via_full = (
                                    df_fonte_grafico_full.groupby(via_col, as_index=False)[periodo_cols_full]
                                    .sum(min_count=1)
                                )
                            else:
                                # fallback (não deveria acontecer) -- usa a
                                # versão visível mesmo, com o aviso de sempre.
                                periodo_cols_full = periodo_cols
                                df_via_full = df_via

                            periodo_cols_forecast = (
                                periodo_cols_full[-JANELA_MESES_FORECAST:]
                                if len(periodo_cols_full) > JANELA_MESES_FORECAST
                                else periodo_cols_full
                            )

                            if len(periodo_cols_full) < JANELA_MESES_FORECAST:
                                st.info(
                                    f"Esta consulta tem só {len(periodo_cols_full)} meses de "
                                    f"histórico (ideal: {JANELA_MESES_FORECAST}, ~5 anos). "
                                    "A projeção usa todo o histórico disponível, mas pode "
                                    "sair diferente de uma consulta com mais anos no filtro "
                                    "-- amplie o **Intervalo de anos** na barra lateral para "
                                    "obter uma previsão consistente entre diferentes consultas."
                                )

                            df_wide_forecast = pd.DataFrame({
                                "data": [periodo_label_para_data(p) for p in periodo_cols_forecast]
                            })
                            vias_intermitentes = []
                            for via in vias_selecionadas:
                                linhas_via = df_via_full[df_via_full[via_col] == via]
                                if linhas_via.empty:
                                    continue
                                row = linhas_via.iloc[0]
                                valores_janela = [row.get(c, 0) for c in periodo_cols_forecast]
                                if _proporcao_meses_zerados(valores_janela) >= LIMIAR_INTERMITENCIA:
                                    vias_intermitentes.append(via)
                                    continue
                                df_wide_forecast[via] = valores_janela

                            if vias_intermitentes:
                                st.caption(
                                    "⚠️ Sem projeção para: **" + "**, **".join(vias_intermitentes) +
                                    "** -- histórico com muitos meses sem comércio (padrão "
                                    "intermitente/esporádico), onde este modelo de projeção "
                                    "não é confiável."
                                )

                            forecast_df = _forecast_cached(
                                df_wide_forecast.to_json(orient="split", date_format="iso"),
                                h=6,
                            )
                            for via in vias_selecionadas:
                                if via in forecast_df.columns:
                                    serie = forecast_df[via].dropna()
                                    if not serie.empty:
                                        forecast_por_via[via] = serie
                        except Exception:
                            mostrar_projecao = False

                    # Legenda Realizado/Projetado -- só quando existe de
                    # fato ao menos uma via com projeção pra mostrar.
                    if monthly and any(len(s) > 0 for s in forecast_por_via.values()):
                        st.markdown(
                            "<div style='display:flex; gap:24px; align-items:center; "
                            "margin:4px 0 12px 0; font-size:0.9rem; color:#31333F;'>"
                            "<span><span style='display:inline-block; width:12px; height:12px; "
                            "background:blue; border-radius:2px; margin-right:6px;'></span>Realizado</span>"
                            "<span><span style='display:inline-block; width:12px; height:12px; "
                            "background:green; border-radius:2px; margin-right:6px;'></span>Projetado</span>"
                            "</div>",
                            unsafe_allow_html=True,
                        )

                    # Vias sem NENHUM dado no período visível e sem
                    # projeção (histórico insuficiente/esparso para o
                    # modelo) não geram gráfico vazio -- são filtradas
                    # antes de montar a grade, pra não sobrar espaço em branco.
                    vias_com_conteudo = []
                    for via in vias_selecionadas:
                        row_check = df_via[df_via[via_col] == via].iloc[0]
                        valores_check = [row_check[c] for c in periodo_visivel]
                        tem_dado_real = any((v not in (0, None) and not pd.isna(v)) for v in valores_check)
                        # Não basta a série existir -- precisa ter algum
                        # valor de fato diferente de zero (senão uma
                        # projeção "toda zero" passava no filtro igual).
                        tem_projecao_check = (
                            via in forecast_por_via
                            and len(forecast_por_via[via]) > 0
                            and forecast_por_via[via].abs().max() > 1
                        )
                        if tem_dado_real or (monthly and tem_projecao_check):
                            vias_com_conteudo.append(via)

                    if not vias_com_conteudo:
                        st.info(
                            "Nenhuma das vias de entrada selecionadas tem dado "
                            "(real ou projetado) no período visível."
                        )

                    cols_por_linha = 2
                    for i in range(0, len(vias_com_conteudo), cols_por_linha):
                        cols = st.columns(cols_por_linha)
                        for j, via in enumerate(vias_com_conteudo[i:i + cols_por_linha]):
                            with cols[j]:
                                row = df_via[df_via[via_col] == via].iloc[0]
                                valores = [row[c] for c in periodo_visivel]

                                fig = go.Figure()
                                fig.add_trace(
                                    go.Bar(
                                        x=periodo_visivel, y=valores,
                                        name="Realizado", marker_color="blue",
                                        hovertemplate="%{x}<br>%{y:,.0f}<extra></extra>",
                                    )
                                )
                                if monthly:
                                    serie_proj = forecast_por_via.get(via)
                                    if serie_proj is not None and len(serie_proj) > 0:
                                        periodos_proj = [data_para_periodo_label(d) for d in serie_proj.index]
                                        valores_proj = serie_proj.tolist()
                                        fig.add_trace(
                                            go.Bar(
                                                x=periodos_proj, y=valores_proj,
                                                name="Projetado", marker_color="green",
                                                hovertemplate="%{x}<br>%{y:,.0f}<extra></extra>",
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
                # Gráfico 2 -- todas as vias de entrada combinadas num único gráfico
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
                    st.markdown("**Vias de Entrada exibidas**")
                with col_btn2:
                    if st.button("🔝 Restaurar Top 5", key=f"vias_grafico2_reset_btn_{combo_id}", use_container_width=True):
                        st.session_state[reset_flag_key2] = True
                        st.rerun()

                vias_selecionadas2 = st.multiselect(
                    "Vias de Entrada exibidas neste gráfico",
                    options=todas_vias_alfa,
                    default=top5_default,
                    key=ms_key2,
                    label_visibility="collapsed",
                    max_selections=MAX_VIAS_GRAFICO2,
                )
                st.caption(
                    f"Máximo de {MAX_VIAS_GRAFICO2} vias de entrada por vez neste gráfico, "
                    "para manter as cores e a leitura claras."
                )

                if not vias_selecionadas2:
                    st.info("Selecione ao menos uma via de entrada para exibir o gráfico.")
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
                        legend_title="Via de Entrada",
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
                    "Nenhuma quebra por via de entrada nos dados retornados para "
                    "esta consulta -- isso costuma acontecer quando os filtros "
                    "aplicados (país, HTS ou via de entrada) resultam em uma única via "
                    "de entrada, ou "
                    "quando nenhuma via de entrada específica foi selecionada e "
                    "há poucos registros no período. Tente ampliar o intervalo "
                    "de anos ou os países/HTS selecionados."
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

                        # Barras horizontais, maior no topo -- por isso a
                        # ordem é invertida antes de plotar (Plotly desenha
                        # a primeira categoria embaixo por padrão).
                        labels_h = labels[::-1]
                        percentuais_h = percentuais[::-1]
                        cores_h = cores[::-1]

                        fig3 = go.Figure(
                            go.Bar(
                                x=percentuais_h,
                                y=labels_h,
                                orientation="h",
                                marker_color=cores_h,
                                text=[f"{p:.1f}%" for p in percentuais_h],
                                textposition="outside",
                                cliponaxis=False,
                                hovertemplate="%{y}<br>%{x:.2f}%<extra></extra>",
                            )
                        )
                        fig3.update_layout(
                            xaxis_title="% do total importado",
                            xaxis=dict(range=[0, max(percentuais_h) * 1.2]),
                            yaxis_title="País",
                            yaxis=dict(categoryorder="array", categoryarray=labels_h),
                            plot_bgcolor="#DBF7FF",
                            paper_bgcolor="white",
                            height=500,
                            showlegend=False,
                            margin=dict(t=50, b=50, l=50, r=90),
                        )
                        fig3.update_xaxes(showline=True, linewidth=2, linecolor="#042373", mirror=True)
                        fig3.update_yaxes(showline=True, linewidth=2, linecolor="#042373", mirror=True)
                        st.plotly_chart(fig3, use_container_width=True, key=f"grafico3_pct_pais_{combo_id}")
                else:
                    st.info("Sem valores no período selecionado para calcular percentuais.")
            else:
                st.info(
                    "Nenhuma quebra por país nos dados retornados para esta "
                    "consulta -- isso costuma acontecer quando apenas um país "
                    "foi selecionado no filtro (não há o que separar) ou quando "
                    "há poucos registros no período. Selecione mais de um país "
                    "de origem, ou deixe o filtro vazio para trazer todos, e "
                    "tente novamente."
                )

            # ------------------------------------------------------------
            # Gráfico 4 -- porcentagem por país, um mini-gráfico por via de entrada
            # (top 5 países + "Outros" fixo por via de entrada -- sem seletor de país
            # aqui; o que é ajustável é QUAIS VIAS aparecem).
            # ------------------------------------------------------------
            st.divider()
            st.markdown(
                f"""
                <h2 style='text-align:center; color:#042373; font-family:Arial; font-weight:bold;'>
                    Porcentagem por País de {metrica_grafico} (Realizado) Importado Separado por Via de Entrada
                </h2>
                """,
                unsafe_allow_html=True,
            )
            legenda_unidade_hts()

            if via_col and country_col:
                if len(periodo_cols) > 1:
                    periodo4_inicio, periodo4_fim = st.select_slider(
                        "Período considerado neste gráfico",
                        options=periodo_cols,
                        value=(periodo_cols[0], periodo_cols[-1]),
                        key=f"periodo_slicer_grafico4_{combo_id}",
                    )
                    idx4_ini = periodo_cols.index(periodo4_inicio)
                    idx4_fim = periodo_cols.index(periodo4_fim)
                    periodo_visivel4 = periodo_cols[idx4_ini: idx4_fim + 1]
                else:
                    periodo_visivel4 = periodo_cols

                ms_key4 = f"vias_grafico4_multiselect_{combo_id}"
                reset_flag_key4 = f"vias_grafico4_reset_flag_{combo_id}"
                if st.session_state.get(reset_flag_key4):
                    st.session_state[ms_key4] = top5_default
                    st.session_state[reset_flag_key4] = False

                col_label4, col_btn4 = st.columns([5, 1])
                with col_label4:
                    st.markdown("**Vias de Entrada exibidas (cada uma vira um mini-gráfico)**")
                with col_btn4:
                    if st.button("🔝 Restaurar Top 5", key=f"vias_grafico4_reset_btn_{combo_id}", use_container_width=True):
                        st.session_state[reset_flag_key4] = True
                        st.rerun()

                vias_selecionadas4 = st.multiselect(
                    "Vias de Entrada exibidas neste gráfico",
                    options=todas_vias_alfa,
                    default=top5_default,
                    key=ms_key4,
                    label_visibility="collapsed",
                    max_selections=8,
                )
                st.caption("Máximo de 8 vias de entrada por vez (cada uma gera um mini-gráfico).")

                if not vias_selecionadas4:
                    st.info("Selecione ao menos uma via de entrada para exibir os gráficos.")
                else:
                    paleta4 = pcolors.qualitative.Alphabet
                    cols_por_linha4 = 2
                    for i in range(0, len(vias_selecionadas4), cols_por_linha4):
                        cols4 = st.columns(cols_por_linha4)
                        for j, via in enumerate(vias_selecionadas4[i:i + cols_por_linha4]):
                            with cols4[j]:
                                st.markdown(f"**Via de Entrada: {via}**")

                                df_via_pais = df_fonte_grafico[df_fonte_grafico[via_col] == via]
                                df_via_pais = (
                                    df_via_pais.groupby(country_col, as_index=False)[periodo_visivel4]
                                    .sum(min_count=1)
                                )
                                df_via_pais["_valor"] = df_via_pais[periodo_visivel4].sum(axis=1, skipna=True)
                                df_via_pais = df_via_pais.sort_values("_valor", ascending=False)
                                total_via = df_via_pais["_valor"].sum()

                                if not total_via or total_via <= 0:
                                    st.info("Sem dados nessa via de entrada no período selecionado.")
                                    continue

                                top5_via = df_via_pais.head(5)
                                resto_via = df_via_pais["_valor"].iloc[5:].sum()

                                labels4 = top5_via[country_col].tolist()
                                valores4 = top5_via["_valor"].tolist()
                                if resto_via > 0:
                                    labels4.append("Outros")
                                    valores4.append(resto_via)

                                percentuais4 = [v / total_via * 100 for v in valores4]
                                cores4 = [paleta4[k % len(paleta4)] for k in range(len(labels4))]
                                if "Outros" in labels4:
                                    cores4[labels4.index("Outros")] = "#E4572E"

                                labels4_h = labels4[::-1]
                                percentuais4_h = percentuais4[::-1]
                                cores4_h = cores4[::-1]

                                fig4 = go.Figure(
                                    go.Bar(
                                        x=percentuais4_h,
                                        y=labels4_h,
                                        orientation="h",
                                        marker_color=cores4_h,
                                        text=[f"{p:.1f}%" for p in percentuais4_h],
                                        textposition="outside",
                                cliponaxis=False,
                                        hovertemplate="%{y}<br>%{x:.2f}%<extra></extra>",
                                    )
                                )
                                fig4.update_layout(
                                    xaxis_title="% do total nessa via de entrada",
                                    xaxis=dict(range=[0, max(percentuais4_h) * 1.2]),
                                    yaxis=dict(categoryorder="array", categoryarray=labels4_h),
                                    plot_bgcolor="#DBF7FF",
                                    paper_bgcolor="white",
                                    height=350,
                                    showlegend=False,
                                    margin=dict(t=30, b=40, l=100, r=70),
                                )
                                fig4.update_xaxes(showline=True, linewidth=2, linecolor="#042373", mirror=True)
                                fig4.update_yaxes(showline=True, linewidth=2, linecolor="#042373", mirror=True)
                                chart4_key = "grafico4_" + re.sub(r"\W+", "_", str(via).lower()) + f"_{combo_id}"
                                st.plotly_chart(fig4, use_container_width=True, key=chart4_key)
            else:
                st.info(
                    "Este gráfico exige quebra por país E por via de entrada ao "
                    "mesmo tempo nos dados retornados -- selecione mais de um "
                    "país e mais de uma via de entrada nos filtros (ou deixe ambos vazios "
                    "para trazer todos) e tente novamente."
                )

            # ------------------------------------------------------------
            # Gráfico 4.5 -- volume por MODAL DE TRANSPORTE (Aéreo, Marítimo,
            # Terrestre) -- fonte diferente (Census Bureau International
            # Trade API), só disponível para Valor (USD). Terrestre é
            # calculado por diferença (GEN - AIR - VES), validado
            # manualmente via Postman (ver histórico da conversa).
            # ------------------------------------------------------------
            st.divider()
            st.markdown(
                """
                <h2 style='text-align:center; color:#042373; font-family:Arial; font-weight:bold;'>
                    Volume total transacionado em Valor (USD) (Realizado) por Modal de Transporte
                </h2>
                <p style='text-align:center; font-size:0.85rem; color:#666; margin:0;'>
                    Fonte: Census Bureau International Trade API -- Aéreo e Marítimo
                    diretos da fonte; Terrestre (rodoviário/ferroviário) calculado
                    por diferença em relação ao total.
                </p>
                """,
                unsafe_allow_html=True,
            )

            if metrica_grafico == "Quantidade":
                st.info(
                    "Modal de transporte só está disponível para **Valor (USD)** -- "
                    "a Census API não expõe quantidade quebrada por modal."
                )
            elif not CENSUS_API_KEY:
                st.info(
                    "Configure `CENSUS_API_KEY` em st.secrets para habilitar este "
                    "gráfico (chave gratuita: https://api.census.gov/data/key_signup.html)."
                )
            else:
                hts_para_buscar = [hts_escolhido] if hts_escolhido else hts_codes

                # Legenda de HTS exibido, embaixo da fonte -- mesmo padrão
                # usado nos demais gráficos (legenda_unidade_hts): só
                # aparece quando um HTS específico foi escolhido (não o
                # Total nem quando há só um HTS na consulta).
                if hts_escolhido is not None:
                    hts_texto_modal = f"HTS exibido: {hts_labels.get(hts_escolhido, hts_escolhido)}"
                    st.markdown(
                        f"<p style='text-align:center; font-size:0.85rem; color:#666; margin:0 0 12px 0;'>"
                        f"{hts_texto_modal}</p>",
                        unsafe_allow_html=True,
                    )

                # Filtros exclusivos deste gráfico -- país e via de entrada,
                # independentes dos filtros globais da barra lateral. Vazio
                # (padrão) = sem filtro, mesmo comportamento dos filtros da
                # barra lateral -- não vem pré-preenchido com tudo marcado.
                # Opções restritas ao que já apareceu nos dados do DataWeb
                # para esse(s) HTS (evita oferecer país/via sem nenhum
                # comércio registrado, que sempre daria "sem dados" -- ou
                # pior, deixaria a consulta lenta à toa na Census API, que
                # é uma fonte diferente da base usada nos outros gráficos).
                if country_col:
                    paises_modal_opcoes = sorted(
                        v for v in df_fonte_grafico[country_col].dropna().unique()
                        if v in COUNTRY_CODES
                    )
                else:
                    paises_modal_opcoes = sorted(COUNTRY_CODES.keys())

                if via_col:
                    vias_modal_opcoes = sorted(
                        v for v in df_fonte_grafico[via_col].dropna().unique()
                        if v in DISTRICT_CODES
                    )
                else:
                    vias_modal_opcoes = sorted(DISTRICT_CODES.keys())

                col_fp, col_fv = st.columns(2)
                with col_fp:
                    paises_modal_sel = st.multiselect(
                        "Países considerados neste gráfico (opcional — vazio = todos)",
                        options=paises_modal_opcoes,
                        default=[],
                        key=f"paises_modal_{combo_id}",
                    )
                with col_fv:
                    vias_modal_sel = st.multiselect(
                        "Vias de entrada consideradas neste gráfico (opcional — vazio = todas)",
                        options=vias_modal_opcoes,
                        default=[],
                        key=f"vias_modal_{combo_id}",
                    )

                country_codes_census = [COUNTRY_CODES[c] for c in paises_modal_sel] or None
                district_codes_census = [DISTRICT_CODES[d] for d in vias_modal_sel] or None

                with st.spinner("Consultando Census Bureau International Trade API..."):
                    try:
                        df_census = _fetch_census_modal_cached(
                            tuple(hts_para_buscar), str(year_start), str(year_end), CENSUS_API_KEY,
                            tuple(district_codes_census) if district_codes_census else None,
                            tuple(country_codes_census) if country_codes_census else None,
                        )
                    except Exception as e:
                        df_census = None
                        erro_txt = str(e).lower()
                        if "timed out" in erro_txt or "timeout" in erro_txt:
                            st.warning(
                                "A Census API demorou demais para responder (timeout). "
                                "Isso pode acontecer em consultas maiores (vários HTS "
                                "ou muitos anos de uma vez). Tente reduzir o intervalo "
                                "de anos ou tente novamente em instantes."
                            )
                        else:
                            st.warning(
                                "Não foi possível consultar a Census API agora. "
                                "Tente novamente em instantes."
                            )
                        with st.expander("Detalhes técnicos do erro"):
                            st.code(str(e))

                if df_census is not None and df_census.empty:
                    st.info(
                        "Sem dados de modal de transporte para essa combinação de "
                        "HTS, período, país e/ou via de entrada. Isso pode acontecer "
                        "porque: (1) o HTS foi revisado na classificação usada por "
                        "essa fonte, ou (2) o país/via filtrado não teve comércio "
                        "registrado nesse recorte. Tente ampliar os filtros, o "
                        "intervalo de anos, ou usar outro HTS."
                    )
                elif df_census is not None:
                    if monthly:
                        df_census["_periodo_label"] = (
                            df_census["Mes_Num"].map(MES_ABBR_PT) + "/" + df_census["Ano"]
                        )
                        df_census["_periodo_sort"] = df_census["Ano"] + df_census["Mes_Num"]
                    else:
                        df_census["_periodo_label"] = df_census["Ano"]
                        df_census["_periodo_sort"] = df_census["Ano"]

                    df_modal = (
                        df_census.groupby(["_periodo_label", "_periodo_sort"], as_index=False)[
                            ["Valor Aereo", "Valor Maritimo", "Valor Terrestre"]
                        ]
                        .sum()
                        .sort_values("_periodo_sort")
                        .reset_index(drop=True)
                    )

                    # No Anual, mesma regra usada no resto do app: o ano
                    # corrente não aparece (agregação ainda incompleta).
                    if not monthly:
                        df_modal = df_modal[df_modal["_periodo_label"] != str(ano_atual)].reset_index(drop=True)

                    periodo_modal_cols = df_modal["_periodo_label"].tolist()

                    if len(periodo_modal_cols) > 1:
                        pm_inicio, pm_fim = st.select_slider(
                            "Período considerado neste gráfico",
                            options=periodo_modal_cols,
                            value=(periodo_modal_cols[0], periodo_modal_cols[-1]),
                            key=f"periodo_slicer_modal_{combo_id}",
                        )
                        idx_pm_ini = periodo_modal_cols.index(pm_inicio)
                        idx_pm_fim = periodo_modal_cols.index(pm_fim)
                        df_modal_visivel = df_modal.iloc[idx_pm_ini: idx_pm_fim + 1]
                    else:
                        df_modal_visivel = df_modal

                    cores_modal = {
                        "Valor Aereo": "#1CBE4F",
                        "Valor Maritimo": "#042373",
                        "Valor Terrestre": "#E4572E",
                    }
                    nomes_modal = {
                        "Valor Aereo": "Aéreo",
                        "Valor Maritimo": "Marítimo",
                        "Valor Terrestre": "Terrestre",
                    }
                    colunas_valor = ["Valor Aereo", "Valor Maritimo", "Valor Terrestre"]

                    # Modais zerados em todos os pontos visíveis não agregam
                    # informação -- tira da linha, do rótulo e do tooltip.
                    colunas_com_dado = [
                        c for c in colunas_valor if df_modal_visivel[c].fillna(0).any()
                    ]

                    if not colunas_com_dado:
                        st.info(
                            "Todos os modais de transporte estão zerados no "
                            "período/filtros selecionados."
                        )
                    else:
                        # Ordena os modais pelo total no período visível -- só
                        # define a ordem dos rótulos diretos no fim das linhas.
                        totais_modal = {col: df_modal_visivel[col].sum() for col in colunas_com_dado}
                        ordem_modal = sorted(totais_modal, key=totais_modal.get, reverse=True)

                        fig_modal = go.Figure()
                        for col in ordem_modal:
                            fig_modal.add_trace(
                                go.Scatter(
                                    x=df_modal_visivel["_periodo_label"],
                                    y=df_modal_visivel[col],
                                    mode="lines+markers",
                                    name=nomes_modal[col],
                                    line=dict(color=cores_modal[col]),
                                    showlegend=False,
                                    hoverinfo="skip",  # o tooltip combinado é a trace invisível abaixo
                                )
                            )

                        # Rótulos diretos no fim das linhas, à direita -- substituem
                        # a legenda tradicional. Calculados à parte (não dentro do
                        # loop acima) para poder afastar rótulos que ficariam
                        # sobrepostos quando os valores finais são muito próximos.
                        ultimo_x = df_modal_visivel["_periodo_label"].iloc[-1]
                        ultimos_y = {col: df_modal_visivel[col].iloc[-1] for col in colunas_com_dado}

                        y_max_eixo = max(df_modal_visivel[c].max() for c in colunas_com_dado)
                        gap_minimo = y_max_eixo * 0.07 if y_max_eixo > 0 else 1

                        # Ajusta de baixo para cima, garantindo distância mínima
                        # entre rótulos consecutivos (sem alterar a posição da
                        # própria linha -- só a do texto).
                        ordenado_por_y = sorted(ultimos_y.items(), key=lambda par: par[1])
                        y_ajustado = {}
                        y_anterior = None
                        for col, y in ordenado_por_y:
                            if y_anterior is not None and y - y_anterior < gap_minimo:
                                y = y_anterior + gap_minimo
                            y_ajustado[col] = y
                            y_anterior = y

                        for col in colunas_com_dado:
                            fig_modal.add_annotation(
                                x=ultimo_x, y=y_ajustado[col],
                                text=nomes_modal[col],
                                showarrow=False,
                                xanchor="left",
                                xshift=10,
                                font=dict(color=cores_modal[col], size=13),
                            )

                        # Trace invisível com o tooltip combinado dos modais
                        # exibidos, ordenado do maior para o menor -- por
                        # CADA ponto (mês/ano), não pela ordem geral do período.
                        hover_textos = []
                        y_topo = []
                        for _, row in df_modal_visivel.iterrows():
                            pares = sorted(
                                ((nomes_modal[c], row[c]) for c in colunas_com_dado),
                                key=lambda par: par[1],
                                reverse=True,
                            )
                            texto = f"<b>{row['_periodo_label']}</b><br>" + "<br>".join(
                                f"{nome}: {valor:,.0f}" for nome, valor in pares
                            )
                            hover_textos.append(texto)
                            y_topo.append(max(row[c] for c in colunas_com_dado))

                        fig_modal.add_trace(
                            go.Scatter(
                                x=df_modal_visivel["_periodo_label"],
                                y=y_topo,
                                mode="markers",
                                marker=dict(opacity=0, size=20),
                                hoverinfo="text",
                                hovertext=hover_textos,
                                showlegend=False,
                            )
                        )

                        fig_modal.update_layout(
                            xaxis_title="Período",
                            yaxis_title="Valor (USD)",
                            plot_bgcolor="#DBF7FF",
                            paper_bgcolor="white",
                            height=500,
                            showlegend=False,
                            hovermode="x",  # dispara o tooltip em qualquer ponto da
                                            # coluna (independente da distância vertical
                                            # até o marcador invisível)
                            margin=dict(t=40, b=50, l=50, r=110),
                        )
                        fig_modal.update_xaxes(showline=True, linewidth=2, linecolor="#042373", mirror=True)
                        fig_modal.update_yaxes(showline=True, linewidth=2, linecolor="#042373", mirror=True)
                        st.plotly_chart(fig_modal, use_container_width=True, key=f"grafico_modal_{combo_id}")

            # ------------------------------------------------------------
            # Gráfico 5 -- volume total (valor absoluto) por país, barras
            # verticais, corte por Top 95% acumulado + "Outros" (não top 5
            # fixo -- equivalente ao gráfico "Volume total transacionado
            # por País" do dash Brasil).
            # ------------------------------------------------------------
            st.divider()
            st.markdown(
                f"""
                <h2 style='text-align:center; color:#042373; font-family:Arial; font-weight:bold;'>
                    Volume total transacionado em {metrica_grafico} (Realizado) por País
                </h2>
                <p style='text-align:center; font-size:0.85rem; color:#666; margin:0;'>
                    Top 95% acumulado + "Outros"
                </p>
                """,
                unsafe_allow_html=True,
            )
            legenda_unidade_hts()

            if country_col:
                if len(periodo_cols) > 1:
                    periodo5_inicio, periodo5_fim = st.select_slider(
                        "Período considerado neste gráfico",
                        options=periodo_cols,
                        value=(periodo_cols[0], periodo_cols[-1]),
                        key=f"periodo_slicer_grafico5_{combo_id}",
                    )
                    idx5_ini = periodo_cols.index(periodo5_inicio)
                    idx5_fim = periodo_cols.index(periodo5_fim)
                    periodo_visivel5 = periodo_cols[idx5_ini: idx5_fim + 1]
                else:
                    periodo_visivel5 = periodo_cols

                df_pais5 = (
                    df_fonte_grafico.groupby(country_col, as_index=False)[periodo_visivel5]
                    .sum(min_count=1)
                )
                df_pais5["_valor"] = df_pais5[periodo_visivel5].sum(axis=1, skipna=True)
                df_pais5 = df_pais5.sort_values("_valor", ascending=False).reset_index(drop=True)

                total_geral5 = df_pais5["_valor"].sum()

                if total_geral5 and total_geral5 > 0:
                    df_pais5["_perc_acumulado"] = df_pais5["_valor"].cumsum() / total_geral5
                    candidatos_95 = df_pais5[df_pais5["_perc_acumulado"] >= 0.95].index
                    idx_limite5 = candidatos_95.min() if len(candidatos_95) else len(df_pais5) - 1

                    df_top95 = df_pais5.iloc[: idx_limite5 + 1].copy()
                    outros_valor5 = df_pais5["_valor"].iloc[idx_limite5 + 1:].sum()

                    labels5 = df_top95[country_col].tolist()
                    valores5 = df_top95["_valor"].tolist()
                    if outros_valor5 > 0:
                        labels5.append("Outros")
                        valores5.append(outros_valor5)

                    fig5 = go.Figure(
                        go.Bar(
                            x=labels5,
                            y=valores5,
                            marker_color="#042373",
                            text=[f"{v:,.0f}" for v in valores5],
                            textposition="outside",
                                cliponaxis=False,
                            hovertemplate="%{x}<br>%{y:,.0f}<extra></extra>",
                        )
                    )
                    fig5.update_layout(
                        xaxis_title="País",
                        yaxis_title=metrica_grafico,
                        xaxis=dict(categoryorder="array", categoryarray=labels5, tickangle=0),
                        yaxis=dict(range=[0, max(valores5) * 1.15]),
                        plot_bgcolor="#DBF7FF",
                        paper_bgcolor="white",
                        height=550,
                        showlegend=False,
                        margin=dict(t=60, b=100, l=60, r=40),
                    )
                    fig5.update_xaxes(showline=True, linewidth=2, linecolor="#042373", mirror=True)
                    fig5.update_yaxes(showline=True, linewidth=2, linecolor="#042373", mirror=True)
                    st.plotly_chart(fig5, use_container_width=True, key=f"grafico5_pais_{combo_id}")
                else:
                    st.info("Sem valores no período selecionado para exibir este gráfico.")
            else:
                st.info(
                    "Nenhuma quebra por país nos dados retornados para esta "
                    "consulta -- selecione mais de um país de origem, ou "
                    "deixe o filtro vazio para trazer todos, e tente novamente."
                )

            # ------------------------------------------------------------
            # Gráfico 6 -- volume total (valor absoluto) por país, um
            # mini-gráfico por via de entrada, corte Top 5 + "Outros" (mesma lógica
            # do Gráfico 4, mas com valor absoluto -- equivalente ao "Volume
            # total transacionado por País separado por URF" do Brasil).
            # Barras horizontais (diferente do Brasil, que usa verticais).
            # ------------------------------------------------------------
            st.divider()
            st.markdown(
                f"""
                <h2 style='text-align:center; color:#042373; font-family:Arial; font-weight:bold;'>
                    Volume total transacionado em {metrica_grafico} (Realizado) por País Separado por Via de Entrada
                </h2>
                <p style='text-align:center; font-size:0.85rem; color:#666; margin:0;'>
                    Top 5 + "Outros", por via de entrada
                </p>
                """,
                unsafe_allow_html=True,
            )
            legenda_unidade_hts()

            if via_col and country_col:
                if len(periodo_cols) > 1:
                    periodo6_inicio, periodo6_fim = st.select_slider(
                        "Período considerado neste gráfico",
                        options=periodo_cols,
                        value=(periodo_cols[0], periodo_cols[-1]),
                        key=f"periodo_slicer_grafico6_{combo_id}",
                    )
                    idx6_ini = periodo_cols.index(periodo6_inicio)
                    idx6_fim = periodo_cols.index(periodo6_fim)
                    periodo_visivel6 = periodo_cols[idx6_ini: idx6_fim + 1]
                else:
                    periodo_visivel6 = periodo_cols

                ms_key6 = f"vias_grafico6_multiselect_{combo_id}"
                reset_flag_key6 = f"vias_grafico6_reset_flag_{combo_id}"
                if st.session_state.get(reset_flag_key6):
                    st.session_state[ms_key6] = top5_default
                    st.session_state[reset_flag_key6] = False

                col_label6, col_btn6 = st.columns([5, 1])
                with col_label6:
                    st.markdown("**Vias de Entrada exibidas (cada uma vira um mini-gráfico)**")
                with col_btn6:
                    if st.button("🔝 Restaurar Top 5", key=f"vias_grafico6_reset_btn_{combo_id}", use_container_width=True):
                        st.session_state[reset_flag_key6] = True
                        st.rerun()

                vias_selecionadas6 = st.multiselect(
                    "Vias de Entrada exibidas neste gráfico",
                    options=todas_vias_alfa,
                    default=top5_default,
                    key=ms_key6,
                    label_visibility="collapsed",
                    max_selections=8,
                )
                st.caption("Máximo de 8 vias de entrada por vez (cada uma gera um mini-gráfico).")

                if not vias_selecionadas6:
                    st.info("Selecione ao menos uma via de entrada para exibir os gráficos.")
                else:
                    cols_por_linha6 = 2
                    for i in range(0, len(vias_selecionadas6), cols_por_linha6):
                        cols6 = st.columns(cols_por_linha6)
                        for j, via in enumerate(vias_selecionadas6[i:i + cols_por_linha6]):
                            with cols6[j]:
                                st.markdown(f"**Via de Entrada: {via}**")

                                df_via_pais6 = df_fonte_grafico[df_fonte_grafico[via_col] == via]
                                df_via_pais6 = (
                                    df_via_pais6.groupby(country_col, as_index=False)[periodo_visivel6]
                                    .sum(min_count=1)
                                )
                                df_via_pais6["_valor"] = df_via_pais6[periodo_visivel6].sum(axis=1, skipna=True)
                                df_via_pais6 = df_via_pais6.sort_values("_valor", ascending=False).reset_index(drop=True)
                                total_via6 = df_via_pais6["_valor"].sum()

                                if not total_via6 or total_via6 <= 0:
                                    st.info("Sem dados nessa via de entrada no período selecionado.")
                                    continue

                                top5_via6 = df_via_pais6.head(5)
                                outros6 = df_via_pais6["_valor"].iloc[5:].sum()

                                labels6 = top5_via6[country_col].tolist()
                                valores6 = top5_via6["_valor"].tolist()
                                if outros6 > 0:
                                    labels6.append("Outros")
                                    valores6.append(outros6)

                                # Horizontal, maior no topo -- inverte antes de plotar.
                                labels6_h = labels6[::-1]
                                valores6_h = valores6[::-1]

                                fig6 = go.Figure(
                                    go.Bar(
                                        x=valores6_h,
                                        y=labels6_h,
                                        orientation="h",
                                        marker_color="#042373",
                                        text=[f"{v:,.0f}" for v in valores6_h],
                                        textposition="outside",
                                cliponaxis=False,
                                        hovertemplate="%{y}<br>%{x:,.0f}<extra></extra>",
                                    )
                                )
                                fig6.update_layout(
                                    xaxis_title=metrica_grafico,
                                    xaxis=dict(range=[0, max(valores6_h) * 1.2]),
                                    yaxis=dict(categoryorder="array", categoryarray=labels6_h),
                                    plot_bgcolor="#DBF7FF",
                                    paper_bgcolor="white",
                                    height=350,
                                    showlegend=False,
                                    margin=dict(t=30, b=40, l=100, r=80),
                                )
                                fig6.update_xaxes(showline=True, linewidth=2, linecolor="#042373", mirror=True)
                                fig6.update_yaxes(showline=True, linewidth=2, linecolor="#042373", mirror=True)
                                chart6_key = "grafico6_" + re.sub(r"\W+", "_", str(via).lower()) + f"_{combo_id}"
                                st.plotly_chart(fig6, use_container_width=True, key=chart6_key)
            else:
                st.info(
                    "Este gráfico exige quebra por país E por via de entrada ao "
                    "mesmo tempo nos dados retornados -- selecione mais de um "
                    "país e mais de uma via de entrada nos filtros (ou deixe ambos vazios "
                    "para trazer todos) e tente novamente."
                )
