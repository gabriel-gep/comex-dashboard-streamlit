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
    build_export_query,
    truncate_hts_for_export,
    EXPORT_TRADE_TYPES,
    run_report,
    parse_report,
    response_error_message,
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

# Na exportação o DataWeb chama a medida de valor de "FAS Value" (na
# importação é "Customs Value"). O resto da página usa as chaves da
# importação, então os rótulos de exportação são convertidos para elas.
LABEL_EXPORT_PARA_CANONICO = {"FAS Value": "Customs Value"}

def label_canonico(label: str) -> str:
    return LABEL_EXPORT_PARA_CANONICO.get(label, label)

# --------------------------------------------------------------------
# Projeção (ETS) -- portada da mesma lógica usada no dash Brasil.
# --------------------------------------------------------------------
MES_NUM_PT = {
    "Jan": 1, "Fev": 2, "Mar": 3, "Abr": 4, "Mai": 5, "Jun": 6,
    "Jul": 7, "Ago": 8, "Set": 9, "Out": 10, "Nov": 11, "Dez": 12,
}
MES_ABBR_PT_INV = {v: k for k, v in MES_NUM_PT.items()}
EN_MESES = ["January", "February", "March", "April", "May", "June",
            "July", "August", "September", "October", "November", "December"]

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

# --------------------------------------------------------------------
# Fluxo: Importação ou Exportação. Todo o resto da página segue o fluxo
# escolhido. `tx()` ajusta a terminologia (origem/destino, entrada/saída,
# importado/exportado) e não altera nada no modo Importação.
# --------------------------------------------------------------------
st.sidebar.header("🔍 Filtros")
fluxo = st.sidebar.radio("Fluxo", ["Importação", "Exportação"], horizontal=True, key="fluxo_comercio")
eh_export = fluxo == "Exportação"
tipo_export_label = "Doméstica"
if eh_export:
    tipo_export_label = st.sidebar.radio(
        "Tipo de exportação",
        list(EXPORT_TRADE_TYPES.keys()),
        horizontal=True,
        key="tipo_exportacao",
        help=(
            "**Doméstica:** bens produzidos nos EUA. "
            "**Total:** doméstica + reexportações (mercadoria estrangeira "
            "importada e reexportada sem transformação relevante)."
        ),
    )
tipo_export = EXPORT_TRADE_TYPES[tipo_export_label]

_TERMOS_EXPORT = [
    ("Vias de Entrada", "Vias de Saída"), ("Via de Entrada", "Via de Saída"),
    ("Vias de entrada", "Vias de saída"), ("Via de entrada", "Via de saída"),
    ("vias de entrada", "vias de saída"), ("via de entrada", "via de saída"),
    ("Importações", "Exportações"), ("importações", "exportações"),
    ("Importado", "Exportado"), ("importado", "exportado"),
    ("Origem", "Destino"), ("origem", "destino"),
]

def tx(texto):
    """Troca a terminologia de importação pela de exportação (só no modo
    Exportação; no modo Importação devolve o texto sem alteração)."""
    if not eh_export:
        return texto
    for de, para in _TERMOS_EXPORT:
        texto = texto.replace(de, para)
    return texto

st.markdown(
    f"""
    <h1 style='text-align:center; color:#042373; font-family:Arial; font-weight:bold;'>
        Dashboard COMEX {"Exportações" if eh_export else "Importações"} - EUA
    </h1>
    """,
    unsafe_allow_html=True,
)

with st.expander("Fontes e base de valor"):
    if eh_export:
        st.markdown(
            "**Fontes:** USITC DataWeb (dados oficiais de comércio exterior dos EUA). "
            f"Consulta de **Exportações {'domésticas' if tipo_export == 'Export' else 'totais'}** "
            + ("(*Domestic Exports*: bens produzidos nos EUA)" if tipo_export == "Export"
               else "(*Total Exports*: domésticas + reexportações)")
            + ", por código HTS de até **6 dígitos** (exportações não têm nível oficial de 8 ou 10 dígitos; "
            "códigos maiores são reduzidos automaticamente aos 6 primeiros).  \n"
            "**Base de valor:** os valores em USD (*FAS Value*) são *Free Alongside Ship*: "
            "valor da mercadoria no porto de embarque nos EUA, sem frete nem seguro internacionais.  \n"
            "O gráfico de Modal de Transporte (Census API) ainda não está disponível para Exportações."
        )
    else:
        st.markdown(
            "**Fontes:** USITC DataWeb e Census Bureau International Trade API "
            "(dados oficiais de comércio exterior dos EUA). "
            "Consulta cobre **Importações** (Import For Consumption), por código HTS. "
            "O gráfico de Modal de Transporte usa a Census API; os demais usam o DataWeb.  \n"
            "**Base de valor:** os valores em USD (*Customs Value*) seguem a base FAS, "
            "equivalente a **FOB** - ou seja, sem frete, seguro nem tarifas de importação embutidos."
        )


with st.expander("Utilização do aplicativo"):
    st.markdown(tx("""
1. Escolha o **Fluxo** na barra lateral: **Importação** ou **Exportação** (doméstica ou total).
2. Informe um ou mais códigos **HTS** (Harmonized Tariff Schedule), um por linha, e aplique com **Ctrl+Enter** - pontos são removidos automaticamente.
3. Escolha a(s) métrica(s): **Valor** (USD) e/ou **Quantidade** (unidade do produto).
4. Escolha o período: **Anual** ou **Mensal** (linha do tempo contínua) e o **intervalo de anos**.
5. Depois do HTS e do intervalo de anos, aparecem os filtros opcionais, em cascata: primeiro **país(es) de origem** e em seguida **via de entrada** (porto/distrito aduaneiro). Cada lista mostra só o que tem comércio registrado para o(s) HTS, os anos e os filtros anteriores.
6. Clique em **Buscar dados** SEMPRE que quiser carregar ou atualizar as visualizações.
"""))

# --------------------------------------------------------------------
# Vias especiais do Census: não são portos reais (ver textos abaixo).
# Ficam fora do Top 5 automático, ganham um "ⓘ" ao lado do título dos
# gráficos e uma dica ao passar o mouse sobre elas nos seletores.
# --------------------------------------------------------------------
import json as _json
from html import escape as _html_escape

VIAS_ESPECIAIS_INFO = {
    "low-valued": (
        "Transações de baixo valor (até US$ 2.500 em exportações e US$ 2.000 em "
        "importações). O Census não recebe esses embarques um a um: estima o total "
        "por país com fatores históricos. Não é um porto real."
    ),
    "mail shipments": (
        "Exportações enviadas pelo correio. Não passam por um porto e esta "
        "categoria só existe em exportação."
    ),
    "vessels under their own power": (
        "Embarcações que cruzam a fronteira por meios próprios. É um agrupamento "
        "especial do Census, não um porto."
    ),
}


# --------------------------------------------------------------------
# Padrão visual dos gráficos (uma só paleta em todo o dashboard)
#   - COR_REALIZADO / COR_PROJETADO: série principal e projeção
#   - PALETA_CATEGORICA: no máx. 8 cores para separar vias/países/modais
#   - COR_OUTROS: cinza, só para "Outros"/resto
#   - COR_DESTAQUE: laranja, só para destacar algo (ex.: % acumulado)
# --------------------------------------------------------------------
COR_REALIZADO = "#042373"
COR_PROJETADO = "green"
COR_OUTROS = "#9AA5B1"
COR_DESTAQUE = "#E8590C"
PALETA_CATEGORICA = [
    "#042373", "#E8590C", "#2A9D8F", "#8E44AD",
    "#F4A300", "#5B9BD5", "#C0392B", "#6B8E23",
]


def info_via_especial(nome):
    """Texto explicativo se `nome` é uma via especial do Census; senão None."""
    n = str(nome).lower()
    for chave, texto in VIAS_ESPECIAIS_INFO.items():
        if chave in n:
            return texto
    return None


def titulo_via_html(nome, prefixo=""):
    """Título de gráfico por via, com "ⓘ" (dica ao passar o mouse) nas especiais."""
    info = info_via_especial(nome)
    icone = (
        f'<span class="info-via" data-tip="{_html_escape(info, quote=True)}">ⓘ</span>'
        if info else ""
    )
    return f'<div class="titulo-via">{_html_escape(prefixo + str(nome))}{icone}</div>'


