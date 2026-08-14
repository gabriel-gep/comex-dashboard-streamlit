"""
dataweb_client.py

Módulo de integração com a API do USITC DataWeb (dados oficiais de comércio
exterior dos EUA) para uso em apps Streamlit.

Cobre apenas fluxo de IMPORTAÇÃO (Import For Consumption), com seleção
dinâmica de códigos HTS, países e anos.

Documentação oficial:
- User Guide: https://www.usitc.gov/applications/dataweb/api/dataweb_query_api.html
- Swagger:    https://datawebws.usitc.gov/dataweb/swagger-ui/index.html
"""

from __future__ import annotations

import datetime as dt
from typing import Optional

import pandas as pd
import requests

BASE_URL = "https://datawebws.usitc.gov/dataweb"
TOKEN_VALIDITY_DAYS = 180  # tokens duram ~6 meses


# --------------------------------------------------------------------------
# 0. Códigos de país (Schedule C - Census Bureau / USITC)
# --------------------------------------------------------------------------
# Fonte oficial: https://www.census.gov/foreign-trade/schedules/c/country.txt
# Confirmado empiricamente via API: China -> "5700", Mexico -> "2010".
# Chave = nome do país (como aparece no Schedule C), Valor = código DataWeb.
COUNTRY_CODES: dict[str, str] = {
    "United States of America": "1000",
    "Greenland": "1010",
    "Canada": "1220",
    "Saint Pierre and Miquelon": "1610",
    "Mexico": "2010",
    "Guatemala": "2050",
    "Belize": "2080",
    "El Salvador": "2110",
    "Honduras": "2150",
    "Nicaragua": "2190",
    "Costa Rica": "2230",
    "Panama": "2250",
    "Bermuda": "2320",
    "Bahamas": "2360",
    "Cuba": "2390",
    "Jamaica": "2410",
    "Turks and Caicos Islands": "2430",
    "Cayman Islands": "2440",
    "Haiti": "2450",
    "Dominican Republic": "2470",
    "Anguilla": "2481",
    "British Virgin Islands": "2482",
    "Saint Kitts and Nevis": "2483",
    "Antigua and Barbuda": "2484",
    "Montserrat": "2485",
    "Dominica": "2486",
    "Saint Lucia": "2487",
    "Saint Vincent and the Grenadines": "2488",
    "Grenada": "2489",
    "Barbados": "2720",
    "Trinidad and Tobago": "2740",
    "Sint Maarten": "2774",
    "Curacao": "2777",
    "Aruba": "2779",
    "Guadeloupe": "2831",
    "Martinique": "2839",
    "Colombia": "3010",
    "Venezuela": "3070",
    "Guyana": "3120",
    "Suriname": "3150",
    "French Guiana": "3170",
    "Ecuador": "3310",
    "Peru": "3330",
    "Bolivia": "3350",
    "Chile": "3370",
    "Brazil": "3510",
    "Paraguay": "3530",
    "Uruguay": "3550",
    "Argentina": "3570",
    "Falkland Islands (Islas Malvinas)": "3720",
    "Iceland": "4000",
    "Sweden": "4010",
    "Svalbard and Jan Mayen": "4031",
    "Norway": "4039",
    "Finland": "4050",
    "Faroe Islands": "4091",
    "Denmark, except Greenland": "4099",
    "United Kingdom": "4120",
    "Ireland": "4190",
    "Netherlands": "4210",
    "Belgium": "4231",
    "Luxembourg": "4239",
    "Andorra": "4271",
    "Monaco": "4272",
    "France": "4279",
    "Germany (Federal Republic of Germany)": "4280",
    "Austria": "4330",
    "Czech Republic": "4351",
    "Slovakia": "4359",
    "Hungary": "4370",
    "Liechtenstein": "4411",
    "Switzerland": "4419",
    "Estonia": "4470",
    "Latvia": "4490",
    "Lithuania": "4510",
    "Poland": "4550",
    "Russia": "4621",
    "Belarus": "4622",
    "Ukraine": "4623",
    "Armenia": "4631",
    "Azerbaijan": "4632",
    "Georgia": "4633",
    "Kazakhstan": "4634",
    "Kyrgyzstan": "4635",
    "Moldova (Republic of Moldova)": "4641",
    "Tajikistan": "4642",
    "Turkmenistan": "4643",
    "Uzbekistan": "4644",
    "Spain": "4700",
    "Portugal": "4710",
    "Gibraltar": "4720",
    "Malta": "4730",
    "San Marino": "4751",
    "Holy See (Vatican City)": "4752",
    "Italy": "4759",
    "Croatia": "4791",
    "Slovenia": "4792",
    "Bosnia and Herzegovina": "4793",
    "North Macedonia": "4794",
    "Serbia": "4801",
    "Kosovo": "4803",
    "Montenegro": "4804",
    "Albania": "4810",
    "Greece": "4840",
    "Romania": "4850",
    "Bulgaria": "4870",
    "Turkey": "4890",
    "Cyprus": "4910",
    "Syria (Syrian Arab Republic)": "5020",
    "Lebanon": "5040",
    "Iraq": "5050",
    "Iran": "5070",
    "Israel": "5081",
    "Gaza Strip administered by Israel": "5082",
    "West Bank administered by Israel": "5083",
    "Jordan": "5110",
    "Kuwait": "5130",
    "Saudi Arabia": "5170",
    "Qatar": "5180",
    "United Arab Emirates": "5200",
    "Yemen (Republic of Yemen)": "5210",
    "Oman": "5230",
    "Bahrain": "5250",
    "Afghanistan": "5310",
    "India": "5330",
    "Pakistan": "5350",
    "Nepal": "5360",
    "Bangladesh": "5380",
    "Sri Lanka": "5420",
    "Burma (Myanmar)": "5460",
    "Thailand": "5490",
    "Vietnam": "5520",
    "Laos (Lao People's Democratic Republic)": "5530",
    "Cambodia": "5550",
    "Malaysia": "5570",
    "Singapore": "5590",
    "Indonesia": "5600",
    "Timor-Leste": "5601",
    "Brunei": "5610",
    "Philippines": "5650",
    "Macao": "5660",
    "Bhutan": "5682",
    "Maldives": "5683",
    "China": "5700",
    "Mongolia": "5740",
    "North Korea (Democratic People's Republic of Korea)": "5790",
    "South Korea (Republic of Korea)": "5800",
    "Hong Kong": "5820",
    "Taiwan": "5830",
    "Japan": "5880",
    "Australia": "6021",
    "Norfolk Island": "6022",
    "Cocos (Keeling) Islands": "6023",
    "Christmas Island (in the Indian Ocean)": "6024",
    "Heard Island and McDonald Islands": "6029",
    "Papua New Guinea": "6040",
    "New Zealand": "6141",
    "Cook Islands": "6142",
    "Tokelau": "6143",
    "Niue": "6144",
    "Samoa (Western Samoa)": "6150",
    "Solomon Islands": "6223",
    "Vanuatu": "6224",
    "Pitcairn Islands": "6225",
    "Kiribati": "6226",
    "Tuvalu": "6227",
    "New Caledonia": "6412",
    "Wallis and Futuna": "6413",
    "French Polynesia": "6414",
    "Marshall Islands": "6810",
    "Micronesia, Federated States of": "6820",
    "Palau": "6830",
    "Nauru": "6862",
    "Fiji": "6863",
    "Tonga": "6864",
    "Morocco": "7140",
    "Algeria": "7210",
    "Tunisia": "7230",
    "Libya": "7250",
    "Egypt": "7290",
    "Sudan": "7321",
    "South Sudan": "7323",
    "Western Sahara": "7370",
    "Equatorial Guinea": "7380",
    "Mauritania": "7410",
    "Cameroon": "7420",
    "Senegal": "7440",
    "Mali": "7450",
    "Guinea": "7460",
    "Sierra Leone": "7470",
    "Cote d'Ivoire": "7480",
    "Ghana": "7490",
    "Gambia": "7500",
    "Niger": "7510",
    "Togo": "7520",
    "Nigeria": "7530",
    "Central African Republic": "7540",
    "Gabon": "7550",
    "Chad": "7560",
    "Saint Helena": "7580",
    "Burkina Faso": "7600",
    "Benin": "7610",
    "Angola": "7620",
    "Congo, Republic of the Congo": "7630",
    "Guinea-Bissau": "7642",
    "Cabo Verde": "7643",
    "Sao Tome and Principe": "7644",
    "Liberia": "7650",
    "Congo, Democratic Republic of the Congo (formerly Zaire)": "7660",
    "Burundi": "7670",
    "Rwanda": "7690",
    "Somalia": "7700",
    "Eritrea": "7741",
    "Ethiopia": "7749",
    "Djibouti": "7770",
    "Uganda": "7780",
    "Kenya": "7790",
    "Seychelles": "7800",
    "British Indian Ocean Territory": "7810",
    "Tanzania (United Republic of Tanzania)": "7830",
    "Mauritius": "7850",
    "Mozambique": "7870",
    "Madagascar": "7880",
    "Mayotte": "7881",
    "Comoros": "7890",
    "Reunion": "7904",
    "French Southern and Antarctic Lands": "7905",
    "South Africa": "7910",
    "Namibia": "7920",
    "Botswana": "7930",
    "Zambia": "7940",
    "Eswatini": "7950",
    "Zimbabwe": "7960",
    "Malawi": "7970",
    "Lesotho": "7990",
    "Puerto Rico": "9030",
    "Virgin Islands of the United States": "9110",
    "Guam": "9350",
    "American Samoa": "9510",
    "Northern Mariana Islands": "9610",
    "United States Minor Outlying Islands": "9800",
}


