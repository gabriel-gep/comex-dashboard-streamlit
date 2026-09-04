"""
census_trade_client.py

Cliente para a Census Bureau International Trade API -- usado
especificamente para trazer dados de MODO DE TRANSPORTE (Aéreo, Marítimo,
Terrestre), que não está disponível na API do DataWeb.

Esse módulo é independente do dataweb_client.py -- fonte de dados
diferente, formato de resposta diferente, autenticação diferente (chave
simples via querystring, sem token com prazo de validade).

Documentação oficial:
https://www.census.gov/data/developers/data-sets/international-trade.html
Cadastro de chave (gratuita, aparentemente permanente):
https://api.census.gov/data/key_signup.html

Lógica validada manualmente via Postman (ver histórico da conversa):
- CNT_VAL_MO (marítimo containerizado) é sempre igual a VES_VAL_MO nesse
  endpoint -- é um subconjunto do marítimo, não usamos.
- Não existe uma variável direta de "terrestre" (rodoviário/ferroviário).
  Ela é obtida por diferença: GEN_VAL_MO - AIR_VAL_MO - VES_VAL_MO.
  Testado em dois produtos/meses diferentes (crabs congelados e tomates
  frescos) -- nunca deu negativo; distritos de fronteira terrestre
  (Laredo, Nogales, Detroit etc.) vêm 100% como "terrestre calculado",
  e distritos portuários/aeroportuários batem exatamente com AIR+VES.
"""

from __future__ import annotations

import re
from typing import Optional

import pandas as pd
import requests

BASE_URL = "https://api.census.gov/data/timeseries/intltrade/imports/hs"

FIELDS = [
    "I_COMMODITY",
    "I_COMMODITY_SDESC",
    "DISTRICT",
    "DIST_NAME",
    "GEN_VAL_MO",
    "AIR_VAL_MO",
    "VES_VAL_MO",
]

_COMM_LVL_BY_LENGTH = {2: "HS2", 4: "HS4", 6: "HS6", 10: "HS10"}


def _inferir_comm_lvl(hts_code: str) -> Optional[str]:
    return _COMM_LVL_BY_LENGTH.get(len(hts_code))


def _time_param(year_start: str, year_end: str) -> str:
    """
    Monta o parâmetro 'time' no formato de intervalo mensal esperado pela
    Census API: 'from YYYY-01 to YYYY-12'.
    """
    return f"from {year_start}-01 to {year_end}-12"


def fetch_mode_of_transport(
    hts_code: str,
    year_start: str,
    year_end: str,
    api_key: str,
    comm_lvl: Optional[str] = None,
    timeout: int = 30,
) -> pd.DataFrame:
    """
    Busca valor de importação por modo de transporte (Aéreo, Marítimo,
    Terrestre calculado), por distrito e mês, para um código HTS, num
    intervalo de anos.

    Parameters
    ----------
    hts_code : código HTS sem pontuação (2, 4, 6 ou 10 dígitos). HTS de
        10 dígitos pode não ter dado se o código tiver sido revisado --
        nesse caso a API retorna 204 e essa função devolve um DataFrame
        vazio (não é erro).
    year_start, year_end : anos como string (ex: "2020", "2023").
    api_key : chave da Census API (gratuita, ver link no topo do arquivo).
    comm_lvl : nível de agregação do código ("HS2","HS4","HS6","HS10").
        Se None, é inferido automaticamente pelo tamanho do hts_code.

    Returns
    -------
    DataFrame com uma linha por (distrito, ano, mês), já com a coluna
    "Valor Terrestre" calculada. Colunas:
    HTS, Description, District_Code, District, Ano, Mes_Num,
    Valor Aereo, Valor Maritimo, Valor Terrestre, Valor Total.
    DataFrame vazio (sem erro) se a API não tiver dados para essa consulta.
    """
    if comm_lvl is None:
        comm_lvl = _inferir_comm_lvl(hts_code)

    params = {
        "get": ",".join(FIELDS),
        "I_COMMODITY": hts_code,
        "time": _time_param(year_start, year_end),
        "key": api_key,
    }
    if comm_lvl:
        params["COMM_LVL"] = comm_lvl

    resp = requests.get(BASE_URL, params=params, timeout=timeout)

    if resp.status_code == 204:
        # Consulta válida, mas sem dados para essa combinação -- comum
        # quando o HTS foi revisado/descontinuado no período pedido.
        return pd.DataFrame()

    resp.raise_for_status()

    raw = resp.json()
    if not raw or len(raw) < 2:
        return pd.DataFrame()

    header, *rows = raw
    df = pd.DataFrame(rows, columns=header)

    # Remove a linha de total agregado (DISTRICT == "-") -- mantém só o
    # detalhe por distrito, consistente com o padrão "sempre desagregado"
    # já usado no restante do app.
    df = df[df["DISTRICT"] != "-"].copy()
    if df.empty:
        return df

    for col in ["GEN_VAL_MO", "AIR_VAL_MO", "VES_VAL_MO"]:
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)

    df["TERRESTRE_VAL_MO"] = (
        df["GEN_VAL_MO"] - df["AIR_VAL_MO"] - df["VES_VAL_MO"]
    ).clip(lower=0)

    df["Ano"] = df["time"].str[:4]
    df["Mes_Num"] = df["time"].str[5:7]

    df = df.rename(columns={
        "I_COMMODITY": "HTS",
        "I_COMMODITY_SDESC": "Description",
        "DISTRICT": "District_Code",
        "DIST_NAME": "District",
        "GEN_VAL_MO": "Valor Total",
        "AIR_VAL_MO": "Valor Aereo",
        "VES_VAL_MO": "Valor Maritimo",
        "TERRESTRE_VAL_MO": "Valor Terrestre",
    })

    colunas_finais = [
        "HTS", "Description", "District_Code", "District", "Ano", "Mes_Num",
        "Valor Aereo", "Valor Maritimo", "Valor Terrestre", "Valor Total",
    ]
    return df[colunas_finais].reset_index(drop=True)


def fetch_mode_of_transport_multi_hts(
    hts_codes: list[str],
    year_start: str,
    year_end: str,
    api_key: str,
    comm_lvl: Optional[str] = None,
) -> pd.DataFrame:
    """
    Mesma coisa que fetch_mode_of_transport, mas para vários códigos HTS
    de uma vez (a API só aceita um I_COMMODITY por chamada, então isso
    faz uma chamada por HTS e concatena os resultados). HTS sem dados
    (204) são simplesmente ignorados no resultado final -- não geram erro.
    """
    partes = []
    for hts in hts_codes:
        df_hts = fetch_mode_of_transport(hts, year_start, year_end, api_key, comm_lvl)
        if not df_hts.empty:
            partes.append(df_hts)

    if not partes:
        return pd.DataFrame()
    return pd.concat(partes, ignore_index=True)