st.markdown(
    """
    <style>
    .titulo-via { font-weight: 600; font-size: 1.05rem; color: #31333F; margin: 0.4rem 0 0.1rem 0; }
    .info-via { position: relative; display: inline-block; margin-left: 0.4rem; cursor: help;
                color: #042373; font-weight: 700; }
    .info-via:hover::after {
        content: attr(data-tip); position: absolute; left: 0; top: 1.6em; z-index: 1000;
        width: 280px; padding: 0.5rem 0.65rem; background: #042373; color: #fff;
        font-size: 0.8rem; font-weight: 400; line-height: 1.35; border-radius: 6px;
        white-space: normal;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

# Dica ao passar o mouse sobre as vias especiais nos seletores (opções da lista
# e "chips" já selecionados). O Streamlit não oferece dica por opção, então um
# pequeno script preenche o atributo `title` desses elementos.
st.html(
    "<script>(function(){"
    "const INFO = " + _json.dumps(VIAS_ESPECIAIS_INFO, ensure_ascii=False) + ";"
    "function aplicar(){"
    "document.querySelectorAll('li[role=\"option\"], [data-baseweb=\"tag\"]').forEach(function(el){"
    "const t=(el.textContent||'').toLowerCase();"
    "for (const k in INFO){ if (t.indexOf(k)>=0){ if (el.title!==INFO[k]) el.title=INFO[k]; break; } }"
    "});}"
    "if(!window.__viaInfoObs){"
    "let agendado=false;"
    "window.__viaInfoObs=new MutationObserver(function(){"
    "if(agendado) return; agendado=true;"
    "requestAnimationFrame(function(){agendado=false; aplicar();});});"
    "window.__viaInfoObs.observe(document.body,{childList:true,subtree:true});}"
    "aplicar();})();</script>",
    unsafe_allow_javascript=True,
)

# --------------------------------------------------------------------
# Sidebar - filtros
# --------------------------------------------------------------------
# O Streamlit escreve "Press Ctrl+Enter to apply" dentro do campo de texto e não
# permite trocar o texto por parâmetro. Aqui os <span> originais são escondidos e
# a mensagem é escrita em português no mesmo lugar (mesma fonte/posição).
st.sidebar.markdown(
    """
    <style>
    [data-testid="stTextArea"] [data-testid="InputInstructions"] > span {
        display: none !important;
    }
    [data-testid="stTextArea"] [data-testid="InputInstructions"]::after {
        content: "Ctrl+Enter para aplicar";
    }
    </style>
    """,
    unsafe_allow_html=True,
)

hts_input = st.sidebar.text_area(
    "Códigos HTS (um por linha)",
    value="",
    placeholder="Ex: 0306144030",
    help=(
        "Ex: 0306144030 ou 2505.10.10.00 — pontos são removidos "
        "automaticamente. Pode informar vários, um por linha."
    ),
)
_raw_lines = [c.strip() for c in hts_input.splitlines() if c.strip()]
hts_codes = [re.sub(r"[^0-9]", "", line) for line in _raw_lines]
hts_codes = [c for c in hts_codes if c]
if eh_export:
    _hts_originais = list(hts_codes)
    hts_codes = truncate_hts_for_export(hts_codes)
    if hts_codes != _hts_originais:
        st.sidebar.caption(
            "ℹ️ Exportações só têm detalhamento oficial até **6 dígitos**: "
            "HTS maiores foram reduzidos aos 6 primeiros (" + ", ".join(hts_codes) + ")."
        )

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

aggregate_countries = False  # sempre desagregado
aggregate_districts = False  # sempre desagregado

# --------------------------------------------------------------------
# Tokens/chaves vêm dos secrets, sem alerta visual (monitoramento é feito
# separadamente via GitHub Actions + e-mail)
# --------------------------------------------------------------------
TOKEN = st.secrets.get("DATAWEB_TOKEN")
CENSUS_API_KEY = st.secrets.get("CENSUS_API_KEY")


# --------------------------------------------------------------------
# Opções dos filtros em cascata: HTS -> anos -> país -> via de entrada.
# Cada lista vem de uma consulta anual leve ao DataWeb (quebrando só pela
# dimensão pedida) e mostra apenas o que tem comércio registrado para o(s)
# HTS, o intervalo de anos e -- no caso da via -- os países já escolhidos.
# Se o intervalo inclui o ano em andamento, confere também os meses já
# publicados dele (o consolidado anual desse ano vem zerado).
# Em cache: repetir a mesma combinação não gera nova chamada. `_token` com
# underscore: o Streamlit não usa esse argumento na chave do cache.
# --------------------------------------------------------------------
@st.cache_data(show_spinner=False, ttl=3600)
def _descobrir_opcoes(hts_tuple, ano_ini, ano_fim, paises_tuple, dimensao, _token,
                      fluxo_="Importação", tipo_export_=None):
    """dimensao: "pais" -> países com comércio; "via" -> vias com comércio
    (restritas aos países em paises_tuple, se houver)."""
    anos = [str(y) for y in range(ano_ini, ano_fim + 1)]
    if dimensao == "pais":
        codigos, agg_pais, agg_via, paises_filtro = COUNTRY_CODES, False, True, []
    else:
        codigos, agg_pais, agg_via, paises_filtro = DISTRICT_CODES, True, False, list(paises_tuple)

    def _consulta(anos_q, mensal):
        if fluxo_ == "Exportação":
            q = build_export_query(
                hts_codes=list(hts_tuple), years=anos_q, export_type=tipo_export_,
                countries=paises_filtro, aggregate_commodities=False,
                aggregate_countries=agg_pais, measures=["FAS_VALUE"],
                monthly=mensal, districts=[], aggregate_districts=agg_via,
            )
        else:
            q = build_import_query(
                hts_codes=list(hts_tuple), years=anos_q, countries=paises_filtro,
                aggregate_commodities=False, aggregate_countries=agg_pais,
                measures=["CONS_CUSTOMS_VALUE"], monthly=mensal,
                districts=[], aggregate_districts=agg_via,
            )
        df_d = parse_report(run_report(q, _token), measure_num=0)
        cols_dado = [c for c in (EN_MESES if mensal else anos_q) if c in df_d.columns]
        for c in cols_dado:
            df_d[c] = pd.to_numeric(
                df_d[c].astype(str).str.replace(",", "", regex=False).str.strip(),
                errors="coerce",
            ).fillna(0)
        if cols_dado:
            df_d = df_d[df_d[cols_dado].sum(axis=1) > 0]
        for c in df_d.columns:
            if c in cols_dado or c == "Year":
                continue
            valores = df_d[c].dropna().astype(str)
            if len(valores) and valores.isin(codigos.keys()).mean() >= 0.5:
                return {v for v in valores.unique() if v in codigos}
        return set()

    achados = _consulta(anos, False)
    if ano_fim == ano_atual:
        achados |= _consulta([str(ano_atual)], True)
    return sorted(achados)


def _listar_opcoes(hts_t, paises_t, dimensao, rotulo_spinner):
    """Retorna (opções, falhou). Em caso de falha devolve a lista completa."""
    completo = sorted(COUNTRY_CODES.keys() if dimensao == "pais" else DISTRICT_CODES.keys())
    if not TOKEN:
        return completo, True
    with st.sidebar:
        with st.spinner(rotulo_spinner):
            try:
                return _descobrir_opcoes(
                    hts_t, year_start, year_end, paises_t, dimensao, TOKEN,
                    fluxo, tipo_export if eh_export else None,
                ), False
            except Exception:
                return completo, True


# Os filtros só aparecem depois que o usuário informa um HTS, um a um:
# país de origem -> via de entrada (cada um filtra as opções do seguinte).
countries = []
districts = []
if hts_codes:
    hts_t = tuple(hts_codes)

    paises_opcoes, falhou_p = _listar_opcoes(
        hts_t, (), "pais", tx("Buscando os países de origem...")
    )
    if falhou_p:
        st.sidebar.caption(
            "⚠️ Não foi possível listar os países deste(s) HTS agora "
            "(confira o código informado). Exibindo todas as opções."
        )

    if not paises_opcoes:
        st.sidebar.info(
            tx("Nenhum país de origem com comércio registrado para esse(s) HTS "
               "no intervalo de anos selecionado.")
        )
    else:
        countries = st.sidebar.multiselect(
            tx("Países de origem (opcional - vazio = todos)"),
            options=paises_opcoes,
            default=[],
        )

        vias_opcoes, falhou_v = _listar_opcoes(
            hts_t, tuple(sorted(countries)), "via", tx("Buscando as vias de entrada...")
        )
        if falhou_v:
            st.sidebar.caption(
                "⚠️ Não foi possível listar as vias deste(s) HTS agora. "
                "Exibindo todas as opções."
            )

        if not vias_opcoes:
            st.sidebar.info(
                tx("Nenhuma via de entrada com comércio registrado para esse(s) "
                   "HTS, anos e países selecionados.")
            )
        else:
            districts = st.sidebar.multiselect(
                tx("Via de entrada / distrito aduaneiro (opcional - vazio = todos)"),
                options=vias_opcoes,
                default=[],
            )

buscar = st.sidebar.button(
    "Buscar dados",
    disabled=not (metrica_valor or metrica_quantidade) or not hts_codes,
)

# --------------------------------------------------------------------
# Ano corrente no modo Anual (acumulado do ano até o último mês publicado)
# --------------------------------------------------------------------
def _buscar_ytd_ano_corrente(hts_codes_, countries_, districts_, measures_, token_):
    """Consulta MENSAL só do ano corrente e soma os meses já publicados.
    Retorna ({label da medida: DataFrame ids + _YTD}, {label: 'Jul'})."""
    hoje_ = dt.date.today()
    ultimo_mes_completo_ = hoje_.replace(day=1) - dt.timedelta(days=1)
    if ultimo_mes_completo_.year != ano_atual:
        return {}, {}  # janeiro: ainda não há mês fechado do ano corrente
    n_max = ultimo_mes_completo_.month

    if eh_export:
        query_m = build_export_query(
            hts_codes=hts_codes_, years=[str(ano_atual)], export_type=tipo_export,
            countries=countries_, aggregate_commodities=False,
            aggregate_countries=False, measures=measures_, monthly=True,
            districts=districts_, aggregate_districts=False,
        )
    else:
        query_m = build_import_query(
            hts_codes=hts_codes_, years=[str(ano_atual)], countries=countries_,
            aggregate_commodities=False, aggregate_countries=False,
            measures=measures_, monthly=True, districts=districts_,
            aggregate_districts=False,
        )
    resp_m = run_report(query_m, token_)

    dfs_ytd, ate_mes = {}, {}
    for i in range(num_tables(resp_m)):
        label_m = label_canonico(get_table_label(resp_m, measure_num=i))
        d = parse_report(resp_m, measure_num=i)
        meses = [m for m in EN_MESES[:n_max] if m in d.columns]
        if not meses:
            continue
        for m in meses:
            d[m] = pd.to_numeric(
                d[m].astype(str).str.replace(",", "", regex=False).str.strip()
                .replace({"": None, "nan": None}),
                errors="coerce",
            ).fillna(0)

        # O acumulado soma todos os meses fechados (os ainda não publicados são
        # zero e não alteram a soma). O rótulo "até <mês>" usa o ÚLTIMO MÊS COM
        # DADO de fato -- sem limite de defasagem.
        meses_com_dado = [m for m in meses if d[m].sum() > 0]
        if not meses_com_dado:
            continue
        d["_YTD"] = d[meses].sum(axis=1)

        id_cols_m = [c for c in d.columns if c not in EN_MESES and c not in ("Year", "_YTD")]
        d = d.groupby(id_cols_m, as_index=False, dropna=False)["_YTD"].sum()
        dfs_ytd[label_m] = d
        ate_mes[label_m] = MES_ABBR_PT_INV[EN_MESES.index(meses_com_dado[-1]) + 1]
    return dfs_ytd, ate_mes


def _incorporar_ytd(df_anual, df_ytd):
    """Coloca o acumulado do ano corrente na coluna do ano (que o DataWeb
    devolve zerada no Anual), casando pelas colunas de identificação."""
    col = str(ano_atual)
    id_cols = [c for c in df_anual.columns if c not in years]
    comuns = [c for c in id_cols if c in df_ytd.columns]
    if not comuns:
        return df_anual
    ytd = (
        df_ytd[comuns + ["_YTD"]]
        .groupby(comuns, as_index=False, dropna=False)["_YTD"].sum()
        .rename(columns={"_YTD": col})
    )
    base = df_anual.drop(columns=[col], errors="ignore")
    merged = base.merge(ytd, on=comuns, how="outer")
    for c in years:
        if c in merged.columns:
            merged[c] = merged[c].fillna(0)
    if col not in merged.columns:
        merged[col] = 0.0
    return merged


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
        measures.append("FAS_VALUE" if eh_export else "CONS_CUSTOMS_VALUE")
    if metrica_quantidade:
        measures.append("FIRST_UNIT_QUANTITY" if eh_export else "CONS_FIR_UNIT_QUANT")
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

    def _montar_query(anos_q):
        if eh_export:
            return build_export_query(
                hts_codes=hts_codes,
                years=anos_q,
                export_type=tipo_export,
                countries=countries,
                aggregate_commodities=aggregate_commodities,
                aggregate_countries=aggregate_countries,
                measures=measures,
                monthly=monthly,
                districts=districts,
                aggregate_districts=aggregate_districts,
            )
        return build_import_query(
            hts_codes=hts_codes,
            years=anos_q,
            countries=countries,
            aggregate_commodities=aggregate_commodities,
            aggregate_countries=aggregate_countries,
            measures=measures,
            monthly=monthly,
            districts=districts,
            aggregate_districts=aggregate_districts,
        )

    def _tabelas_da_resposta(resp):
        """{label canônico: DataFrame bruto} de uma resposta do DataWeb."""
        return {
            label_canonico(get_table_label(resp, measure_num=k)): parse_report(resp, measure_num=k)
            for k in range(num_tables(resp))
        }

    with st.spinner("Consultando USITC DataWeb..."):
        try:
            response = run_report(_montar_query(years_query), TOKEN)
        except Exception as e:
            st.error(f"Erro ao consultar a API DataWeb: {e}")
            st.stop()

        # Consultas Mensais com muitos países x vias x anos (ex.: vestuário)
        # podem estourar o limite de linhas/complexidade do DataWeb, que então
        # devolve tabelas vazias ou nulas. Nesse caso refaz a consulta ANO A
        # ANO e junta os resultados.
        dfs_brutos = {}
        try:
            erro_resp = response_error_message(response)
            if not erro_resp:
                dfs_brutos = _tabelas_da_resposta(response)
                # tabela sem nenhuma linha em TODAS as medidas = suspeito
                if monthly and dfs_brutos and all(d.empty for d in dfs_brutos.values()):
                    erro_resp = "a API devolveu tabelas sem linhas."
        except Exception as e:
            erro_resp = f"falha ao ler a resposta ({e})"

        if erro_resp:
            if monthly and len(years_query) > 1:
                st.info(
                    "A consulta mensal completa foi recusada pelo DataWeb "
                    f"({erro_resp}). Consultando ano a ano..."
                )
                partes, erros_ano = {}, []
                for y in years_query:
                    try:
                        resp_y = run_report(_montar_query([y]), TOKEN)
                        msg_y = response_error_message(resp_y)
                        if msg_y:
                            erros_ano.append(f"{y}: {msg_y}")
                            continue
                        for lab, df_y in _tabelas_da_resposta(resp_y).items():
                            if not df_y.empty:
                                partes.setdefault(lab, []).append(df_y)
                    except Exception as e:
                        erros_ano.append(f"{y}: {e}")
                dfs_brutos = {
                    lab: pd.concat(frames, ignore_index=True)
                    for lab, frames in partes.items()
                }
                if erros_ano and not dfs_brutos:
                    st.error("Erro ao consultar a API DataWeb: " + " | ".join(erros_ano[:3]))
                    st.stop()
                if erros_ano:
                    st.warning(
                        "Alguns anos não puderam ser consultados e ficarão sem dado: "
                        + " | ".join(erros_ano[:5])
                    )
            else:
                st.error(f"Erro ao consultar a API DataWeb: {erro_resp}")
                st.stop()

        if not dfs_brutos or all(d.empty for d in dfs_brutos.values()):
            st.warning(
                "A consulta não retornou dados para esse(s) HTS, período e filtros."
            )
            st.stop()

        # No Anual, o DataWeb só consolida anos COMPLETOS -- o ano em andamento
        # volta zerado. Para incluí-lo, soma os meses já publicados (consulta
        # mensal só do ano corrente) e guarda até qual mês há dado.
        ytd_por_label, ytd_ate_mes = {}, {}
        if (not monthly) and str(ano_atual) in years:
            try:
                ytd_por_label, ytd_ate_mes = _buscar_ytd_ano_corrente(
                    hts_codes, countries, districts, measures, TOKEN
                )
            except Exception:
                st.warning(
                    f"Não foi possível obter o acumulado de {ano_atual}; "
                    "esse ano não aparecerá nos gráficos anuais."
                )

        dfs_por_medida = {}
        dfs_por_medida_full = {}  # versão com o histórico extra, só para a projeção
        try:
            for label, df_i in dfs_brutos.items():
                if df_i.empty:
                    continue  # medida sem nenhuma linha: não entra nas tabelas/gráficos

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
                    if label in ytd_por_label:
                        df_i = _incorporar_ytd(df_i, ytd_por_label[label])
                    dfs_por_medida_full[label] = df_i  # anual não estende nada

                dfs_por_medida[label] = df_i
        except Exception as e:
            st.error(f"Erro ao processar a resposta da API: {e}")
            st.stop()

    st.session_state["df_eua_multi"] = dfs_por_medida
    st.session_state["df_eua_multi_full"] = dfs_por_medida_full
    st.session_state["df_eua_monthly"] = monthly
    st.session_state["df_eua_years"] = years
    st.session_state["df_eua_ytd_mes"] = ytd_ate_mes
    st.session_state["df_eua_hts"] = hts_codes
    st.session_state["df_eua_paises_filtro"] = list(countries)
    st.session_state["df_eua_vias_filtro"] = list(districts)
    st.session_state["df_eua_fluxo"] = fluxo
    st.session_state["df_eua_tipo_export"] = tipo_export_label
    # Identifica a busca: entra na chave dos sliders/multiselects dos gráficos, para
    # que uma busca nova não herde o recorte de período/seleção da busca anterior
    # (ex.: slider preso em "até 2025" depois de uma busca que inclui 2026).
    st.session_state["df_eua_busca_id"] = st.session_state.get("df_eua_busca_id", 0) + 1

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
    ytd_mes_por_medida = st.session_state.get("df_eua_ytd_mes", {})
    # Os resultados exibidos pertencem ao(s) HTS da última busca -- não ao que
    # está digitado agora na barra lateral (que pode já ter sido alterado).
    hts_codes = st.session_state.get("df_eua_hts", hts_codes)
    # Terminologia e regras da exibição seguem o fluxo da última BUSCA (não o
    # seletor atual da barra lateral, que pode ter mudado sem nova busca).
    eh_export = st.session_state.get("df_eua_fluxo", fluxo) == "Exportação"
    if eh_export != (fluxo == "Exportação"):
        st.info(
            "O fluxo selecionado na barra lateral mudou desde a última busca. "
            "Clique em **Buscar dados** para atualizar os resultados."
        )

    # ----------------------------------------------------------------
    # Cabeçalho do HTS pesquisado: "código | descrição" em destaque e, se
    # houver mais de um HTS, os demais ficam em "Outros" (expansível).
    # ----------------------------------------------------------------
    from html import escape as _esc

    def _fmt_hts(c):
        c = re.sub(r"\D", "", str(c))
        if len(c) > 6:
            return f"{c[:4]}.{c[4:6]}.{c[6:]}"
        if len(c) > 4:
            return f"{c[:4]}.{c[4:]}"
        return c

    _df0 = next(iter(dfs_por_medida.values()))
    _cols_p = (
        [c for c in _df0.columns if re.match(r"^[A-Za-zçã]{3}/\d{4}$", str(c))]
        if monthly else [c for c in years if c in _df0.columns]
    )
    _lab = [c for c in _df0.columns if c not in _cols_p]
    _hts_c = None
    for c in _lab:
        _v = set(str(x) for x in _df0[c].dropna().unique())
        if _v and _v.issubset(set(hts_codes)):
            _hts_c = c
            break
    _desc_c = None
    if _hts_c:
        if "Description" in _df0.columns:
            _desc_c = "Description"
        else:
            for c in _lab:
                if c in (_hts_c, "Quantity Description"):
                    continue
                _vs = _df0[c].dropna().astype(str)
                if (_vs.isin(COUNTRY_CODES.keys()).mean() < 0.5
                        and _vs.isin(DISTRICT_CODES.keys()).mean() < 0.5):
                    _desc_c = c
                    break
    _descricoes = {}
    if _hts_c and _desc_c:
        for h, d in zip(_df0[_hts_c], _df0[_desc_c]):
            if pd.notna(h) and pd.notna(d):
                _descricoes.setdefault(str(h), str(d))

    def _linha_hts(h):
        d = _descricoes.get(str(h))
        return f"{_fmt_hts(h)} | {d}" if d else _fmt_hts(h)

    _tipo_exp = st.session_state.get("df_eua_tipo_export", "Doméstica")
    _fluxo_txt = f"Exportação {_tipo_exp.lower()}" if eh_export else "Importação"
    if hts_codes:
        st.markdown(
            f"""
            <div style="margin: 0.75rem 0 0.25rem 0;">
              <div style="font-size:1.8rem; line-height:1.25; color:#31333F;">{_esc(_linha_hts(hts_codes[0]))}</div>
              <div style="font-size:0.95rem; color:#555; margin-top:0.3rem;">
                Fonte: USITC DataWeb | País: Estados Unidos | Fluxo: {_esc(_fluxo_txt)}
              </div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        if len(hts_codes) > 1:
            with st.expander(f"Outros ({len(hts_codes) - 1})"):
                for h in hts_codes[1:]:
                    st.markdown(f"**{_esc(_fmt_hts(h))}**" + (f" | {_esc(_descricoes[str(h)])}" if str(h) in _descricoes else ""))

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
        df_linhas = pd.DataFrame(linhas)
        df_linhas[nome_valor] = df_linhas[nome_valor].round(0)  # projeção em números inteiros
        return df_linhas

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

                # Linhas projetadas não trazem a unidade de medida: preenche pelo HTS.
                if "Unidade de Medida" in df_longo.columns:
                    if hts_col and hts_col in df_agg.columns:
                        _mapa_un = (
                            df_agg.dropna(subset=[hts_col]).drop_duplicates(hts_col)
                            .set_index(hts_col)["Unidade de Medida"].to_dict()
                        )
                        _faltam = df_longo["Unidade de Medida"].isna()
                        df_longo.loc[_faltam, "Unidade de Medida"] = df_longo.loc[_faltam, hts_col].map(_mapa_un)
                    else:
                        _un = df_agg["Unidade de Medida"].dropna()
                        if not _un.empty:
                            df_longo["Unidade de Medida"] = df_longo["Unidade de Medida"].fillna(_un.iloc[0])

        # Ordem das colunas (tabela em tela e Excel): Data, HTS, Descrição,
        # Via de Entrada/Distrito, Valor, Unidade de Medida (só em
        # Quantidade) e Tipo (só no Mensal). O índice da linha é o próprio
        # índice do DataFrame.
        cols_df = list(df_longo.columns)
        inicio = [c for c in ("Data", hts_col, desc_col, via_col_local) if c and c in cols_df]
        fim = [c for c in ("Unidade de Medida", "Tipo") if c in cols_df]
        meio = [c for c in cols_df if c not in inicio and c not in fim and c != nome_valor]
        ordem = inicio + meio + [nome_valor] + fim
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

                # Linhas projetadas não trazem a unidade de medida: preenche pelo HTS.
                if "Unidade de Medida" in df_longo.columns:
                    if hts_col and hts_col in df_tot.columns:
                        _mapa_un = (
                            df_tot.dropna(subset=[hts_col]).drop_duplicates(hts_col)
                            .set_index(hts_col)["Unidade de Medida"].to_dict()
                        )
                        _faltam = df_longo["Unidade de Medida"].isna()
                        df_longo.loc[_faltam, "Unidade de Medida"] = df_longo.loc[_faltam, hts_col].map(_mapa_un)
                    else:
                        _un = df_tot["Unidade de Medida"].dropna()
                        if not _un.empty:
                            df_longo["Unidade de Medida"] = df_longo["Unidade de Medida"].fillna(_un.iloc[0])

        # Ordem das colunas: Data, HTS, Descrição, Valor, Unidade de Medida
        # (só em Quantidade) e Tipo (só no Mensal). O índice da linha é o
        # próprio índice do DataFrame.
        cols_df = list(df_longo.columns)
        inicio = [c for c in ("Data", hts_col, desc_col) if c and c in cols_df]
        fim = [c for c in ("Unidade de Medida", "Tipo") if c in cols_df]
        meio = [c for c in cols_df if c not in inicio and c not in fim and c != nome_valor]
        ordem = inicio + meio + [nome_valor] + fim
        df_longo = df_longo[ordem].sort_values("Data").reset_index(drop=True)

        return df_longo

    def renderizar_medida(df, medida_label, tab_key, df_exibicao, periodo_cols, df_totais=None, excel_buffer=None):
        eh_medida_valor = "Quantity" not in medida_label
        nome_valor = "Valor (USD)" if eh_medida_valor else "Volume"
        formato_data = "YYYY-MM-DD" if monthly else "YYYY"

        ate_mes_atual = ytd_mes_por_medida.get(medida_label)
        if (not monthly) and ate_mes_atual and str(ano_atual) in periodo_cols:
            st.caption(
                f"ℹ️ {ano_atual} é o ano em andamento: inclui dados apenas até "
                f"{ate_mes_atual}/{ano_atual} (acumulado parcial)."
            )

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
            st.markdown(tx("**Detalhe (por HTS e Via de Entrada)**"))

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


    # ----------------------------------------------------------------
    # Controles globais (topo dos resultados): métrica das tabelas e de
    # TODOS os gráficos, e liga/desliga dos rótulos de dados.
    # ----------------------------------------------------------------
    tem_valor = "Customs Value" in dfs_por_medida
    tem_qtd = "First Unit of Quantity" in dfs_por_medida

    # Seletor de métrica com aparência de abas (sublinhado na opção ativa).
    # É um st.radio estilizado: assim o valor escolhido fica disponível na
    # hora (as abas nativas não informam qual está ativa) e vale para a
    # página inteira.
    st.markdown(
        """
        <style>
        .st-key-metrica_abas div[role="radiogroup"] {
            gap: 0; border-bottom: 1px solid rgba(49, 51, 63, 0.2);
        }
        .st-key-metrica_abas label {
            padding: 0.5rem 1rem; margin: 0; cursor: pointer;
            border-bottom: 2px solid transparent;
        }
        .st-key-metrica_abas label > div:first-child { display: none; }
        .st-key-metrica_abas label p { color: rgb(49, 51, 63); }
        .st-key-metrica_abas label:hover p { color: #042373; }
        .st-key-metrica_abas label:has(input:checked) {
            border-bottom: 2px solid #042373;
        }
        .st-key-metrica_abas label:has(input:checked) p { color: #042373; }
        </style>
        """,
        unsafe_allow_html=True,
    )
    with st.container(key="metrica_abas"):
        if tem_valor and tem_qtd:
            metrica_grafico = st.radio(
                "Métrica (tabelas e gráficos)", ["Valor (USD)", "Quantidade"],
                horizontal=True, key="metrica_grafico_toggle",
                label_visibility="collapsed",
            )
        elif tem_valor:
            metrica_grafico = "Valor (USD)"
        else:
            metrica_grafico = "Quantidade"
    mostrar_rotulos = st.toggle(
        "Rótulos de dados nos gráficos", value=True, key="rotulos_dados_toggle",
    )

    def aplicar_rotulos_dados(fig):
        """Liga ou desliga os rótulos de dados (valores sobre barras/pontos)
        de uma figura, conforme o botão global. Devolve a própria figura.

        Gráficos com muitas barras/pontos recebem rótulos ESPAÇADOS (um a
        cada N), na vertical e acima da barra, para não poluir. Barras que
        já têm rótulo próprio (percentuais, países) mantêm o formato."""
        import math
        MAX_ROTULOS = 24          # alvo de rótulos por série em gráficos densos
        MAX_ROTULOS_AGRUPADO = 50  # alvo total em barras agrupadas (várias séries)
        FONTE_ROTULO = dict(size=13, color="#000000")

        def _fmt(v):
            try:
                if v is None or v != v or float(v) == 0:
                    return ""  # sem rótulo para valor zero (ou ausente)
                return f"{float(v):,.0f}"
            except (TypeError, ValueError):
                return ""

        barras = [t for t in fig.data if t.type == "bar"]
        barras_v = [t for t in barras
                    if getattr(t, "orientation", None) != "h" and t.text is None]

        if not mostrar_rotulos:
            for tr in barras:
                tr.update(text=None, texttemplate=None)
            for tr in fig.data:
                if tr.type == "scatter" and tr.mode and "lines" in tr.mode and tr.meta != "sem_rotulo":
                    tr.update(mode="lines+markers")
            return fig

        # Barras que já vêm com rótulo próprio: garante posição e tamanho
        # (sem isso o Plotly encolhe o texto para caber na barra).
        _tem_proprio = False
        for tr in barras:
            if tr not in barras_v:
                if tr.text is None:  # horizontais sem rótulo
                    tr.update(texttemplate="%{x:,.0f}")
                tr.update(textposition="outside", cliponaxis=False,
                          constraintext="none", textfont=FONTE_ROTULO)
                _tem_proprio = True
        if _tem_proprio:
            fig.update_layout(uniformtext=dict(minsize=FONTE_ROTULO["size"], mode="show"))

        # Barras verticais sem rótulo: rótulos verticais, de N em N barras.
        if barras_v:
            cats, vistos = [], {}
            for tr in barras_v:
                for x in tr.x:
                    if x not in vistos:
                        vistos[x] = len(cats)
                        cats.append(x)
            total_pts = sum(len(tr.x) for tr in barras_v)
            n = len(cats)
            compartilhado = total_pts > n          # várias barras por categoria
            m = len(barras_v) if compartilhado else 1
            if compartilhado:
                passo = max(1, math.ceil(n * m / MAX_ROTULOS_AGRUPADO))
            else:
                passo = max(1, math.ceil(n / MAX_ROTULOS))
            ymax = 0.0
            for j, tr in enumerate(barras_v):
                textos = []
                for x, y in zip(tr.x, tr.y):
                    c = vistos[x]
                    g = c * m + (j if compartilhado else 0)
                    textos.append(_fmt(y) if g % passo == 0 else "")
                    if y is not None and y == y:
                        ymax = max(ymax, float(y))
                tr.update(
                    text=textos, texttemplate=None, textposition="outside",
                    textangle=-90, textfont=FONTE_ROTULO, cliponaxis=False,
                    constraintext="none",  # o Plotly encolhia o texto p/ caber na barra
                )
            if ymax > 0:  # folga no topo para os rótulos verticais
                fig.update_yaxes(range=[0, ymax * 1.35])
                # garante o tamanho mínimo do texto (sem isso o Plotly reduz a fonte)
                fig.update_layout(uniformtext=dict(minsize=FONTE_ROTULO["size"], mode="show"))

        # Linhas: rótulos espaçados nos pontos.
        for tr in fig.data:
            if tr.type == "scatter" and tr.mode and "lines" in tr.mode and tr.meta != "sem_rotulo":
                npts = len(tr.y)
                passo = max(1, math.ceil(npts / MAX_ROTULOS))
                tr.update(
                    mode="lines+markers+text",
                    text=[_fmt(y) if k % passo == 0 else "" for k, y in enumerate(tr.y)],
                    textposition="top center", textfont=FONTE_ROTULO,
                )
        return fig

    chave_medida = "Customs Value" if metrica_grafico == "Valor (USD)" else "First Unit of Quantity"

    # No Anual, o ano em andamento é rotulado com o último mês que tem dado.
    ate_mes_ytd = ytd_mes_por_medida.get(chave_medida)

    def rotulo_periodo(p):
        if (not monthly) and ate_mes_ytd and str(p) == str(ano_atual):
            return f"{p} (até {ate_mes_ytd})"
        return str(p)

    # Período exibido -- um único slicer no topo, vale para as tabelas e
    # para TODOS os gráficos (inclusive o Excel para download).
    _periodos_opcoes = periodo_cols_de(dfs_por_medida[chave_medida])
    if len(_periodos_opcoes) > 1:
        _busca_id = st.session_state.get("df_eua_busca_id", 0)
        _p_ini, _p_fim = st.select_slider(
            "Período exibido (tabelas e gráficos)",
            options=_periodos_opcoes,
            value=(_periodos_opcoes[0], _periodos_opcoes[-1]),
            format_func=rotulo_periodo,
            key=f"periodo_slicer_global_{_busca_id}_{_periodos_opcoes[0]}_{_periodos_opcoes[-1]}_{len(_periodos_opcoes)}",
        )
        periodos_sel_global = set(
            _periodos_opcoes[_periodos_opcoes.index(_p_ini): _periodos_opcoes.index(_p_fim) + 1]
        )
    else:
        periodos_sel_global = set(_periodos_opcoes)

    def recortar_periodo(colunas):
        """Períodos de `colunas` dentro do recorte escolhido no slicer global."""
        return [c for c in colunas if c in periodos_sel_global]

    def recortar_df_periodo(df_):
        """Remove as colunas de período fora do recorte do slicer do topo."""
        cols_periodo = (
            [c for c in df_.columns if re.match(r"^[A-Za-zçã]{3}/\d{4}$", str(c))]
            if monthly else [c for c in years if c in df_.columns]
        )
        return df_.drop(columns=[c for c in cols_periodo if c not in periodos_sel_global])

    # Monta o Excel único (Totais + Detalhe por medida), já no recorte de
    # período do slicer do topo. Sempre em Excel,
    # nunca mais CSV -- com 1 medida selecionada, tem 2 abas; com as 2
    # medidas, tem 4 abas.
    import io
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        for label, df in dfs_por_medida.items():
            df = recortar_df_periodo(df)  # mesmo recorte do slicer do topo
            df_exibicao_tmp, _ = preparar_df_exibicao(df, label)
            df_totais_tmp = preparar_df_totais(df, label)
            nome_base = label_pt(label)
            if not df_totais_tmp.empty:
                df_totais_tmp.to_excel(writer, sheet_name=f"{nome_base} - Totais"[:31], index=False)
            df_exibicao_tmp.to_excel(writer, sheet_name=f"{nome_base} - Detalhe"[:31], index=False)
        if (not monthly) and ytd_mes_por_medida and str(ano_atual) in periodos_sel_global:
            notas = [
                f"{ano_atual} é o ano em andamento: inclui dados apenas até "
                f"{mes}/{ano_atual} ({label_pt(lbl)})."
                for lbl, mes in ytd_mes_por_medida.items()
            ]
            pd.DataFrame({"Notas": notas}).to_excel(writer, sheet_name="Notas", index=False)
    excel_bytes = buffer.getvalue()

    # Tabelas: só as colunas de período dentro do recorte do slicer.
    label, df = chave_medida, recortar_df_periodo(dfs_por_medida[chave_medida])
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

    # Painel ÚNICO de filtros dos gráficos (HTS, vias e países). É criado aqui
    # para aparecer antes do primeiro gráfico e preenchido mais abaixo, quando
    # os dados já foram analisados.
    painel_filtros = st.container()

    df_fonte = dfs_por_medida.get(chave_medida)

    def nota_ano_em_andamento(periodos, mes=None, fonte=""):
        """Aviso sob o gráfico quando o período selecionado inclui o ano em
        andamento (Anual): diz até qual mês há dado. Só aparece se o recorte
        escolhido no slider do gráfico realmente inclui esse ano."""
        mes = mes or ate_mes_ytd
        if (not monthly) and mes and str(ano_atual) in [str(p) for p in periodos]:
            st.caption(
                f"ℹ️ O período selecionado inclui {ano_atual}, ano em andamento: "
                f"dados apenas até {mes}/{ano_atual}{fonte} (acumulado parcial, "
                "não o ano completo)."
            )

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

            with painel_filtros:
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
        busca_id = st.session_state.get("df_eua_busca_id", 0)
        combo_id = re.sub(r"\W+", "_", f"{busca_id}_{metrica_grafico}_{hts_escolhido or 'total'}".lower())

        # Unidade de medida do HTS exibido (só em Quantidade) -- usada na
        # legenda sob o título e no título do eixo dos gráficos maiores.
        unidades_txt = ""
        if metrica_grafico == "Quantidade" and "Quantity Description" in df_fonte_grafico.columns:
            _un = (
                df_fonte_grafico["Quantity Description"]
                .dropna().astype(str)
                .str.replace("Value for: ", "", regex=False)
                .unique()
            )
            unidades_txt = ", ".join(sorted(u for u in _un if u and u != "nan"))
        titulo_eixo_metrica = (
            f"Quantidade ({unidades_txt})" if unidades_txt else metrica_grafico
        )

        def legenda_unidade_hts():
            """Mostra, embaixo do título de cada gráfico, qual HTS está
            sendo exibido (quando um HTS específico foi escolhido, não o
            Total) e, numa linha abaixo, a unidade de medida (quando
            Quantidade)."""
            linhas = []
            _h = hts_escolhido
            if _h is None and len(hts_presentes) == 1:
                _h = hts_presentes[0]  # um único HTS na consulta
            if _h is not None:
                linhas.append(f"HTS exibido: {_linha_hts(_h)}")
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
            tx(f"Volume Importado em {metrica_grafico}: Realizado vs Projetado")
            if monthly else
            tx(f"Volume Importado em {metrica_grafico}: Realizado")
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
                if ate_mes_ytd:
                    texto_aviso_anual += (
                        f"  \n⚠️ **{ano_atual}** é o ano em andamento: o total "
                        f"reflete apenas os meses publicados, até "
                        f"**{ate_mes_ytd}/{ano_atual}** (acumulado parcial, não o ano completo)."
                    )
                else:
                    texto_aviso_anual += (
                        f"  \n⚠️ **{ano_atual}** ainda não tem dados publicados "
                        "na fonte e por isso não aparece nos gráficos abaixo."
                    )
            st.info(texto_aviso_anual)

        if not periodo_cols:
            st.info("Sem colunas de período disponíveis para exibir gráficos.")
        else:
            # Slicer -- limita o período exibido nos gráficos, dentro do
            # intervalo já consultado.
            periodo_visivel = recortar_periodo(periodo_cols)

            # Detecta a coluna de via de entrada/distrito para quebrar os gráficos
            via_col = None
            for c in label_cols:
                valores = df_fonte_grafico[c].dropna().astype(str)
                if len(valores) == 0:
                    continue
                if valores.isin(DISTRICT_CODES.keys()).mean() >= 0.5:
                    via_col = c
                    break

            # País: detectado aqui porque o painel de filtros precisa dele.
            country_col = None
            for c in label_cols:
                valores = df_fonte_grafico[c].dropna().astype(str)
                if len(valores) == 0:
                    continue
                if valores.isin(COUNTRY_CODES.keys()).mean() >= 0.5:
                    country_col = c
                    break

            # ------------------------------------------------------------
            # Painel de filtros GLOBAL dos gráficos: vale para TODOS os
            # gráficos (sem repetir seletor em cada um).
            #   - Vias exibidas: gráficos por via (evolução, combinado e os
            #     por país/via).
            #   - Países exibidos: gráfico de % por país (o resto vira
            #     "Outros").
            # Os gráficos de total (Pareto de países e modal) mostram o
            # universo consultado; o modal segue os filtros da barra lateral.
            # ------------------------------------------------------------
            vias_global, paises_global = [], []
            top5_vias_default, top5_paises_default = [], []
            todas_vias_alfa, todos_paises_alfa = [], []
            ranking_paises = []

            if via_col:
                _dv = (
                    df_fonte_grafico.groupby(via_col, as_index=False)[periodo_cols]
                    .sum(min_count=1)
                )
                _dv["_total"] = _dv[periodo_cols].sum(axis=1, skipna=True)
                _dv = _dv.sort_values("_total", ascending=False)
                _reais = _dv[_dv["_total"] > 0][via_col].tolist()
                # Vias especiais do Census (baixo valor, correio...) não são
                # portos: ficam fora do Top 5 automático.
                _normais = [v for v in _reais if info_via_especial(v) is None]
                top5_vias_default = sorted((_normais or _reais)[:5])
                todas_vias_alfa = sorted(_dv[via_col].tolist())

            if country_col:
                _dp = (
                    df_fonte_grafico.groupby(country_col, as_index=False)[periodo_visivel]
                    .sum(min_count=1)
                )
                _dp["_total"] = _dp[periodo_visivel].sum(axis=1, skipna=True)
                _dp = _dp.sort_values("_total", ascending=False)
                ranking_paises = _dp[country_col].tolist()
                top5_paises_default = sorted(_dp[_dp["_total"] > 0][country_col].tolist()[:5])
                todos_paises_alfa = sorted(ranking_paises)

            MAX_VIAS_GLOBAL = 8
            MAX_PAISES_GLOBAL = 12
            ms_key_vias = f"vias_global_multiselect_{combo_id}"
            ms_key_paises = f"paises_global_multiselect_{combo_id}"
            flag_reset_global = f"filtros_global_reset_flag_{combo_id}"
            if st.session_state.get(flag_reset_global):
                st.session_state[ms_key_vias] = top5_vias_default
                st.session_state[ms_key_paises] = top5_paises_default
                st.session_state[flag_reset_global] = False

            if via_col or country_col:
                with painel_filtros:
                    col_titulo_f, col_btn_f = st.columns([5, 1])
                    with col_titulo_f:
                        st.markdown("**Filtros dos gráficos**")
                    with col_btn_f:
                        if st.button("Restaurar Top 5", key=f"filtros_global_reset_btn_{combo_id}", use_container_width=True):
                            st.session_state[flag_reset_global] = True
                            st.rerun()
                    col_fv, col_fp = st.columns(2)
                    with col_fv:
                        if via_col:
                            vias_global = st.multiselect(
                                tx("Vias de Entrada exibidas"),
                                options=todas_vias_alfa,
                                default=top5_vias_default,
                                key=ms_key_vias,
                                max_selections=MAX_VIAS_GLOBAL,
                                help=tx(f"Máximo de {MAX_VIAS_GLOBAL} vias por vez, para manter as cores e a leitura claras."),
                            )
                    with col_fp:
                        if country_col:
                            paises_global = st.multiselect(
                                "Países exibidos",
                                options=todos_paises_alfa,
                                default=top5_paises_default,
                                key=ms_key_paises,
                                max_selections=MAX_PAISES_GLOBAL,
                                help=(
                                    "No gráfico de % por país, os países não selecionados "
                                    f"entram somados em \"Outros\". Máximo de {MAX_PAISES_GLOBAL} por vez."
                                ),
                            )

            # Cores consistentes: a mesma via/país tem a mesma cor em todos os gráficos.
            cor_via = {
                v: PALETA_CATEGORICA[i % len(PALETA_CATEGORICA)]
                for i, v in enumerate(vias_global)
            }
            _cor_pais_map = {
                c: PALETA_CATEGORICA[i]
                for i, c in enumerate(ranking_paises[: len(PALETA_CATEGORICA)])
            }

            def cor_pais(nome):
                return COR_OUTROS if nome == "Outros" else _cor_pais_map.get(nome, COR_OUTROS)

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
                # Vias especiais do Census (baixo valor, correio...) não são
                # portos: ficam fora do Top 5 automático (continuam na lista).
                _vias_reais = [v for v in vias_com_total_real if info_via_especial(v) is None]
                top5_default = sorted((_vias_reais or vias_com_total_real)[:5])
                todas_vias_alfa = sorted(todas_vias)

                vias_selecionadas = vias_global

                if not vias_selecionadas:
                    st.info(tx("Selecione ao menos uma via de entrada para exibir os gráficos."))
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
                            tx("Nenhuma das vias de entrada selecionadas tem dado "
                            "(real ou projetado) no período visível.")
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
                                        x=[rotulo_periodo(p) for p in periodo_visivel], y=valores,
                                        name="Realizado", marker_color=COR_REALIZADO,
                                        hovertemplate="%{x}<br>%{y:,.0f}<extra></extra>",
                                    )
                                )
                                if monthly:
                                    serie_proj = forecast_por_via.get(via)
                                    if serie_proj is not None and len(serie_proj) > 0:
                                        periodos_proj = [data_para_periodo_label(d) for d in serie_proj.index]
                                        valores_proj = [round(v) for v in serie_proj.tolist()]
                                        fig.add_trace(
                                            go.Bar(
                                                x=periodos_proj, y=valores_proj,
                                                name="Projetado", marker_color=COR_PROJETADO,
                                                hovertemplate="%{x}<br>%{y:,.0f}<extra></extra>",
                                            )
                                        )
                                st.markdown(titulo_via_html(via), unsafe_allow_html=True)
                                fig.update_layout(
                                    height=290,
                                    plot_bgcolor="#DBF7FF",
                                    paper_bgcolor="white",
                                    margin=dict(t=15, b=40, l=40, r=20),
                                    showlegend=False,
                                )
                                fig.update_xaxes(type="category", showline=True, linewidth=2, linecolor="#042373", mirror=True)
                                fig.update_yaxes(showline=True, linewidth=2, linecolor="#042373", mirror=True)
                                chart_key = "via_chart_" + re.sub(r"\W+", "_", str(via).lower())
                                st.plotly_chart(aplicar_rotulos_dados(fig), use_container_width=True, key=chart_key)

                # ------------------------------------------------------
                # Gráfico 2 -- todas as vias de entrada combinadas num único gráfico
                # (padrão do dash Brasil: "Volume total transacionado por URF")
                # ------------------------------------------------------
                st.divider()
                st.markdown(
                    tx(f"""
                    <h2 style='text-align:center; color:#042373; font-family:Arial; font-weight:bold;'>
                        Volume total transacionado em {metrica_grafico} (Realizado) por Via de Entrada
                    </h2>
                    """),
                    unsafe_allow_html=True,
                )
                legenda_unidade_hts()

                periodo_visivel2 = recortar_periodo(periodo_cols)

                vias_selecionadas2 = vias_global

                if not vias_selecionadas2:
                    st.info(tx("Selecione ao menos uma via de entrada para exibir o gráfico."))
                else:
                    fig2 = go.Figure()
                    for i, via in enumerate(vias_selecionadas2):
                        row = df_via[df_via[via_col] == via].iloc[0]
                        valores = [row[c] for c in periodo_visivel2]
                        fig2.add_trace(
                            go.Bar(
                                x=[rotulo_periodo(p) for p in periodo_visivel2],
                                y=valores,
                                name=str(via),
                                marker_color=cor_via.get(via, COR_OUTROS),
                                hovertemplate="%{x}<br>%{y:,.0f}<extra></extra>",
                            )
                        )
                    fig2.update_layout(
                        barmode="group",
                        xaxis_title="Período",
                        yaxis_title=titulo_eixo_metrica,
                        plot_bgcolor="#DBF7FF",
                        paper_bgcolor="white",
                        height=600,
                        legend_title=tx("Via de Entrada"),
                        legend=dict(
                            orientation="h", yanchor="top", y=-0.2,
                            xanchor="center", x=0.5,
                            bgcolor="white", bordercolor="#042373", borderwidth=1,
                        ),
                        margin=dict(t=60, b=140, l=50, r=50),
                    )
                    fig2.update_xaxes(type="category", showline=True, linewidth=2, linecolor="#042373", mirror=True)
                    fig2.update_yaxes(showline=True, linewidth=2, linecolor="#042373", mirror=True)
                    st.plotly_chart(aplicar_rotulos_dados(fig2), use_container_width=True, key=f"grafico2_combinado_{combo_id}")
            else:
                st.info(
                    tx("Nenhuma quebra por via de entrada nos dados retornados para "
                    "esta consulta -- isso costuma acontecer quando os filtros "
                    "aplicados (país, HTS ou via de entrada) resultam em uma única via "
                    "de entrada, ou "
                    "quando nenhuma via de entrada específica foi selecionada e "
                    "há poucos registros no período. Tente ampliar o intervalo "
                    "de anos ou os países/HTS selecionados.")
                )

            # ------------------------------------------------------------
            # Gráfico 3 -- porcentagem por país (top 5 + "Outros"), em barras
            # (equivalente ao gráfico de pizza/rosca do dash Brasil).
            # ------------------------------------------------------------
            st.divider()
            st.markdown(
                tx(f"""
                <h2 style='text-align:center; color:#042373; font-family:Arial; font-weight:bold;'>
                    Porcentagem por País de {metrica_grafico} (Realizado) Importado
                </h2>
                """),
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
                periodo_visivel3 = recortar_periodo(periodo_cols)

                nota_ano_em_andamento(periodo_visivel3)

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

                    paises_selecionados = paises_global

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

                        cores = [cor_pais(l) for l in labels]

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
                            xaxis_title=tx("% do total importado"),
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
                        st.plotly_chart(aplicar_rotulos_dados(fig3), use_container_width=True, key=f"grafico3_pct_pais_{combo_id}")
                else:
                    st.info("Sem valores no período selecionado para calcular percentuais.")
            else:
                st.info(
                    tx("Nenhuma quebra por país nos dados retornados para esta "
                    "consulta -- isso costuma acontecer quando apenas um país "
                    "foi selecionado no filtro (não há o que separar) ou quando "
                    "há poucos registros no período. Selecione mais de um país "
                    "de origem, ou deixe o filtro vazio para trazer todos, e "
                    "tente novamente.")
                )

            # ------------------------------------------------------------
            # Gráfico 4 -- porcentagem por país, um mini-gráfico por via de entrada
            # (top 5 países + "Outros" fixo por via de entrada -- sem seletor de país
            # aqui; o que é ajustável é QUAIS VIAS aparecem).
            # ------------------------------------------------------------
            st.divider()
            st.markdown(
                tx(f"""
                <h2 style='text-align:center; color:#042373; font-family:Arial; font-weight:bold;'>
                    Porcentagem por País de {metrica_grafico} (Realizado) Importado Separado por Via de Entrada
                </h2>
                """),
                unsafe_allow_html=True,
            )
            legenda_unidade_hts()

            if via_col and country_col:
                periodo_visivel4 = recortar_periodo(periodo_cols)

                nota_ano_em_andamento(periodo_visivel4)

                vias_selecionadas4 = vias_global

                if not vias_selecionadas4:
                    st.info(tx("Selecione ao menos uma via de entrada para exibir os gráficos."))
                else:
                    cols_por_linha4 = 2
                    for i in range(0, len(vias_selecionadas4), cols_por_linha4):
                        cols4 = st.columns(cols_por_linha4)
                        for j, via in enumerate(vias_selecionadas4[i:i + cols_por_linha4]):
                            with cols4[j]:
                                st.markdown(titulo_via_html(via, prefixo=tx("Via de Entrada: ")), unsafe_allow_html=True)

                                df_via_pais = df_fonte_grafico[df_fonte_grafico[via_col] == via]
                                df_via_pais = (
                                    df_via_pais.groupby(country_col, as_index=False)[periodo_visivel4]
                                    .sum(min_count=1)
                                )
                                df_via_pais["_valor"] = df_via_pais[periodo_visivel4].sum(axis=1, skipna=True)
                                df_via_pais = df_via_pais.sort_values("_valor", ascending=False)
                                total_via = df_via_pais["_valor"].sum()

                                if not total_via or total_via <= 0:
                                    st.info(tx("Sem dados nessa via de entrada no período selecionado."))
                                    continue

                                top5_via = df_via_pais.head(5)
                                resto_via = df_via_pais["_valor"].iloc[5:].sum()

                                labels4 = top5_via[country_col].tolist()
                                valores4 = top5_via["_valor"].tolist()
                                if resto_via > 0:
                                    labels4.append("Outros")
                                    valores4.append(resto_via)

                                percentuais4 = [v / total_via * 100 for v in valores4]
                                cores4 = [cor_pais(l) for l in labels4]

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
                                    xaxis_title=tx("% do total nessa via de entrada"),
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
                                st.plotly_chart(aplicar_rotulos_dados(fig4), use_container_width=True, key=chart4_key)
            else:
                st.info(
                    tx("Este gráfico exige quebra por país E por via de entrada ao "
                    "mesmo tempo nos dados retornados -- selecione mais de um "
                    "país e mais de uma via de entrada nos filtros (ou deixe ambos vazios "
                    "para trazer todos) e tente novamente.")
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

            if eh_export:
                st.info(
                    "O gráfico de Modal de Transporte (Census API) ainda não está "
                    "disponível para **Exportações**."
                )
            elif metrica_grafico == "Quantidade":
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

                # Este gráfico segue os filtros de país e via da BARRA LATERAL
                # (vazio = todos). Não tem seletores próprios.
                paises_modal_sel = [
                    c for c in st.session_state.get("df_eua_paises_filtro", []) if c in COUNTRY_CODES
                ]
                vias_modal_sel = [
                    d for d in st.session_state.get("df_eua_vias_filtro", []) if d in DISTRICT_CODES
                ]
                st.caption(tx("Segue os filtros de país e via de entrada da barra lateral (vazio = todos)."))

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
                        tx("Sem dados de modal de transporte para essa combinação de "
                        "HTS, período, país e/ou via de entrada. Isso pode acontecer "
                        "porque: (1) o HTS foi revisado na classificação usada por "
                        "essa fonte, ou (2) o país/via filtrado não teve comércio "
                        "registrado nesse recorte. Tente ampliar os filtros, o "
                        "intervalo de anos, ou usar outro HTS.")
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

                    # No Anual, o ano corrente entra como acumulado parcial,
                    # rotulado com o último mês que tem dado na Census.
                    ate_mes_modal = None
                    if not monthly:
                        d_atual = df_census[
                            (df_census["Ano"] == str(ano_atual)) & (df_census["Valor Total"] > 0)
                        ]
                        if not d_atual.empty:
                            ate_mes_modal = MES_ABBR_PT[d_atual["Mes_Num"].max()]
                        else:
                            df_modal = df_modal[
                                df_modal["_periodo_label"] != str(ano_atual)
                            ].reset_index(drop=True)

                    def rotulo_modal(lbl):
                        if (not monthly) and ate_mes_modal and lbl == str(ano_atual):
                            return f"{lbl} (até {ate_mes_modal})"
                        return lbl

                    df_modal["_rotulo"] = df_modal["_periodo_label"].map(rotulo_modal)
                    periodo_modal_cols = df_modal["_periodo_label"].tolist()

                    df_modal_visivel = df_modal[df_modal["_periodo_label"].isin(periodos_sel_global)]
                    if df_modal_visivel.empty:  # recorte global sem sobreposição com a Census
                        df_modal_visivel = df_modal

                    nota_ano_em_andamento(
                        df_modal_visivel["_periodo_label"].tolist(),
                        mes=ate_mes_modal, fonte=" (Census)",
                    )

                    cores_modal = {
                        "Valor Aereo": PALETA_CATEGORICA[2],
                        "Valor Maritimo": PALETA_CATEGORICA[0],
                        "Valor Terrestre": PALETA_CATEGORICA[1],
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
                                    x=df_modal_visivel["_rotulo"],
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
                        ultimo_x = df_modal_visivel["_rotulo"].iloc[-1]
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
                            texto = f"<b>{row['_rotulo']}</b><br>" + "<br>".join(
                                f"{nome}: {valor:,.0f}" for nome, valor in pares
                            )
                            hover_textos.append(texto)
                            y_topo.append(max(row[c] for c in colunas_com_dado))

                        fig_modal.add_trace(
                            go.Scatter(
                                x=df_modal_visivel["_rotulo"],
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
                        fig_modal.update_xaxes(type="category", showline=True, linewidth=2, linecolor="#042373", mirror=True)
                        fig_modal.update_yaxes(showline=True, linewidth=2, linecolor="#042373", mirror=True)
                        st.plotly_chart(aplicar_rotulos_dados(fig_modal), use_container_width=True, key=f"grafico_modal_{combo_id}")

            # ------------------------------------------------------------
            # Gráfico 5 -- volume total (valor absoluto) por país, barras
            # verticais, no formato Pareto 80-20: países que somam 80% do total
            # + "Outros", com linha de % acumulado (não top 5 fixo --
            # equivalente ao gráfico "Volume total transacionado por País"
            # do dash Brasil).
            # ------------------------------------------------------------
            st.divider()
            st.markdown(
                f"""
                <h2 style='text-align:center; color:#042373; font-family:Arial; font-weight:bold;'>
                    Volume total transacionado em {metrica_grafico} (Realizado) por País
                </h2>
                <p style='text-align:center; font-size:0.85rem; color:#666; margin:0;'>
                    Pareto 80-20: países que somam 80% do total + "Outros" (linha = % acumulado)
                </p>
                """,
                unsafe_allow_html=True,
            )
            legenda_unidade_hts()

            if country_col:
                periodo_visivel5 = recortar_periodo(periodo_cols)

                nota_ano_em_andamento(periodo_visivel5)

                df_pais5 = (
                    df_fonte_grafico.groupby(country_col, as_index=False)[periodo_visivel5]
                    .sum(min_count=1)
                )
                df_pais5["_valor"] = df_pais5[periodo_visivel5].sum(axis=1, skipna=True)
                df_pais5 = df_pais5.sort_values("_valor", ascending=False).reset_index(drop=True)

                total_geral5 = df_pais5["_valor"].sum()

                if total_geral5 and total_geral5 > 0:
                    df_pais5["_perc_acumulado"] = df_pais5["_valor"].cumsum() / total_geral5
                    # Pareto 80-20: menor conjunto de países que soma >= 80% do total;
                    # o restante vira "Outros".
                    LIMITE_PARETO = 0.80
                    candidatos_corte = df_pais5[df_pais5["_perc_acumulado"] >= LIMITE_PARETO].index
                    idx_limite5 = candidatos_corte.min() if len(candidatos_corte) else len(df_pais5) - 1

                    df_top = df_pais5.iloc[: idx_limite5 + 1].copy()
                    outros_valor5 = df_pais5["_valor"].iloc[idx_limite5 + 1:].sum()

                    labels5 = df_top[country_col].tolist()
                    valores5 = df_top["_valor"].tolist()
                    acumulado5 = (df_top["_perc_acumulado"] * 100).tolist()
                    if outros_valor5 > 0:
                        labels5.append("Outros")
                        valores5.append(outros_valor5)
                        acumulado5.append(100.0)

                    fig5 = go.Figure()
                    fig5.add_trace(
                        go.Bar(
                            x=labels5,
                            y=valores5,
                            marker_color="#042373",
                            text=[f"{v:,.0f}" if v else "" for v in valores5],
                            textposition="outside",
                            textangle=-90,
                            cliponaxis=False,
                            hovertemplate="%{x}<br>%{y:,.0f}<extra></extra>",
                        )
                    )
                    # Linha de % acumulado (eixo secundário, 0-100%) + referência de 80%.
                    fig5.add_trace(
                        go.Scatter(
                            x=labels5, y=acumulado5, yaxis="y2", mode="lines+markers",
                            line=dict(color=COR_DESTAQUE, width=2), marker=dict(size=7),
                            meta="sem_rotulo",  # não recebe rótulos de dados
                            hovertemplate="%{x}<br>% acumulado: %{y:.1f}%<extra></extra>",
                        )
                    )
                    fig5.add_hline(
                        y=LIMITE_PARETO * 100, yref="y2", line=dict(color="#E8590C", width=1, dash="dash"),
                        annotation_text="80%", annotation_position="top right",
                        annotation_font=dict(size=12, color="#E8590C"),
                    )
                    fig5.update_layout(
                        xaxis_title="País",
                        yaxis_title=titulo_eixo_metrica,
                        xaxis=dict(
                            type="category", categoryorder="array", categoryarray=labels5,
                            tickmode="linear", dtick=1,  # mostra todos os países
                            tickangle=-45, tickfont=dict(size=12), automargin=True,
                        ),
                        yaxis=dict(range=[0, max(valores5) * 1.4]),
                        yaxis2=dict(
                            title="% acumulado", overlaying="y", side="right",
                            range=[0, 105], ticksuffix="%", showgrid=False,
                        ),
                        plot_bgcolor="#DBF7FF",
                        paper_bgcolor="white",
                        height=650,
                        showlegend=False,
                        margin=dict(t=40, b=60, l=60, r=60),
                    )
                    fig5.update_xaxes(showline=True, linewidth=2, linecolor="#042373", mirror=True)
                    fig5.update_yaxes(showline=True, linewidth=2, linecolor="#042373", mirror=True)
                    st.plotly_chart(aplicar_rotulos_dados(fig5), use_container_width=True, key=f"grafico5_pais_{combo_id}")
                else:
                    st.info("Sem valores no período selecionado para exibir este gráfico.")
            else:
                st.info(
                    tx("Nenhuma quebra por país nos dados retornados para esta "
                    "consulta -- selecione mais de um país de origem, ou "
                    "deixe o filtro vazio para trazer todos, e tente novamente.")
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
                tx(f"""
                <h2 style='text-align:center; color:#042373; font-family:Arial; font-weight:bold;'>
                    Volume total transacionado em {metrica_grafico} (Realizado) por País Separado por Via de Entrada
                </h2>
                <p style='text-align:center; font-size:0.85rem; color:#666; margin:0;'>
                    Top 5 + "Outros", por via de entrada
                </p>
                """),
                unsafe_allow_html=True,
            )
            legenda_unidade_hts()

            if via_col and country_col:
                periodo_visivel6 = recortar_periodo(periodo_cols)

                nota_ano_em_andamento(periodo_visivel6)

                vias_selecionadas6 = vias_global

                if not vias_selecionadas6:
                    st.info(tx("Selecione ao menos uma via de entrada para exibir os gráficos."))
                else:
                    cols_por_linha6 = 2
                    for i in range(0, len(vias_selecionadas6), cols_por_linha6):
                        cols6 = st.columns(cols_por_linha6)
                        for j, via in enumerate(vias_selecionadas6[i:i + cols_por_linha6]):
                            with cols6[j]:
                                st.markdown(titulo_via_html(via, prefixo=tx("Via de Entrada: ")), unsafe_allow_html=True)

                                df_via_pais6 = df_fonte_grafico[df_fonte_grafico[via_col] == via]
                                df_via_pais6 = (
                                    df_via_pais6.groupby(country_col, as_index=False)[periodo_visivel6]
                                    .sum(min_count=1)
                                )
                                df_via_pais6["_valor"] = df_via_pais6[periodo_visivel6].sum(axis=1, skipna=True)
                                df_via_pais6 = df_via_pais6.sort_values("_valor", ascending=False).reset_index(drop=True)
                                total_via6 = df_via_pais6["_valor"].sum()

                                if not total_via6 or total_via6 <= 0:
                                    st.info(tx("Sem dados nessa via de entrada no período selecionado."))
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
                                    xaxis_title=titulo_eixo_metrica,
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
                                st.plotly_chart(aplicar_rotulos_dados(fig6), use_container_width=True, key=chart6_key)
            else:
                st.info(
                    tx("Este gráfico exige quebra por país E por via de entrada ao "
                    "mesmo tempo nos dados retornados -- selecione mais de um "
                    "país e mais de uma via de entrada nos filtros (ou deixe ambos vazios "
                    "para trazer todos) e tente novamente.")
                )