def country_names_to_codes(names: list[str]) -> list[str]:
    """
    Converte uma lista de nomes de país (conforme COUNTRY_CODES) para os
    códigos internos usados pela API. Nomes não encontrados são ignorados
    silenciosamente -- valide a lista antes de exibir ao usuário.
    """
    return [COUNTRY_CODES[name] for name in names if name in COUNTRY_CODES]


# --------------------------------------------------------------------------
# 1. Construção dinâmica da query
# --------------------------------------------------------------------------

def build_import_query(
    hts_codes: list[str],
    years: list[str],
    countries: Optional[list[str]] = None,
    aggregate_commodities: bool = False,
    aggregate_countries: bool = True,
    granularity: str = "10",
) -> dict:
    """
    Monta o payload JSON esperado pelo endpoint runReport para consultas
    de Importação (Import For Consumption) por código HTS.

    Parameters
    ----------
    hts_codes : lista de códigos HTS (ex: ["0306144030", "0901.21"])
    years : lista de anos como string (ex: ["2020", "2021", ...])
    countries : lista de NOMES de país conforme as chaves de COUNTRY_CODES
        (ex.: ["China", "Mexico"]). Se None ou vazia, consulta "todos os
        países". Internamente são convertidos para os códigos Schedule C
        que a API exige.
    aggregate_commodities : se True, soma todos os HTS codes numa única
        linha. Se False, mostra uma linha por código (Break Out).
    aggregate_countries : se True, soma todos os países numa única coluna.
        Se False, quebra por país (exige lista de países preenchida).
    granularity : nível de detalhe HTS (ex: "10" para HTS-10).

    Returns
    -------
    dict pronto para ser enviado como JSON body em POST /api/v2/report2/runReport
    """
    countries = countries or []
    country_codes = country_names_to_codes(countries)

    query = {
        "savedQueryDatabaseId": None,
        "savedQueryID": "",
        "savedQueryName": "",
        "savedQueryDesc": "",
        "savedQueryType": "U",
        "jobID": None,
        "jobState": None,
        "folderID": None,
        "folderName": None,
        "expandedGroups": {"commodities": [], "countries": [], "districts": []},
        "isOwner": True,
        "apiToken": "",
        "captchaResponse": "",
        "captchaValid": False,
        "queryJSON": "",
        "runMonthly": None,
        "deletedCountryUserGroups": [],
        "deletedCommodityUserGroups": [],
        "deletedDistrictUserGroups": [],
        "reportOptions": {
            "tradeType": "Import",
            "classificationSystem": "HTS",
        },
        "searchOptions": {
            "componentSettings": {
                "dataToReport": ["CONS_CUSTOMS_VALUE"],
                "scale": "1",
                "timeframeSelectType": "fullYears",
                "years": years,
                "startDate": None,
                "endDate": None,
                "startMonth": None,
                "endMonth": None,
                "yearsTimeline": "Annual",
            },
            "commodities": {
                "commodities": hts_codes,
                "commoditiesExpanded": [
                    {"name": code, "value": code, "hasChildren": None}
                    for code in hts_codes
                ],
                "commoditiesManual": ",".join(hts_codes),
                "commodityGroups": {"systemGroups": [], "userGroups": []},
                "granularity": granularity,
                "searchGranularity": "2",
                "groupGranularity": "2",
                "aggregation": "Aggregate Commodities"
                if aggregate_commodities
                else "Break Out Commodities",
                "codeDisplayFormat": "YES",
                "commoditySelectType": "list",
                "showHTSValidDetails": False,
            },
            "countries": {
                "countries": country_codes,
                "countriesExpanded": [
                    {"name": name, "value": COUNTRY_CODES[name]}
                    for name in countries
                    if name in COUNTRY_CODES
                ],
                "countryGroups": {"systemGroups": [], "userGroups": []},
                "aggregation": "Aggregate Countries"
                if aggregate_countries
                else "Break Out Countries",
                "countriesSelectType": "all" if not country_codes else "list",
            },
            "MiscGroup": {
                "importPrograms": {"importPrograms": [], "aggregation": "Aggregate CSC"},
                "extImportPrograms": {
                    "programsSelectType": "all",
                    "extImportPrograms": [],
                    "extImportProgramsExpanded": [],
                    "aggregation": "Aggregate CSC",
                },
                "provisionCodes": {
                    "rateProvisionCodes": [],
                    "rateProvisionCodesExpanded": [],
                    "aggregation": "Aggregate RPCODE",
                    "provisionCodesSelectType": "all",
                    "rateProvisionGroups": {"systemGroups": []},
                },
                "districts": {
                    "districts": [],
                    "districtsExpanded": [],
                    "districtGroups": {"userGroups": []},
                    "aggregation": "Aggregate District",
                    "districtsSelectType": "all",
                },
            },
        },
        "sortingAndDataFormat": {
            "DataSort": {"sortOrder": [], "columnOrder": [], "sortYear": None},
            "reportCustomizations": {
                "totalRecords": "20000",
                "exportCombineTables": False,
                "reportsGrid": True,
                "removeDuplicateValues": True,
                "suppressZeroValues": False,
                "displayCommodityList": False,
                "reportsFontSize": "m",
                "exportRawData": False,
            },
        },
        "unitConversion": "0",
        "manualConversions": [],
    }
    return query


# --------------------------------------------------------------------------
# 2. Chamada à API
# --------------------------------------------------------------------------

def run_report(query: dict, token: str) -> dict:
    """Executa a query no endpoint runReport e retorna o JSON bruto."""
    headers = {
        "Content-Type": "application/json; charset=utf-8",
        "Authorization": f"Bearer {token}",
    }
    resp = requests.post(
        f"{BASE_URL}/api/v2/report2/runReport",
        headers=headers,
        json=query,
        verify=False,
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


# --------------------------------------------------------------------------
# 3. Parsing da resposta -> DataFrame
# --------------------------------------------------------------------------

def _get_columns(column_groups, prev_cols=None):
    columns = [] if prev_cols is None else prev_cols
    for group in column_groups:
        if isinstance(group, dict) and "columns" in group:
            _get_columns(group["columns"], columns)
        elif isinstance(group, dict) and "label" in group:
            columns.append(group["label"])
        elif isinstance(group, list):
            _get_columns(group, columns)
    return columns


def _get_data(data_groups):
    data = []
    for row in data_groups:
        row_data = [field["value"] for field in row["rowEntries"]]
        data.append(row_data)
    return data


def parse_report(response_json: dict, measure_num: int = 0) -> pd.DataFrame:
    """Converte a resposta do runReport em DataFrame do pandas."""
    table = response_json["dto"]["tables"][measure_num]
    columns = _get_columns(table["column_groups"])
    data = _get_data(table["row_groups"][0]["rowsNew"])
    return pd.DataFrame(data, columns=columns)


# --------------------------------------------------------------------------
# 4. Controle de expiração do token
# --------------------------------------------------------------------------

def token_status(token_generated_on: str, warn_days_before: int = 15) -> dict:
    """
    Calcula quantos dias faltam para o token expirar.

    Parameters
    ----------
    token_generated_on : data no formato "YYYY-MM-DD" em que o token
        foi gerado manualmente no site do DataWeb.
    warn_days_before : quantos dias antes do vencimento o alerta deve
        ser disparado.

    Returns
    -------
    dict com: expires_on (date), days_left (int), should_warn (bool),
    is_expired (bool)
    """
    generated = dt.date.fromisoformat(token_generated_on)
    expires_on = generated + dt.timedelta(days=TOKEN_VALIDITY_DAYS)
    days_left = (expires_on - dt.date.today()).days

    return {
        "expires_on": expires_on,
        "days_left": days_left,
        "should_warn": days_left <= warn_days_before,
        "is_expired": days_left < 0,
    }
